import { useEffect, useState } from "react";
import { Bell, BellOff, Loader2, Send, Smartphone, Trash2 } from "lucide-react";
import { api, type PushConfig } from "../api/client";

// Settings → Notifications: push notifications on this phone/computer.

function b64ToBytes(b64: string) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

function deviceLabel() {
  const ua = navigator.userAgent;
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "Browser";
  const os = /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Mac OS X/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "";
  return os ? `${browser} on ${os}` : browser;
}

const btn = "flex min-h-[36px] items-center gap-1.5 rounded-lg px-3 text-sm disabled:opacity-50";
const btnGhost = `${btn} border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800`;
const btnPrimary = `${btn} bg-blue-600 font-medium text-white hover:bg-blue-700`;

export default function PushSettings() {
  const supported = typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const secure = typeof window !== "undefined" && window.isSecureContext;
  const [cfg, setCfg] = useState<PushConfig | null>(null);
  const [mine, setMine] = useState<PushSubscription | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = async () => {
    setCfg(await api.push.config());
    if (supported && secure) {
      const reg = await navigator.serviceWorker.getRegistration();
      setMine((await reg?.pushManager.getSubscription()) ?? null);
    }
  };
  useEffect(() => { load().catch(() => {}); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const enable = async () => {
    setBusy(true);
    setMsg(null);
    try {
      if ((await Notification.requestPermission()) !== "granted") {
        setMsg("Notifications are blocked for this site — allow them in the browser's site settings.");
        return;
      }
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(cfg!.public_key) });
      await api.push.subscribe(sub.toJSON(), deviceLabel());
      setMsg("This device will now get MediaMate notifications.");
      await load();
    } catch (e) {
      const m = (e as Error).message;
      setMsg(/permission|denied|NotAllowed/i.test(m)
        ? "The browser wouldn't allow push here. Allow notifications for this site (padlock icon → Notifications), then try again."
        : m);
    } finally {
      setBusy(false);
    }
  };

  const disable = async () => {
    setBusy(true);
    try {
      if (mine) {
        await api.push.unsubscribe(mine.endpoint);
        await mine.unsubscribe();
      }
      setMsg("Turned off on this device.");
      await load();
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.push.test();
      setMsg(`Sent to ${r.sent} device${r.sent === 1 ? "" : "s"}.`);
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">Push notifications</h3>
        <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
          Get the same alerts as Apprise straight on this phone or computer — no extra service needed.
          On iPhone, add MediaMate to the Home Screen first (Share → Add to Home Screen).
        </p>
      </div>
      {!supported ? (
        <p className="text-sm text-gray-500">This browser doesn't support push notifications.</p>
      ) : !secure ? (
        <p className="rounded-lg bg-amber-50 dark:bg-amber-950/30 p-3 text-xs text-amber-800 dark:text-amber-200">
          Browsers only allow push notifications over HTTPS. Open MediaMate through your HTTPS address
          (e.g. a Cloudflare Tunnel or reverse proxy) to turn them on for this device.
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {mine ? (
            <button className={btnGhost} onClick={disable} disabled={busy}><BellOff className="h-4 w-4" />Turn off on this device</button>
          ) : (
            <button className={btnPrimary} onClick={enable} disabled={busy || !cfg}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Bell className="h-4 w-4" />}Turn on for this device
            </button>
          )}
          <button className={btnGhost} onClick={test} disabled={busy || !cfg?.devices.length}><Send className="h-4 w-4" />Send a test</button>
        </div>
      )}
      {msg && <p className="text-sm text-gray-600 dark:text-gray-300">{msg}</p>}
      {!!cfg?.devices.length && (
        <ul className="divide-y divide-gray-100 dark:divide-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          {cfg.devices.map((d) => (
            <li key={d.id} className="flex items-center gap-2 px-3 py-2 text-sm text-gray-700 dark:text-gray-200">
              <Smartphone className="h-4 w-4 text-gray-400" />
              <span className="flex-1">{d.label ?? "Device"}</span>
              <button aria-label={`Remove ${d.label ?? "device"}`} className="rounded p-1.5 text-gray-400 hover:bg-red-50 dark:hover:bg-red-950 hover:text-red-500"
                      onClick={async () => { await api.push.removeDevice(d.id); load(); }}>
                <Trash2 className="h-4 w-4" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
