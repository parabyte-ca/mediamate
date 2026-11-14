import { useState, useRef, useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowDownUp,
  Clapperboard,
  Clock,
  Compass,
  Download,
  ExternalLink,
  Loader2,
  Plus,
  Search as SearchIcon,
  Music,
  Shield,
  Users,
  X,
} from "lucide-react";
import { api, type BrowseMovie, type BrowseShow, type DownloadPath, type DuplicateCheckResult, type TorrentResult, type WatchlistCreate } from "../api/client";
import MusicSearch from "../components/MusicSearch";
import { AddShowDialog, MovieCard, ShowCard, useAddMovie } from "../components/TitleCards";

// ── Search history ────────────────────────────────────────────────────────────

const HISTORY_KEY = "ar-search-history";
const MAX_HISTORY = 10;

function loadHistory(): string[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === "string") : [];
  } catch { return []; }
}

function pushHistory(q: string, current: string[]): string[] {
  const t = q.trim();
  if (!t) return current;
  const next = [t, ...current.filter((x) => x !== t)].slice(0, MAX_HISTORY);
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(next)); } catch {}
  return next;
}

function removeHistory(q: string, current: string[]): string[] {
  const next = current.filter((x) => x !== q);
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(next)); } catch {}
  return next;
}

const QUALITIES = ["Any", "4K", "1080p", "720p", "480p"];
const CODECS    = ["x265", "x264", "AV1", "Any"];
const INDEXERS  = [
  { value: "all",     label: "All sources"      },
  { value: "nyaa",    label: "NYAA"             },
  { value: "tpb",     label: "The Pirate Bay"   },
  { value: "eztv",    label: "EZTV"             },
  { value: "yts",     label: "YTS"              },
  { value: "tgx",     label: "TorrentGalaxy"    },
  { value: "1337x",   label: "1337x"            },
  { value: "jackett", label: "Jackett"          },
];

const SOURCE_COLORS: Record<string, string> = {
  nyaa:    "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400",
  tpb:     "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-400",
  eztv:    "bg-blue-100   text-blue-700   dark:bg-blue-900/40   dark:text-blue-400",
  yts:     "bg-teal-100   text-teal-700   dark:bg-teal-900/40   dark:text-teal-400",
  tgx:     "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-400",
  "1337x": "bg-red-100    text-red-700    dark:bg-red-900/40    dark:text-red-400",
  jackett: "bg-green-100  text-green-700  dark:bg-green-900/40  dark:text-green-400",
};
const QUALITY_COLORS: Record<string, string> = {
  "4K":     "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-400",
  "1080p":  "bg-blue-100   text-blue-700   dark:bg-blue-900/40   dark:text-blue-400",
  "720p":   "bg-sky-100    text-sky-700    dark:bg-sky-900/40    dark:text-sky-400",
  "480p":   "bg-gray-100   text-gray-700   dark:bg-gray-700      dark:text-gray-300",
};

const INPUT = [
  "rounded-lg border border-gray-300 px-3 py-2.5 text-sm",
  "focus:outline-none focus:ring-2 focus:ring-blue-500",
  "dark:bg-gray-800 dark:border-gray-600 dark:text-gray-100",
].join(" ");

function fmtFree(bytes?: number | null): string | null {
  if (bytes == null) return null;
  if (bytes >= 1e12) return `${(bytes / 1e12).toFixed(1)} TB free`;
  if (bytes >= 1e9)  return `${(bytes / 1e9).toFixed(1)} GB free`;
  return `${(bytes / 1e6).toFixed(0)} MB free`;
}

function sourceLabel(src: string) {
  return src.startsWith("jackett/") ? src.replace("jackett/", "") : src.toUpperCase();
}
function sourceBadgeClass(src: string) {
  const k = src.startsWith("jackett") ? "jackett" : src;
  return SOURCE_COLORS[k] ?? "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300";
}

// ── Download modal ────────────────────────────────────────────────────────────

