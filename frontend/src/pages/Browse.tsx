import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { CheckCircle2, Clapperboard, Disc3, Download, Loader2, Music, Plus, RefreshCw, Search, Sparkles, Star, Tv, X } from "lucide-react";
import { api, type BrowseMovie, type BrowseRelease, type BrowseShow, type MovieList } from "../api/client";
import Poster from "../components/Poster";
import { fmtDate, fmtDay } from "../utils";

function AddDialog({ show, onClose, onAdded }: {
  show: BrowseShow;
  onClose: () => void;
  onAdded: () => void;
}) {
  const running = show.status === "Running";
  const [start, setStart] = useState<"next" | "beginning">(running ? "next" : "beginning");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const add = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.watchlist.addFromTvmaze({ tvmaze_id: show.tvmaze_id, start });
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not add the show.");
      setSaving(false);
    }
  };

  const option = (value: "next" | "beginning", title: string, hint: string) => (
    <label className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 ${start === value
      ? "border-blue-500 bg-blue-50 dark:bg-blue-950/30"
      : "border-gray-200 dark:border-gray-700"}`}>
      <input type="radio" name="start" checked={start === value} onChange={() => setStart(value)} className="mt-0.5" />
      <div>
        <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{title}</span>
        <p className="text-xs text-gray-500 dark:text-gray-400">{hint}</p>
      </div>
    </label>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-6 shadow-xl">
        <div className="mb-4 flex gap-3">
          <Poster url={show.poster_url} title={show.name} className="h-24 w-16" />
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">{show.name}</h2>
            <p className="text-xs text-gray-500 dark:text-gray-400">
              {[show.network, show.status].filter(Boolean).join(" · ")}
            </p>
          </div>
        </div>
        <p className="mb-2 text-sm font-medium text-gray-700 dark:text-gray-300">Where should MediaMate start?</p>
        <div className="space-y-2">
          {option("next", "Next new episode", "Only download episodes from now on.")}
          {option("beginning", "From the beginning", "Download every episode, starting with season 1.")}
        </div>
        <p className="mt-3 text-xs text-gray-400 dark:text-gray-500">
          Uses your default quality profile if you've set one — change anything later from the Watchlist.
        </p>
        {error && <p className="mt-3 text-sm text-red-600 dark:text-red-400">{error}</p>}
        <div className="mt-5 flex gap-3">
          <button onClick={onClose} className="flex-1 rounded-lg border border-gray-300 dark:border-gray-600 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
            Cancel
          </button>
          <button onClick={add} disabled={saving} className="flex-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
            {saving ? "Adding…" : "Add to watchlist"}
          </button>
        </div>
      </div>
    </div>
  );
}

const MOVIE_LISTS: [MovieList, string][] = [["popular", "Popular"], ["now_playing", "In theatres"], ["upcoming", "Coming soon"]];

/** Movies from TMDB: popular, in theatres, coming soon, or a search. "Find downloads" opens Search. */
function MovieBrowse() {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [list, setList] = useState<MovieList>("popular");
  const [movies, setMovies] = useState<BrowseMovie[] | null>(null);
  const [needKey, setNeedKey] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reqId = useRef(0);
  const navigate = useNavigate();

  useEffect(() => {
    const t = setTimeout(() => setQuery(input.trim()), 400);
    return () => clearTimeout(t);
  }, [input]);

  useEffect(() => {
    const id = ++reqId.current;
    setMovies(null);
    setError(null);
    api.browse.movies(list, query)
      .then((r) => { if (id === reqId.current) { setNeedKey(r.need_key); setMovies(r.movies); } })
      .catch((e) => { if (id === reqId.current) { setError(e instanceof Error ? e.message : "Could not load movies."); setMovies([]); } });
  }, [list, query]);

  const find = (m: BrowseMovie) => navigate(`/search?q=${encodeURIComponent(`${m.title}${m.year ? ` ${m.year}` : ""}`)}`);
  const [adding, setAdding] = useState<number | null>(null);
  const [addError, setAddError] = useState<string | null>(null);
  const add = async (m: BrowseMovie) => {
    setAdding(m.tmdb_id);
    setAddError(null);
    try {
      const w = await api.watchlist.addFromTmdb({ tmdb_id: m.tmdb_id });
      setMovies((ms) => ms && ms.map((x) => x.tmdb_id === m.tmdb_id ? { ...x, on_watchlist: true, watchlist_id: w.id } : x));
    } catch (e) {
      setAddError(e instanceof Error ? e.message : "Couldn't add the movie.");
    } finally {
      setAdding(null);
    }
  };
  // Coming soon and in cinemas: nothing good to download yet, so only offer the watchlist,
  // which waits for the digital release. Older movies can be searched for right away too.
  const today = new Date().toISOString().slice(0, 10);
  const canFindNow = (m: BrowseMovie) =>
    list !== "upcoming" && list !== "now_playing" && !!m.release_date && m.release_date <= today;

  if (needKey) {
    return (
      <div className="max-w-xl rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-5 text-sm text-gray-600 dark:text-gray-300">
        <p className="mb-2 font-medium text-gray-900 dark:text-gray-100">Movies come from TMDB</p>
        <p className="mb-3">Add your free TMDB API key in Settings → Integrations to browse popular, in-theatre and upcoming movies.
          (It also gives movie posters and age ratings for the Mature folders.)</p>
        <Link to="/settings" className="inline-flex min-h-[36px] items-center rounded-lg bg-blue-600 px-3 text-sm font-medium text-white hover:bg-blue-700">Open Settings</Link>
      </div>
    );
  }

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative w-full max-w-xl">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Search for any movie…"
            className="w-full rounded-lg border border-gray-300 dark:border-gray-600 py-2.5 pl-9 pr-9 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:text-gray-100" />
          {input && (
            <button onClick={() => setInput("")} tabIndex={-1} aria-label="Clear search"
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-400 hover:text-gray-600"><X className="h-4 w-4" /></button>
          )}
        </div>
        {!query && (
          <div className="inline-flex rounded-lg border border-gray-200 dark:border-gray-700 p-0.5" role="radiogroup" aria-label="Movie list">
            {MOVIE_LISTS.map(([id, label]) => (
              <button key={id} role="radio" aria-checked={list === id} onClick={() => setList(id)}
                className={`rounded-md px-3 py-1.5 text-sm ${list === id ? "bg-blue-600 text-white" : "text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"}`}>
                {label}
              </button>
            ))}
          </div>
        )}
      </div>

      {error && <p className="mb-4 text-sm text-red-600 dark:text-red-400">{error}</p>}
      {movies === null && !error && <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-blue-600" /></div>}
      {movies !== null && movies.length === 0 && !error && (
        <p className="py-12 text-center text-sm text-gray-400 dark:text-gray-600">
          {query.length === 1 ? "Keep typing…" : query ? `No movies found for "${query}".` : "Nothing to show right now."}
        </p>
      )}

      {addError && <p role="alert" className="mb-3 text-sm text-red-600 dark:text-red-400">{addError}</p>}
      {list === "upcoming" && !query && movies !== null && movies.length > 0 && (
        <p className="mb-3 text-xs text-gray-500 dark:text-gray-400">
          Add one to your watchlist and MediaMate downloads it when it's released digitally.
        </p>
      )}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {(movies ?? []).map((m) => (
          <div key={m.tmdb_id} className="flex gap-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-3 shadow-sm">
            <Poster url={m.poster_url} title={m.title} className="h-32 w-[5.5rem]" />
            <div className="flex min-w-0 flex-1 flex-col">
              <div className="flex items-start justify-between gap-2">
                <h3 className="font-semibold leading-tight text-gray-900 dark:text-gray-100">{m.title}</h3>
                {m.rating != null && m.votes > 0 && (
                  <span className="flex shrink-0 items-center gap-0.5 text-xs text-gray-500 dark:text-gray-400">
                    <Star className="h-3 w-3 fill-yellow-400 text-yellow-400" />{m.rating.toFixed(1)}
                  </span>
                )}
              </div>
              <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
                {m.release_date && <span>{list === "popular" || query ? m.year : fmtDay(m.release_date)}</span>}
                {m.certification && <span className="rounded border border-gray-300 dark:border-gray-600 px-1 text-[10px] font-medium">{m.certification}</span>}
              </p>
              {m.overview && <p className="mt-1 line-clamp-2 text-xs text-gray-400 dark:text-gray-500">{m.overview}</p>}
              <div className="mt-auto flex flex-wrap items-center gap-2 pt-2">
                {m.in_library && (
                  <Link to={`/library?q=${encodeURIComponent(m.title)}`} className="inline-flex items-center gap-1 text-xs font-medium text-green-600 dark:text-green-400">
                    <CheckCircle2 className="h-3.5 w-3.5" /> In your library
                  </Link>
                )}
                {!m.in_library && (m.on_watchlist ? (
                  <Link to="/watchlist?type=movies" className="inline-flex items-center gap-1 text-xs font-medium text-green-600 dark:text-green-400">
                    <CheckCircle2 className="h-3.5 w-3.5" /> On your watchlist
                  </Link>
                ) : (
                  <button onClick={() => add(m)} disabled={adding !== null}
                    title={canFindNow(m) ? "Download it automatically" : "Downloads automatically once it's out digitally"}
                    className="inline-flex min-h-[36px] items-center gap-1 rounded-lg bg-blue-600 px-3 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-60">
                    {adding === m.tmdb_id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
                    Add to watchlist
                  </button>
                ))}
                {!m.in_library && canFindNow(m) && (
                  <button onClick={() => find(m)}
                    className="inline-flex min-h-[36px] items-center gap-1 rounded-lg border border-gray-300 dark:border-gray-600 px-3 text-xs font-medium text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
                    <Download className="h-3.5 w-3.5" /> Find now
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
      <p className="mt-4 text-xs text-gray-400 dark:text-gray-600">Movie data from TMDB.</p>
    </>
  );
}

export default function Browse() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("type");
  const type = raw === "movies" || raw === "music" ? raw : "tv";
  const setType = (t: "tv" | "movies" | "music") => setParams(t === "tv" ? {} : { type: t }, { replace: true });
  return (
    <div className="px-4 py-6 sm:px-6">
      <div className="mb-5 flex flex-wrap items-end gap-3">
        <div className="mr-auto">
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Browse</h1>
          <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
            {type === "tv" ? "Shows airing soon, or search any show, and add it to your watchlist."
              : type === "movies" ? "Popular, in-theatre and upcoming movies, or search any movie, and add it to your watchlist."
              : "New and popular albums, picked for your taste from your music library."}
          </p>
        </div>
        <div className="inline-flex rounded-lg border border-gray-200 dark:border-gray-700 p-0.5" role="tablist" aria-label="Browse">
          {([["tv", "TV shows", Tv], ["movies", "Movies", Clapperboard], ["music", "Music", Music]] as const).map(([id, label, Icon]) => (
            <button key={id} role="tab" aria-selected={type === id} onClick={() => setType(id)}
              className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm ${type === id ? "bg-blue-600 text-white" : "text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"}`}>
              <Icon className="h-4 w-4" />{label}
            </button>
          ))}
        </div>
      </div>
      {type === "tv" ? <ShowBrowse /> : type === "movies" ? <MovieBrowse /> : <MusicBrowse />}
    </div>
  );
}

