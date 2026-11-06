import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, Clock, Download, RefreshCw, Undo2, Terminal, Wifi, WifiOff } from "lucide-react";
import { api, type UpdateStatus, type UpgradePhase } from "../api/client";
import { marked } from "marked";

marked.setOptions({ async: false });

/** An upgrade started from this browser. MediaMate restarts during it, so the
 *  page keeps reconnecting until the new version answers (survives a refresh). */
type Pending = { from: string; to: string; at: number; phase?: UpgradePhase };
const PENDING_KEY = "sm-upgrade-pending";
const PENDING_MAX_MS = 30 * 60 * 1000;

function readPending(): Pending | null {
  try {
    const p = JSON.parse(sessionStorage.getItem(PENDING_KEY) || "null") as Pending | null;
    return p && Date.now() - p.at < PENDING_MAX_MS ? p : null;
  } catch {
    return null;
  }
}

function writePending(p: Pending | null) {
  try {
    if (p) sessionStorage.setItem(PENDING_KEY, JSON.stringify(p));
    else sessionStorage.removeItem(PENDING_KEY);
  } catch { /* private mode: the page still works, it just forgets on refresh */ }
}

const vtag = (v: string) => (v.startsWith("v") ? v : `v${v}`);

/** Past this, the page suggests looking at the upgrade log on the host. */
const SLOW_MS = 15 * 60 * 1000;