function DownloadModal({
  result, paths, onClose, onConfirm,
}: {
  result: TorrentResult;
  paths: DownloadPath[];
  onClose: () => void;
  onConfirm: (pathId?: number) => void;
}) {
  const [pathId, setPathId] = useState<number | undefined>(paths.find((p) => p.is_default)?.id);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-xl bg-white dark:bg-gray-900 p-6 shadow-xl border border-gray-200 dark:border-gray-700">
        <h2 className="mb-1 text-lg font-semibold text-gray-900 dark:text-gray-100">Download</h2>
        <p className="mb-4 text-sm text-gray-500 dark:text-gray-400 line-clamp-2">{result.title}</p>

        <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Save to</label>
        {paths.length === 0 ? (
          <p className="mb-4 text-sm text-amber-600 dark:text-amber-400">
            No download paths configured. Add one in Settings first.
          </p>
        ) : (
          <select
            value={pathId ?? ""}
            onChange={(e) => setPathId(e.target.value ? Number(e.target.value) : undefined)}
            className={`mb-4 w-full ${INPUT}`}
          >
            {paths.map((p) => {
              const free = fmtFree(p.free_bytes);
              return (
                <option key={p.id} value={p.id}>
                  {p.name} — {p.path}{free ? ` (${free})` : ""}
                </option>
              );
            })}
          </select>
        )}

        <div className="flex gap-3">
          <button onClick={onClose} className="flex-1 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
            Cancel
          </button>
          <button onClick={() => onConfirm(pathId)} disabled={paths.length === 0} className="flex-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
            Download
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Duplicate warning modal ───────────────────────────────────────────────────

const MATCH_TYPE_LABEL: Record<string, string> = {
  hash:   "Exact match by torrent hash.",
  name:   "Match by torrent name.",
  active: "Currently active in qBittorrent.",
};

function DuplicateWarningModal({ result, dup, onClose, onForce }: {
  result: TorrentResult;
  dup: DuplicateCheckResult;
  onClose: () => void;
  onForce: () => void;
}) {
  const matchedAt = dup.matched_at
    ? ` on ${new Date(dup.matched_at).toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" })}`
    : "";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-xl bg-white dark:bg-gray-900 p-6 shadow-xl border border-gray-200 dark:border-gray-700">
        <h2 className="mb-1 text-lg font-semibold text-gray-900 dark:text-gray-100">Possible duplicate</h2>
        <p className="mb-2 text-sm text-gray-700 dark:text-gray-300">
          <strong>{dup.matched_name ?? result.title}</strong> was already downloaded{matchedAt}. Download again anyway?
        </p>
        <p className="mb-4 text-xs text-gray-400 dark:text-gray-500">
          {dup.match_type ? MATCH_TYPE_LABEL[dup.match_type] : ""}
        </p>
        <div className="flex gap-3">
          <button onClick={onClose} className="flex-1 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
            Cancel
          </button>
          <button onClick={onForce} className="flex-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">
            Download anyway
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Watchlist modal ───────────────────────────────────────────────────────────

function WatchlistModal({
  result, paths, defaultCodec, onClose, onConfirm,
}: {
  result: TorrentResult;
  paths: DownloadPath[];
  defaultCodec: string;
  onClose: () => void;
  onConfirm: (data: WatchlistCreate) => void;
}) {
  const defaultPath = paths.find((p) => p.is_default);
  const [form, setForm] = useState<WatchlistCreate>({
    title:            result.title.replace(/S\d+E\d+.*/i, "").trim(),
    search_query:     result.title.replace(/S\d+E\d+.*/i, "").trim(),
    quality:          result.quality && result.quality !== "Unknown" ? result.quality : "1080p",
    codec:            defaultCodec !== "Any" ? defaultCodec : "x265",
    season:           1,
    episode:          1,
    download_path_id: defaultPath?.id,
    enabled:          true,
  });
  const set = (k: keyof WatchlistCreate, v: unknown) => setForm((f) => ({ ...f, [k]: v }));

  const FIELD = "w-full rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:text-gray-100";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-xl bg-white dark:bg-gray-900 p-6 shadow-xl border border-gray-200 dark:border-gray-700">
        <h2 className="mb-4 text-lg font-semibold text-gray-900 dark:text-gray-100">Add to Watchlist</h2>

        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Show title</label>
            <input value={form.title} onChange={(e) => set("title", e.target.value)} className={FIELD} />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              Search query <span className="text-xs font-normal text-gray-400">(what to search for)</span>
            </label>
            <input value={form.search_query} onChange={(e) => set("search_query", e.target.value)} className={FIELD} />
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Season</label>
              <input type="number" min={1} value={form.season} onChange={(e) => set("season", Number(e.target.value))} className={FIELD} />
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Next episode</label>
              <input type="number" min={1} value={form.episode} onChange={(e) => set("episode", Number(e.target.value))} className={FIELD} />
            </div>
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Quality</label>
              <select value={form.quality} onChange={(e) => set("quality", e.target.value)} className={FIELD}>
                {QUALITIES.filter((q) => q !== "Any").map((q) => <option key={q}>{q}</option>)}
              </select>
            </div>
            <div className="flex-1">
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Codec</label>
              <select value={form.codec} onChange={(e) => set("codec", e.target.value)} className={FIELD}>
                {CODECS.map((c) => <option key={c}>{c}</option>)}
              </select>
            </div>
          </div>
          {paths.length > 0 && (
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Save to</label>
              <select value={form.download_path_id ?? ""} onChange={(e) => set("download_path_id", e.target.value ? Number(e.target.value) : undefined)} className={FIELD}>
                <option value="">Use default</option>
                {paths.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </div>
          )}
        </div>

        <div className="mt-5 flex gap-3">
          <button onClick={onClose} className="flex-1 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
            Cancel
          </button>
          <button onClick={() => onConfirm(form)} className="flex-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">
            Add to Watchlist
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

const TYPE_KEY = "mm-search-type";

/** Search finds something by name ("I know what I want"); Browse is for discovering.
 *  Two tabs: Shows & movies (matching titles to add, then downloads) and Music. */
export default function Search() {
  const [params, setParams] = useSearchParams();
  const type = params.get("type") === "music" ? "music" : "video";
  // Remember the tab; a link with a query (e.g. "Find now" on a movie) decides for itself.
  useEffect(() => {
    if (params.get("type") || params.get("q")) return;
    let saved: string | null = null;
    try { saved = localStorage.getItem(TYPE_KEY); } catch { /* private mode */ }
    if (saved === "music") setParams({ type: "music" }, { replace: true });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!params.get("type") && !params.get("q") && type === "video") return;   // bare /search: keep the saved tab
    try { localStorage.setItem(TYPE_KEY, type); } catch { /* private mode */ }
  }, [type]); // eslint-disable-line react-hooks/exhaustive-deps
  const setType = (t: "video" | "music") => {
    try { localStorage.setItem(TYPE_KEY, t); } catch { /* private mode */ }
    setParams(t === "music" ? { type: "music" } : {}, { replace: true });
  };
  return (
    <div className="px-4 py-6 sm:px-6">
      <div className="mb-5 flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Search</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            {type === "music" ? "Find an artist or album: play what you have, follow artists and add albums."
              : "Find a show or movie by name: add it to your watchlist, or grab a download now."}
          </p>
        </div>
        <div className="inline-flex rounded-lg border border-gray-200 p-0.5 dark:border-gray-700" role="tablist" aria-label="Search">
          {([["video", "Shows & movies", Clapperboard], ["music", "Music", Music]] as const).map(([id, label, Icon]) => (
            <button key={id} role="tab" aria-selected={type === id} onClick={() => setType(id)}
              className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm ${type === id ? "bg-blue-600 text-white" : "text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"}`}>
              <Icon className="h-4 w-4" />{label}
            </button>
          ))}
        </div>
      </div>
      {type === "music" ? <MusicSearch /> : <TorrentSearch />}
    </div>
  );
}

function TorrentSearch() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialQ = searchParams.get("q") ?? "";

  const [query,         setQuery]         = useState(initialQ);
  const [quality,       setQuality]       = useState("Any");
  const [codec,         setCodec]         = useState("x265");
  const [indexer,       setIndexer]       = useState("all");
  const [filterAdult,   setFilterAdult]   = useState(true);
  const [minSeeds,      setMinSeeds]      = useState(0);
  const [sortBy,        setSortBy]        = useState<"seeds" | "size-asc" | "size-desc">("seeds");
  const [results,       setResults]       = useState<TorrentResult[]>([]);
  const [loading,       setLoading]       = useState(false);
  const [error,         setError]         = useState<string | null>(null);
  const [paths,         setPaths]         = useState<DownloadPath[]>([]);
  const [dlTarget,      setDlTarget]      = useState<TorrentResult | null>(null);
  const [dlForce,       setDlForce]       = useState(false);
  const [checkingDl,    setCheckingDl]    = useState<TorrentResult | null>(null);
  const [dupWarning,    setDupWarning]    = useState<{ result: TorrentResult; dup: DuplicateCheckResult } | null>(null);
  const [wlTarget,      setWlTarget]      = useState<TorrentResult | null>(null);
  const [toast,         setToast]         = useState<string | null>(null);
  const [searchHistory, setSearchHistory] = useState<string[]>(loadHistory);
  const [showHistory,   setShowHistory]   = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const [titles,        setTitles]        = useState<{ q: string; shows: BrowseShow[]; movies: BrowseMovie[] } | null>(null);
  const [showTarget,    setShowTarget]    = useState<BrowseShow | null>(null);
  const titleReq = useRef(0);
  const movieAdd = useAddMovie((tmdbId, wid) => setTitles((t) => t && {
    ...t, movies: t.movies.map((m) => m.tmdb_id === tmdbId ? { ...m, on_watchlist: true, watchlist_id: wid } : m) }));

  /** Matching shows (TVMaze) and movies (TMDB, when there's a key), shown above the downloads. */
  const loadTitles = (q: string) => {
    const id = ++titleReq.current;
    const bare = q.replace(/\bS\d{1,2}(E\d{1,3})?\b.*$/i, "").trim() || q;   // "Show S02E03" → "Show"
    if (bare.length < 2) { setTitles(null); return; }
    Promise.allSettled([api.browse.get(bare), api.browse.movies("popular", bare)]).then(([s, m]) => {
      if (id !== titleReq.current) return;
      setTitles({
        q: bare,
        shows: s.status === "fulfilled" ? s.value.shows.slice(0, 4) : [],
        movies: m.status === "fulfilled" && !m.value.need_key ? m.value.movies.slice(0, 4) : [],
      });
    });
  };

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 3000); };

  const doSearch = async (overrideQ?: string) => {
    const q = (overrideQ ?? query).trim();
    if (!q) return;
    setLoading(true);
    setError(null);
    setShowHistory(false);
    loadTitles(q);
    try {
      const [res, p] = await Promise.all([
        api.search(q, indexer, quality, codec, filterAdult),
        api.paths.list(),
      ]);
      setResults(res);
      setPaths(p.filter((x) => x.downloads !== false));  // only folders downloads can go to
      setSearchHistory((prev) => pushHistory(q, prev));
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  // Auto-search when arriving via the "Re-search" link from Downloads
  const hasAutoSearched = useRef(false);
  useEffect(() => {
    if (initialQ && !hasAutoSearched.current) {
      hasAutoSearched.current = true;
      setSearchParams({}, { replace: true });
      doSearch(initialQ);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleClickDownload = async (r: TorrentResult) => {
    setCheckingDl(r);
    try {
      const dup = await api.downloads.checkDuplicate({ torrent_hash: r.info_hash ?? null, torrent_name: r.title });
      if (dup.is_duplicate) {
        setDupWarning({ result: r, dup });
      } else {
        setDlForce(false);
        setDlTarget(r);
      }
    } catch {
      // Check failed — proceed as if no duplicate found
      setDlForce(false);
      setDlTarget(r);
    } finally {
      setCheckingDl(null);
    }
  };

  const handleDownload = async (pathId?: number) => {
    if (!dlTarget) return;
    try {
      await api.downloads.add({ magnet: dlTarget.magnet, title: dlTarget.title, download_path_id: pathId, indexer: dlTarget.source, force: dlForce });
      showToast("Added to qBittorrent!");
    } catch (e: unknown) {
      showToast(`Error: ${(e as Error).message}`);
    }
    setDlTarget(null);
    setDlForce(false);
  };

  const handleWatchlist = async (data: WatchlistCreate) => {
    try {
      await api.watchlist.create(data);
      showToast(`"${data.title}" added to watchlist!`);
    } catch (e: unknown) {
      showToast(`Error: ${(e as Error).message}`);
    }
    setWlTarget(null);
  };

  const CTRL = "rounded-lg border border-gray-300 dark:border-gray-600 px-2.5 py-1.5 text-sm dark:bg-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500";

  // Apply client-side filter and sort after results arrive.
  const displayResults = [...results]
    .filter((r) => r.seeds >= minSeeds)
    .sort((a, b) => {
      if (sortBy === "size-asc")  return a.size_bytes - b.size_bytes;
      if (sortBy === "size-desc") return b.size_bytes - a.size_bytes;
      return b.seeds - a.seeds;
    });

  const hasTitles = !!titles && (titles.shows.length > 0 || titles.movies.length > 0);

  return (
    <div>

      {/* Search bar */}
      <div className="mb-3 flex flex-col gap-3 sm:flex-row">
        <div className="relative flex-1">
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && doSearch()}
            onFocus={() => setShowHistory(true)}
            onBlur={() => setTimeout(() => setShowHistory(false), 150)}
            placeholder="Search for a show, movie, or episode…"
            className="w-full rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2.5 pr-9 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:text-gray-100 dark:placeholder-gray-500"
          />
          {query && (
            <button
              tabIndex={-1}
              onClick={() => { setQuery(""); inputRef.current?.focus(); }}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
              title="Clear"
            >
              <X className="h-4 w-4" />
            </button>
          )}
          {/* Search history dropdown */}
          {showHistory && searchHistory.length > 0 && (
            <div className="absolute left-0 right-0 top-full z-20 mt-1 overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-lg">
              {searchHistory.map((item) => (
                <div
                  key={item}
                  onMouseDown={(e) => { e.preventDefault(); setQuery(item); doSearch(item); }}
                  className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm hover:bg-gray-50 dark:hover:bg-gray-800"
                >
                  <Clock className="h-3.5 w-3.5 shrink-0 text-gray-400" />
                  <span className="flex-1 truncate text-gray-700 dark:text-gray-300">{item}</span>
                  <button
                    tabIndex={-1}
                    onMouseDown={(e) => {
                      e.stopPropagation();
                      e.preventDefault();
                      setSearchHistory((prev) => removeHistory(item, prev));
                    }}
                    className="rounded p-0.5 text-gray-300 hover:text-gray-500 dark:text-gray-600 dark:hover:text-gray-400"
                    title="Remove from history"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
        <button
          onClick={() => doSearch()}
          disabled={loading}
          className="flex items-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <SearchIcon className="h-4 w-4" />}
          Search
        </button>
      </div>

      {/* Filters row */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1.5">
          <label className="text-xs font-medium text-gray-500 dark:text-gray-400">Quality</label>
          <select value={quality} onChange={(e) => setQuality(e.target.value)} className={CTRL}>
            {QUALITIES.map((q) => <option key={q}>{q}</option>)}
          </select>
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs font-medium text-gray-500 dark:text-gray-400">Codec</label>
          <select value={codec} onChange={(e) => setCodec(e.target.value)} className={CTRL}>
            {CODECS.map((c) => <option key={c}>{c}</option>)}
          </select>
        </div>
        <div className="flex items-center gap-1.5">
          <label className="text-xs font-medium text-gray-500 dark:text-gray-400">Source</label>
          <select value={indexer} onChange={(e) => setIndexer(e.target.value)} className={CTRL}>
            {INDEXERS.map((i) => <option key={i.value} value={i.value}>{i.label}</option>)}
          </select>
        </div>

        <div className="flex items-center gap-1.5">
          <label className="text-xs font-medium text-gray-500 dark:text-gray-400">Min seeds</label>
          <input
            type="number"
            min={0}
            value={minSeeds}
            onChange={(e) => setMinSeeds(Math.max(0, Number(e.target.value)))}
            className={`w-16 ${CTRL}`}
          />
        </div>
        <div className="flex items-center gap-1.5">
          <ArrowDownUp className="h-3.5 w-3.5 text-gray-400" />
          <select value={sortBy} onChange={(e) => setSortBy(e.target.value as typeof sortBy)} className={CTRL}>
            <option value="seeds">Seeds ↓</option>
            <option value="size-asc">Size ↑</option>
            <option value="size-desc">Size ↓</option>
          </select>
        </div>

        <button
          onClick={() => setFilterAdult((v) => !v)}
          title={filterAdult ? "Adult content is filtered — click to disable" : "Adult content is visible — click to filter"}
          className={`ml-auto flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
            filterAdult
              ? "border-green-300 bg-green-50 text-green-700 hover:bg-green-100 dark:border-green-800 dark:bg-green-950 dark:text-green-400"
              : "border-red-300 bg-red-50 text-red-600 hover:bg-red-100 dark:border-red-800 dark:bg-red-950 dark:text-red-400"
          }`}
        >
          <Shield className="h-3.5 w-3.5" />
          {filterAdult ? "Adult filter: ON" : "Adult filter: OFF"}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-200 dark:border-red-900 bg-red-50 dark:bg-red-950 px-4 py-3 text-sm text-red-700 dark:text-red-400">
          <AlertCircle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      {/* Titles: add to the watchlist */}
      {hasTitles && (
        <section className="mb-6" aria-label="Titles">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">Titles</h2>
          {movieAdd.error && <p role="alert" className="mb-2 text-sm text-red-600 dark:text-red-400">{movieAdd.error}</p>}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {titles!.shows.map((s) => <ShowCard key={`s${s.tvmaze_id}`} show={s} onAdd={setShowTarget} />)}
            {titles!.movies.map((m) => (
              <MovieCard key={`m${m.tmdb_id}`} movie={m} adding={movieAdd.adding === m.tmdb_id} onAdd={movieAdd.add} />
            ))}
          </div>
        </section>
      )}

      {/* Results */}
      {hasTitles && results.length > 0 && (
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">Downloads</h2>
      )}
      {results.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm">
          <div className="border-b border-gray-100 dark:border-gray-800 px-4 py-3 text-sm text-gray-500 dark:text-gray-400 flex items-center gap-2">
            <span>{displayResults.length} result{displayResults.length !== 1 ? "s" : ""}</span>
            {results.length !== displayResults.length && (
              <span className="text-xs text-gray-400 dark:text-gray-600">
                ({results.length - displayResults.length} filtered by min seeds)
              </span>
            )}
          </div>
          <ul className="divide-y divide-gray-100 dark:divide-gray-800">
            {displayResults.map((r, i) => (
              <li key={r.info_hash ?? i} className="flex flex-col gap-2 px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-800 sm:flex-row sm:items-center">
                <div className="flex-1 min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5 mb-1">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${sourceBadgeClass(r.source)}`}>
                      {sourceLabel(r.source)}
                    </span>
                    {r.quality && r.quality !== "Unknown" && (
                      <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${QUALITY_COLORS[r.quality] ?? "bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300"}`}>
                        {r.quality}
                      </span>
                    )}
                  </div>
                  <p className="text-sm font-medium text-gray-900 dark:text-gray-100 line-clamp-1">{r.title}</p>
                  <div className="mt-0.5 flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400">
                    <span className="flex items-center gap-1">
                      <Users className="h-3 w-3" />
                      <span className="text-green-600 dark:text-green-400 font-medium">{r.seeds}</span> seeds
                    </span>
                    <span>{r.size}</span>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {r.url && (
                    <a href={r.url} target="_blank" rel="noopener noreferrer"
                      className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 hover:text-gray-600 dark:hover:text-gray-300"
                      title="View on site">
                      <ExternalLink className="h-4 w-4" />
                    </a>
                  )}
                  <button onClick={() => setWlTarget(r)}
                    className="flex min-h-[44px] items-center gap-1.5 rounded-lg border border-gray-300 dark:border-gray-600 px-3 py-1.5 text-xs font-medium text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700">
                    <Plus className="h-3.5 w-3.5" />
                    Watchlist
                  </button>
                  <button
                    onClick={() => handleClickDownload(r)}
                    disabled={checkingDl !== null}
                    className="flex min-h-[44px] items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-60"
                  >
                    {checkingDl === r ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                    Download
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {!loading && displayResults.length === 0 && results.length > 0 && !error && (
        <div className="py-16 text-center text-gray-400 dark:text-gray-600">
          <SearchIcon className="mx-auto mb-3 h-10 w-10 opacity-30" />
          <p>All {results.length} result{results.length !== 1 ? "s" : ""} filtered out. Try lowering the Min seeds threshold.</p>
        </div>
      )}
      {!loading && results.length === 0 && query && !error && (
        <div className="py-16 text-center text-gray-400 dark:text-gray-600">
          <SearchIcon className="mx-auto mb-3 h-10 w-10 opacity-30" />
          <p>No downloads found. Try a different query or source.</p>
        </div>
      )}
      {!query && (
        <div className="py-16 text-center text-gray-400 dark:text-gray-600">
          <SearchIcon className="mx-auto mb-3 h-10 w-10 opacity-30" />
          <p className="text-sm">Type a show or movie and press Search.</p>
          <Link to="/browse" className="mt-3 inline-flex items-center gap-1.5 text-sm text-blue-600 hover:underline dark:text-blue-400">
            <Compass className="h-4 w-4" />Not sure what you want? Browse what's airing
          </Link>
        </div>
      )}

      {dlTarget && (
        <DownloadModal result={dlTarget} paths={paths} onClose={() => setDlTarget(null)} onConfirm={handleDownload} />
      )}
      {dupWarning && (
        <DuplicateWarningModal
          result={dupWarning.result}
          dup={dupWarning.dup}
          onClose={() => setDupWarning(null)}
          onForce={() => {
            setDlForce(true);
            setDlTarget(dupWarning.result);
            setDupWarning(null);
          }}
        />
      )}
      {showTarget && (
        <AddShowDialog show={showTarget} onClose={() => setShowTarget(null)}
          onAdded={() => {
            const name = showTarget.name;
            setShowTarget(null);
            setTitles((t) => t && { ...t, shows: t.shows.map((s) => s.tvmaze_id === showTarget.tvmaze_id ? { ...s, on_watchlist: true } : s) });
            showToast(`Added ${name}`);
          }} />
      )}
      {wlTarget && (
        <WatchlistModal result={wlTarget} paths={paths} defaultCodec={codec} onClose={() => setWlTarget(null)} onConfirm={handleWatchlist} />
      )}

      {toast && (
        <div className="fixed bottom-20 right-4 z-50 rounded-lg bg-gray-900 dark:bg-gray-700 px-4 py-3 text-sm text-white shadow-lg md:bottom-6 md:right-6">
          {toast}
        </div>
      )}
    </div>
  );
}