function ShowBrowse() {
  const [input, setInput] = useState("");
  const [query, setQuery] = useState("");
  const [shows, setShows] = useState<BrowseShow[] | null>(null);
  const [mode, setMode] = useState<"airing" | "search">("airing");
  const [country, setCountry] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<BrowseShow | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const reqId = useRef(0);

  // Debounce the search box.
  useEffect(() => {
    const t = setTimeout(() => setQuery(input.trim()), 400);
    return () => clearTimeout(t);
  }, [input]);

  const load = async (q: string) => {
    const id = ++reqId.current;
    setShows(null);
    setError(null);
    try {
      const r = await api.browse.get(q);
      if (id !== reqId.current) return;   // a newer request superseded this one
      setShows(r.shows);
      setMode(r.mode);
      setCountry(r.country);
    } catch (err) {
      if (id !== reqId.current) return;
      setError(err instanceof Error ? err.message : "Could not load shows.");
      setShows([]);
    }
  };

  useEffect(() => { load(query); }, [query]);

  const added = () => {
    const name = target?.name;
    setTarget(null);
    setToast(`Added ${name}`);
    setTimeout(() => setToast(null), 3000);
    load(query);
  };

  return (
    <div>
      <p className="mb-3 text-sm text-gray-500 dark:text-gray-400">
        {mode === "airing" && !query
          ? `Popular shows airing in the next week${country ? ` (${country})` : ""}. Add one to start tracking it.`
          : "Search results from TVMaze."}
      </p>

      <div className="relative mb-5 max-w-xl">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Search for any show…"
          className="w-full rounded-lg border border-gray-300 dark:border-gray-600 py-2.5 pl-9 pr-9 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-800 dark:text-gray-100"
        />
        {input && (
          <button onClick={() => setInput("")} tabIndex={-1} aria-label="Clear search"
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-gray-400 hover:text-gray-600">
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {error && <p className="mb-4 text-sm text-red-600 dark:text-red-400">{error}</p>}
      {shows === null && !error && (
        <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-blue-600" /></div>
      )}
      {shows !== null && shows.length === 0 && !error && (
        <p className="py-12 text-center text-sm text-gray-400 dark:text-gray-600">
          {query.length === 1 ? "Keep typing…" : query ? `No shows found for "${query}".` : "Nothing is airing right now."}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {(shows ?? []).map((s) => (
          <div key={s.tvmaze_id} className="flex gap-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-3 shadow-sm">
            <Poster url={s.poster_url} title={s.name} className="h-32 w-[5.5rem]" />
            <div className="flex min-w-0 flex-1 flex-col">
              <div className="flex items-start justify-between gap-2">
                <h3 className="font-semibold leading-tight text-gray-900 dark:text-gray-100">{s.name}</h3>
                {s.rating != null && (
                  <span className="flex shrink-0 items-center gap-0.5 text-xs text-gray-500 dark:text-gray-400">
                    <Star className="h-3 w-3 fill-yellow-400 text-yellow-400" />{s.rating.toFixed(1)}
                  </span>
                )}
              </div>
              <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                {[s.network, s.premiered?.slice(0, 4), s.genres.slice(0, 2).join(", ")].filter(Boolean).join(" · ")}
              </p>
              {s.next_airs && (
                <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-gray-600 dark:text-gray-300">
                  {s.season_premiere && (
                    <span className="rounded bg-blue-100 dark:bg-blue-900/60 px-1.5 py-0.5 font-medium text-blue-700 dark:text-blue-300">Premiere</span>
                  )}
                  {s.next_episode} · {fmtDate(s.next_airs)}
                </p>
              )}
              {s.summary && <p className="mt-1 line-clamp-2 text-xs text-gray-400 dark:text-gray-500">{s.summary}</p>}
              <div className="mt-auto pt-2">
                {s.on_watchlist ? (
                  <Link to="/watchlist" className="inline-flex items-center gap-1 text-xs font-medium text-green-600 dark:text-green-400">
                    <CheckCircle2 className="h-3.5 w-3.5" /> On your watchlist
                  </Link>
                ) : (
                  <button onClick={() => setTarget(s)}
                    className="inline-flex min-h-[36px] items-center gap-1 rounded-lg bg-blue-600 px-3 text-xs font-medium text-white hover:bg-blue-700">
                    <Plus className="h-3.5 w-3.5" /> Add
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {target && <AddDialog show={target} onClose={() => setTarget(null)} onAdded={added} />}
      {toast && (
        <div className="fixed bottom-20 right-4 z-50 rounded-lg bg-gray-900 dark:bg-gray-700 px-4 py-3 text-sm text-white shadow-lg md:bottom-6 md:right-6">
          {toast}
        </div>
      )}
    </div>
  );
}

const STATUS_TEXT: Record<string, string> = {
  wanted: "Wanted", grabbed: "Downloading", have: "Downloaded", ignored: "Skipped",
};

/** Browse → Music: new releases and popular albums (ListenBrainz), ranked for your taste. */
function MusicBrowse() {
  const [params, setParams] = useSearchParams();
  const list = params.get("list") === "popular" ? "popular" : "new";
  const setList = (l: "new" | "popular") => { const p = new URLSearchParams(params); p.set("list", l); setParams(p, { replace: true }); };
  const [forYou, setForYou] = useState(true);
  const [data, setData] = useState<Awaited<ReturnType<typeof api.browse.music>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const load = async () => {
    setError(null);
    try { setData(await api.browse.music(list, forYou)); } catch (e) { setError((e as Error).message); setData(null); }
  };
  useEffect(() => { setData(null); load(); }, [list, forYou]); // eslint-disable-line react-hooks/exhaustive-deps
  // While genres are being looked up, refresh now and then so "for you" fills in.
  useEffect(() => {
    if (!data || (!data.building && data.learning === 0)) return;
    const t = setTimeout(load, 20000);
    return () => clearTimeout(t);
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(null), 6000); return () => clearTimeout(t); }, [toast]);

  const add = async (r: BrowseRelease) => {
    setBusy(r.mbid);
    try {
      const res = await api.browse.musicAdd(r);
      setToast(res.message || "Added");
      await load();
    } catch (e) { setToast((e as Error).message); } finally { setBusy(null); }
  };

  const shown = data?.releases ?? [];
  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-lg border border-gray-200 p-0.5 dark:border-gray-700" role="radiogroup" aria-label="Music list">
          {([["new", "New releases"], ["popular", "Popular this week"]] as const).map(([id, label]) => (
            <button key={id} role="radio" aria-checked={list === id} onClick={() => setList(id)}
              className={`rounded-md px-3 py-1.5 text-sm ${list === id ? "bg-blue-600 text-white" : "text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"}`}>
              {label}
            </button>
          ))}
        </div>
        <label className="flex min-h-[36px] cursor-pointer items-center gap-1.5 rounded-lg border border-gray-300 px-2.5 text-sm text-gray-700 dark:border-gray-600 dark:text-gray-200">
          <input type="checkbox" checked={forYou} onChange={(e) => setForYou(e.target.checked)} />
          <Sparkles className="h-4 w-4 text-amber-500" />For you
        </label>
      </div>
      {data && (
        <p className="mb-3 text-xs text-gray-500 dark:text-gray-400">
          {data.taste_ready ? <>Your taste: {data.taste.slice(0, 8).join(", ")}. </> : <>Learning your taste from your music library… </>}
          {(data.building || data.learning > 0) && <span className="inline-flex items-center gap-1"><Loader2 className="h-3 w-3 animate-spin" />looking up genres{data.learning ? ` (${data.learning} artists to go)` : ""}</span>}
          {data.taste_ready && !data.building && (
            <button className="ml-1 inline-flex items-center gap-1 text-blue-600 hover:underline dark:text-blue-400"
                    onClick={async () => { await api.browse.musicTaste(); load(); }}>
              <RefreshCw className="h-3 w-3" />Refresh my taste
            </button>
          )}
        </p>
      )}
      {error && <p className="mb-4 text-sm text-red-600 dark:text-red-400">{error}</p>}
      {data === null && !error && <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-blue-600" /></div>}
      {data && shown.length === 0 && (
        <p className="py-12 text-center text-sm text-gray-400 dark:text-gray-600">
          {forYou && data.taste_ready ? "Nothing here matches your taste right now. Untick For you to see everything." : "Nothing to show right now."}
        </p>
      )}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {shown.map((r) => (
          <div key={r.mbid} className="flex min-w-0 gap-3 rounded-xl border border-gray-200 bg-white p-3 shadow-sm dark:border-gray-700 dark:bg-gray-900">
            <Poster url={r.cover} title={`${r.artist} – ${r.title}`} className="h-24 w-24" icon={Disc3} />
            <div className="flex min-w-0 flex-1 flex-col">
              <h3 className="truncate font-semibold leading-tight text-gray-900 dark:text-gray-100" title={r.title}>{r.title}</h3>
              <p className="truncate text-sm text-gray-600 dark:text-gray-300">{r.artist}</p>
              <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                {[r.date ? fmtDay(r.date) : null, r.type !== "Album" ? r.type : null,
                  list === "popular" && r.listens ? `${r.listens.toLocaleString()} listens` : null].filter(Boolean).join(" · ")}
              </p>
              {r.why && <p className="mt-0.5 truncate text-xs text-amber-700 dark:text-amber-400" title={r.why}>{r.why}</p>}
              <div className="mt-auto flex flex-wrap items-center gap-2 pt-2">
                {r.in_library ? (
                  <span className="inline-flex items-center gap-1 text-xs font-medium text-green-600 dark:text-green-400"><CheckCircle2 className="h-3.5 w-3.5" /> In your library</span>
                ) : r.status && r.status !== "ignored" ? (
                  <Link to="/watchlist?tab=music" className="inline-flex items-center gap-1 text-xs font-medium text-green-600 dark:text-green-400">
                    <CheckCircle2 className="h-3.5 w-3.5" /> {STATUS_TEXT[r.status]}
                  </Link>
                ) : (
                  <button onClick={() => add(r)} disabled={busy !== null || !r.artist_mbids.length}
                          className="inline-flex min-h-[32px] items-center gap-1 rounded-lg bg-blue-600 px-3 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                          title={r.following ? "Download this album" : "Follow the artist and download this album"}>
                    {busy === r.mbid ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
                    {r.following ? "Get album" : "Add"}
                  </button>
                )}
                {r.following && <span className="text-xs text-gray-500 dark:text-gray-400">Following</span>}
              </div>
            </div>
          </div>
        ))}
      </div>
      {toast && (
        <div role="status" onClick={() => setToast(null)}
             className="fixed bottom-20 right-4 z-50 max-w-sm rounded-lg bg-gray-900 px-4 py-3 text-sm text-white shadow-lg dark:bg-gray-700 md:bottom-6 md:right-6">
          {toast}
        </div>
      )}
    </>
  );
}
