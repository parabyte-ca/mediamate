import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type UpdateStatus } from "../api/client";
import { effectiveStages, isActive, knownEstimate, progressFraction, readPending, startedAt } from "../lib/updateProgress";

/** A slim bar across the top of every page while an upgrade runs, and the percentage in the
 *  tab title. Also shown while MediaMate restarts mid-upgrade (the server doesn't answer then). */
export function UpdateBar() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [offline, setOffline] = useState(false);
  const [now, setNow] = useState(Date.now());
  const pending = readPending(now);
  const active = isActive(status?.upgrade_state) || (offline && !!pending) || (!!pending && !status);

  // Ask often while an upgrade runs or one started here is pending; rarely otherwise.
  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const s = await api.updates.status();
        if (!stop) { setStatus(s); setOffline(false); }
      } catch {
        if (!stop) setOffline(true);
      }
    };
    tick();
    const id = setInterval(tick, active ? 5000 : 60_000);
    return () => { stop = true; clearInterval(id); };
  }, [active]);

  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);

  const p = status?.upgrade_progress;
  const stages = effectiveStages(p, offline);
  const begin = startedAt(p, pending) ?? now;
  const best = useRef(0);
  const frac = active ? progressFraction(stages, (now - begin) / 1000, knownEstimate(status?.upgrade_estimate_seconds), false) : 0;
  best.current = active ? Math.max(best.current, frac) : 0;
  const pct = Math.round(100 * best.current);

  useEffect(() => {
    if (!active) return;
    const before = document.title.replace(/^\d+% · /, "");
    document.title = `${pct}% · ${before}`;
    return () => { document.title = before; };
  }, [active, pct]);

  if (!active) return null;
  return (
    <Link to="/updates" title={`Upgrading MediaMate: ${pct}%`} aria-label={`Upgrade in progress, ${pct}%`}
      className="fixed inset-x-0 top-0 z-[70] block h-[3px] bg-gray-200/80 dark:bg-gray-800/80 print:hidden">
      <span className="relative block h-full overflow-hidden bg-gradient-to-r from-blue-500 via-cyan-400 to-emerald-400 shadow-[0_0_10px_rgba(34,211,238,0.8)] transition-[width] duration-1000 ease-linear"
        style={{ width: `${Math.max(3, pct)}%` }}>
        <span className="absolute inset-y-0 left-0 w-1/4 bg-gradient-to-r from-transparent via-white/70 to-transparent motion-safe:animate-sweep" />
      </span>
    </Link>
  );
}
