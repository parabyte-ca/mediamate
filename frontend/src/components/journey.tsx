import {
  Bell, CheckCircle2, Circle, Download, Film, FolderInput, Hourglass, Library, Loader2, MinusCircle, Scissors, Search,
  Timer, Workflow, XCircle,
  RotateCcw,
} from "lucide-react";
import type { Journey, JourneyStep, ProgressEvent, StepStatus } from "../api/client";

// Shared bits for the Activity list and the journey drawer.

export function fmtSpeed(bps?: number | null) {
  if (!bps) return null;
  return bps >= 1024 * 1024 ? `${(bps / 1024 / 1024).toFixed(1)} MB/s` : `${(bps / 1024).toFixed(0)} KB/s`;
}

export function fmtEta(s?: number | null) {
  if (!s || s <= 0 || s > 86400 * 7) return null;
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

export function fmtDuration(a?: string | null, b?: string | null) {
  if (!a || !b) return null;
  const s = Math.max(0, Math.round((new Date(b).getTime() - new Date(a).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

const KIND_ICON: Record<string, typeof Search> = {
  found: Search, download: Download, encode: Film, split: Scissors, trim: Timer, organise: FolderInput,
  library: Library, notify: Bell, waiting: Hourglass, pipeline: Workflow,
};

export function StepIcon({ step, className = "h-4 w-4" }: { step: JourneyStep; className?: string }) {
  const Icon = KIND_ICON[step.kind] ?? Circle;
  return <Icon className={className} />;
}

export const STATUS_STYLE: Record<StepStatus, { ring: string; text: string; label: string }> = {
  pending:   { ring: "border-gray-300 dark:border-gray-600 text-gray-400",            text: "text-gray-500 dark:text-gray-400",   label: "Waiting" },
  running:   { ring: "border-blue-500 bg-blue-50 dark:bg-blue-950 text-blue-600 dark:text-blue-400", text: "text-blue-600 dark:text-blue-400", label: "In progress" },
  done:      { ring: "border-green-500 bg-green-50 dark:bg-green-950 text-green-600 dark:text-green-400", text: "text-green-600 dark:text-green-400", label: "Done" },
  failed:    { ring: "border-red-500 bg-red-50 dark:bg-red-950 text-red-600 dark:text-red-400",   text: "text-red-600 dark:text-red-400",     label: "Failed" },
  cancelled: { ring: "border-gray-400 text-gray-400",                                   text: "text-gray-500 dark:text-gray-400",   label: "Cancelled" },
  skipped:   { ring: "border-gray-300 border-dashed text-gray-400",                     text: "text-gray-500 dark:text-gray-400",   label: "Skipped" },
  retried:   { ring: "border-gray-300 text-gray-400",                                   text: "text-gray-500 dark:text-gray-400",   label: "Retried" },
};

export function StatusIcon({ status, className = "h-3.5 w-3.5" }: { status: StepStatus; className?: string }) {
  if (status === "running") return <Loader2 className={`${className} animate-spin`} />;
  if (status === "done") return <CheckCircle2 className={className} />;
  if (status === "failed") return <XCircle className={className} />;
  if (status === "cancelled" || status === "skipped") return <MinusCircle className={className} />;
  if (status === "retried") return <RotateCcw className={className} />;
  return <Circle className={className} />;
}

/** found → download → encode … as connected icons. */
export function StepStrip({ steps, current }: { steps: JourneyStep[]; current: number }) {
  return (
    <ol className="flex items-center" aria-label="Steps">
      {steps.map((s, i) => (
        <li key={`${s.kind}-${s.ref.id}-${i}`} className="flex items-center">
          {i > 0 && (
            <span className={`h-px w-3 sm:w-5 ${steps[i - 1].status === "done" ? "bg-green-400" : "bg-gray-300 dark:bg-gray-600"}`} />
          )}
          <span
            title={`${s.label}: ${STATUS_STYLE[s.status].label}`}
            className={`flex h-7 w-7 items-center justify-center rounded-full border-2 ${STATUS_STYLE[s.status].ring} ${
              i === current && s.status === "running" ? "ring-2 ring-blue-200 dark:ring-blue-900" : ""
            }`}
          >
            <StepIcon step={s} className="h-3.5 w-3.5" />
          </span>
        </li>
      ))}
    </ol>
  );
}

export function ProgressBar({ pct, failed }: { pct: number; failed?: boolean }) {
  return (
    <div className="h-1.5 w-full rounded-full bg-gray-200 dark:bg-gray-700" role="progressbar"
         aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
      <div className={`h-1.5 rounded-full transition-all duration-500 ${failed ? "bg-red-500" : "bg-blue-500"}`}
           style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} />
    </div>
  );
}

/** Merge a transient progress event into a journey snapshot. */
export function applyProgress(j: Journey, ev: ProgressEvent): Journey {
  const steps = j.steps.map((s) => {
    const match =
      (ev.download_id != null && s.ref.type === "download" && s.ref.id === ev.download_id && s.kind === "download") ||
      (ev.job_id != null && s.ref.type === "media_job" && s.ref.id === ev.job_id);
    if (!match) return s;
    return {
      ...s,
      status: s.status === "pending" ? ("running" as StepStatus) : s.status,
      progress: ev.progress,
      speed: ev.speed ?? s.speed,
      eta: ev.eta ?? s.eta,
      log: ev.log ?? s.log,
    };
  });
  return { ...j, steps };
}
