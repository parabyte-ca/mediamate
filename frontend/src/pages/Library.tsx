import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ChevronLeft, ChevronRight, Download, Edit2, Eye, Film, FolderInput, FolderSearch, Layers, Loader2, MoreHorizontal,
  CheckCircle2, Pause, Play, Scissors, SkipForward, Square, Stethoscope, Timer, Trash2, X,
} from "lucide-react";
import { api, type DoctorKind, type DoctorState, type MediaFile, type MediaJobType, type MediaStats, type OrganisePlan } from "../api/client";
import { useEvent } from "../events";
import { fmtSize } from "../utils";

// The media library (FileSplitter's Files tab): everything under the media
// paths, with filters, per-file and bulk encode / split / trim, preview,
// rename, skip and delete-to-trash.

const PAGE_SIZE = 50;
const RESOLUTIONS = ["original", "480p", "720p", "1080p", "1440p", "2160p"];

const STATUS_STYLE: Record<string, string> = {
  pending: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300",
  queued: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400",
  processing: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400",
  done: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400",
  error: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400",
  skipped: "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400",
  cancelled: "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400",
};

type Filters = Record<"q" | "category" | "status" | "codec" | "type" | "res" | "dur" | "size", string>;
const FILTER_KEYS: (keyof Filters)[] = ["q", "category", "status", "codec", "type", "res", "dur", "size"];

