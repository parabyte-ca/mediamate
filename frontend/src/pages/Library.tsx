import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  ChevronLeft, ChevronRight, Download, Edit2, Eye, Film, FolderInput, FolderSearch, Layers, Loader2, MoreHorizontal,
  CheckCircle2, ShieldAlert, ShieldCheck, Music, Pause, Play, Scissors, Share2, SkipForward, Square, Star, Stethoscope, Tags, Timer, Trash2, X,
} from "lucide-react";
import { api, type DoctorKind, type DownloadPath, type MediaFile, type MediaJobType, type MediaStats, type OrganisePlan } from "../api/client";
import { useEvent } from "../events";
import Stars from "../components/Stars";
import VideoPlayer from "../components/VideoPlayer";
import ShareDialog from "../components/ShareDialog";
import MusicLibrary from "../components/MusicLibrary";
import DoctorDialog, { type DoctorKindInfo } from "../components/DoctorDialog";
import { fmtSize } from "../utils";

// The media library (FileSplitter's Files tab): everything under the media
// paths, with filters, per-file and bulk encode / split / trim, preview,
// rename, skip and delete-to-trash.

const PAGE_SIZE = 50;
const RESOLUTIONS = ["original", "480p", "720p", "1080p", "1440p", "2160p"];

/** Short labels for the row chips; the filter and tiles spell "ok" out in full. */
const STATUS_LABEL: Record<string, string> = { ok: "on target" };
const STATUS_STYLE: Record<string, string> = {
  pending: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300",
  ok: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400",
  queued: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400",
  processing: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400",
  done: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400",
  error: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400",
  skipped: "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400",
  cancelled: "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400",
};

type Filters = Record<"q" | "category" | "status" | "codec" | "type" | "res" | "dur" | "size" | "rating" | "coll", string>;
const FILTER_KEYS: (keyof Filters)[] = ["q", "category", "status", "codec", "type", "res", "dur", "size", "rating", "coll"];

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
  if (fl.rating === "unrated" && f.rating) return false;
  if (fl.rating && fl.rating !== "unrated" && (f.rating ?? 0) < Number(fl.rating)) return false;
  if (fl.coll && !(f.collections ?? []).includes(fl.coll)) return false;
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
  | { kind: "share"; file: MediaFile }
  | { kind: "organise"; files: MediaFile[] }
  | { kind: "move"; files: MediaFile[] }
  | { kind: "collections"; files: MediaFile[] }
  | { kind: "rate"; files: MediaFile[] }
  | { kind: "doctor" };

// ── Row actions menu ──────────────────────────────────────────────────────────

