import { useEffect, useState } from "react";
import { CheckCircle2, ChevronDown, Disc3, Download, Loader2, Music, RefreshCw, Search, Trash2, X } from "lucide-react";
import { api, type DownloadPath, type MusicAlbum, type MusicArtist, type WatchlistItem } from "../api/client";
import Poster from "./Poster";
import { fmtDay } from "../utils";

// Music on the Watchlist: follow an artist and each new album downloads once it's
// out (FLAC first). The albums list lets you want, skip or grab any of them.

const STATUS: Record<MusicAlbum["status"], [string, string]> = {
  wanted: ["Wanted", "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300"],
  grabbed: ["Downloading", "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300"],
  have: ["Downloaded", "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300"],
  ignored: ["Skipped", "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"],
};

const year = (d?: string | null) => (d ? d.slice(0, 4) : "");
const when = (d?: string | null) => (d && d.length >= 10 ? fmtDay(d) : year(d) || "date unknown");

export function ArtistBadge() {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-fuchsia-100 px-2 py-0.5 text-[11px] font-semibold text-fuchsia-700 dark:bg-fuchsia-900/40 dark:text-fuchsia-300">
      <Music className="h-3 w-3" aria-hidden />Artist
    </span>
  );
}

export function ArtistCard({ item, selected, onToggleSelect, onToggle, onDelete, onRefresh, onToast }: {
  item: WatchlistItem; selected: boolean;
  onToggleSelect: () => void; onToggle: () => void; onDelete: () => void; onRefresh: () => void; onToast: (m: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [albums, setAlbums] = useState<MusicAlbum[] | null>(null);
  const [busy, setBusy] = useState<number | "refresh" | null>(null);
  const [showSkipped, setShowSkipped] = useState(false);
  const a = item.albums;

  const loadAlbums = () => api.music.albums(item.id).then(setAlbums).catch((e) => onToast(e.message));
  useEffect(() => { if (open && albums === null) loadAlbums(); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (id: number | "refresh", fn: () => Promise<string | void>) => {
    setBusy(id);
    try { const m = await fn(); if (m) onToast(m); } catch (e) { onToast((e as Error).message); }
    setBusy(null);
    await loadAlbums();
    onRefresh();
  };

  const shown = (albums ?? []).filter((x) => showSkipped || x.status !== "ignored");
  const skipped = (albums ?? []).filter((x) => x.status === "ignored").length;

  return (
    <div className={`min-w-0 rounded-xl border bg-white dark:bg-gray-900 p-4 shadow-sm ${selected ? "border-blue-400" : "border-gray-200 dark:border-gray-700"} ${item.enabled ? "" : "opacity-70"}`}>
      <div className="flex gap-3">
        <input type="checkbox" checked={selected} onChange={onToggleSelect} aria-label={`Select ${item.title}`}
               className="mt-1 h-4 w-4 shrink-0 rounded border-gray-300 text-blue-600" />
        <Poster url={item.poster_url} title={item.title} className="h-16 w-16" icon={Music} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate font-semibold text-gray-900 dark:text-gray-100">{item.title}</h3>
            <ArtistBadge />
          </div>
          {(item.summary || item.network) && (
            <p className="truncate text-xs text-gray-500 dark:text-gray-400">{[item.summary, item.network].filter(Boolean).join(" · ")}</p>
          )}
          {a && (
            <p className="mt-1 text-sm text-gray-600 dark:text-gray-300">
              {a.have} downloaded · {a.wanted} wanted{a.grabbed ? ` · ${a.grabbed} downloading` : ""}
            </p>
          )}
          {a?.next && (
            <p className="text-xs text-gray-500 dark:text-gray-400">Next: <b>{a.next.title}</b>, {when(a.next.date)}</p>
          )}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button onClick={() => setOpen((v) => !v)} aria-expanded={open}
                className="flex items-center gap-1 rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800">
          <Disc3 className="h-4 w-4" />Albums{a ? ` (${a.total})` : ""}
          <ChevronDown className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`} />
        </button>
        <label className="flex cursor-pointer items-center gap-1.5 text-sm text-gray-600 dark:text-gray-300">
          <input type="checkbox" checked={item.enabled} onChange={onToggle} className="h-4 w-4 rounded border-gray-300 text-blue-600" />
          Follow
        </label>
        <button onClick={() => act("refresh", async () => {
                  const r = await api.music.refreshAlbums(item.id);
                  return r.new ? `${r.new} new album${r.new === 1 ? "" : "s"} found` : "No new albums";
                })}
                disabled={busy !== null} title="Check MusicBrainz for new albums" aria-label={`Check ${item.title} for new albums`}
                className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200">
          <RefreshCw className={`h-4 w-4 ${busy === "refresh" ? "animate-spin" : ""}`} />
        </button>
        <button onClick={onDelete} aria-label={`Stop following ${item.title}`} title="Stop following"
                className="ml-auto rounded p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600 dark:hover:bg-red-950">
          <Trash2 className="h-4 w-4" />
        </button>
      </div>

      {open && (
        <div className="mt-3 border-t border-gray-100 pt-3 dark:border-gray-800">
          {albums === null ? <Loader2 className="h-5 w-5 animate-spin text-blue-600" /> : (
            <>
              <ul className="space-y-2">
                {shown.map((al) => {
                  const [label, style] = al.problem
                    ? ["Problem", "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300"]
                    : STATUS[al.status];
                  return (
                    <li key={al.id} className="flex items-center gap-2">
                      <Poster url={al.cover_url} title={al.title} className="h-10 w-10" icon={Disc3} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-gray-800 dark:text-gray-200" title={al.torrent_name ?? undefined}>{al.title}</p>
                        <p className="text-xs text-gray-500 dark:text-gray-400">
                          {al.released ? year(al.release_date) : `Out ${when(al.release_date)}`}
                          {al.type && al.type !== "Album" ? ` · ${al.type}` : ""}
                          {al.quality ? ` · ${al.quality === "flac" ? "FLAC" : al.quality.toUpperCase()}` : ""}
                        </p>
                        {al.problem && (
                          <p className="break-words text-xs text-red-600 dark:text-red-400" title={al.problem}>
                            {al.problem}. Fix it (Settings → Folders → Test), then press download.
                          </p>
                        )}
                      </div>
                      <span className={`whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-medium ${style}`}>{label}</span>
                      {al.status !== "have" && al.status !== "grabbed" && (
                        <button onClick={() => act(al.id, async () => (await api.music.searchAlbum(al.id)).message)}
                                disabled={busy !== null} aria-label={`Download ${al.title} now`} title="Search and download now"
                                className="rounded p-1.5 text-blue-600 hover:bg-blue-50 disabled:opacity-40 dark:text-blue-400 dark:hover:bg-blue-950">
                          {busy === al.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                        </button>
                      )}
                      {al.status === "wanted" && (
                        <button onClick={() => act(al.id, async () => { await api.music.setAlbum(al.id, "ignored"); })}
                                disabled={busy !== null} aria-label={`Skip ${al.title}`} title="Skip this album"
                                className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:opacity-40 dark:hover:bg-gray-800">
                          <X className="h-4 w-4" />
                        </button>
                      )}
                      {al.status === "ignored" && (
                        <button onClick={() => act(al.id, async () => { await api.music.setAlbum(al.id, "wanted"); })}
                                disabled={busy !== null} aria-label={`Want ${al.title}`} title="Download it once it's found"
                                className="rounded px-2 py-1 text-xs font-medium text-blue-600 hover:bg-blue-50 disabled:opacity-40 dark:text-blue-400 dark:hover:bg-blue-950">
                          Want
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
              {skipped > 0 && (
                <button onClick={() => setShowSkipped((v) => !v)} className="mt-2 text-xs text-blue-600 hover:underline dark:text-blue-400">
                  {showSkipped ? "Hide" : "Show"} {skipped} skipped album{skipped === 1 ? "" : "s"}
                </button>
              )}
              {!albums.length && <p className="text-sm text-gray-500">MusicBrainz lists no albums for this artist yet.</p>}
            </>
          )}
        </div>
      )}
    </div>
  );
}

const START: { id: "new" | "latest" | "all"; label: string; hint: string }[] = [
  { id: "new", label: "New releases", hint: "Only albums that come out from now on." },
  { id: "latest", label: "Latest album too", hint: "The most recent album, then new ones." },
  { id: "all", label: "Everything", hint: "The whole back catalogue (can be a lot)." },
];

export function AddArtistDialog({ paths, onClose, onAdded }: {
  paths: DownloadPath[]; onClose: () => void; onAdded: (name: string) => void;
}) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<MusicArtist[] | null>(null);
  const [pick, setPick] = useState<MusicArtist | null>(null);
  const [start, setStart] = useState<"new" | "latest" | "all">("new");
  const [eps, setEps] = useState(false);
  const [folder, setFolder] = useState<number | "">("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);

  const search = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!q.trim()) return;
    setBusy(true); setError(null); setPick(null);
    try { setResults(await api.music.searchArtists(q.trim())); } catch (err) { setError((err as Error).message); }
    setBusy(false);
  };
  const follow = async () => {
    if (!pick) return;
    setBusy(true); setError(null);
    try {
      await api.music.follow({ mbid: pick.mbid, start, include_eps: eps, download_path_id: folder === "" ? null : folder });
      onAdded(pick.name);
    } catch (err) { setError((err as Error).message); setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 sm:items-center sm:p-4" role="dialog" aria-modal="true" aria-label="Follow an artist">
      <div className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-t-2xl bg-white p-5 shadow-xl dark:bg-gray-900 sm:rounded-2xl">
        <div className="mb-3 flex items-center gap-2">
          <Music className="h-5 w-5 text-fuchsia-600" />
          <h2 className="flex-1 text-lg font-semibold text-gray-900 dark:text-gray-100">Follow an artist</h2>
          <button onClick={onClose} aria-label="Close" className="rounded p-1.5 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"><X className="h-4 w-4" /></button>
        </div>
        <p className="mb-3 text-sm text-gray-500 dark:text-gray-400">New albums download in FLAC once they're out (MP3 320 if no FLAC turns up within two days).</p>
        <form onSubmit={search} className="flex gap-2">
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Artist name" aria-label="Artist name"
                 className="min-w-0 flex-1 rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100" />
          <button type="submit" disabled={busy || !q.trim()}
                  className="flex items-center gap-1 rounded-lg border border-gray-300 px-3 text-sm text-gray-700 hover:bg-gray-50 disabled:opacity-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800">
            {busy && !pick ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}Search
          </button>
        </form>

        {results && (
          <ul className="mt-3 space-y-1" role="radiogroup" aria-label="Artists">
            {results.length === 0 && <li className="text-sm text-gray-500">No artists found.</li>}
            {results.map((r) => (
              <li key={r.mbid}>
                <button type="button" role="radio" aria-checked={pick?.mbid === r.mbid} onClick={() => setPick(r)}
                        className={`flex w-full items-center gap-2 rounded-lg border px-3 py-2 text-left text-sm ${pick?.mbid === r.mbid
                          ? "border-blue-500 bg-blue-50 dark:bg-blue-950/40" : "border-gray-200 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800"}`}>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium text-gray-900 dark:text-gray-100">{r.name}</span>
                    <span className="block truncate text-xs text-gray-500 dark:text-gray-400">
                      {[r.disambiguation, r.type, r.country, r.begin && `since ${r.begin}`].filter(Boolean).join(" · ") || " "}
                    </span>
                  </span>
                  {pick?.mbid === r.mbid && <CheckCircle2 className="h-4 w-4 text-blue-600" />}
                </button>
              </li>
            ))}
          </ul>
        )}

        {pick && (
          <div className="mt-4 space-y-3 border-t border-gray-100 pt-4 dark:border-gray-800">
            <fieldset>
              <legend className="mb-1 text-sm font-medium text-gray-700 dark:text-gray-300">Download</legend>
              {START.map((s) => (
                <label key={s.id} className="flex cursor-pointer items-start gap-2 py-1 text-sm">
                  <input type="radio" name="start" checked={start === s.id} onChange={() => setStart(s.id)} className="mt-0.5" />
                  <span><span className="font-medium text-gray-800 dark:text-gray-200">{s.label}</span>
                    <span className="block text-xs text-gray-500 dark:text-gray-400">{s.hint}</span></span>
                </label>
              ))}
            </fieldset>
            <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
              <input type="checkbox" checked={eps} onChange={(e) => setEps(e.target.checked)} className="h-4 w-4 rounded border-gray-300" />
              Include EPs
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium text-gray-700 dark:text-gray-300">Download to</span>
              <select value={folder} onChange={(e) => setFolder(e.target.value === "" ? "" : Number(e.target.value))}
                      className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100">
                <option value="">The music download folder (Settings)</option>
                {paths.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </label>
          </div>
        )}

        {error && <p className="mt-3 text-sm text-red-600 dark:text-red-400">{error}</p>}
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800">Cancel</button>
          <button onClick={follow} disabled={!pick || busy}
                  className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
            {busy && pick && <Loader2 className="h-4 w-4 animate-spin" />}Follow
          </button>
        </div>
      </div>
    </div>
  );
}