function fmtDur(s?: number | null) {
  if (!s) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

function resBucket(h?: number | null) {
  if (!h) return "other";
  if (h >= 2000) return "2160";
  if (h >= 1000) return "1080";
  if (h >= 700) return "720";
  if (h >= 470) return "480";
  return "other";
}

function matches(f: MediaFile, fl: Filters) {
  if (fl.q && !f.filename.toLowerCase().includes(fl.q.toLowerCase())) return false;
  if (fl.category && f.category !== fl.category) return false;
  if (fl.status && f.status !== fl.status) return false;
  if (fl.codec && (f.codec ?? "") !== fl.codec) return false;
  if (fl.type === "anthology" && !f.is_anthology) return false;
  if (fl.type === "normal" && f.is_anthology) return false;
  if (fl.res && resBucket(f.height) !== fl.res) return false;
  const min = (f.duration_sec ?? 0) / 60;
  if (fl.dur === "lt30" && min >= 30) return false;
  if (fl.dur === "30to90" && (min < 30 || min > 90)) return false;
  if (fl.dur === "gt90" && min <= 90) return false;
  const mb = (f.size_bytes ?? 0) / 1e6;
  if (fl.size === "lt500" && mb >= 500) return false;
  if (fl.size === "500to2000" && (mb < 500 || mb > 2000)) return false;
  if (fl.size === "2000to5000" && (mb < 2000 || mb > 5000)) return false;
  if (fl.size === "gt5000" && mb <= 5000) return false;
  return true;
}

const busy = (f: MediaFile) => f.status === "queued" || f.status === "processing";

const sel = "rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1.5 text-sm text-gray-700 dark:text-gray-200";
const btn = "flex min-h-[36px] items-center gap-1.5 rounded-lg px-3 text-sm disabled:opacity-50";
const btnGhost = `${btn} border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800`;
const btnPrimary = `${btn} bg-blue-600 font-medium text-white hover:bg-blue-700`;
const iconBtn = "rounded p-1.5 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-700 dark:hover:text-gray-200 disabled:opacity-30";

// ── Modals ────────────────────────────────────────────────────────────────────

function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-label={title}>
      <div className={`w-full ${wide ? "max-w-3xl" : "max-w-md"} rounded-xl bg-white dark:bg-gray-900 shadow-xl`}>
        <div className="flex items-center gap-2 border-b border-gray-200 dark:border-gray-700 px-4 py-3">
          <h3 className="min-w-0 flex-1 truncate font-semibold text-gray-900 dark:text-gray-100">{title}</h3>
          <button onClick={onClose} aria-label="Close" className={iconBtn}><X className="h-4 w-4" /></button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

type Dialog =
  | { kind: "encode"; files: MediaFile[] }
  | { kind: "split"; files: MediaFile[] }
  | { kind: "rename"; file: MediaFile }
  | { kind: "delete"; files: MediaFile[] }
  | { kind: "preview"; file: MediaFile }
  | { kind: "organise"; files: MediaFile[] }
  | { kind: "doctor" };

// ── Row actions menu ──────────────────────────────────────────────────────────

function RowMenu({ f, onAction }: { f: MediaFile; onAction: (a: string) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const item = (a: string, label: string, Icon: typeof Film, disabled = false, danger = false) => (
    <button disabled={disabled} onClick={() => { setOpen(false); onAction(a); }}
            className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-800 ${danger ? "text-red-600 dark:text-red-400" : "text-gray-700 dark:text-gray-200"}`}>
      <Icon className="h-3.5 w-3.5" />{label}
    </button>
  );
  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen((v) => !v)} aria-label={`More actions for ${f.filename}`} className={iconBtn}>
        <MoreHorizontal className="h-4 w-4" />
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-1 w-48 overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-lg">
          {item("preview", "Preview", Eye)}
          <div className="sm:hidden">
            {item("encode", "Encode…", Film, busy(f))}
            {item("split", "Split…", Scissors, busy(f))}
          </div>
          {item("trim", "Trim intro (3.5 s)", Timer, busy(f))}
          {item("rename", "Rename…", Edit2, busy(f))}
          {item("organise", "Rename & organise…", FolderInput, busy(f))}
          {item("anthology", f.is_anthology ? "Mark not anthology" : "Mark as anthology", Layers)}
          {f.anthology_override != null && item("anthology-auto", "Anthology: automatic", Layers)}
          {busy(f) ? item("cancel", "Cancel job", Square) : item("skip", "Skip", SkipForward, f.status === "skipped")}
          {item("delete", "Delete to trash…", Trash2, busy(f), true)}
        </div>
      )}
    </div>
  );
}

/** A still frame from the file; falls back to an icon when there is none. */
function Thumb({ f, onOpen }: { f: MediaFile; onOpen: () => void }) {
  const [failed, setFailed] = useState(false);
  return (
    <button type="button" onClick={onOpen} aria-label={`Preview ${f.filename}`}
            className="group relative h-9 w-16 shrink-0 overflow-hidden rounded bg-gray-100 dark:bg-gray-800">
      {failed ? (
        <Film className="absolute inset-0 m-auto h-4 w-4 text-gray-400" />
      ) : (
        <img src={api.media.thumbUrl(f.id, f.updated_at)} alt="" loading="lazy" decoding="async"
             onError={() => setFailed(true)} className="h-full w-full object-cover" />
      )}
      <span className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
        <Play className="h-4 w-4 fill-white text-white" />
      </span>
    </button>
  );
}

function fmtClock(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

/** Plays the original file when the browser can; otherwise (or on request) a version
 *  ScreenMate converts on the fly. The converted stream has no timeline, so this
 *  dialog shows its own seek bar and restarts the conversion from the chosen point. */
function PreviewDialog({ file, onClose }: { file: MediaFile; onClose: () => void }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [mode, setMode] = useState<"original" | "converted">("original");
  const [offset, setOffset] = useState(0);        // where the conversion started (s)
  const [pos, setPos] = useState(0);              // current position in the file (s)
  const [note, setNote] = useState<string | null>(null);
  const duration = file.duration_sec ?? 0;

  const convert = useCallback((from: number, why?: string) => {
    setMode("converted");
    setOffset(from);
    setPos(from);
    if (why) setNote(why);
  }, []);

  // The original plays but shows no picture (e.g. HEVC in a browser without it): convert.
  useEffect(() => {
    if (mode !== "original") return;
    const v = videoRef.current;
    if (!v) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onMeta = () => {
      timer = setTimeout(() => {
        if (v.videoWidth === 0) convert(v.currentTime, "This browser can't show this file's video, so ScreenMate is converting it.");
      }, 2500);
    };
    v.addEventListener("loadedmetadata", onMeta);
    return () => { v.removeEventListener("loadedmetadata", onMeta); if (timer) clearTimeout(timer); };
  }, [mode, convert]);

  const src = mode === "original" ? api.media.streamUrl(file.id) : api.media.playUrl(file.id, offset);

  return (
    <Modal title={file.filename} onClose={onClose} wide>
      <video
        key={src}
        ref={videoRef}
        src={src}
        controls
        autoPlay
        playsInline
        className="max-h-[65vh] w-full rounded bg-black"
        onError={() => mode === "original"
          ? convert(videoRef.current?.currentTime ?? 0, "This browser can't play this file directly, so ScreenMate is converting it.")
          : setNote("ScreenMate couldn't convert this file. Try Download instead.")}
        onTimeUpdate={(e) => mode === "converted" && setPos(offset + e.currentTarget.currentTime)}
      />

      {mode === "converted" && duration > 0 && (
        <div className="mt-3 flex items-center gap-3 text-xs tabular-nums text-gray-600 dark:text-gray-300">
          <span>{fmtClock(pos)}</span>
          <input type="range" min={0} max={Math.floor(duration)} step={1} value={Math.min(pos, duration)}
                 aria-label="Seek" className="flex-1 accent-blue-600"
                 onChange={(e) => setPos(Number(e.target.value))}
                 onMouseUp={(e) => convert(Number((e.target as HTMLInputElement).value))}
                 onTouchEnd={(e) => convert(Number((e.target as HTMLInputElement).value))}
                 onKeyUp={(e) => convert(Number((e.target as HTMLInputElement).value))} />
          <span>{fmtClock(duration)}</span>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-gray-500 dark:text-gray-400">
          {note ?? (mode === "original" ? "Playing the original file." : "Converted for your browser as it plays (uses some CPU on the server).")}
        </p>
        <div className="flex gap-2">
          {mode === "original" ? (
            <button className={btnGhost} title="Use this if there's no picture or no sound"
                    onClick={() => convert(videoRef.current?.currentTime ?? 0, "Converted for your browser as it plays (uses some CPU on the server).")}>
              <Film className="h-4 w-4" />Compatible playback
            </button>
          ) : (
            <button className={btnGhost} onClick={() => { setMode("original"); setNote(null); }}>Original file</button>
          )}
          <a href={api.media.streamUrl(file.id, true)} className={btnGhost}><Download className="h-4 w-4" />Download</a>
        </div>
      </div>
    </Modal>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function Library() {
  const [params, setParams] = useSearchParams();
  const filters = Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) ?? ""])) as Filters;
  const page = Math.max(1, Number(params.get("page") || 1));
  const [files, setFiles] = useState<MediaFile[]>([]);
  const [stats, setStats] = useState<MediaStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const say = (m: string) => { setToast(m); window.setTimeout(() => setToast(null), 4000); };
  const load = useCallback(async () => {
    const [f, s] = await Promise.all([api.media.files(), api.media.stats()]);
    setFiles(f);
    setStats(s);
    setLoading(false);
  }, []);
  useEffect(() => { load().catch(() => setLoading(false)); }, [load]);

  // Live: refetch (at most every 1.5 s) when jobs change; poll while a scan runs.
  const pending = useRef<number | undefined>(undefined);
  const soon = useCallback(() => {
    if (pending.current) return;
    pending.current = window.setTimeout(() => { pending.current = undefined; load().catch(() => {}); }, 1500);
  }, [load]);
  useEvent("journey.updated", soon);
  useEvent("journey.removed", soon);
  useEffect(() => {
    if (!stats?.scan_running) return;
    const id = window.setInterval(() => load().catch(() => {}), 2000);
    return () => window.clearInterval(id);
  }, [stats?.scan_running, load]);

  const setFilter = (k: keyof Filters, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    next.delete("page");
    setParams(next, { replace: true });
  };
  const setPage = (p: number) => {
    const next = new URLSearchParams(params);
    if (p > 1) next.set("page", String(p)); else next.delete("page");
    setParams(next, { replace: true });
  };
  const clearFilters = () => setParams(new URLSearchParams(), { replace: true });

  const categories = useMemo(() => [...new Set(files.map((f) => f.category))].sort(), [files]);
  const codecs = useMemo(() => [...new Set(files.map((f) => f.codec ?? "").filter(Boolean))].sort(), [files]);
  const filtered = useMemo(() => files.filter((f) => matches(f, filters)), [files, params]); // eslint-disable-line react-hooks/exhaustive-deps
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const rows = filtered.slice((Math.min(page, pages) - 1) * PAGE_SIZE, Math.min(page, pages) * PAGE_SIZE);
  const pickedFiles = files.filter((f) => picked.has(f.id));
  const allOnPage = rows.length > 0 && rows.every((f) => picked.has(f.id));
  const anyFilter = FILTER_KEYS.some((k) => filters[k]);

  const togglePick = (id: number) => setPicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const togglePage = () => setPicked((s) => {
    const n = new Set(s);
    rows.forEach((f) => (allOnPage ? n.delete(f.id) : n.add(f.id)));
    return n;
  });

  const run = async (fn: () => Promise<string | void>) => {
    try {
      const m = await fn();
      if (m) say(m);
    } catch (e) {
      say((e as Error).message);
    }
    load().catch(() => {});
  };

  const queue = (list: MediaFile[], type: MediaJobType, resolution = "original", episodes = 0) => run(async () => {
    const ids = list.filter((f) => !busy(f)).map((f) => f.id);
    if (!ids.length) return "Those files are already queued or processing";
    if (ids.length === 1) {
      await api.media.queue(ids[0], type, resolution, episodes);
      return `${type[0].toUpperCase()}${type.slice(1)} queued — follow it on Activity`;
    }
    const r = await api.media.queueBulk(ids, type, resolution, episodes);
    setPicked(new Set());
    return `Queued ${r.queued} file${r.queued === 1 ? "" : "s"}${r.skipped ? ` (${r.skipped} skipped)` : ""}`;
  });

  const onRowAction = (f: MediaFile, a: string) => {
    if (a === "preview") return setDialog({ kind: "preview", file: f });
    if (a === "organise") return setDialog({ kind: "organise", files: [f] });
    if (a === "encode") setDialog({ kind: "encode", files: [f] });
    else if (a === "split") setDialog({ kind: "split", files: [f] });
    else if (a === "trim") queue([f], "trim");
    else if (a === "rename") setDialog({ kind: "rename", file: f });
    else if (a === "delete") setDialog({ kind: "delete", files: [f] });
    else if (a === "skip") run(async () => { await api.media.skip(f.id); });
    else if (a === "cancel") run(async () => (await api.media.cancel(f.id)).msg);
    else if (a === "anthology") run(async () => { await api.media.setAnthology(f.id, !f.is_anthology); });
    else if (a === "anthology-auto") run(async () => { await api.media.setAnthology(f.id, null); });
  };

  const queueAll = (type: MediaJobType) => {
    const list = filtered.filter((f) => f.status === "pending" && (type !== "split" || f.is_anthology));
    if (!list.length) return say(type === "split" ? "No pending anthologies match the filters" : "No pending files match the filters");
    if (type === "encode") setDialog({ kind: "encode", files: list });
    else setDialog({ kind: "split", files: list });
  };

  const tiles: [string, number | string, string][] = stats ? [
    ["Files", stats.total, ""], ["Pending", stats.pending, "pending"], ["Queued", stats.queued, "queued"],
    ["Processing", stats.processing, "processing"], ["Done", stats.done, "done"], ["Errors", stats.errors, "error"],
  ] : [];

  return (
    <div className="mx-auto max-w-6xl p-4 md:p-6">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-xl font-bold text-gray-900 dark:text-gray-100">Library</h1>
        <button className={btnGhost} onClick={() => run(async () => { await api.media.scan(); return "Scan started"; })} disabled={stats?.scan_running}>
          {stats?.scan_running ? <Loader2 className="h-4 w-4 animate-spin" /> : <FolderSearch className="h-4 w-4" />}
          {stats?.scan_running ? "Scanning…" : "Scan now"}
        </button>
        <button className={btnGhost} onClick={() => run(async () => { if (stats?.paused) await api.media.resume(); else await api.media.pause(); })}>
          {stats?.paused ? <><Play className="h-4 w-4" />Resume queue</> : <><Pause className="h-4 w-4" />Pause queue</>}
        </button>
        <button className={btnGhost} onClick={() => queueAll("encode")}><Film className="h-4 w-4" />Encode all pending</button>
        <button className={btnGhost} onClick={() => queueAll("split")}><Scissors className="h-4 w-4" />Split all anthologies</button>
        <button className={btnGhost} onClick={() => setDialog({ kind: "doctor" })} title="Find and fix problems in your library">
          <Stethoscope className="h-4 w-4" />Library doctor
        </button>
        <button className={btnGhost} title="Remove records for files that no longer exist on disk"
                onClick={() => run(async () => `Removed ${(await api.media.purge()).removed} missing file record(s)`)}>
          <Trash2 className="h-4 w-4" />Purge missing
        </button>
      </div>

      {stats && (
        <div className="mb-4 grid grid-cols-3 gap-2 sm:grid-cols-7">
          {tiles.map(([label, value, status]) => (
            <button key={label} onClick={() => setFilter("status", status === filters.status ? "" : status)}
                    className={`rounded-xl border p-3 text-left transition-colors ${filters.status === status && status ? "border-blue-400 bg-blue-50 dark:bg-blue-950/40" : "border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 hover:border-gray-300"}`}>
              <div className="text-xs text-gray-500 dark:text-gray-400">{label}</div>
              <div className="text-lg font-semibold text-gray-900 dark:text-gray-100">{value}</div>
            </button>
          ))}
          <div className="rounded-xl border border-green-200 dark:border-green-900 bg-green-50 dark:bg-green-950/30 p-3">
            <div className="text-xs text-green-700 dark:text-green-400">Space saved</div>
            <div className="text-lg font-semibold text-green-800 dark:text-green-300">{fmtSize(stats.total_saved_bytes) === "—" ? "0 B" : fmtSize(stats.total_saved_bytes)}</div>
          </div>
        </div>
      )}

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input value={filters.q} onChange={(e) => setFilter("q", e.target.value)} placeholder="Search file names…"
               aria-label="Search file names" className={`${sel} min-w-[200px] flex-1`} />
        <select aria-label="Category" className={sel} value={filters.category} onChange={(e) => setFilter("category", e.target.value)}>
          <option value="">All folders</option>{categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select aria-label="Status" className={sel} value={filters.status} onChange={(e) => setFilter("status", e.target.value)}>
          <option value="">All statuses</option>
          {["pending", "queued", "processing", "done", "error", "skipped"].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select aria-label="Codec" className={sel} value={filters.codec} onChange={(e) => setFilter("codec", e.target.value)}>
          <option value="">All codecs</option>{codecs.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select aria-label="Type" className={sel} value={filters.type} onChange={(e) => setFilter("type", e.target.value)}>
          <option value="">All types</option><option value="anthology">Anthologies</option><option value="normal">Normal</option>
        </select>
        <select aria-label="Resolution" className={sel} value={filters.res} onChange={(e) => setFilter("res", e.target.value)}>
          <option value="">All resolutions</option><option value="2160">4K</option><option value="1080">1080p</option>
          <option value="720">720p</option><option value="480">480p</option><option value="other">Other</option>
        </select>
        <select aria-label="Duration" className={sel} value={filters.dur} onChange={(e) => setFilter("dur", e.target.value)}>
          <option value="">All lengths</option><option value="lt30">&lt; 30 min</option><option value="30to90">30–90 min</option><option value="gt90">&gt; 90 min</option>
        </select>
        <select aria-label="Size" className={sel} value={filters.size} onChange={(e) => setFilter("size", e.target.value)}>
          <option value="">All sizes</option><option value="lt500">&lt; 500 MB</option><option value="500to2000">500 MB–2 GB</option>
          <option value="2000to5000">2–5 GB</option><option value="gt5000">&gt; 5 GB</option>
        </select>
        {anyFilter && <button className="text-sm text-blue-600 hover:underline" onClick={clearFilters}>Clear filters</button>}
      </div>

      {pickedFiles.length > 0 && (
        <div className="sticky top-12 z-20 mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-blue-200 dark:border-blue-900 bg-blue-50 dark:bg-blue-950/60 px-3 py-2 md:top-2">
          <span className="text-sm font-medium text-blue-800 dark:text-blue-200">{pickedFiles.length} selected</span>
          <button className={btnPrimary} onClick={() => setDialog({ kind: "encode", files: pickedFiles })}><Film className="h-4 w-4" />Encode</button>
          <button className={btnGhost} onClick={() => setDialog({ kind: "split", files: pickedFiles })}><Scissors className="h-4 w-4" />Split</button>
          <button className={btnGhost} onClick={() => queue(pickedFiles, "trim")}><Timer className="h-4 w-4" />Trim</button>
          <button className={btnGhost} onClick={() => run(async () => { const r = await api.media.bulk("skip", [...picked]); setPicked(new Set()); return `Skipped ${r.done}`; })}><SkipForward className="h-4 w-4" />Skip</button>
          <button className={btnGhost} onClick={() => setDialog({ kind: "organise", files: files.filter((x) => picked.has(x.id)) })}><FolderInput className="h-4 w-4" />Organise</button>
          <button className={`${btnGhost} text-red-600 dark:text-red-400`} onClick={() => setDialog({ kind: "delete", files: pickedFiles })}><Trash2 className="h-4 w-4" />Delete</button>
          <button className="ml-auto text-sm text-blue-700 dark:text-blue-300 hover:underline" onClick={() => setPicked(new Set())}>Clear selection</button>
        </div>
      )}

      <div className="overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm">
        {loading ? (
          <div className="flex justify-center p-10"><Loader2 className="h-5 w-5 animate-spin text-blue-600" /></div>
        ) : files.length === 0 ? (
          <div className="p-10 text-center text-sm text-gray-500 dark:text-gray-400">
            <Film className="mx-auto mb-2 h-6 w-6 text-gray-300 dark:text-gray-600" />
            No files yet. Tick <b>Scan &amp; encode</b> on a folder in <Link to="/settings#folders" className="text-blue-600 hover:underline">Settings → Folders</Link>, then <b>Scan now</b>.
          </div>
        ) : rows.length === 0 ? (
          <p className="p-10 text-center text-sm text-gray-500">No files match these filters.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="border-b border-gray-200 dark:border-gray-700 text-left text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
              <tr>
                <th className="w-10 px-3 py-2"><input type="checkbox" aria-label="Select page" checked={allOnPage} onChange={togglePage} /></th>
                <th className="w-full px-2 py-2">Name</th>
                <th className="hidden whitespace-nowrap px-2 py-2 md:table-cell">Codec</th>
                <th className="hidden whitespace-nowrap px-2 py-2 md:table-cell">Res</th>
                <th className="hidden whitespace-nowrap px-2 py-2 lg:table-cell">Length</th>
                <th className="hidden whitespace-nowrap px-2 py-2 sm:table-cell">Size</th>
                <th className="px-2 py-2">Status</th>
                <th className="w-px px-2 py-2"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
              {rows.map((f) => (
                <tr key={f.id} className={picked.has(f.id) ? "bg-blue-50/60 dark:bg-blue-950/30" : "hover:bg-gray-50 dark:hover:bg-gray-800/50"}>
                  <td className="px-3 py-2"><input type="checkbox" aria-label={`Select ${f.filename}`} checked={picked.has(f.id)} onChange={() => togglePick(f.id)} /></td>
                  <td className="max-w-0 px-2 py-2">
                   <div className="flex items-center gap-3">
                    <Thumb f={f} onOpen={() => setDialog({ kind: "preview", file: f })} />
                    <div className="min-w-0 flex-1">
                    <button type="button" onClick={() => setDialog({ kind: "preview", file: f })}
                            className="block max-w-full truncate text-left font-medium text-gray-900 dark:text-gray-100 hover:text-blue-600 dark:hover:text-blue-400 hover:underline"
                            title={`Preview ${f.path}`}>{f.filename}</button>
                    <p className="flex flex-wrap items-center gap-x-2 text-xs text-gray-500 dark:text-gray-400">
                      <span>{f.category}</span>
                      {!!f.is_anthology && <span className="rounded bg-violet-100 dark:bg-violet-900/40 px-1.5 text-violet-700 dark:text-violet-300">anthology</span>}
                      <span className="md:hidden">{f.codec} · {f.height ? `${f.height}p` : "?"} · {fmtSize(f.size_bytes)}</span>
                      {f.error_msg && f.status === "error" && <span className="truncate text-red-600 dark:text-red-400" title={f.error_msg}>{f.error_msg}</span>}
                    </p>
                    </div>
                   </div>
                  </td>
                  <td className="hidden whitespace-nowrap px-2 py-2 text-gray-600 dark:text-gray-300 md:table-cell">{f.codec ?? "—"}</td>
                  <td className="hidden whitespace-nowrap px-2 py-2 text-gray-600 dark:text-gray-300 md:table-cell">{f.height ? `${f.height}p` : "—"}</td>
                  <td className="hidden whitespace-nowrap px-2 py-2 text-gray-600 dark:text-gray-300 lg:table-cell">{fmtDur(f.duration_sec)}</td>
                  <td className="hidden whitespace-nowrap px-2 py-2 text-gray-600 dark:text-gray-300 sm:table-cell">{fmtSize(f.size_bytes)}</td>
                  <td className="px-2 py-2">
                    <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[f.status] ?? STATUS_STYLE.pending}`}>
                      {f.status === "processing" && <Loader2 className="h-3 w-3 animate-spin" />}{f.status}
                    </span>
                  </td>
                  <td className="px-2 py-2">
                    <div className="flex items-center justify-end">
                      <button className={`${iconBtn} hidden text-blue-600 dark:text-blue-400 sm:block`} aria-label={`Preview ${f.filename}`} title="Preview" onClick={() => setDialog({ kind: "preview", file: f })}><Play className="h-4 w-4" /></button>
                      <button className={`${iconBtn} hidden sm:block`} aria-label={`Encode ${f.filename}`} title="Encode to x265" disabled={busy(f)} onClick={() => setDialog({ kind: "encode", files: [f] })}><Film className="h-4 w-4" /></button>
                      <button className={`${iconBtn} hidden sm:block`} aria-label={`Split ${f.filename}`} title="Split scenes" disabled={busy(f)} onClick={() => setDialog({ kind: "split", files: [f] })}><Scissors className="h-4 w-4" /></button>
                      <RowMenu f={f} onAction={(a) => onRowAction(f, a)} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {filtered.length > PAGE_SIZE && (
        <div className="mt-3 flex items-center justify-center gap-3 text-sm text-gray-600 dark:text-gray-300">
          <button className={iconBtn} aria-label="Previous page" disabled={page <= 1} onClick={() => setPage(page - 1)}><ChevronLeft className="h-4 w-4" /></button>
          Page {Math.min(page, pages)} of {pages} · {filtered.length} files
          <button className={iconBtn} aria-label="Next page" disabled={page >= pages} onClick={() => setPage(page + 1)}><ChevronRight className="h-4 w-4" /></button>
        </div>
      )}

      {toast && (
        <div role="status" className="fixed bottom-20 left-1/2 z-50 -translate-x-1/2 rounded-lg bg-gray-900 px-4 py-2 text-sm text-white shadow-lg md:bottom-6 dark:bg-gray-100 dark:text-gray-900">
          {toast}
        </div>
      )}

      {dialog?.kind === "encode" && <EncodeDialog files={dialog.files} onClose={() => setDialog(null)}
                                                  onConfirm={(res) => { queue(dialog.files, "encode", res); setDialog(null); }} />}
      {dialog?.kind === "split" && <SplitDialog files={dialog.files} onClose={() => setDialog(null)}
                                                onConfirm={(n) => { queue(dialog.files, "split", "original", n); setDialog(null); }} />}
      {dialog?.kind === "rename" && <RenameDialog file={dialog.file} onClose={() => setDialog(null)} onDone={(m) => { say(m); load(); }} />}
      {dialog?.kind === "delete" && (
        <Modal title={dialog.files.length === 1 ? `Delete ${dialog.files[0].filename}?` : `Delete ${dialog.files.length} files?`} onClose={() => setDialog(null)}>
          <p className="text-sm text-gray-600 dark:text-gray-300">
            Files move to the <code>.trash</code> folder in their media path and are removed for good after 30 days
            (or when you empty the trash in Settings).
          </p>
          <div className="mt-4 flex justify-end gap-2">
            <button className={btnGhost} onClick={() => setDialog(null)}>Cancel</button>
            <button className={`${btn} bg-red-600 font-medium text-white hover:bg-red-700`} onClick={() => {
              const ids = dialog.files.map((f) => f.id);
              setDialog(null);
              run(async () => {
                if (ids.length === 1) { await api.media.remove(ids[0]); return "Moved to trash"; }
                const r = await api.media.bulk("delete", ids);
                setPicked(new Set());
                return `Moved ${r.done} to trash${r.skipped ? ` (${r.skipped} busy, left alone)` : ""}${r.errors.length ? ` — ${r.errors[0]}` : ""}`;
              });
            }}>Move to trash</button>
          </div>
        </Modal>
      )}
      {dialog?.kind === "preview" && <PreviewDialog file={dialog.file} onClose={() => setDialog(null)} />}
      {dialog?.kind === "doctor" && <DoctorDialog onClose={() => setDialog(null)} onChanged={load} />}
      {dialog?.kind === "organise" && <OrganiseDialog files={dialog.files} onClose={() => setDialog(null)}
                                                      onDone={(m) => { setPicked(new Set()); say(m); load(); }} />}
    </div>
  );
}

const DOCTOR_KINDS: { kind: DoctorKind; title: string; hint: string }[] = [
  { kind: "broken", title: "Can't be played", hint: "Damaged or incomplete files." },
  { kind: "duplicate", title: "Duplicates", hint: "The best copy (resolution, then HEVC, then size) is kept." },
  { kind: "audience", title: "Wrong folder", hint: "Rated for adults in a regular folder, or the other way round." },
  { kind: "missing", title: "Missing files", hint: "Listed in the Library, but the file is gone." },
  { kind: "name", title: "Not named for Plex", hint: "Fixing moves them with Rename & organise." },
  { kind: "sample", title: "Sample clips", hint: "Fixed automatically when that's on." },
  { kind: "junk", title: "Leftover files", hint: "Fixed automatically when that's on." },
  { kind: "empty", title: "Empty folders", hint: "Fixed automatically when that's on." },
];

/** Library doctor: last report, Check now, Fix (one, or all of a kind), and auto-fix. */
function DoctorDialog({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const [state, setState] = useState<DoctorState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { api.doctor.get().then(setState).catch((e) => setMsg(e.message)); }, []);

  const check = async () => {
    setBusy("check"); setMsg(null);
    try {
      const s = await api.doctor.run();
      setState(s);
      const fixed = s.report?.fixed.length ?? 0;
      setMsg(`${s.report?.total ? `${s.report.total} to look at` : "Nothing to fix"}${fixed ? ` · ${fixed} fixed automatically` : ""}`);
      onChanged();
    } catch (e) { setMsg((e as Error).message); } finally { setBusy(null); }
  };
  const fix = async (ids: string[], key: string) => {
    setBusy(key); setMsg(null);
    try {
      const r = await api.doctor.fix(ids);
      setState(r);
      setMsg(`Fixed ${r.done}${r.errors.length ? ` · ${r.errors.length} couldn't be: ${r.errors[0]}` : ""}`);
      onChanged();
    } catch (e) { setMsg((e as Error).message); } finally { setBusy(null); }
  };

  const report = state?.report;
  const byKind = (k: DoctorKind) => report?.issues.filter((i) => i.kind === k) ?? [];
  return (
    <Modal title="Library doctor" onClose={onClose} wide>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <p className="mr-auto text-gray-600 dark:text-gray-300">
          {report ? <>Last checked {new Date(report.checked_at).toLocaleString()}. Checks run every Saturday.</> : "Not checked yet."}
        </p>
        <button className={btnPrimary} onClick={check} disabled={!!busy}>
          {busy === "check" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Stethoscope className="h-4 w-4" />}Check now
        </button>
      </div>
      <label className="mb-3 flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
        <input type="checkbox" checked={!!state?.auto_fix} disabled={!state}
               onChange={async (e) => setState(await api.doctor.setAutoFix(e.target.checked))} />
        Fix sample clips, leftover files and empty folders automatically (they go to the 30-day trash)
      </label>
      {msg && <p role="status" className="mb-3 rounded-lg bg-blue-50 dark:bg-blue-950/40 px-3 py-2 text-xs text-blue-800 dark:text-blue-200">{msg}</p>}

      <div className="max-h-[55vh] space-y-3 overflow-y-auto">
        {report && report.issues.length === 0 && (
          <p className="flex items-center gap-2 rounded-lg border border-green-200 dark:border-green-900 bg-green-50 dark:bg-green-950/30 p-3 text-sm text-green-800 dark:text-green-300">
            <CheckCircle2 className="h-4 w-4" />Your library looks healthy.
          </p>
        )}
        {DOCTOR_KINDS.map(({ kind, title, hint }) => {
          const list = byKind(kind);
          if (!list.length) return null;
          return (
            <section key={kind} className="rounded-lg border border-gray-200 dark:border-gray-700">
              <header className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-800 px-3 py-2">
                <div className="mr-auto">
                  <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{title} <span className="text-gray-400">({list.length})</span></p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">{hint}</p>
                </div>
                <button className={btnGhost} disabled={!!busy} onClick={() => fix(list.map((i) => i.id), kind)}>
                  {busy === kind ? <Loader2 className="h-4 w-4 animate-spin" /> : null}Fix all
                </button>
              </header>
              <ul className="divide-y divide-gray-100 dark:divide-gray-800">
                {list.slice(0, 100).map((i) => (
                  <li key={i.id} className="flex items-start gap-2 px-3 py-2 text-xs">
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-mono text-gray-800 dark:text-gray-200" title={i.path}>{i.path}</p>
                      <p className="break-words text-gray-500 dark:text-gray-400">{i.detail}</p>
                      {i.error && <p className="text-red-600 dark:text-red-400">{i.error}</p>}
                    </div>
                    <button className="shrink-0 rounded-md border border-gray-300 dark:border-gray-600 px-2 py-1 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50"
                            disabled={!!busy} title={i.fix} onClick={() => fix([i.id], i.id)}>
                      {busy === i.id ? <Loader2 className="h-3 w-3 animate-spin" /> : "Fix"}
                    </button>
                  </li>
                ))}
                {list.length > 100 && <li className="px-3 py-2 text-xs text-gray-500">…and {list.length - 100} more (Fix all includes them).</li>}
              </ul>
            </section>
          );
        })}
        {report && report.fixed.length > 0 && (
          <details className="rounded-lg border border-gray-200 dark:border-gray-700 px-3 py-2 text-xs text-gray-600 dark:text-gray-300">
            <summary className="cursor-pointer">Fixed ({report.fixed.length})</summary>
            <ul className="mt-2 space-y-1">
              {report.fixed.slice(-100).map((i) => <li key={i.id} className="truncate" title={i.path}>{i.label}: {i.path} — {i.result}</li>)}
            </ul>
          </details>
        )}
      </div>
    </Modal>
  );
}

/** Rename & organise: show where each file would go, then move them. */
function OrganiseDialog({ files, onClose, onDone }: { files: MediaFile[]; onClose: () => void; onDone: (msg: string) => void }) {
  const [plans, setPlans] = useState<Record<number, OrganisePlan>>({});
  const [chosen, setChosen] = useState<Record<number, "general" | "mature">>({});
  const [busy, setBusy] = useState(false);
  const preview = (id: number, audience?: "general" | "mature") =>
    api.media.organisePreview(id, audience).then((r) => setPlans((p) => ({ ...p, [id]: r })))
      .catch((e) => setPlans((p) => ({ ...p, [id]: { ok: false, msg: String(e.message ?? e) } })));
  useEffect(() => { files.slice(0, 50).forEach((f) => preview(f.id)); }, [files]);
  const choose = (id: number, a: "general" | "mature") => { setChosen((c) => ({ ...c, [id]: a })); preview(id, a); };
  const movable = files.filter((f) => plans[f.id]?.ok && !plans[f.id]?.already);
  const go = async () => {
    setBusy(true);
    try {
      const audiences = Object.fromEntries(movable.map((f) => [f.id, chosen[f.id] ?? plans[f.id]?.audience ?? "mature"]));
      const r = await api.media.bulk("organise", movable.map((f) => f.id), audiences);
      onClose();
      onDone(`Organised ${r.done}${r.skipped ? ` (${r.skipped} busy, left alone)` : ""}${r.errors.length ? ` — ${r.errors[0]}` : ""}`);
    } catch (e) {
      onClose();
      onDone((e as Error).message);
    }
  };
  return (
    <Modal title={files.length === 1 ? `Rename & organise ${files[0].filename}` : `Rename & organise ${files.length} files`} onClose={onClose} wide>
      <ul className="max-h-[50vh] space-y-2 overflow-y-auto text-xs">
        {files.slice(0, 50).map((f) => {
          const p = plans[f.id];
          return (
            <li key={f.id} className="rounded-lg border border-gray-200 dark:border-gray-700 p-2">
              <p className="truncate font-medium text-gray-900 dark:text-gray-100" title={f.path}>{f.filename}</p>
              {!p ? <p className="text-gray-400">Working it out…</p>
                : p.ok ? <>
                    <p className="break-all text-gray-600 dark:text-gray-300">{p.already ? "Already in place" : <>→ <span className="font-mono">{p.target}</span></>}</p>
                    {p.mature_folders && (
                      <div className="mt-1 flex flex-wrap items-center gap-2">
                        <div className="inline-flex rounded-md border border-gray-300 dark:border-gray-600 p-0.5" role="radiogroup" aria-label={`Folder for ${f.filename}`}>
                          {(["general", "mature"] as const).map((a) => (
                            <button key={a} type="button" role="radio" aria-checked={p.audience === a} onClick={() => choose(f.id, a)}
                                    className={`rounded px-2 py-0.5 ${p.audience === a ? (a === "mature" ? "bg-rose-600 text-white" : "bg-blue-600 text-white") : "text-gray-600 dark:text-gray-300"}`}>
                              {a === "mature" ? "Mature" : "Regular"}
                            </button>
                          ))}
                        </div>
                        {p.reason && <span className="text-gray-500 dark:text-gray-400">{p.reason}</span>}
                      </div>
                    )}
                  </>
                : <p className="text-amber-700 dark:text-amber-400">{p.msg}</p>}
            </li>
          );
        })}
      </ul>
      {files.length > 50 && <p className="mt-2 text-xs text-gray-500">Showing the first 50.</p>}
      <p className="mt-3 text-xs text-gray-500 dark:text-gray-400">
        Files move with Plex-style names, along with matching subtitles. Nothing is overwritten.
        Folders and naming are in Settings → Auto pipeline.
      </p>
      <div className="mt-4 flex justify-end gap-2">
        <button className={btnGhost} onClick={onClose}>Cancel</button>
        <button className={btnPrimary} onClick={go} disabled={busy || !movable.length}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <FolderInput className="h-4 w-4" />}
          Move {movable.length || ""}
        </button>
      </div>
    </Modal>
  );
}

function EncodeDialog({ files, onClose, onConfirm }: { files: MediaFile[]; onClose: () => void; onConfirm: (res: string) => void }) {
  const [res, setRes] = useState("original");
  const one = files.length === 1 ? files[0] : null;
  return (
    <Modal title={one ? `Encode ${one.filename}` : `Encode ${files.length} files`} onClose={onClose}>
      <p className="mb-3 text-sm text-gray-600 dark:text-gray-300">
        Re-encode to HEVC (x265) using the quality and speed from Settings → Media engine. The original is replaced only after the new file checks out.
        {one?.height ? ` Current resolution: ${one.height}p.` : ""}
      </p>
      <label className="block text-sm">
        <span className="mb-1 block font-medium text-gray-700 dark:text-gray-300">Output resolution</span>
        <select className={`${sel} w-full`} value={res} onChange={(e) => setRes(e.target.value)}>
          {RESOLUTIONS.map((r) => <option key={r} value={r}>{r === "original" ? "Keep original" : r}</option>)}
        </select>
      </label>
      <div className="mt-4 flex justify-end gap-2">
        <button className={btnGhost} onClick={onClose}>Cancel</button>
        <button className={btnPrimary} onClick={() => onConfirm(res)}><Film className="h-4 w-4" />Queue encode</button>
      </div>
    </Modal>
  );
}

function SplitDialog({ files, onClose, onConfirm }: { files: MediaFile[]; onClose: () => void; onConfirm: (n: number) => void }) {
  const [n, setN] = useState("");
  return (
    <Modal title={files.length === 1 ? `Split ${files[0].filename}` : `Split ${files.length} files`} onClose={onClose}>
      <p className="mb-3 text-sm text-gray-600 dark:text-gray-300">
        Detects scene changes and cuts the file into one file per scene (no re-encode). The original is removed once every part checks out.
      </p>
      <label className="block text-sm">
        <span className="mb-1 block font-medium text-gray-700 dark:text-gray-300">Number of scenes, if you know it</span>
        <input className={`${sel} w-full`} type="number" min={0} placeholder="Leave blank to detect automatically" value={n} onChange={(e) => setN(e.target.value)} />
      </label>
      <div className="mt-4 flex justify-end gap-2">
        <button className={btnGhost} onClick={onClose}>Cancel</button>
        <button className={btnPrimary} onClick={() => onConfirm(Math.max(0, Number(n) || 0))}><Scissors className="h-4 w-4" />Queue split</button>
      </div>
    </Modal>
  );
}

function RenameDialog({ file, onClose, onDone }: { file: MediaFile; onClose: () => void; onDone: (msg: string) => void }) {
  const ext = file.filename.includes(".") ? file.filename.slice(file.filename.lastIndexOf(".")) : "";
  const [name, setName] = useState(file.filename.slice(0, file.filename.length - ext.length));
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    try {
      const r = await api.media.rename(file.id, name);
      onDone(r.filename ? `Renamed to ${r.filename}` : r.msg ?? "Renamed");
      onClose();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <Modal title="Rename file" onClose={onClose}>
      <div className="flex items-center gap-1">
        <input autoFocus aria-label="New name" className={`${sel} flex-1`} value={name} onChange={(e) => setName(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && save()} />
        <span className="text-sm text-gray-500">{ext}</span>
      </div>
      {error && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <button className={btnGhost} onClick={onClose}>Cancel</button>
        <button className={btnPrimary} onClick={save} disabled={!name.trim()}>Rename</button>
      </div>
    </Modal>
  );
}
