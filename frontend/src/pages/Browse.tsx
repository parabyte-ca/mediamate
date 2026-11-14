import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Clapperboard, Loader2, Music, RefreshCw, Search, Sparkles, Star, Tv } from "lucide-react";
import { api, type BrowseMovie, type BrowseRelease, type BrowseShow, type MovieList } from "../api/client";
import { AddShowDialog, MovieCard, ReleaseCard, ShowCard, useAddMovie } from "../components/TitleCards";
import { fmtDay } from "../utils";

const MOVIE_LISTS: [MovieList, string][] = [["popular", "Popular"], ["now_playing", "In theatres"], ["upcoming", "Coming soon"]];

/** Movies from TMDB: popular, in theatres, coming soon. "Find now" opens Search. */
function MovieBrowse() {
  const [list, setList] = useState<MovieList>("popular");
  const [movies, setMovies] = useState<BrowseMovie[] | null>(null);
  const [needKey, setNeedKey] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reqId = useRef(0);
  const navigate = useNavigate();

  useEffect(() => {
    const id = ++reqId.current;
    setMovies(null);
    setError(null);
    api.browse.movies(list)
      .then((r) => { if (id === reqId.current) { setNeedKey(r.need_key); setMovies(r.movies); } })
      .catch((e) => { if (id === reqId.current) { setError(e instanceof Error ? e.message : "Could not load movies."); setMovies([]); } });
  }, [list]);

  const find = (m: BrowseMovie) => navigate(`/search?q=${encodeURIComponent(`${m.title}${m.year ? ` ${m.year}` : ""}`)}`);
  const { adding, error: addError, add } = useAddMovie((tmdbId, wid) =>
    setMovies((ms) => ms && ms.map((x) => x.tmdb_id === tmdbId ? { ...x, on_watchlist: true, watchlist_id: wid } : x)));
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
        <div className="inline-flex rounded-lg border border-gray-200 dark:border-gray-700 p-0.5" role="radiogroup" aria-label="Movie list">
          {MOVIE_LISTS.map(([id, label]) => (
            <button key={id} role="radio" aria-checked={list === id} onClick={() => setList(id)}
              className={`rounded-md px-3 py-1.5 text-sm ${list === id ? "bg-blue-600 text-white" : "text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"}`}>
              {label}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="mb-4 text-sm text-red-600 dark:text-red-400">{error}</p>}
      {movies === null && !error && <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-blue-600" /></div>}
      {movies !== null && movies.length === 0 && !error && (
        <p className="py-12 text-center text-sm text-gray-400 dark:text-gray-600">Nothing to show right now.</p>
      )}

      {addError && <p role="alert" className="mb-3 text-sm text-red-600 dark:text-red-400">{addError}</p>}
      {list === "upcoming" && movies !== null && movies.length > 0 && (
        <p className="mb-3 text-xs text-gray-500 dark:text-gray-400">
          Add one to your watchlist and MediaMate downloads it when it's released digitally.
        </p>
      )}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {(movies ?? []).map((m) => (
          <MovieCard key={m.tmdb_id} movie={m} adding={adding === m.tmdb_id} onAdd={add}
                     onFind={canFindNow(m) ? find : undefined} dateAsDay={list !== "popular"} />
        ))}
      </div>
      <p className="mt-4 text-xs text-gray-400 dark:text-gray-600">Movie data from TMDB.</p>
    </>
  );
}

/** Browse is for discovering; Search finds something by name. */
function SearchInstead({ type }: { type: "tv" | "movies" | "music" }) {
  const to = type === "music" ? "/search?type=music" : "/search";
  const what = type === "tv" ? "a show" : type === "movies" ? "a movie" : "an artist or album";
  return (
    <Link to={to} className="inline-flex min-h-[36px] items-center gap-1.5 rounded-lg border border-gray-300 px-3 text-sm text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
          title={`Search for ${what} by name`}>
      <Search className="h-4 w-4" />Search for something specific
    </Link>
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
            {type === "tv" ? "Popular shows airing this week. Add one to your watchlist."
              : type === "movies" ? "Popular, in-theatre and upcoming movies. Add one to your watchlist."
              : "New and popular albums, picked for your taste from your music library."}
          </p>
        </div>
        <SearchInstead type={type} />
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
  const [shows, setShows] = useState<BrowseShow[] | null>(null);
  const [country, setCountry] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [target, setTarget] = useState<BrowseShow | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const load = async () => {
    setError(null);
    try {
      const r = await api.browse.get();
      setShows(r.shows);
      setCountry(r.country);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load shows.");
      setShows([]);
    }
  };

  useEffect(() => { load(); }, []);

  const added = () => {
    const name = target?.name;
    setTarget(null);
    setToast(`Added ${name}`);
    setTimeout(() => setToast(null), 3000);
    load();
  };

  return (
    <div>
      <p className="mb-3 text-sm text-gray-500 dark:text-gray-400">
        Popular shows airing in the next week{country ? ` (${country})` : ""}. Add one to start tracking it.
      </p>

      {error && <p className="mb-4 text-sm text-red-600 dark:text-red-400">{error}</p>}
      {shows === null && !error && (
        <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-blue-600" /></div>
      )}
      {shows !== null && shows.length === 0 && !error && (
        <p className="py-12 text-center text-sm text-gray-400 dark:text-gray-600">Nothing is airing right now.</p>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {(shows ?? []).map((s) => <ShowCard key={s.tvmaze_id} show={s} onAdd={setTarget} />)}
      </div>

      {target && <AddShowDialog show={target} onClose={() => setTarget(null)} onAdded={added} />}
      {toast && (
        <div className="fixed bottom-20 right-4 z-50 rounded-lg bg-gray-900 dark:bg-gray-700 px-4 py-3 text-sm text-white shadow-lg md:bottom-6 md:right-6">
          {toast}
        </div>
      )}
    </div>
  );
}

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
    if (!data || (!data.building && data.learning === 0 && !data.ratings_pending)) return;
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
          {data.ratings_pending > 0 && !data.building && data.learning === 0 && <span className="inline-flex items-center gap-1"><Loader2 className="h-3 w-3 animate-spin" />looking up ratings ({data.ratings_pending} to go)</span>}
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
          <ReleaseCard key={r.mbid} release={r} busy={busy === r.mbid} disabled={busy !== null} onAdd={add}
            extra={<>
              <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                {[r.date ? fmtDay(r.date) : null, r.type !== "Album" ? r.type : null,
                  list === "popular" && r.listens ? `${r.listens.toLocaleString()} listens` : null].filter(Boolean).join(" · ")}
              </p>
              {r.rating != null && (
                <p className="mt-0.5 inline-flex items-center gap-1 text-xs text-gray-600 dark:text-gray-300"
                   title={`MusicBrainz rating: ${r.rating.toFixed(1)} out of 5 from ${r.rating_votes} votes`}>
                  <Star className="h-3 w-3 fill-yellow-400 text-yellow-400" />{r.rating.toFixed(1)}
                  <span className="text-gray-400 dark:text-gray-500">({r.rating_votes})</span>
                </p>
              )}
              {r.why && <p className="mt-0.5 truncate text-xs text-amber-700 dark:text-amber-400" title={r.why}>{r.why}</p>}
            </>} />
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
