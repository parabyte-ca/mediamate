import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Disc3, FolderInput, FolderSearch, Loader2, Pause, Play, Search, Tags, Wand2, X } from "lucide-react";
import { api, type LibAlbum, type LibAlbumDetail, type MusicIssue } from "../api/client";
import Stars from "./Stars";
import { fmtSize } from "../utils";

// The music half of the Library: album folders from the music library and folders
// whose target is Music. Stars and collections sync with Plex like videos.

const ISSUE: Record<MusicIssue, [string, string]> = {
  untagged: ["Missing tags", "Fix tags fills them in from MusicBrainz."],
  missing_tracks: ["Missing tracks", "Track numbers have gaps, or fewer tracks than the album has."],
  low_bitrate: ["Low bitrate", "A lossy copy under 220 kbps. Look for a FLAC or 320 kbps copy."],
  duplicate: ["Duplicate", "The same album is in another folder too. Keep the better copy and delete the other."],
};

const sel = "rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1.5 text-sm text-gray-700 dark:text-gray-200";
const btn = "flex min-h-[36px] items-center gap-1.5 rounded-lg px-3 text-sm disabled:opacity-50";
const ghost = `${btn} border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800`;
const primary = `${btn} bg-blue-600 font-medium text-white hover:bg-blue-700`;

