import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertCircle, CheckCircle2, Clock, Compass, Disc3, Loader2, Plus, Search as SearchIcon, UserRound, X } from "lucide-react";
import { api, type BrowseRelease, type DownloadPath, type MusicArtist, type MusicSearchResult } from "../api/client";
import { AddArtistDialog } from "./MusicWatch";
import { PlayAlbumButton } from "./MusicPlayer";
import { ReleaseCard } from "./TitleCards";
import { fmtDay } from "../utils";

const HISTORY_KEY = "mm-music-search-history";

function loadHistory(): string[] {
  try {
    const arr = JSON.parse(localStorage.getItem(HISTORY_KEY) || "[]");
    return Array.isArray(arr) ? arr.filter((x): x is string => typeof x === "string") : [];
  } catch { return []; }
}
function saveHistory(list: string[]) {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(list)); } catch { /* private mode */ }
}

const H2 = "mb-2 text-sm font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400";

/** Search → Music: find an artist or album by name. Shows what's already in your
 *  library (play it), artists to follow, and albums to add. The query lives in the
 *  URL (?type=music&q=…), so Back and "More by this artist" links work. */
export default function MusicSearch() {
  const [params, setParams] = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const [input, setInput] = useState(q);
  const [data, setData] = useState<MusicSearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<string[]>(loadHistory);
  const [showHistory, setShowHistory] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [following, setFollowing] = useState<MusicArtist | null>(null);
  const [paths, setPaths] = useState<DownloadPath[]>([]);
  const reqId = useRef(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const run = async (query: string, quiet = false) => {
    const id = ++reqId.current;
    if (!quiet) { setLoading(true); setData(null); }
    setError(null);
    try {
      const r = await api.searchMusic(query);
      if (id === reqId.current) setData(r);
    } catch (e) {
      if (id === reqId.current) setError(e instanceof Error ? e.message : "Search failed.");
    } finally {
      if (id === reqId.current) setLoading(false);
    }
  };

  useEffect(() => {
    setInput(q);
    if (q.length >= 2) {
      run(q);
      setHistory((h) => { const next = [q, ...h.filter((x) => x !== q)].slice(0, 10); saveHistory(next); return next; });
    } else {
      setData(null);
    }
  }, [q]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(null), 6000); return () => clearTimeout(t); }, [toast]);

  const submit = (value = input) => {
    const v = value.trim();
    setShowHistory(false);
    if (v.length < 2) { setError("Type at least 2 letters."); return; }
    if (v === q) run(v); else setParams({ type: "music", q: v });
  };

  const addAlbum = async (r: BrowseRelease) => {
    setBusy(r.mbid);
    try {
      const res = await api.browse.musicAdd(r);
      setToast(res.message || "Added");
      await run(q, true);
    } catch (e) { setToast((e as Error).message); } finally { setBusy(null); }
  };

  const openFollow = async (a: MusicArtist) => {
    setFollowing(a);
    try { setPaths((await api.paths.list()).filter((p) => p.downloads !== false)); } catch { setPaths([]); }
  };

  const nothing = data && !data.library.length && !data.artists.length && !data.albums.length;

  return (
    <div>
      <div className="mb-4 flex flex-col gap-3 sm:flex-row">
        <form className="relative flex-1" onSubmit={(e) => { e.preventDefault(); submit(); }}>
          <input ref={inputRef} value={input} onChange={(e) => setInput(e.target.value)}
                 onFocus={() => setShowHistory(true)} onBlur={() => setTimeout(() => setShowHistory(false), 150)}
                 placeholder="Search for an artist or album…" aria-label="Search music"
                 className="w-full rounded-lg border border-gray-300 px-4 py-2.5 pr-9 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100 dark:placeholder-gray-500" />
          {input && (
            <button type="button" tabIndex={-1} title="Clear" onClick={() => { setInput(""); inputRef.current?.focus(); }}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200">
              <X className="h-4 w-4" />
            </button>
          )}
          {showHistory && history.length > 0 && (
            <div className="absolute left-0 right-0 top-full z-20 mt-1 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-lg dark:border-gray-700 dark:bg-gray-900">
              {history.map((item) => (
                <div key={item} onMouseDown={(e) => { e.preventDefault(); setInput(item); submit(item); }}
                     className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm hover:bg-gray-50 dark:hover:bg-gray-800">
                  <Clock className="h-3.5 w-3.5 shrink-0 text-gray-400" />
                  <span className="flex-1 truncate text-gray-700 dark:text-gray-300">{item}</span>
                  <button tabIndex={-1} title="Remove from history"
                          onMouseDown={(e) => { e.stopPropagation(); e.preventDefault(); setHistory((h) => { const n = h.filter((x) => x !== item); saveHistory(n); return n; }); }}
                          className="rounded p-0.5 text-gray-300 hover:text-gray-500 dark:text-gray-600 dark:hover:text-gray-400">
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </form>
        <button onClick={() => submit()} disabled={loading}
                className="flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60">
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <SearchIcon className="h-4 w-4" />}
          Search
        </button>
      </div>

      {error && (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          <AlertCircle className="h-4 w-4 shrink-0" />{error}
        </div>
      )}
      {loading && <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-blue-600" /></div>}
      {data?.mb_error && (
        <p className="mb-4 rounded-lg bg-amber-50 px-4 py-2 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
          {data.mb_error}. Showing what's in your library.
        </p>
      )}

      {data && data.library.length > 0 && (
        <section className="mb-6" aria-label="In your library">
          <h2 className={H2}>In your library</h2>
          <ul className="divide-y divide-gray-100 overflow-hidden rounded-xl border border-gray-200 bg-white dark:divide-gray-800 dark:border-gray-700 dark:bg-gray-900">
            {data.library.map((a) => (
              <li key={a.id} className="flex min-w-0 items-center gap-3 px-3 py-2">
                {a.has_cover
                  ? <img src={api.music.coverUrl(a.id, a.scanned_at, 96)} alt="" loading="lazy" decoding="async" className="h-10 w-10 shrink-0 rounded object-cover" />
                  : <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded bg-gray-100 dark:bg-gray-800"><Disc3 className="h-5 w-5 text-gray-400" /></span>}
                <Link to={`/library?lib=music&album=${a.id}`} className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-gray-900 dark:text-gray-100">{a.title || "Untitled"}</span>
                  <span className="block truncate text-xs text-gray-500 dark:text-gray-400">
                    {[a.artist, a.year, a.format?.toUpperCase(), `${a.tracks} tracks`].filter(Boolean).join(" · ")}
                  </span>
                </Link>
                <PlayAlbumButton albumId={a.id} label={a.title || "album"} className="h-9 w-9 shrink-0" />
              </li>
            ))}
          </ul>
        </section>
      )}

      {data && data.artists.length > 0 && (
        <section className="mb-6" aria-label="Artists">
          <h2 className={H2}>Artists</h2>
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {data.artists.map((a) => (
              <li key={a.mbid} className="flex min-w-0 items-center gap-3 rounded-xl border border-gray-200 bg-white px-3 py-2 dark:border-gray-700 dark:bg-gray-900">
                <UserRound className="h-5 w-5 shrink-0 text-gray-400" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-gray-900 dark:text-gray-100">{a.name}</span>
                  <span className="block truncate text-xs text-gray-500 dark:text-gray-400">
                    {[a.disambiguation, a.type, a.country, a.begin && `since ${a.begin}`].filter(Boolean).join(" · ") || " "}
                  </span>
                </span>
                {a.following ? (
                  <Link to="/watchlist?tab=music" className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-green-600 dark:text-green-400">
                    <CheckCircle2 className="h-3.5 w-3.5" />Following
                  </Link>
                ) : (
                  <button onClick={() => openFollow(a)}
                          className="inline-flex min-h-[36px] shrink-0 items-center gap-1 rounded-lg bg-blue-600 px-3 text-xs font-medium text-white hover:bg-blue-700">
                    <Plus className="h-3.5 w-3.5" />Follow
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {data && data.albums.length > 0 && (
        <section className="mb-6" aria-label="Albums">
          <h2 className={H2}>Albums</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {data.albums.map((r) => (
              <ReleaseCard key={r.mbid} release={r} busy={busy === r.mbid} disabled={busy !== null} onAdd={addAlbum}
                moreByArtist={r.artist.toLowerCase() !== q.toLowerCase()}
                extra={<p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                  {[r.date ? (r.date.length > 4 ? fmtDay(r.date) : r.date) : null, r.type].filter(Boolean).join(" · ")}
                </p>} />
            ))}
          </div>
          <p className="mt-3 text-xs text-gray-400 dark:text-gray-600">Artist and album data from MusicBrainz.</p>
        </section>
      )}

      {nothing && !loading && (
        <div className="py-12 text-center text-gray-400 dark:text-gray-600">
          <SearchIcon className="mx-auto mb-3 h-10 w-10 opacity-30" />
          <p className="text-sm">Nothing found for "{q}".</p>
        </div>
      )}
      {!q && !loading && (
        <div className="py-12 text-center text-gray-400 dark:text-gray-600">
          <SearchIcon className="mx-auto mb-3 h-10 w-10 opacity-30" />
          <p className="text-sm">Type an artist or album and press Search.</p>
          <Link to="/browse?type=music" className="mt-3 inline-flex items-center gap-1.5 text-sm text-blue-600 hover:underline dark:text-blue-400">
            <Compass className="h-4 w-4" />Not sure what you want? Browse new releases
          </Link>
        </div>
      )}

      {following && (
        <AddArtistDialog preset={following} paths={paths} onClose={() => setFollowing(null)}
          onAdded={(name) => { setFollowing(null); setToast(`Following ${name}`); run(q, true); }} />
      )}
      {toast && (
        <div role="status" onClick={() => setToast(null)}
             className="fixed bottom-20 right-4 z-50 max-w-sm rounded-lg bg-gray-900 px-4 py-3 text-sm text-white shadow-lg dark:bg-gray-700 md:bottom-6 md:right-6">
          {toast}
        </div>
      )}
    </div>
  );
}
