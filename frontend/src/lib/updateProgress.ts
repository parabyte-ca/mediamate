import type { UpgradeProgressInfo, UpgradeStage } from "../api/client";

/** An upgrade started from this browser. MediaMate restarts during it, so this is kept in
 *  sessionStorage: the Updates page and the top bar keep going while the server doesn't answer. */
export interface PendingUpgrade {
  from: string;
  to: string;
  at: number;
  action?: "update" | "rollback";
}

const PENDING_KEY = "sm-upgrade-pending";
const ESTIMATE_KEY = "sm-upgrade-estimate-s";
const PENDING_MAX_MS = 45 * 60_000;
/** Without any history: half the helper's usual wait plus a typical pull and restart. */
export const DEFAULT_ESTIMATE_S = 270;
/** Past this, suggest looking at the helper on the host. */
export const SLOW_MS = 15 * 60_000;
/** A finished upgrade stays on screen this long (the success or failure view). */
export const RECENT_MS = 10 * 60_000;

export function readPending(now = Date.now()): PendingUpgrade | null {
  try {
    const p = JSON.parse(sessionStorage.getItem(PENDING_KEY) || "null") as PendingUpgrade | null;
    return p && typeof p.at === "number" && now - p.at < PENDING_MAX_MS ? p : null;
  } catch {
    return null;
  }
}

export function writePending(p: PendingUpgrade | null) {
  try {
    if (p) sessionStorage.setItem(PENDING_KEY, JSON.stringify(p));
    else sessionStorage.removeItem(PENDING_KEY);
  } catch { /* private mode: the page still works, it just forgets on refresh */ }
}

/** The server's typical time, kept for while it restarts and can't be asked. */
export function rememberEstimate(s: number | null | undefined) {
  try { if (s && s > 0) localStorage.setItem(ESTIMATE_KEY, String(Math.round(s))); } catch { /* not remembered */ }
}

export function knownEstimate(s: number | null | undefined): number {
  if (s && s > 0) return s;
  try {
    const v = Number(localStorage.getItem(ESTIMATE_KEY));
    if (v > 0) return v;
  } catch { /* fall through */ }
  return DEFAULT_ESTIMATE_S;
}

/** The steps to show when the server sent none (it's restarting, or an older version). */
export const UPGRADE_STAGES: UpgradeStage[] = [
  ["requested", "Requested"], ["started", "Helper started"], ["code", "Get the release"],
  ["test", "Test the database"], ["download", "Download"], ["restart", "Restart"],
  ["health", "Health check"], ["done", "Done"],
].map(([key, label], i) => ({ key: key!, label: label!, at: null, reached: i === 0 }));

export const parseTs = (v?: string | null) => {
  const t = v ? Date.parse(v) : NaN;
  return Number.isNaN(t) ? null : t;
};

/** When this upgrade began, in ms: this browser's click, else the helper's start. */
export function startedAt(p: UpgradeProgressInfo | null | undefined, pending: PendingUpgrade | null): number | null {
  return pending?.at ?? parseTs(p?.stages?.[0]?.at) ?? parseTs(p?.started_at);
}

/** Steps with what the page knows on top of the server's view: while MediaMate doesn't
 *  answer after the helper started, it is restarting. */
export function effectiveStages(p: UpgradeProgressInfo | null | undefined, offline: boolean): UpgradeStage[] {
  const stages = p?.stages?.length ? p.stages : UPGRADE_STAGES;
  if (!offline) return stages;
  const started = stages.findIndex((s) => s.key === "started");
  const restart = stages.findIndex((s) => s.key === "restart");
  if (started < 0 || restart < 0 || !stages[started]!.reached) return stages;
  return stages.map((s, i) => (i <= restart ? { ...s, reached: true } : s));
}

/** 0..1. Each step owns an equal slice of the bar; inside the current slice the bar moves
 *  with time against the estimate, but never into the next slice before that step is reached. */
export function progressFraction(stages: UpgradeStage[], elapsedS: number, estimateS: number, done: boolean): number {
  if (done) return 1;
  const n = Math.max(2, stages.length);
  let current = 0;
  stages.forEach((s, i) => { if (s.reached) current = i; });
  const floor = current / (n - 1);
  const ceiling = (current + 1) / (n - 1) - 0.015;
  const byTime = Math.min(0.97, Math.max(0, elapsedS / Math.max(1, estimateS)));
  return Math.min(0.98, Math.max(floor, Math.min(byTime, ceiling)));
}

export function mmss(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export const isActive = (state?: string | null) => state === "requested" || state === "running";
export const vtag = (v: string) => (v.startsWith("v") ? v : `v${v}`);