function fmtLen(s?: number | null) {
  if (!s) return "";
  const m = Math.floor(s / 60), sec = Math.round(s % 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}:${String(sec).padStart(2, "0")}`;
}

function Cover({ a, className }: { a: LibAlbum; className: string }) {
  const [bad, setBad] = useState(false);
  if (!a.has_cover || bad) {
    return <div className={`flex items-center justify-center bg-gray-100 text-gray-300 dark:bg-gray-800 dark:text-gray-600 ${className}`} aria-hidden><Disc3 className="h-1/3 w-1/3" /></div>;
  }
  return <img src={api.music.coverUrl(a.id, a.scanned_at)} alt="" loading="lazy" onError={() => setBad(true)} className={`object-cover ${className}`} />;
}

export default function MusicLibrary() {
  const [albums, setAlbums] = useState<LibAlbum[] | null>(null);
  const [status, setStatus] = useState<Awaited<ReturnType<typeof api.music.libraryStatus>> | null>(null);
  const [q, setQ] = useState("");
  const [fmt, setFmt] = useState("");
  const [rating, setRating] = useState("");
  const [coll, setColl] = useState("");
  const [issue, setIssue] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const say = (m: string) => { setToast(m); window.setTimeout(() => setToast(null), 4000); };

  const load = useCallback(async () => {
    const [a, s] = await Promise.all([api.music.library(), api.music.libraryStatus()]);
    setAlbums(a);
    setStatus(s);
  }, []);
  useEffect(() => { load().catch((e) => { say(e.message); setAlbums([]); }); }, [load]);
  useEffect(() => {
    if (!status?.running) return;
    const id = window.setInterval(() => load().catch(() => {}), 2000);
    return () => window.clearInterval(id);
  }, [status?.running, load]);

  const patch = (id: number, p: Partial<LibAlbum>) => setAlbums((l) => (l ?? []).map((x) => (x.id === id ? { ...x, ...p } : x)));
  const rate = (a: LibAlbum, n: number) => {
    patch(a.id, { rating: n || null });
    api.music.rateAlbum(a.id, n).then((r) => { patch(a.id, { rating: r.rating }); if (r.plex === "queued") say(r.msg); })
      .catch((e) => { say(e.message); load(); });
  };

  const formats = useMemo(() => [...new Set((albums ?? []).map((a) => a.format ?? "").filter(Boolean))].sort(), [albums]);
  const colls = useMemo(() => [...new Set((albums ?? []).flatMap((a) => a.collections))].sort((x, y) => x.localeCompare(y)), [albums]);
  const shown = (albums ?? []).filter((a) => {
    const t = q.trim().toLowerCase();
    if (t && !`${a.artist ?? ""} ${a.title ?? ""}`.toLowerCase().includes(t)) return false;
    if (fmt && a.format !== fmt) return false;
    if (rating === "unrated" ? !!a.rating : rating && (a.rating ?? 0) < Number(rating)) return false;
    if (coll && !a.collections.includes(coll)) return false;
    if (issue === "any" ? !a.issues.length : issue && !a.issues.includes(issue as MusicIssue)) return false;
    return true;
  });
  const needing = (albums ?? []).filter((a) => a.issues.length).length;

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <button className={ghost} disabled={status?.running} onClick={async () => {
          try { await api.music.libraryScan(); } catch (e) { say((e as Error).message); }
          load().catch(() => {});
        }}>
          {status?.running ? <Loader2 className="h-4 w-4 animate-spin" /> : <FolderSearch className="h-4 w-4" />}
          {status?.running ? "Scanning…" : "Scan music"}
        </button>
        {status && (
          <span className="text-xs text-gray-500 dark:text-gray-400">
            {albums?.length ?? 0} albums
            {status.last?.tracks != null && ` · ${status.last.tracks} tracks`}
            {needing > 0 && <> · <button className="text-amber-600 hover:underline dark:text-amber-400" onClick={() => setIssue("any")}>{needing} need attention</button></>}
          </span>
        )}
      </div>

      {status && !status.music_dir && (
        <div className="mb-3 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          Set the <b>music library folder</b> in <Link to="/settings#music" className="underline">Settings → Music</Link>: finished albums are put there as Artist / Album (Year), ready for Plex.
        </div>
      )}

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search artists and albums…" aria-label="Search artists and albums" className={`${sel} w-full pl-8`} />
        </div>
        <select aria-label="Format" className={sel} value={fmt} onChange={(e) => setFmt(e.target.value)}>
          <option value="">All formats</option>{formats.map((f) => <option key={f} value={f}>{f.toUpperCase()}</option>)}
        </select>
        <select aria-label="Album rating" className={sel} value={rating} onChange={(e) => setRating(e.target.value)}>
          <option value="">Any rating</option>
          {[5, 4, 3, 2, 1].map((n) => <option key={n} value={n}>{"★".repeat(n)}{n < 5 ? " or more" : ""}</option>)}
          <option value="unrated">Not rated</option>
        </select>
        {colls.length > 0 && (
          <select aria-label="Album collection" className={sel} value={coll} onChange={(e) => setColl(e.target.value)}>
            <option value="">All collections</option>{colls.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        )}
        <select aria-label="Problems" className={sel} value={issue} onChange={(e) => setIssue(e.target.value)}>
          <option value="">All albums</option><option value="any">Needs attention</option>
          {(Object.keys(ISSUE) as MusicIssue[]).map((k) => <option key={k} value={k}>{ISSUE[k][0]}</option>)}
        </select>
      </div>

      {albums === null ? (
        <div className="flex justify-center p-10"><Loader2 className="h-5 w-5 animate-spin text-blue-600" /></div>
      ) : albums.length === 0 ? (
        <div className="rounded-xl border border-gray-200 bg-white p-10 text-center text-sm text-gray-500 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-400">
          <Disc3 className="mx-auto mb-2 h-6 w-6 text-gray-300 dark:text-gray-600" />
          No music yet. Set the music library folder (or a folder with the <b>Music</b> target) in Settings, then <b>Scan music</b>.
          Follow artists on the <Link to="/watchlist?type=music" className="text-blue-600 hover:underline">Watchlist</Link> to download new albums.
        </div>
      ) : shown.length === 0 ? (
        <p className="p-10 text-center text-sm text-gray-500">No albums match these filters.</p>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {shown.map((a) => (
            <div key={a.id} className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-900">
              <button type="button" onClick={() => setOpen(a.id)} className="block w-full text-left" aria-label={`Open ${a.title} by ${a.artist}`}>
                <Cover a={a} className="aspect-square w-full" />
              </button>
              <div className="p-2.5">
                <button type="button" onClick={() => setOpen(a.id)} className="block w-full text-left">
                  <p className="truncate text-sm font-semibold text-gray-900 hover:text-blue-600 dark:text-gray-100 dark:hover:text-blue-400" title={a.title ?? ""}>{a.title}</p>
                  <p className="truncate text-xs text-gray-500 dark:text-gray-400">{a.artist}{a.year ? ` · ${a.year}` : ""}</p>
                </button>
                <div className="mt-1 flex flex-wrap items-center gap-1 text-[11px]">
                  {a.format && <span className={`rounded px-1.5 font-medium ${a.format === "flac" ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300" : "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300"}`}>{a.format.toUpperCase()}</span>}
                  <span className="text-gray-400">{a.tracks} tracks</span>
                  {a.issues.map((i) => (
                    <span key={i} title={ISSUE[i][1]} className="inline-flex items-center gap-0.5 rounded bg-amber-100 px-1.5 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
                      <AlertTriangle className="h-3 w-3" />{ISSUE[i][0]}
                    </span>
                  ))}
                </div>
                <Stars value={a.rating} label={`${a.title}`} onChange={(n) => rate(a, n)} />
                {a.collections.length > 0 && (
                  <div className="mt-0.5 flex flex-wrap gap-1">
                    {a.collections.map((c) => (
                      <button key={c} type="button" onClick={() => setColl(c)} className="inline-flex items-center gap-1 rounded bg-sky-100 px-1.5 text-[11px] text-sky-700 hover:underline dark:bg-sky-900/40 dark:text-sky-300">
                        <Tags className="h-3 w-3" />{c}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {open != null && <AlbumDialog id={open} onClose={() => setOpen(null)} onChanged={(m) => { if (m) say(m); load().catch(() => {}); }} />}
      {toast && (
        <div role="status" className="fixed bottom-20 left-1/2 z-50 -translate-x-1/2 rounded-lg bg-gray-900 px-4 py-2 text-sm text-white shadow-lg md:bottom-6 dark:bg-gray-100 dark:text-gray-900">{toast}</div>
      )}
    </>
  );
}

function AlbumDialog({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: (msg?: string) => void }) {
  const [a, setA] = useState<LibAlbumDetail | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [playing, setPlaying] = useState<number | null>(null);
  const [names, setNames] = useState<string[] | null>(null);
  const [fresh, setFresh] = useState("");
  const [error, setError] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement>(null);

  const load = useCallback(() => api.music.libraryAlbum(id).then(setA).catch((e) => setError(e.message)), [id]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);

  const play = (tid: number) => {
    const el = audio.current;
    if (!el) return;
    if (playing === tid) { el.pause(); setPlaying(null); return; }
    el.src = api.music.trackUrl(tid);
    el.play().then(() => setPlaying(tid)).catch(() => setError("This browser can't play that file"));
  };
  const act = async (key: string, fn: () => Promise<string>) => {
    setBusy(key); setError(null);
    try { const m = await fn(); await load(); onChanged(m); } catch (e) { setError((e as Error).message); }
    setBusy(null);
  };
  const toggleColl = (n: string, on: boolean) => act("coll", async () => {
    const r = await api.music.albumCollections(id, on ? [n] : [], on ? [] : [n]);
    return r.msg;
  });

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 sm:items-center sm:p-4" role="dialog" aria-modal="true" aria-label={a?.title ?? "Album"}>
      <div className="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-t-2xl bg-white shadow-xl dark:bg-gray-900 sm:rounded-2xl">
        {!a ? <div className="flex justify-center p-10"><Loader2 className="h-5 w-5 animate-spin text-blue-600" /></div> : (
          <>
            <div className="flex gap-4 border-b border-gray-200 p-4 dark:border-gray-700">
              <Cover a={a} className="h-24 w-24 shrink-0 rounded-lg sm:h-32 sm:w-32" />
              <div className="min-w-0 flex-1">
                <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100">{a.title}</h3>
                <p className="text-sm text-gray-600 dark:text-gray-300">{a.artist}{a.year ? ` · ${a.year}` : ""}</p>
                <p className="text-xs text-gray-500 dark:text-gray-400">{a.tracks} tracks · {fmtLen(a.duration_sec)} · {fmtSize(a.size_bytes)} · {a.format?.toUpperCase()}</p>
                <p className="truncate font-mono text-[11px] text-gray-400" title={a.folder}>{a.folder}</p>
                <Stars value={a.rating} label={a.title ?? "album"} onChange={(n) => act("rate", async () => (await api.music.rateAlbum(id, n)).msg)} />
              </div>
              <button onClick={onClose} aria-label="Close" className="self-start rounded p-1.5 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"><X className="h-4 w-4" /></button>
            </div>

            {a.issues.length > 0 && (
              <ul className="space-y-1 border-b border-gray-200 bg-amber-50 px-4 py-2 text-sm text-amber-800 dark:border-gray-700 dark:bg-amber-950/30 dark:text-amber-200">
                {a.issues.map((i) => <li key={i} className="flex gap-2"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><span><b>{ISSUE[i][0]}.</b> {ISSUE[i][1]}</span></li>)}
              </ul>
            )}

            <div className="flex flex-wrap gap-2 px-4 pt-3">
              <button className={ghost} disabled={busy !== null} onClick={() => act("tags", async () => (await api.music.fixTags(id)).msg)}
                      title="Fill in titles, track numbers and cover art from MusicBrainz">
                {busy === "tags" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}Fix tags
              </button>
              <button className={ghost} disabled={busy !== null} onClick={() => act("org", async () => (await api.music.organise(id)).msg)}
                      title="Move into the music library as Artist / Album (Year) / 01 - Title">
                {busy === "org" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FolderInput className="h-4 w-4" />}Organise for Plex
              </button>
              <button className={ghost} disabled={busy !== null} onClick={() => (names ? setNames(null) : api.music.collectionNames().then((r) => setNames(r.names)).catch(() => setNames([])))}>
                <Tags className="h-4 w-4" />Collections
              </button>
            </div>

            {names && (
              <div className="mx-4 mt-3 rounded-lg border border-gray-200 p-3 dark:border-gray-700">
                <div className="flex flex-wrap gap-2">
                  {[...new Set([...names, ...a.collections])].map((n) => {
                    const on = a.collections.includes(n);
                    return (
                      <label key={n} className="flex cursor-pointer items-center gap-1.5 text-sm text-gray-800 dark:text-gray-200">
                        <input type="checkbox" checked={on} disabled={busy !== null} onChange={() => toggleColl(n, !on)} />{n}
                      </label>
                    );
                  })}
                </div>
                <div className="mt-2 flex gap-2">
                  <input value={fresh} onChange={(e) => setFresh(e.target.value)} placeholder="New collection" aria-label="New collection name" className={`${sel} min-w-0 flex-1`} />
                  <button className={ghost} disabled={!fresh.trim() || busy !== null} onClick={() => { toggleColl(fresh.trim(), true); setFresh(""); }}>Add</button>
                </div>
              </div>
            )}

            {error && <p className="px-4 pt-2 text-sm text-red-600 dark:text-red-400">{error}</p>}

            <ol className="divide-y divide-gray-100 p-2 dark:divide-gray-800">
              {a.track_list.map((t) => (
                <li key={t.id} className="flex items-center gap-2 px-2 py-1.5">
                  <button onClick={() => play(t.id)} aria-label={`${playing === t.id ? "Pause" : "Play"} ${t.title}`}
                          className="rounded-full p-1.5 text-blue-600 hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-950">
                    {playing === t.id ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                  </button>
                  <span className="w-8 text-right text-xs tabular-nums text-gray-400">{t.disc_no && t.disc_no > 1 ? `${t.disc_no}-` : ""}{t.track_no ?? "–"}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm text-gray-800 dark:text-gray-200">{t.title}</span>
                    {t.artist && t.artist !== a.artist && <span className="block truncate text-xs text-gray-500">{t.artist}</span>}
                  </span>
                  <span className="hidden text-xs text-gray-400 sm:inline">{t.lossless ? t.format?.toUpperCase() : t.bitrate ? `${t.bitrate} kbps` : ""}</span>
                  <span className="text-xs tabular-nums text-gray-500">{fmtLen(t.duration_sec)}</span>
                </li>
              ))}
            </ol>
            <audio ref={audio} onEnded={() => setPlaying(null)} className="hidden" />
            <div className="flex justify-end border-t border-gray-200 p-3 dark:border-gray-700">
              <button className={primary} onClick={onClose}>Done</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
