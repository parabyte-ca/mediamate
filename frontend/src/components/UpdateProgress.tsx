import { useEffect, useRef, useState } from "react";
import type { UpgradeProgressInfo, UpgradeStage } from "../api/client";
import {
  SLOW_MS, effectiveStages, isActive, knownEstimate, mmss, parseTs, progressFraction, readPending,
  rememberEstimate, startedAt, vtag,
} from "../lib/updateProgress";

type State = "requested" | "running" | "succeeded" | "failed" | null | undefined;

function Step({ s, i, n, current, failed, took }: {
  s: UpgradeStage; i: number; n: number; current: number; failed: boolean; took: string | null;
}) {
  const done = i < current || (i === current && s.key === "done");
  const active = i === current && !done;
  const red = failed && active;
  const dot = red
    ? "bg-rose-500 ring-4 ring-rose-500/25"
    : done
      ? "bg-blue-500 dark:bg-blue-400"
      : active
        ? "bg-blue-500 dark:bg-blue-400 ring-4 ring-blue-500/25 motion-safe:animate-pulse"
        : "bg-gray-300 dark:bg-gray-700";
  const line = (on: boolean) => (on ? "bg-blue-500 dark:bg-blue-400" : "bg-gray-300 dark:bg-gray-700");
  return (
    <li className="flex min-w-0 flex-col items-center gap-1.5 text-center" aria-current={active ? "step" : undefined}>
      <div className="flex w-full items-center">
        <span className={`h-0.5 flex-1 ${i === 0 ? "invisible" : line(i <= current)}`} />
        <span className={`h-3 w-3 shrink-0 rounded-full transition-colors duration-500 ${dot}`} />
        <span className={`h-0.5 flex-1 ${i === n - 1 ? "invisible" : line(i < current)}`} />
      </div>
      <span className={`hidden text-[11px] leading-tight sm:block ${red ? "font-semibold text-rose-600 dark:text-rose-300"
        : active ? "font-semibold text-gray-900 dark:text-white" : done ? "text-gray-600 dark:text-gray-300" : "text-gray-400 dark:text-gray-500"}`}>
        {s.label}
      </span>
      {took && <span className="hidden font-mono text-[10px] tabular-nums text-gray-400 dark:text-gray-500 sm:block">{took}</span>}
    </li>
  );
}

function Check() {
  return (
    <svg viewBox="0 0 24 24" className="h-10 w-10 shrink-0" aria-hidden>
      <circle cx="12" cy="12" r="11" className="fill-emerald-500/15 stroke-emerald-500 dark:stroke-emerald-400" strokeWidth="1.5" />
      <path d="M7 12.5l3.2 3.2L17.5 8.5" fill="none" className="stroke-emerald-600 dark:stroke-emerald-300 motion-safe:animate-draw"
        strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" strokeDasharray="48" />
    </svg>
  );
}

function Cross() {
  return (
    <svg viewBox="0 0 24 24" className="h-10 w-10 shrink-0" aria-hidden>
      <circle cx="12" cy="12" r="11" className="fill-rose-500/15 stroke-rose-500 dark:stroke-rose-400" strokeWidth="1.5" />
      <path d="M8.5 8.5l7 7M15.5 8.5l-7 7" fill="none" className="stroke-rose-600 dark:stroke-rose-300 motion-safe:animate-draw"
        strokeWidth="2.2" strokeLinecap="round" strokeDasharray="48" />
    </svg>
  );
}

/** What an upgrade is doing: real steps from the upgrade log (with how long each took), a bar
 *  that moves between them on the usual run time and never goes backwards, a timer, the latest
 *  log line, and a finish for success or failure. */
