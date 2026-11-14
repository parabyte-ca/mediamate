import { useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Disc3, Download, Loader2, Plus, Search, Star } from "lucide-react";
import { api, type BrowseMovie, type BrowseRelease, type BrowseShow } from "../api/client";
import Poster from "./Poster";
import { fmtDate, fmtDay } from "../utils";

/** Show, movie and album cards shared by Browse (discover) and Search (find by name),
 *  so a title looks and acts the same wherever it turns up. */

export const CARD = "flex min-w-0 gap-3 rounded-xl border border-gray-200 bg-white p-3 shadow-sm dark:border-gray-700 dark:bg-gray-900";
const ADD_BTN = "inline-flex min-h-[36px] items-center gap-1 rounded-lg bg-blue-600 px-3 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-60";
const SOFT_BTN = "inline-flex min-h-[36px] items-center gap-1 rounded-lg border border-gray-300 px-3 text-xs font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800";
const OK = "inline-flex items-center gap-1 text-xs font-medium text-green-600 dark:text-green-400";

export const STATUS_TEXT: Record<string, string> = {
  wanted: "Wanted", grabbed: "Downloading", have: "Downloaded", ignored: "Skipped",
};

/** Add a TVMaze show: start from the next episode or from the beginning. */
export function AddShowDialog({ show, onClose, onAdded }: { show: BrowseShow; onClose: () => void; onAdded: () => void }) {
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-label={`Add ${show.name}`}>
      <div className="w-full max-w-md rounded-xl border border-gray-200 bg-white p-6 shadow-xl dark:border-gray-700 dark:bg-gray-900">
        <div className="mb-4 flex gap-3">
          <Poster url={show.poster_url} title={show.name} className="h-24 w-16" />
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">{show.name}</h2>
            <p className="text-xs text-gray-500 dark:text-gray-400">{[show.network, show.status].filter(Boolean).join(" · ")}</p>
          </div>
        </div>
        <p className="mb-2 text-sm font-medium text-gray-700 dark:text-gray-300">Where should MediaMate start?</p>
        <div className="space-y-2">
          {option("next", "Next new episode", "Only download episodes from now on.")}
          {option("beginning", "From the beginning", "Download every episode, starting with season 1.")}
        </div>
        <p className="mt-3 text-xs text-gray-400 dark:text-gray-500">
          Uses your default quality profile if you've set one. Change anything later from the Watchlist.
        </p>
        {error && <p className="mt-3 text-sm text-red-600 dark:text-red-400">{error}</p>}
        <div className="mt-5 flex gap-3">
          <button onClick={onClose} className="flex-1 rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800">
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

function Rating({ value }: { value: number | null | undefined }) {
  if (value == null) return null;
  return (
    <span className="flex shrink-0 items-center gap-0.5 text-xs text-gray-500 dark:text-gray-400">
      <Star className="h-3 w-3 fill-yellow-400 text-yellow-400" />{value.toFixed(1)}
    </span>
  );
}

export function ShowCard({ show: s, onAdd }: { show: BrowseShow; onAdd: (s: BrowseShow) => void }) {
  return (
    <div className={CARD}>
      <Poster url={s.poster_url} title={s.name} className="h-32 w-[5.5rem]" />
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-start justify-between gap-2">
          <h3 className="font-semibold leading-tight text-gray-900 dark:text-gray-100">{s.name}</h3>
          <Rating value={s.rating} />
        </div>
        <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
          {[s.network, s.premiered?.slice(0, 4), s.genres.slice(0, 2).join(", ")].filter(Boolean).join(" · ")}
        </p>
        {s.next_airs && (
          <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-gray-600 dark:text-gray-300">
            {s.season_premiere && (
              <span className="rounded bg-blue-100 px-1.5 py-0.5 font-medium text-blue-700 dark:bg-blue-900/60 dark:text-blue-300">Premiere</span>
            )}
            {s.next_episode} · {fmtDate(s.next_airs)}
          </p>
        )}
        {s.summary && <p className="mt-1 line-clamp-2 text-xs text-gray-400 dark:text-gray-500">{s.summary}</p>}
        <div className="mt-auto pt-2">
          {s.on_watchlist ? (
            <Link to="/watchlist" className={OK}><CheckCircle2 className="h-3.5 w-3.5" /> On your watchlist</Link>
          ) : (
            <button onClick={() => onAdd(s)} className={ADD_BTN}><Plus className="h-3.5 w-3.5" /> Add</button>
          )}
        </div>
      </div>
    </div>
  );
}

/** A movie. `dateAsDay` shows the full release date (coming soon, in theatres) instead of the year. */
export function MovieCard({ movie: m, adding, onAdd, onFind, dateAsDay = false }: {
  movie: BrowseMovie; adding: boolean; onAdd: (m: BrowseMovie) => void;
  onFind?: (m: BrowseMovie) => void; dateAsDay?: boolean;
}) {
  return (
    <div className={CARD}>
      <Poster url={m.poster_url} title={m.title} className="h-32 w-[5.5rem]" />
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-start justify-between gap-2">
          <h3 className="font-semibold leading-tight text-gray-900 dark:text-gray-100">{m.title}</h3>
          {m.votes > 0 && <Rating value={m.rating} />}
        </div>
        <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
          {m.release_date && <span>{dateAsDay ? fmtDay(m.release_date) : m.year}</span>}
          {m.certification && <span className="rounded border border-gray-300 px-1 text-[10px] font-medium dark:border-gray-600">{m.certification}</span>}
        </p>
        {m.overview && <p className="mt-1 line-clamp-2 text-xs text-gray-400 dark:text-gray-500">{m.overview}</p>}
        <div className="mt-auto flex flex-wrap items-center gap-2 pt-2">
          {m.in_library && (
            <Link to={`/library?q=${encodeURIComponent(m.title)}`} className={OK}><CheckCircle2 className="h-3.5 w-3.5" /> In your library</Link>
          )}
          {!m.in_library && (m.on_watchlist ? (
            <Link to="/watchlist?type=movies" className={OK}><CheckCircle2 className="h-3.5 w-3.5" /> On your watchlist</Link>
          ) : (
            <button onClick={() => onAdd(m)} disabled={adding}
              title={onFind ? "Download it automatically" : "Downloads automatically once it's out digitally"}
              className={ADD_BTN}>
              {adding ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
              Add to watchlist
            </button>
          ))}
          {!m.in_library && onFind && (
            <button onClick={() => onFind(m)} className={SOFT_BTN}><Download className="h-3.5 w-3.5" /> Find now</button>
          )}
        </div>
      </div>
    </div>
  );
}

/** Add a movie to the watchlist; returns a function and the id being added. */
export function useAddMovie(update: (tmdbId: number, watchlistId: number) => void) {
  const [adding, setAdding] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const add = async (m: BrowseMovie) => {
    setAdding(m.tmdb_id);
    setError(null);
    try {
      const w = await api.watchlist.addFromTmdb({ tmdb_id: m.tmdb_id });
      update(m.tmdb_id, w.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't add the movie.");
    } finally {
      setAdding(null);
    }
  };
  return { adding, error, add };
}

/** An album (MusicBrainz release group): Add follows the artist and wants this album. */
export function ReleaseCard({ release: r, busy, disabled, onAdd, extra, moreByArtist = true }: {
  release: BrowseRelease; busy: boolean; disabled: boolean; onAdd: (r: BrowseRelease) => void;
  extra?: React.ReactNode; moreByArtist?: boolean;
}) {
  return (
    <div className={CARD}>
      <Poster url={r.cover} title={`${r.artist} – ${r.title}`} className="h-24 w-24" icon={Disc3} />
      <div className="flex min-w-0 flex-1 flex-col">
        <h3 className="truncate font-semibold leading-tight text-gray-900 dark:text-gray-100" title={r.title}>{r.title}</h3>
        <p className="truncate text-sm text-gray-600 dark:text-gray-300">{r.artist}</p>
        {extra}
        <div className="mt-auto flex flex-wrap items-center gap-x-2 gap-y-1 pt-2">
          {r.in_library ? (
            <span className={OK}><CheckCircle2 className="h-3.5 w-3.5" /> In your library</span>
          ) : r.status && r.status !== "ignored" ? (
            <Link to="/watchlist?tab=music" className={OK}><CheckCircle2 className="h-3.5 w-3.5" /> {STATUS_TEXT[r.status]}</Link>
          ) : (
            <button onClick={() => onAdd(r)} disabled={disabled || !r.artist_mbids.length}
                    className="inline-flex min-h-[32px] items-center gap-1 rounded-lg bg-blue-600 px-3 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                    title={r.following ? "Download this album" : "Follow the artist and download this album"}>
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
              {r.following ? "Get album" : "Add"}
            </button>
          )}
          {r.following && <span className="text-xs text-gray-500 dark:text-gray-400">Following</span>}
          {moreByArtist && r.artist && (
            <Link to={`/search?type=music&q=${encodeURIComponent(r.artist)}`}
                  className="inline-flex items-center gap-1 text-xs text-blue-600 hover:underline dark:text-blue-400">
              <Search className="h-3 w-3" />More by this artist
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