function RowMenu({ f, onAction }: { f: MediaFile; onAction: (a: string) => void }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<React.CSSProperties | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const shut = () => setOpen(false);
    // Close once the row has really moved away, not on the settle after a tap.
    const top0 = btnRef.current?.getBoundingClientRect().top ?? 0;
    const onScroll = (e: Event) => {
      if (menuRef.current?.contains(e.target as Node)) return;
      const top = btnRef.current?.getBoundingClientRect().top ?? top0;
      if (Math.abs(top - top0) > 24) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    window.addEventListener("resize", shut);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("mousedown", close);
      window.removeEventListener("resize", shut);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);
  // The menu floats over the page (fixed), so the table can't clip it, and opens
  // upwards when there isn't room below (the last rows, the phone tab bar).
  useLayoutEffect(() => {
    if (!open) { setPos(null); return; }
    const b = btnRef.current?.getBoundingClientRect();
    const h = menuRef.current?.offsetHeight ?? 0;
    if (!b) return;
    const vh = window.innerHeight;
    const tabBar = document.querySelector<HTMLElement>("[data-mobile-tabs]")?.getBoundingClientRect();
    const bottomEdge = (tabBar && tabBar.height > 0 ? tabBar.top : vh) - 8;
    const below = bottomEdge - b.bottom - 4;
    const above = b.top - 8 - 4;
    const right = Math.max(8, window.innerWidth - b.right);
    if (h <= below || below >= above) {
      setPos({ top: b.bottom + 4, right, maxHeight: below });
    } else {
      setPos({ top: Math.max(8, b.top - 4 - Math.min(h, above)), right, maxHeight: above });
    }
  }, [open]);
  const item = (a: string, label: string, Icon: typeof Film, disabled = false, danger = false) => (
    <button disabled={disabled} onClick={() => { setOpen(false); onAction(a); }}
            className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm disabled:opacity-40 hover:bg-gray-50 dark:hover:bg-gray-800 ${danger ? "text-red-600 dark:text-red-400" : "text-gray-700 dark:text-gray-200"}`}>
      <Icon className="h-3.5 w-3.5" />{label}
    </button>
  );
  return (
    <div className="relative" ref={ref}>
      <button ref={btnRef} onClick={() => setOpen((v) => !v)} aria-label={`More actions for ${f.filename}`}
              aria-haspopup="menu" aria-expanded={open} className={iconBtn}>
        <MoreHorizontal className="h-4 w-4" />
      </button>
      {open && (
        <div ref={menuRef} role="menu" style={pos ?? { top: 0, right: 0, visibility: "hidden" }}
             className="fixed z-40 w-48 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-lg">
          {item("preview", "Preview", Eye)}
          {item("share", "Share…", Share2)}
          <div className="sm:hidden">
            {item("encode", "Encode…", Film, busy(f))}
            {item("split", "Split…", Scissors, busy(f))}
          </div>
          {item("trim", "Trim intro (3.5 s)", Timer, busy(f))}
          {item("rename", "Rename…", Edit2, busy(f))}
          {item("organise", "Rename & organise…", FolderInput, busy(f))}
          {item("mature", "Move to Mature", ShieldAlert, busy(f))}
          {item("kids", "Kid-friendly (regular folders)", ShieldCheck, busy(f))}
          {item("collections", "Collections…", Tags)}
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

function PreviewDialog({ file, onClose }: { file: MediaFile; onClose: () => void }) {
  return (
    <Modal title={file.filename} onClose={onClose} wide>
      <VideoPlayer original={api.media.streamUrl(file.id)} converted={(from) => api.media.playUrl(file.id, from)}
                   duration={file.duration_sec ?? 0} buttonClass={btnGhost}
                   extra={<a href={api.media.streamUrl(file.id, true)} className={btnGhost}><Download className="h-4 w-4" />Download</a>} />
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
  // Adult content found in the Plex folders by the Library doctor: always shown until fixed.
  const [adultCount, setAdultCount] = useState(0);
  const loadAdult = useCallback(() => {
    api.doctor.get().then((d) => setAdultCount(d.report?.issues.filter((i) => i.kind === "adult_in_plex").length ?? 0))
      .catch(() => {});
  }, []);
  useEffect(() => { loadAdult(); }, [loadAdult]);
  // Titles the doctor says belong in Mature (e.g. PG-13 horror in the regular folders).
  const [toMature, setToMature] = useState(0);
  const loadAudience = useCallback(() => {
    api.doctor.get().then((d) => setToMature(d.report?.issues.filter((i) => i.kind === "audience" && i.bulk !== false).length ?? 0))
      .catch(() => {});
  }, []);
  useEffect(() => { loadAudience(); }, [loadAudience]);
  // "Not in Plex yet" only means something while Plex sync is on.
  const [plexOn, setPlexOn] = useState(false);
  useEffect(() => { api.media.plexStatus().then((s) => setPlexOn(s.enabled)).catch(() => {}); }, []);
  const load = useCallback(async () => {
    const [f, s] = await Promise.all([api.media.files(), api.media.stats()]);
    setFiles(f);
    const want = Number(new URLSearchParams(window.location.search).get("preview") || 0);
    const hit = want ? f.find((x) => x.id === want) : undefined;
    if (hit) setDialog((d) => d ?? { kind: "preview", file: hit });
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
  const collections = useMemo(() => [...new Set(files.flatMap((f) => f.collections ?? []))].sort((a, b) => a.localeCompare(b)), [files]);
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

  const patch = (id: number, p: Partial<MediaFile>) => setFiles((fs) => fs.map((x) => (x.id === id ? { ...x, ...p } : x)));
  const rate = (f: MediaFile, n: number) => {
    patch(f.id, { rating: n || null });
    api.media.rate(f.id, n).then((r) => {
      patch(f.id, { rating: r.rating, collections: r.collections, plex_dirty: r.plex === "queued" ? 1 : 0 });
      if (r.plex === "queued" || r.plex === "local") say(r.msg);
    }).catch((e) => { say((e as Error).message); load().catch(() => {}); });
  };

  const onRowAction = (f: MediaFile, a: string) => {
    if (a === "preview") return setDialog({ kind: "preview", file: f });
    if (a === "share") return setDialog({ kind: "share", file: f });
    if (a === "collections") return setDialog({ kind: "collections", files: [f] });
    if (a === "organise") return setDialog({ kind: "organise", files: [f] });
    if (a === "mature" || a === "kids") {
      const to = a === "mature" ? "mature" : "general";
      if (to === "general" && !window.confirm("Show this title to everyone, including the kids' Plex profiles? Every file of it moves to the regular folders.")) return;
      return run(async () => { const r = await api.media.setAudience(f.id, to); await load(); loadAudience(); return r.msg; });
    }
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
    ["Files", stats.total, ""], ["Pending", stats.pending, "pending"], ["In target format", stats.ok ?? 0, "ok"],
    ["Queued", stats.queued, "queued"],
    ["Processing", stats.processing, "processing"], ["Done", stats.done, "done"], ["Errors", stats.errors, "error"],
  ] : [];

  const libKind = params.get("lib") === "music" ? "music" : "video";
  const setLib = (k: "video" | "music") => setParams(k === "music" ? { lib: "music" } : {}, { replace: true });
  const libTabs = (
    <div className="inline-flex rounded-lg border border-gray-200 p-0.5 dark:border-gray-700" role="tablist" aria-label="Library">
      {([["video", "Videos", Film], ["music", "Music", Music]] as const).map(([k, label, Icon]) => (
        <button key={k} role="tab" aria-selected={libKind === k} onClick={() => setLib(k)}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium ${libKind === k
                  ? "bg-blue-600 text-white" : "text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"}`}>
          <Icon className="h-4 w-4" aria-hidden />{label}
        </button>
      ))}
    </div>
  );
  if (libKind === "music") {
    return (
      <div className="mx-auto max-w-6xl p-4 md:p-6">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">Library</h1>
          {libTabs}
        </div>
        <MusicLibrary />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl p-4 md:p-6">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">Library</h1>
        <div className="mr-auto">{libTabs}</div>
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
        <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-7">
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
          {["pending", "ok", "queued", "processing", "done", "error", "skipped"].map((s) => <option key={s} value={s}>{s === "ok" ? "in target format" : s}</option>)}
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
        <select aria-label="Rating" className={sel} value={filters.rating} onChange={(e) => setFilter("rating", e.target.value)}>
          <option value="">Any rating</option>
          {[5, 4, 3, 2, 1].map((n) => <option key={n} value={n}>{"★".repeat(n)}{n < 5 ? " or more" : ""}</option>)}
          <option value="unrated">Not rated</option>
        </select>
        {collections.length > 0 && (
          <select aria-label="Collection" className={sel} value={filters.coll} onChange={(e) => setFilter("coll", e.target.value)}>
            <option value="">All collections</option>{collections.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        )}
        {anyFilter && <button className="text-sm text-blue-600 hover:underline" onClick={clearFilters}>Clear filters</button>}
      </div>

      {adultCount > 0 && (
        <div role="alert" className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/40 px-3 py-2 text-sm text-red-800 dark:text-red-200">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span className="flex-1">{adultCount} adult file{adultCount === 1 ? " is" : "s are"} in a Plex folder (TV or Movies). Move {adultCount === 1 ? "it" : "them"} back to the protected folder.</span>
          <button className={btnPrimary} onClick={() => setDialog({ kind: "doctor" })}>Review</button>
        </div>
      )}

      {toMature > 0 && (
        <div role="alert" className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-amber-300 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 px-3 py-2 text-sm text-amber-900 dark:text-amber-200">
          <ShieldAlert className="h-4 w-4 shrink-0" />
          <span className="flex-1">{toMature} file{toMature === 1 ? " in the regular folders belongs" : "s in the regular folders belong"} in Mature (for example PG-13 horror), so the kids' Plex profiles can see {toMature === 1 ? "it" : "them"}.</span>
          <button className={btnPrimary} onClick={() => setDialog({ kind: "doctor" })}>Review</button>
        </div>
      )}

      {pickedFiles.length > 0 && (
        <div className="sticky top-12 z-20 mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-blue-200 dark:border-blue-900 bg-blue-50 dark:bg-blue-950/60 px-3 py-2 md:top-2">
          <span className="text-sm font-medium text-blue-800 dark:text-blue-200">{pickedFiles.length} selected</span>
          <button className={btnPrimary} onClick={() => setDialog({ kind: "encode", files: pickedFiles })}><Film className="h-4 w-4" />Encode</button>
          <button className={btnGhost} onClick={() => setDialog({ kind: "split", files: pickedFiles })}><Scissors className="h-4 w-4" />Split</button>
          <button className={btnGhost} onClick={() => queue(pickedFiles, "trim")}><Timer className="h-4 w-4" />Trim</button>
          <button className={btnGhost} onClick={() => run(async () => { const r = await api.media.bulk("skip", [...picked]); setPicked(new Set()); return `Skipped ${r.done}`; })}><SkipForward className="h-4 w-4" />Skip</button>
          <button className={btnGhost} onClick={() => setDialog({ kind: "organise", files: files.filter((x) => picked.has(x.id)) })}><FolderInput className="h-4 w-4" />Organise</button>
          <button className={btnGhost} onClick={() => setDialog({ kind: "move", files: files.filter((x) => picked.has(x.id)) })}><FolderInput className="h-4 w-4" />Move to folder</button>
          <button className={btnGhost} onClick={() => run(async () => {
            const done = new Set<string>(); let moved = 0;
            for (const f of pickedFiles) {
              const r = await api.media.setAudience(f.id, "mature");
              if (r.title && done.has(r.title)) continue;
              if (r.title) done.add(r.title);
              moved += r.moved;
            }
            setPicked(new Set()); await load(); loadAudience();
            return `Moved ${moved} file${moved === 1 ? "" : "s"} to Mature`;
          })}><ShieldAlert className="h-4 w-4" />Move to Mature</button>
          <button className={btnGhost} onClick={() => setDialog({ kind: "rate", files: pickedFiles })}><Star className="h-4 w-4" />Rate</button>
          <button className={btnGhost} onClick={() => setDialog({ kind: "collections", files: pickedFiles })}><Tags className="h-4 w-4" />Collections</button>
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
                      <Stars value={f.rating} label={f.filename} onChange={(n) => rate(f, n)} />
                      {(f.collections ?? []).map((c) => (
                        <button key={c} type="button" onClick={() => setFilter("coll", c)} title={`Show the ${c} collection`}
                                className="inline-flex items-center gap-1 rounded bg-sky-100 px-1.5 text-sky-700 hover:underline dark:bg-sky-900/40 dark:text-sky-300">
                          <Tags className="h-3 w-3" />{c}
                        </button>
                      ))}
                      {plexOn && !!f.plex_dirty && <span className="text-amber-600 dark:text-amber-400" title="Saved here; it goes to Plex on the next sync (or Plex hasn't found this file yet)">not in Plex yet</span>}
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
                    <span title={f.status === "ok" ? `Already in target format${f.target ? `: ${f.target}` : ""}` : undefined}
                          className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_STYLE[f.status] ?? STATUS_STYLE.pending}`}>
                      {f.status === "processing" && <Loader2 className="h-3 w-3 animate-spin" />}{STATUS_LABEL[f.status] ?? f.status}
                    </span>
                    {f.target_note && (
                      <span title={f.target ? `Target: ${f.target}` : undefined}
                            className={`mt-1 block w-fit whitespace-nowrap rounded-full px-2 py-0.5 text-[10px] font-medium ${f.target_note === "above"
                              ? "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400"
                              : "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"}`}>
                        {f.target_note === "above" ? "Above target" : "Below preferred"}
                      </span>
                    )}
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
      {dialog?.kind === "share" && (
        <ShareDialog kind="video" id={dialog.file.id} title={dialog.file.filename}
                     householdPath={`/library?preview=${dialog.file.id}`} onClose={() => setDialog(null)} />
      )}
      {dialog?.kind === "doctor" && <DoctorDialog title="Library doctor" kinds={DOCTOR_KINDS} source={api.doctor}
                                                  autoFixLabel="Fix sample clips, leftover files and empty folders automatically (they go to the 30-day trash)"
                                                  onClose={() => setDialog(null)} onChanged={() => { load(); loadAdult(); loadAudience(); }}
                                                  onKeep={async (i) => (await api.media.setAudience(i.file_id as number, "general")).msg} />}
      {dialog?.kind === "move" && (
        <MoveDialog files={dialog.files} onClose={() => setDialog(null)}
                    onDone={(msg) => { setDialog(null); setPicked(new Set()); say(msg); load(); loadAdult(); }} />
      )}
      {dialog?.kind === "collections" && <CollectionsDialog files={dialog.files} onClose={() => setDialog(null)}
                                                            onDone={(m) => { setDialog(null); setPicked(new Set()); say(m); load(); }} />}
      {dialog?.kind === "rate" && (
        <Modal title={`Rate ${dialog.files.length} file${dialog.files.length === 1 ? "" : "s"}`} onClose={() => setDialog(null)}>
          <p className="mb-3 text-sm text-gray-600 dark:text-gray-300">Pick the stars for all of them. Ratings go to Plex when Plex sync is on.</p>
          <div className="flex flex-wrap items-center gap-3">
            <Stars big value={0} label="the selected files" onChange={(n) => {
              const ids = dialog.files.map((f) => f.id);
              setDialog(null);
              run(async () => { const r = await api.media.tagBulk({ action: "rate", stars: n }, ids); setPicked(new Set());
                return `Rated ${r.done} file${r.done === 1 ? "" : "s"}${r.errors.length ? ` · ${r.errors[0]}` : ""}`; });
            }} />
            <button className={btnGhost} onClick={() => {
              const ids = dialog.files.map((f) => f.id);
              setDialog(null);
              run(async () => { const r = await api.media.tagBulk({ action: "rate", stars: 0 }, ids); setPicked(new Set()); return `Cleared ${r.done} rating${r.done === 1 ? "" : "s"}`; });
            }}>Clear ratings</button>
          </div>
        </Modal>
      )}
      {dialog?.kind === "organise" && <OrganiseDialog files={dialog.files} onClose={() => setDialog(null)}
                                                      onDone={(m) => { setPicked(new Set()); say(m); load(); }} />}
    </div>
  );
}

const DOCTOR_KINDS: DoctorKindInfo<DoctorKind>[] = [
  { kind: "adult_in_plex", title: "Adult content in a Plex folder", hint: "Moved into TV or Movies before folders could be protected. Fixing moves each back to the protected folder (its original name when known). Check any marked \u201cmay not be a real movie\u201d first.",
    confirm: (n) => `Move ${n === 1 ? "this file" : `${n} files`} out of the Plex folders, back to the protected folder?` },
  { kind: "broken", title: "Can't be played", hint: "Damaged or incomplete files." },
  { kind: "duplicate", title: "Duplicates", hint: "The best copy (resolution, then HEVC, then size) is kept." },
  { kind: "audience", title: "Rated for adults, in a regular folder", hint: "Where a file is counts as your decision, so only titles rated R, NC-17 or TV-MA in TV Shows or Movies are listed. \"Keep here\" leaves it and stops asking; Move sends it to Mature. Plex refreshes after.",
    fixAll: "Move all to Mature", fixOne: "Move",
    confirm: (n) => `Move ${n} file${n === 1 ? "" : "s"} to the Mature folders? The kids' Plex profiles won't see ${n === 1 ? "it" : "them"} any more.` },
  { kind: "wrong_library", title: "In the wrong library", hint: "Movies in a TV library (including _Unsorted) or episodes in a Movies library. Fixing moves each to the right library on the same side (regular or Mature). Plex refreshes after.",
    fixAll: "Move all", fixOne: "Move",
    confirm: (n) => `Move ${n === 1 ? "this file" : `${n} files`} to the right library? Regular stays regular, Mature stays Mature.` },
  { kind: "missing", title: "Missing files", hint: "Listed in the Library, but the file is gone." },
  { kind: "name", title: "Not named for Plex", hint: "Fixing moves them with Rename & organise." },
  { kind: "oversize", title: "Larger than target", hint: "Already H.265, but bigger than the folder's target. Fixing re-encodes them smaller.",
    fixAll: "Shrink all", fixOne: "Shrink",
    confirm: (n) => `Re-encode ${n === 1 ? "this file" : `${n} files`} to the folder's target size? It takes a while and replaces the file (the original goes to the 30-day trash).` },
  { kind: "lowres", title: "Lower than preferred", hint: "Can't be improved in place; look for a better copy.",
    link: (i) => ({ to: `/search?q=${encodeURIComponent(searchTitle(i.path))}`, label: "Search" }) },
  { kind: "sample", title: "Sample clips", hint: "Fixed automatically when that's on." },
  { kind: "junk", title: "Leftover files", hint: "Fixed automatically when that's on." },
  { kind: "empty", title: "Empty folders", hint: "Fixed automatically when that's on." },
];

/** Add files to Plex collections, or take them out. */
function CollectionsDialog({ files, onClose, onDone }: { files: MediaFile[]; onClose: () => void; onDone: (msg: string) => void }) {
  const [names, setNames] = useState<string[] | null>(null);
  const [want, setWant] = useState<Record<string, boolean | null>>({});   // null: leave as is (some have it)
  const [fresh, setFresh] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const has = (n: string) => files.filter((f) => (f.collections ?? []).includes(n)).length;
  useEffect(() => {
    const mine = files.flatMap((f) => f.collections ?? []);
    api.media.collectionNames().then((r) => r.names).catch(() => [] as string[]).then((all) => {
      const list = [...new Set([...all, ...mine])].sort((a, b) => a.localeCompare(b));
      setNames(list);
      setWant(Object.fromEntries(list.map((n) => {
        const k = files.filter((f) => (f.collections ?? []).includes(n)).length;
        return [n, k === files.length ? true : k === 0 ? false : null];
      })));
    });
  }, [files]);
  const addNew = () => {
    const n = fresh.trim();
    if (!n) return;
    setNames((l) => (l?.includes(n) ? l : [...(l ?? []), n]));
    setWant((w) => ({ ...w, [n]: true }));
    setFresh("");
  };
  const episodes = files.some((f) => /\bS\d{1,2}E\d{1,3}\b|\b\d{1,2}x\d{2}\b/i.test(f.filename));
  const save = async () => {
    const add = Object.entries(want).filter(([n, v]) => v === true && has(n) < files.length).map(([n]) => n);
    const remove = Object.entries(want).filter(([n, v]) => v === false && has(n) > 0).map(([n]) => n);
    if (!add.length && !remove.length) return onClose();
    setBusy(true); setError(null);
    try {
      if (files.length === 1) {
        const r = await api.media.setCollections(files[0].id, add, remove);
        onDone(r.msg);
      } else {
        const r = await api.media.tagBulk({ action: "collect", add, remove }, files.map((f) => f.id));
        if (r.errors.length && !r.done) setError(r.errors[0]);
        else onDone(`Updated ${r.done} file${r.done === 1 ? "" : "s"}${r.sent ? ` · ${r.sent} sent to Plex` : ""}${r.errors.length ? ` · ${r.errors[0]}` : ""}`);
      }
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <Modal title={files.length === 1 ? `Collections: ${files[0].filename}` : `Collections for ${files.length} files`} onClose={onClose}>
      <p className="mb-3 text-sm text-gray-600 dark:text-gray-300">
        Collections show up in Plex for everyone using that library.
        {episodes && " For TV episodes, Plex adds the whole show."}
      </p>
      {names === null ? <Loader2 className="h-5 w-5 animate-spin text-blue-600" /> : (
        <div className="max-h-64 space-y-1 overflow-y-auto">
          {names.length === 0 && <p className="text-sm text-gray-500">No collections yet. Make one below.</p>}
          {names.map((n) => (
            <label key={n} className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-gray-800 hover:bg-gray-50 dark:text-gray-200 dark:hover:bg-gray-800">
              <input type="checkbox" checked={want[n] === true}
                     ref={(el) => { if (el) el.indeterminate = want[n] === null; }}
                     onChange={() => setWant((w) => ({ ...w, [n]: w[n] !== true }))} />
              <span className="flex-1">{n}</span>
              {files.length > 1 && has(n) > 0 && <span className="text-xs text-gray-400">{has(n)} of {files.length}</span>}
            </label>
          ))}
        </div>
      )}
      <div className="mt-3 flex gap-2">
        <input value={fresh} onChange={(e) => setFresh(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addNew()}
               placeholder="New collection, e.g. Christmas" aria-label="New collection name" className={`${sel} min-w-0 flex-1`} />
        <button className={btnGhost} onClick={addNew} disabled={!fresh.trim()}>Add</button>
      </div>
      {error && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <button className={btnGhost} onClick={onClose}>Cancel</button>
        <button className={btnPrimary} disabled={busy || names === null} onClick={save}>
          {busy && <Loader2 className="h-4 w-4 animate-spin" />}Save
        </button>
      </div>
    </Modal>
  );
}

/** Move files to another folder, keeping their names (e.g. back to Bellesa). */
function MoveDialog({ files, onClose, onDone }: { files: MediaFile[]; onClose: () => void; onDone: (msg: string) => void }) {
  const [folders, setFolders] = useState<DownloadPath[] | null>(null);
  const [target, setTarget] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.paths.list().then((ps) => {
      const usable = ps.filter((p) => p.scan && p.exists_here);
      setFolders(usable);
      setTarget((usable.find((p) => p.protected) ?? usable[0])?.id ?? null);
    }).catch((e) => setError(e.message));
  }, []);
  const go = async () => {
    if (target == null) return;
    setBusy(true); setError(null);
    try {
      const r = await api.media.bulk("move", files.map((f) => f.id), undefined, target);
      if (r.errors.length && !r.done) setError(r.errors[0]);
      else onDone(`Moved ${r.done}${r.errors.length ? ` · ${r.errors.length} couldn't be: ${r.errors[0]}` : ""}`);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <Modal title={files.length === 1 ? `Move ${files[0].filename}` : `Move ${files.length} files`} onClose={onClose}>
      <p className="mb-3 text-sm text-gray-600 dark:text-gray-300">They keep their names. Nothing is overwritten.</p>
      {folders === null ? <Loader2 className="h-5 w-5 animate-spin text-blue-600" /> : (
        <div className="space-y-1.5">
          {folders.map((p) => (
            <label key={p.id} className="flex cursor-pointer items-center gap-2 rounded-lg border border-gray-200 dark:border-gray-700 px-3 py-2 text-sm">
              <input type="radio" name="move-to" checked={target === p.id} onChange={() => setTarget(p.id)} />
              <span className="flex-1 text-gray-800 dark:text-gray-200">{p.name}<span className="block truncate font-mono text-xs text-gray-400">{p.local}</span></span>
              {p.protected && <span className="rounded-full bg-red-100 px-2 py-0.5 text-[10px] font-semibold text-red-700 dark:bg-red-900/40 dark:text-red-300">Protected</span>}
            </label>
          ))}
        </div>
      )}
      {error && <p className="mt-2 text-sm text-red-600 dark:text-red-400">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <button className={btnGhost} onClick={onClose}>Cancel</button>
        <button className={btnPrimary} disabled={busy || target == null} onClick={go}>
          {busy && <Loader2 className="h-4 w-4 animate-spin" />}Move
        </button>
      </div>
    </Modal>
  );
}

/** "Some.Movie.2019.720p.x265.mkv" -> "Some Movie 2019", for a Search link. */
function searchTitle(path: string): string {
  const name = path.split("/").pop()?.replace(/\.[^.]+$/, "") ?? "";
  const words = name.replace(/[._]+/g, " ").replace(/\[[^\]]*\]/g, " ");
  const cut = words.search(/\b(2160p|1080p|720p|480p|4k|x26[45]|h\.?26[45]|hevc|web|bluray|hdtv)\b/i);
  return (cut > 0 ? words.slice(0, cut) : words).replace(/\s+/g, " ").trim();
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
                    <p className="break-all text-gray-600 dark:text-gray-300">{p.already ? "Already in place" : <>→ <span className="font-mono">{p.target}</span></>}
                      {p.genre_confidence === "borderline" && <span className="ml-1 text-amber-700 dark:text-amber-400">(closest genre folder: check it)</span>}
                      {p.genre_confidence === "none" && <span className="ml-1 text-amber-700 dark:text-amber-400">(no genre found)</span>}</p>
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
