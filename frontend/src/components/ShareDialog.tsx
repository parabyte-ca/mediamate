import { useState } from "react";
import { Check, Copy, Globe, Home, Loader2, Share2, X } from "lucide-react";
import { api, type ShareLink } from "../api/client";

const DAYS: { v: 0 | 1 | 7 | 30; label: string }[] = [
  { v: 1, label: "1 day" }, { v: 7, label: "7 days" }, { v: 30, label: "30 days" }, { v: 0, label: "Until I remove it" },
];

async function shareOrCopy(url: string, title: string): Promise<"shared" | "copied"> {
  if (navigator.share && /Android|iPhone|iPad/.test(navigator.userAgent)) {
    try { await navigator.share({ title, url }); return "shared"; } catch { /* cancelled: fall back to copying */ }
  }
  await navigator.clipboard.writeText(url);
  return "copied";
}

/** Share a video or album: a household link (opens it in MediaMate) or a public link
 *  (anyone with it can watch or listen in the browser, until it expires). */
export default function ShareDialog({ kind, id, title, householdPath, onClose }: {
  kind: "video" | "album"; id: number; title: string; householdPath: string; onClose: () => void;
}) {
  const [days, setDays] = useState<0 | 1 | 7 | 30>(7);
  const [link, setLink] = useState<ShareLink | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const home = `${window.location.origin}${householdPath}`;

  const send = async (url: string, what: string) => {
    try { setDone((await shareOrCopy(url, title)) === "shared" ? `${what} shared` : `${what} copied`); }
    catch { setErr("Couldn't copy. Press and hold the link to copy it."); }
  };
  const create = async () => {
    setBusy(true); setErr(null);
    try { const l = await api.shares.create(kind, id, days); setLink(l); await send(l.url, "Link"); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  };
  const btn = "flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium";

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-4" role="dialog" aria-modal aria-label={`Share ${title}`}>
      <div className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-t-2xl bg-white p-5 shadow-xl dark:bg-gray-900 sm:rounded-2xl">
        <div className="mb-4 flex items-start gap-2">
          <Share2 className="mt-0.5 h-5 w-5 shrink-0 text-blue-600" />
          <h2 className="min-w-0 flex-1 break-words font-semibold text-gray-900 dark:text-gray-100">Share “{title}”</h2>
          <button onClick={onClose} aria-label="Close" className="rounded p-1 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"><X className="h-4 w-4" /></button>
        </div>

        <section className="rounded-xl border border-gray-200 p-3 dark:border-gray-700">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold text-gray-800 dark:text-gray-200"><Home className="h-4 w-4" />Household</h3>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Opens it in MediaMate. Works for anyone in the house (or signed in from outside).</p>
          <button onClick={() => send(home, "Household link")} className={`${btn} mt-2 w-full border border-gray-300 text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800`}>
            <Copy className="h-4 w-4" />Copy household link
          </button>
        </section>

        <section className="mt-3 rounded-xl border border-gray-200 p-3 dark:border-gray-700">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold text-gray-800 dark:text-gray-200"><Globe className="h-4 w-4" />Anyone with the link</h3>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            They can {kind === "video" ? "watch it" : "listen to it"} in their browser without logging in, until the link expires. You can remove it any time in Settings → Sharing.
          </p>
          {!link ? (
            <>
              <div className="mt-2 grid grid-cols-2 gap-1.5" role="radiogroup" aria-label="Link lasts">
                {DAYS.map((d) => (
                  <button key={d.v} role="radio" aria-checked={days === d.v} onClick={() => setDays(d.v)}
                          className={`rounded-lg border px-2 py-1.5 text-sm ${days === d.v ? "border-blue-600 bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300" : "border-gray-300 text-gray-600 dark:border-gray-600 dark:text-gray-300"}`}>
                    {d.label}
                  </button>
                ))}
              </div>
              <button onClick={create} disabled={busy} className={`${btn} mt-2 w-full bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-60`}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Share2 className="h-4 w-4" />}Create link
              </button>
            </>
          ) : (
            <div className="mt-2 space-y-2">
              <input readOnly value={link.url} onFocus={(e) => e.target.select()} aria-label="Shared link"
                     className="w-full rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 font-mono text-xs text-gray-700 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200" />
              <button onClick={() => send(link.url, "Link")} className={`${btn} w-full border border-gray-300 text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800`}>
                <Copy className="h-4 w-4" />Copy again
              </button>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                {link.expires_at ? `Works until ${new Date(link.expires_at + "Z").toLocaleString()}.` : "Works until you remove it."}
              </p>
            </div>
          )}
        </section>

        {done && <p className="mt-3 flex items-center gap-1.5 text-sm text-green-600 dark:text-green-400"><Check className="h-4 w-4" />{done}</p>}
        {err && <p className="mt-3 text-sm text-red-600 dark:text-red-400">{err}</p>}
      </div>
    </div>
  );
}