function elapsedText(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** The steps an upgrade goes through, in order. */
const STEPS: { phase: UpgradePhase; label: string }[] = [
  { phase: "requested",   label: "Requested" },
  { phase: "started",     label: "Helper started" },
  { phase: "downloading", label: "Downloading" },
  { phase: "restarting",  label: "Restarting" },
  { phase: "done",        label: "Done" },
];
const stepIndex = (p?: UpgradePhase | null) => Math.max(0, STEPS.findIndex((s) => s.phase === p));
/** The further of two phases, so the steps never go backwards. */
const furthest = (a?: UpgradePhase | null, b?: UpgradePhase | null): UpgradePhase =>
  (stepIndex(a) >= stepIndex(b) ? a : b) || "requested";

/** How long the last upgrade on this device took, to estimate the next one. */
const LAST_KEY = "sm-upgrade-last-ms";
function readLastMs(): number | null {
  try {
    const v = Number(localStorage.getItem(LAST_KEY));
    return v > 0 ? v : null;
  } catch {
    return null;
  }
}
function writeLastMs(ms: number) {
  try { localStorage.setItem(LAST_KEY, String(Math.round(ms))); } catch { /* not remembered */ }
}
/** The server's typical upgrade time, kept here too: while MediaMate restarts the
 *  page can't ask for it, and that's when the bar needs it most. */
const TYPICAL_KEY = "sm-upgrade-typical";
function readTypical(): { sec: number; n: number } | null {
  try {
    const v = JSON.parse(localStorage.getItem(TYPICAL_KEY) || "null");
    return v && v.sec > 0 ? v : null;
  } catch {
    return null;
  }
}
function writeTypical(sec?: number | null, n?: number) {
  try {
    if (sec && sec > 0) localStorage.setItem(TYPICAL_KEY, JSON.stringify({ sec, n: n ?? 1 }));
  } catch { /* not remembered */ }
}

/** What an upgrade started here is doing: steps, an estimated bar and a timer. */
function UpgradeProgress({ pending, everyMinutes, now, typicalSec, basedOn }: {
  pending: Pending; everyMinutes: number; now: number; typicalSec?: number | null; basedOn?: number;
}) {
  const elapsed = now - pending.at;
  const current = stepIndex(pending.phase);
  if (!typicalSec) {
    const kept = readTypical();
    if (kept) { typicalSec = kept.sec; basedOn = kept.n; }
  }
  // Best: the typical (median) time of recent upgrades, kept by the server. Then the
  // last one timed on this device. Without either: on average the helper waits half
  // its interval, then ~3 min of work.
  const last = typicalSec ? null : readLastMs();
  const estimate = typicalSec ? typicalSec * 1000 : last ?? (everyMinutes / 2 + 3) * 60_000;
  const basis = typicalSec
    ? (basedOn ?? 0) > 1
      ? `upgrades usually take ${elapsedText(estimate)} (last ${basedOn})`
      : `last upgrade took ${elapsedText(estimate)}`
    : last ? `last upgrade took ${elapsedText(last)}` : "first-time estimate";
  const pct = Math.min(elapsed / estimate, 0.95);
  const leftMin = Math.ceil((estimate - elapsed) / 60_000);
  return (
    <div className="space-y-4 w-full min-w-0">
      <div className="flex items-baseline justify-between gap-3">
        <p className="font-medium">Upgrading to {vtag(pending.to)}</p>
        <p className="font-mono tabular-nums" aria-label="Time elapsed">{elapsedText(elapsed)}</p>
      </div>

      <ol className="grid grid-cols-5 gap-1" aria-label="Upgrade steps">
        {STEPS.map((step, i) => {
          const done = i < current;
          const active = i === current;
          return (
            <li key={step.phase} className="flex flex-col items-center gap-1.5 text-center min-w-0"
                aria-current={active ? "step" : undefined}>
              <div className="flex items-center w-full">
                <span className={`h-0.5 flex-1 ${i === 0 ? "invisible" : done || active ? "bg-blue-600 dark:bg-blue-400" : "bg-gray-300 dark:bg-gray-700"}`} />
                <span className={`h-3 w-3 rounded-full shrink-0 ${
                  done ? "bg-blue-600 dark:bg-blue-400"
                  : active ? "bg-blue-600 dark:bg-blue-400 ring-4 ring-blue-600/20 dark:ring-blue-400/25 animate-pulse"
                  : "bg-gray-300 dark:bg-gray-700"}`} />
                <span className={`h-0.5 flex-1 ${i === STEPS.length - 1 ? "invisible" : done ? "bg-blue-600 dark:bg-blue-400" : "bg-gray-300 dark:bg-gray-700"}`} />
              </div>
              <span className={`text-[11px] leading-tight ${active ? "font-semibold" : "opacity-75"}`}>{step.label}</span>
            </li>
          );
        })}
      </ol>

      <div className="space-y-1.5">
        <div className="h-2 rounded-full bg-blue-100 dark:bg-blue-950 overflow-hidden" role="progressbar"
             aria-label="Estimated progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct * 100)}>
          <div className="h-full rounded-full bg-blue-600 dark:bg-blue-400 transition-[width] duration-1000 ease-linear"
               style={{ width: `${pct * 100}%` }} />
        </div>
        <p className="text-xs opacity-80">
          {elapsed < estimate
            ? `About ${leftMin} min left`
            : "Taking a little longer than usual…"}
          {" · "}
          {basis}
        </p>
      </div>

      <p className="text-xs opacity-80">
        MediaMate stops answering for a minute or two while it restarts. This page reconnects by itself.
      </p>
      {elapsed > SLOW_MS && (
        <p className="text-xs opacity-90">
          This is taking longer than usual. On the host, check{" "}
          <code className="font-mono">tail -20 upgrade/upgrade.log</code>.
        </p>
      )}
    </div>
  );
}

function renderMarkdown(md: string): string {
  const result = marked.parse(md) as string;
  return result;
}

type UpgradeState = "requested" | "running" | "succeeded" | "failed" | null;

function StateChip({ state }: { state: UpgradeState }) {
  if (!state) return null;
  const map: Record<NonNullable<UpgradeState>, { label: string; className: string }> = {
    requested: { label: "Queued",    className: "bg-yellow-100 dark:bg-yellow-900/30 text-yellow-800 dark:text-yellow-300" },
    running:   { label: "Upgrading", className: "bg-blue-100 dark:bg-blue-900/30 text-blue-800 dark:text-blue-300 animate-pulse" },
    succeeded: { label: "Succeeded", className: "bg-green-100 dark:bg-green-900/30 text-green-800 dark:text-green-300" },
    failed:    { label: "Failed",    className: "bg-red-100 dark:bg-red-900/30 text-red-800 dark:text-red-300" },
  };
  const { label, className } = map[state];
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${className}`}>
      {label}
    </span>
  );
}

export default function Updates() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [upgrading, setUpgrading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<"upgrade" | "rollback" | null>(null);
  const [showLog, setShowLog] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [pending, setPending] = useState<Pending | null>(readPending);
  const [upgraded, setUpgraded] = useState<{ to: string; took: number } | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // Tick the upgrade timer once a second while an upgrade started here is under way.
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [pending]);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const s = await api.updates.status();
      setStatus(s);
      writeTypical(s.upgrade_estimate_seconds, s.upgrade_history_count);
      setError(null);
      const p = readPending();
      if (p) {
        const done = s.current_version.replace(/^v/, "") === p.to.replace(/^v/, "");
        if (done) {
          const took = Date.now() - p.at;
          writeLastMs(took);
          setUpgraded({ to: p.to, took });
        }
        if (done || s.upgrade_state === "failed") {
          writePending(null);
          setPending(null);
        } else {
          const next = { ...p, phase: furthest(p.phase, s.upgrade_phase) };
          writePending(next);
          setPending(next);
        }
      }
    } catch (e) {
      setError((e as Error).message);
      // Once the helper has started, losing MediaMate means it is restarting.
      const p = readPending();
      if (p && stepIndex(p.phase) >= stepIndex("started")) {
        const next = { ...p, phase: furthest(p.phase, "restarting") };
        writePending(next);
        setPending(next);
      }
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  // Poll while an upgrade is in progress, while one started here hasn't finished
  // (MediaMate stops answering while it restarts), and while the page can't load.
  useEffect(() => {
    const inProgress = status?.upgrade_state === "requested" || status?.upgrade_state === "running"
      || !!pending || (!status && !!error);
    if (inProgress && !pollRef.current) {
      pollRef.current = setInterval(() => load(true), 5000);
    }
    if (!inProgress && pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    return () => {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [status?.upgrade_state, status, pending, error, load]);

  useEffect(() => { load(); }, [load]);

  // Auto-scroll log
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [status?.upgrade_log]);

  const handleCheck = async () => {
    setChecking(true);
    try {
      const s = await api.updates.check();
      setStatus(s);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChecking(false);
    }
  };

  const handleUpgrade = async () => {
    const rollback = confirm === "rollback";
    const version = rollback ? status?.previous_version : status?.latest_release?.version;
    if (!version) return;
    setConfirm(null);
    setUpgrading(true);
    setShowLog(true);
    setUpgraded(null);
    const p: Pending = { from: status?.current_version ?? "", to: version, at: Date.now(), phase: "requested" };
    writePending(p);
    setPending(p);
    try {
      const s = await api.updates.upgrade(version, rollback);
      setStatus(s);
    } catch (e) {
      const msg = (e as Error).message;
      // Refused by MediaMate itself (a JSON reason): nothing was started.
      if (!/isn't responding|Failed to fetch|NetworkError|Load failed/i.test(msg)) {
        writePending(null);
        setPending(null);
      }
      setError(msg);
    } finally {
      setUpgrading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="h-6 w-6 animate-spin text-blue-500" />
      </div>
    );
  }

  const inProgress = status?.upgrade_state === "requested" || status?.upgrade_state === "running";
  // While an upgrade started here is under way, losing the connection is expected.
  const reconnecting = !!pending && !!error;
  const everyMinutes = status?.helper?.every_minutes || 5;

  if (!status) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-8 space-y-6">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Updates</h1>
        <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-5 space-y-2">
          {pending ? (
            <div className="flex items-start gap-2 text-sm text-gray-700 dark:text-gray-300">
              <UpgradeProgress pending={pending} everyMinutes={everyMinutes} now={now} />
            </div>
          ) : (
            <>
              <div className="flex items-center gap-2 font-medium text-gray-900 dark:text-gray-100">
                <RefreshCw className="h-4 w-4 animate-spin text-blue-500" />
                Can't reach MediaMate
              </div>
              <p className="text-sm text-gray-600 dark:text-gray-400">{error}</p>
            </>
          )}
          <p className="text-xs text-gray-500 dark:text-gray-400">Retrying every 5 seconds.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Updates</h1>
        <button
          onClick={handleCheck}
          disabled={checking || inProgress}
          className="flex items-center gap-2 rounded-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 px-3 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 transition-colors"
        >
          <RefreshCw className={`h-4 w-4 ${checking ? "animate-spin" : ""}`} />
          {checking ? "Checking…" : "Check now"}
        </button>
      </div>

      {upgraded && (
        <div className="flex items-start gap-3 rounded-lg border border-green-200 dark:border-green-800 bg-green-50 dark:bg-green-950/30 p-4 text-sm text-green-700 dark:text-green-400">
          <CheckCircle2 className="h-4 w-4 shrink-0 mt-0.5" />
          Upgraded to {vtag(upgraded.to)} in {elapsedText(upgraded.took)}.
        </div>
      )}

      {pending && (
        <div className="rounded-xl border border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-950/30 p-4 text-sm text-blue-700 dark:text-blue-300">
          <UpgradeProgress pending={pending} everyMinutes={everyMinutes} now={now}
                           typicalSec={status?.upgrade_estimate_seconds} basedOn={status?.upgrade_history_count} />
        </div>
      )}

      {error && !reconnecting && (
        <div className="flex items-start gap-3 rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-4 text-sm text-red-700 dark:text-red-400">
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
          {error}
        </div>
      )}

      {/* Version info */}
      <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm divide-y divide-gray-100 dark:divide-gray-800">
        <div className="flex items-center justify-between px-5 py-4">
          <div>
            <p className="text-xs text-gray-500 dark:text-gray-400">Running version</p>
            <p className="text-xl font-semibold text-gray-900 dark:text-gray-100 font-mono">
              v{status?.current_version}
            </p>
          </div>
          <div className="text-right">
            <p className="text-xs text-gray-500 dark:text-gray-400">Latest release</p>
            {status?.latest_release ? (
              <p className="text-xl font-semibold text-gray-900 dark:text-gray-100 font-mono">
                v{status.latest_release.version}
              </p>
            ) : (
              <p className="text-sm text-gray-400 dark:text-gray-500">—</p>
            )}
          </div>
        </div>

        <div className="px-5 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            {status?.update_available ? (
              <>
                <Download className="h-4 w-4 text-blue-500" />
                <span className="text-sm font-medium text-blue-700 dark:text-blue-400">
                  Update available
                </span>
              </>
            ) : (
              <>
                <CheckCircle2 className="h-4 w-4 text-green-500" />
                <span className="text-sm text-gray-600 dark:text-gray-400">Up to date</span>
              </>
            )}
          </div>
          <div className="flex items-center gap-3">
            {status?.upgrade_state && <StateChip state={status.upgrade_state} />}
            {status?.can_upgrade && (
              <button
                onClick={() => setConfirm("upgrade")}
                disabled={upgrading || inProgress}
                className="flex items-center gap-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white px-3 py-2 text-sm font-medium transition-colors"
              >
                <Download className="h-4 w-4" />
                Upgrade to v{status.latest_release?.version}
              </button>
            )}
            {status?.can_rollback && (
              <button
                onClick={() => setConfirm("rollback")}
                disabled={upgrading || inProgress}
                className="flex items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50 transition-colors"
              >
                <Undo2 className="h-4 w-4" />
                Roll back to v{status.previous_version}
              </button>
            )}
          </div>
        </div>

        {status?.checked_at && (
          <div className="flex items-center gap-1.5 px-5 py-2.5 text-xs text-gray-400 dark:text-gray-500">
            <Clock className="h-3.5 w-3.5" />
            Last checked {new Date(status.checked_at).toLocaleString()}
            {status.check_error && (
              <span className="ml-2 text-orange-500 dark:text-orange-400">
                (check failed: {status.check_error})
              </span>
            )}
          </div>
        )}
      </div>

      {/* Helper status */}
      <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm px-5 py-4">
        <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300 mb-3">Upgrade Helper</h2>
        <div className="flex items-start gap-3">
          {status?.helper.connected ? (
            <Wifi className="h-5 w-5 text-green-500 mt-0.5 shrink-0" />
          ) : (
            <WifiOff className="h-5 w-5 text-gray-400 mt-0.5 shrink-0" />
          )}
          <div className="flex-1 min-w-0">
            {status?.helper.connected ? (
              <p className="text-sm text-green-700 dark:text-green-400 font-medium">
                {status.helper.busy
                  ? "Connected — busy running an upgrade"
                  : `Connected (heartbeat every ${status.helper.every_minutes} min)`}
              </p>
            ) : status?.helper.configured ? (
              <>
                <p className="text-sm text-orange-600 dark:text-orange-400 font-medium">
                  Helper installed but not reporting
                </p>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  It runs from cron on the host every {status.helper.every_minutes} minutes. On the host, check it's
                  still scheduled, then run it once by hand; its last lines say what's wrong:
                </p>
                <code className="mt-2 block whitespace-pre-wrap rounded bg-gray-100 dark:bg-gray-800 px-3 py-2 text-xs font-mono text-gray-800 dark:text-gray-200 break-all">
                  {"sudo bash scripts/upgrade-agent.sh --print\nsudo bash scripts/upgrade-agent.sh\nsudo tail -20 /var/log/upgrade-agent.log"}
                </code>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  If the schedule is missing, reinstall it with <code>sudo bash scripts/upgrade-agent.sh --install</code>.
                </p>
              </>
            ) : (
              <>
                <p className="text-sm text-gray-700 dark:text-gray-300 font-medium">Not installed</p>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  In-app upgrades require the host helper to be running. Install it with:
                </p>
                <code className="mt-2 block rounded bg-gray-100 dark:bg-gray-800 px-3 py-2 text-xs font-mono text-gray-800 dark:text-gray-200 break-all">
                  {status?.helper_install_cmd ?? "sudo bash scripts/upgrade-agent.sh --install"}
                </code>
              </>
            )}
            {status?.helper.last_seen && (
              <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">
                Last seen: {new Date(status.helper.last_seen).toLocaleString()}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Release notes */}
      {status?.latest_release && (
        <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm px-5 py-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-700 dark:text-gray-300">
              Release notes — {status.latest_release.name}
            </h2>
            {status.latest_release.url && (
              <a
                href={status.latest_release.url}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
              >
                View on GitHub ↗
              </a>
            )}
          </div>
          {status.latest_release.notes ? (
            <div
              className="prose prose-sm dark:prose-invert max-w-none text-gray-700 dark:text-gray-300"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(status.latest_release.notes) }}
            />
          ) : (
            <p className="text-sm text-gray-400 dark:text-gray-500">No release notes.</p>
          )}
        </div>
      )}

      {/* Upgrade log */}
      {(showLog || status?.upgrade_log) && (
        <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm">
          <button
            onClick={() => setShowLog((v) => !v)}
            className="flex w-full items-center gap-2 px-5 py-3 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 rounded-xl transition-colors"
          >
            <Terminal className="h-4 w-4" />
            Upgrade log
            <span className="ml-auto text-gray-400">{showLog ? "▲" : "▼"}</span>
          </button>
          {showLog && (
            <pre
              ref={logRef}
              className="max-h-96 overflow-y-auto rounded-b-xl bg-gray-950 px-5 py-4 text-xs font-mono text-green-400 whitespace-pre-wrap"
            >
              {status?.upgrade_log || "(no log yet)"}
            </pre>
          )}
        </div>
      )}

      {/* Confirm dialog */}
      {confirm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-full max-w-md rounded-2xl bg-white dark:bg-gray-900 shadow-2xl p-6 mx-4">
            <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2">
              {confirm === "rollback" ? "Confirm rollback" : "Confirm upgrade"}
            </h3>
            <p className="text-sm text-gray-600 dark:text-gray-400 mb-6">
              {confirm === "rollback" ? "Roll back" : "Upgrade"} from <strong>v{status?.current_version}</strong> to{" "}
              <strong>v{confirm === "rollback" ? status?.previous_version : status?.latest_release?.version}</strong>?
              The container will restart — this will briefly interrupt service.
              {confirm === "rollback" && " Your data stays as it is; if you have used features added since, back up first (Settings → Data management)."}
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setConfirm(null)}
                className="rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleUpgrade}
                className="rounded-lg bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 text-sm font-medium transition-colors"
              >
                {confirm === "rollback" ? "Roll back" : "Upgrade"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