export function UpdateProgress({ state, progress, estimateS, current, offline, message }: {
  state: State; progress: UpgradeProgressInfo | null | undefined; estimateS?: number | null;
  current: string; offline: boolean; message?: string | null;
}) {
  const pending = readPending();
  // MediaMate is restarting mid-upgrade (the server doesn't answer): still under way.
  const st: State = offline && pending ? "running" : state ?? (pending ? "requested" : null);
  const active = isActive(st);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  useEffect(() => rememberEstimate(estimateS), [estimateS]);

  const begin = startedAt(progress, pending) ?? now;
  const rollback = (progress?.action ?? pending?.action) === "rollback";
  const target = progress?.version ?? pending?.to ?? null;
  const stages = effectiveStages(progress, offline && active);
  let at = 0;
  stages.forEach((s, i) => { if (s.reached) at = i; });
  const succeeded = st === "succeeded";
  const failed = st === "failed";
  const estimate = knownEstimate(estimateS);
  const elapsed = (succeeded || failed ? (parseTs(progress?.finished_at) ?? now) : now) - begin;

  const best = useRef(0);
  const frac = progressFraction(stages, elapsed / 1000, estimate, succeeded);
  if (frac > best.current || succeeded) best.current = frac;
  const pct = best.current;
  const leftMin = Math.ceil((estimate * 1000 - elapsed) / 60_000);

  if (succeeded) {
    const to = target ?? current;
    return (
      <div className="flex items-center gap-3" role="status">
        <Check />
        <div>
          <p className="font-medium text-emerald-700 dark:text-emerald-200">
            {rollback ? `Rolled back to ${vtag(to)}` : `Updated to ${vtag(to)}`} in {mmss(elapsed)}
          </p>
          <p className="text-xs text-gray-500 dark:text-gray-400">Every step passed, including the health check.</p>
        </div>
      </div>
    );
  }

  const failedStage = stages.find((s) => s.key === progress?.failed_at)?.label;
  return (
    <div className="min-w-0 space-y-4">
      {failed ? (
        <div className="flex items-center gap-3" role="alert">
          <Cross />
          <div className="min-w-0">
            <p className="font-medium text-rose-700 dark:text-rose-200">
              {rollback ? "Rollback" : "Upgrade"} failed{failedStage ? ` at ${failedStage}` : ""}
            </p>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              {message ?? "MediaMate keeps running the version it had. See the log below."}
            </p>
          </div>
        </div>
      ) : (
        <div className="flex items-baseline justify-between gap-3">
          <p className="font-medium text-gray-900 dark:text-white">
            {rollback ? `Rolling back${target ? ` to ${vtag(target)}` : ""}` : `Upgrading${target ? ` to ${vtag(target)}` : ""}`}
          </p>
          <p className="font-mono text-sm tabular-nums text-gray-600 dark:text-gray-300" aria-label="Time elapsed">{mmss(elapsed)}</p>
        </div>
      )}

      <ol className="grid gap-1" style={{ gridTemplateColumns: `repeat(${stages.length}, minmax(0, 1fr))` }} aria-label="Upgrade steps">
        {stages.map((s, i) => {
          // How long a finished step took: until the next step's log line.
          const next = stages[i + 1];
          const a = parseTs(s.at), b = parseTs(next?.at);
          const took = a != null && b != null && next?.reached && i < at ? mmss(b - a) : null;
          return <Step key={s.key} s={s} i={i} n={stages.length} current={at} failed={failed} took={took} />;
        })}
      </ol>
      <p className="text-center text-xs text-gray-600 dark:text-gray-300 sm:hidden">{stages[at]?.label}</p>

      {!failed && (
        <div className="space-y-1.5">
          <div className="relative h-3 overflow-hidden rounded-full bg-gray-200 ring-1 ring-gray-300 dark:bg-gray-800 dark:ring-gray-700" role="progressbar"
            aria-label="Upgrade progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct * 100)}>
            <div className="h-full rounded-full bg-gradient-to-r from-blue-500 via-cyan-400 to-emerald-400 shadow-[0_0_14px_rgba(34,211,238,0.55)] transition-[width] duration-1000 ease-linear"
              style={{ width: `${Math.max(3, pct * 100)}%` }}>
              <div className="h-full w-full rounded-full opacity-30 motion-safe:animate-stripes"
                style={{ backgroundImage: "repeating-linear-gradient(45deg, rgba(255,255,255,0.9) 0 7px, transparent 7px 14px)", backgroundSize: "28px 100%" }} />
            </div>
          </div>
          <div className="flex flex-wrap justify-between gap-x-3 text-xs text-gray-500 dark:text-gray-400">
            <span>
              {elapsed < estimate * 1000 ? `About ${Math.max(1, leftMin)} min left` : "Taking a little longer than usual…"}
              {" · "}
              {estimateS ? `usually takes ${mmss(estimate * 1000)}` : "first-time estimate"}
            </span>
            <span className="tabular-nums">{Math.round(pct * 100)}%</span>
          </div>
        </div>
      )}

      {offline && active ? (
        <p className="text-xs text-blue-700 dark:text-blue-200">MediaMate is restarting with the new version. This page reconnects by itself.</p>
      ) : progress?.last_line && !failed ? (
        <p className="truncate font-mono text-xs text-gray-500 dark:text-gray-400" title={progress.last_line} aria-label="Latest log line">
          <span className="text-blue-500 dark:text-blue-400">›</span> {progress.last_line}
        </p>
      ) : null}
      {active && elapsed > SLOW_MS && (
        <p className="text-xs text-amber-700 dark:text-amber-300">
          This is taking longer than usual. On the NAS, check <code className="rounded bg-gray-100 px-1 dark:bg-gray-800">cat upgrade/agent.json</code>{" "}
          and <code className="rounded bg-gray-100 px-1 dark:bg-gray-800">tail -20 upgrade/upgrade.log</code>.
        </p>
      )}
    </div>
  );
}
