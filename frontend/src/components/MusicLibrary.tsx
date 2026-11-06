import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Disc3, FileText, FolderInput, FolderSearch, ImageDown, ImagePlus, Loader2, Pause, PencilLine, Play, Search, Tags, Upload, Wand2, X } from "lucide-react";
import { api, type AlbumTags, type CoverCandidate, type LibAlbum, type LibAlbumDetail, type MusicIssue, type TagRow } from "../api/client";
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
  const [editing, setEditing] = useState(false);
  const [coverOpen, setCoverOpen] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);

  const load = useCallback(() => api.music.libraryAlbum(id).then(setA).catch((e) => setError(e.message)), [id]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && !editing && !coverOpen && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose, editing, coverOpen]);

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
              <button className={ghost} disabled={busy !== null} onClick={() => setEditing(true)}
                      title="Change the album and track tags yourself (live recordings and bootlegs too)">
                <PencilLine className="h-4 w-4" />Edit tags
              </button>
              <button className={ghost} disabled={busy !== null} onClick={() => setCoverOpen(true)}
                      title="Upload a picture, or choose one from MusicBrainz">
                <ImagePlus className="h-4 w-4" />{a.has_cover ? "Change cover" : "Add cover"}
              </button>
              <button className={ghost} disabled={busy !== null} onClick={() => act("tags", async () => (await api.music.fixTags(id)).msg)}
                      title="Fill in titles, track numbers and cover art from MusicBrainz">
                {busy === "tags" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}Fix tags
              </button>
              {!a.has_cover && (
                <button className={ghost} disabled={busy !== null} onClick={() => act("cover", async () => (await api.music.getCover(id)).msg)}
                        title="Use the artwork inside the files, or download it from MusicBrainz. Tags stay as they are.">
                  {busy === "cover" ? <Loader2 className="h-4 w-4 animate-spin" /> : <ImageDown className="h-4 w-4" />}Get cover
                </button>
              )}
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
            {editing && <TagEditor id={id} onClose={() => setEditing(false)}
                                   onSaved={(m) => { setEditing(false); load(); onChanged(m); }} />}
            {coverOpen && <CoverDialog a={a} onClose={() => setCoverOpen(false)}
                                       onSaved={(m) => { setCoverOpen(false); load(); onChanged(m); }} />}
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


// ── Editing tags by hand ──────────────────────────────────────────────────────

const field = "w-full rounded-lg border border-gray-300 bg-white px-2.5 py-1.5 text-sm text-gray-900 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100";
const label = "mb-1 block text-xs font-medium text-gray-600 dark:text-gray-300";

function Overlay({ title, onClose, children, wide }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-[60] flex items-end justify-center bg-black/50 sm:items-center sm:p-4" role="dialog" aria-modal="true" aria-label={title}>
      <div className={`flex max-h-[94vh] w-full ${wide ? "max-w-4xl" : "max-w-xl"} flex-col rounded-t-2xl bg-white shadow-xl dark:bg-gray-900 sm:rounded-2xl`}>
        <div className="flex items-center gap-2 border-b border-gray-200 px-4 py-3 dark:border-gray-700">
          <h3 className="min-w-0 flex-1 truncate font-semibold text-gray-900 dark:text-gray-100">{title}</h3>
          <button onClick={onClose} aria-label="Close" className="rounded p-1.5 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"><X className="h-4 w-4" /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

function TagEditor({ id, onClose, onSaved }: { id: number; onClose: () => void; onSaved: (msg: string) => void }) {
  const [data, setData] = useState<AlbumTags | null>(null);
  const [album, setAlbum] = useState<AlbumTags["album"] | null>(null);
  const [rows, setRows] = useState<TagRow[]>([]);
  const [isLive, setIsLive] = useState(false);
  const [live, setLive] = useState({ date: "", venue: "", city: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    api.music.tags(id).then((d) => { setData(d); setAlbum(d.album); setRows(d.tracks); })
      .catch((e) => setError(e.message));
  }, [id]);

  const setRow = (i: number, p: Partial<TagRow>) => setRows((r) => r.map((x, n) => (n === i ? { ...x, ...p } : x)));
  const fromNames = async () => {
    setError(null);
    try {
      const g = new Map((await api.music.fromNames(id)).map((x) => [x.path, x]));
      let n = 0;
      setRows((r) => r.map((x) => {
        const y = g.get(x.path);
        if (!y) return x;
        n += 1;
        return { ...x, title: y.title ?? x.title, track_no: y.track_no ?? x.track_no,
                 disc_no: y.disc_no ?? x.disc_no, artist: y.artist ?? x.artist };
      }));
      setNote(`Filled from ${n} file name${n === 1 ? "" : "s"}. Check them, then Save.`);
    } catch (e) { setError((e as Error).message); }
  };
  const numberInOrder = () => setRows((r) => {
    const count: Record<number, number> = {};
    return r.map((x) => { const d = x.disc_no || 1; count[d] = (count[d] ?? 0) + 1; return { ...x, track_no: count[d] }; });
  });
  const liveTitle = [live.date.trim(), [live.venue.trim(), live.city.trim()].filter(Boolean).join(", ")].filter(Boolean).join(" ");
  const save = async () => {
    if (!album) return;
    setBusy(true); setError(null);
    try {
      const r = await api.music.saveTags(id, {
        album: { ...album, album: isLive && !album.album.trim() ? "" : album.album },
        tracks: rows.map(({ path, title, artist, track_no, disc_no }) => ({ path, title, artist, track_no, disc_no })),
        live: isLive ? live : null,
      });
      if (!r.ok && !r.saved) setError(r.msg); else onSaved(r.msg);
    } catch (e) { setError((e as Error).message); }
    setBusy(false);
  };

  return (
    <Overlay title="Edit tags" onClose={onClose} wide>
      {!data || !album ? (
        <div className="flex justify-center p-10">{error ? <p className="text-sm text-red-600">{error}</p> : <Loader2 className="h-5 w-5 animate-spin text-blue-600" />}</div>
      ) : (
        <>
          <div className="space-y-4 overflow-y-auto p-4">
            <section className="grid gap-3 sm:grid-cols-2">
              <div><label className={label} htmlFor="t-aa">Album artist</label>
                <input id="t-aa" className={field} value={album.album_artist} onChange={(e) => setAlbum({ ...album, album_artist: e.target.value })} /></div>
              <div><label className={label} htmlFor="t-al">Album{isLive && " (blank: built from the live details)"}</label>
                <input id="t-al" className={field} value={album.album} placeholder={isLive ? liveTitle : ""} onChange={(e) => setAlbum({ ...album, album: e.target.value })} /></div>
              <div><label className={label} htmlFor="t-dt">Date or year</label>
                <input id="t-dt" className={field} value={album.date} placeholder="2020 or 2020-06-18" onChange={(e) => setAlbum({ ...album, date: e.target.value })} disabled={isLive} /></div>
              <div><label className={label} htmlFor="t-gn">Genre</label>
                <input id="t-gn" className={field} value={album.genre} onChange={(e) => setAlbum({ ...album, genre: e.target.value })} /></div>
            </section>
            {data.mixed.length > 0 && (
              <p className="text-xs text-amber-700 dark:text-amber-300">The tracks don't all agree on: {data.mixed.map((m) => m.replace("_", " ")).join(", ")}. Saving gives every track the values above.</p>
            )}

            <section className="rounded-lg border border-gray-200 p-3 dark:border-gray-700">
              <label className="flex cursor-pointer items-center gap-2 text-sm font-medium text-gray-800 dark:text-gray-200">
                <input type="checkbox" checked={isLive} onChange={(e) => setIsLive(e.target.checked)} />Live recording or bootleg
              </label>
              {isLive && (
                <div className="mt-3 grid gap-3 sm:grid-cols-3">
                  <div><label className={label} htmlFor="t-ld">Show date</label>
                    <input id="t-ld" type="date" className={field} value={live.date} onChange={(e) => setLive({ ...live, date: e.target.value })} /></div>
                  <div><label className={label} htmlFor="t-lv">Venue</label>
                    <input id="t-lv" className={field} value={live.venue} placeholder="Barton Hall" onChange={(e) => setLive({ ...live, venue: e.target.value })} /></div>
                  <div><label className={label} htmlFor="t-lc">City</label>
                    <input id="t-lc" className={field} value={live.city} placeholder="Ithaca NY" onChange={(e) => setLive({ ...live, city: e.target.value })} /></div>
                  <p className="text-xs text-gray-500 sm:col-span-3 dark:text-gray-400">
                    Saves the show date, a comment (“Live at {[live.venue, live.city].filter(Boolean).join(", ") || "…"}”) and, if Album is blank, the album title <b>{liveTitle || "…"}</b>.
                  </p>
                </div>
              )}
            </section>

            <section>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <h4 className="mr-auto text-sm font-semibold text-gray-800 dark:text-gray-200">Tracks</h4>
                <button className={ghost} onClick={fromNames} title="Use titles and numbers from file names like ‘03 - Song.flac’ or ‘d1t03’"><FileText className="h-4 w-4" />Fill from file names</button>
                <button className={ghost} onClick={numberInOrder} title="Number the tracks 1, 2, 3… in the order shown, per disc">Number in order</button>
              </div>
              {note && <p className="mb-2 text-xs text-blue-700 dark:text-blue-300">{note}</p>}
              <div className="hidden grid-cols-[3.5rem_3.5rem_1fr_1fr] gap-2 px-1 pb-1 text-xs text-gray-500 sm:grid dark:text-gray-400">
                <span>Disc</span><span>Track</span><span>Title</span><span>Artist (blank: album artist)</span>
              </div>
              <ul className="divide-y divide-gray-100 dark:divide-gray-800">
                {rows.map((r, i) => (
                  <li key={r.path} className="grid grid-cols-[3.5rem_3.5rem_1fr] gap-2 py-1.5 sm:grid-cols-[3.5rem_3.5rem_1fr_1fr]">
                    <input aria-label={`Disc for ${r.file}`} placeholder="Disc" inputMode="numeric" className={`${field} self-start`} value={r.disc_no ?? ""} onChange={(e) => setRow(i, { disc_no: e.target.value ? Number(e.target.value.replace(/\D/g, "")) || null : null })} />
                    <input aria-label={`Track for ${r.file}`} placeholder="#" inputMode="numeric" className={`${field} self-start`} value={r.track_no ?? ""} onChange={(e) => setRow(i, { track_no: e.target.value ? Number(e.target.value.replace(/\D/g, "")) || null : null })} />
                    <div className="min-w-0">
                      <input aria-label={`Title for ${r.file}`} placeholder="Title" className={field} value={r.title} onChange={(e) => setRow(i, { title: e.target.value })} />
                      <span className="mt-0.5 block truncate font-mono text-[10px] text-gray-400" title={r.file}>{r.file}</span>
                    </div>
                    <input aria-label={`Artist for ${r.file}`} className={`${field} col-span-3 sm:col-span-1`} value={r.artist} placeholder={album.album_artist || "Artist"} onChange={(e) => setRow(i, { artist: e.target.value })} />
                  </li>
                ))}
              </ul>
            </section>
            {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
          </div>
          <div className="flex items-center justify-end gap-2 border-t border-gray-200 p-3 dark:border-gray-700">
            <span className="mr-auto hidden text-xs text-gray-500 sm:inline dark:text-gray-400">Saved into the files. Run Organise for Plex afterwards to rename them.</span>
            <button className={ghost} onClick={onClose}>Cancel</button>
            <button className={primary} disabled={busy} onClick={save}>{busy && <Loader2 className="h-4 w-4 animate-spin" />}Save tags</button>
          </div>
        </>
      )}
    </Overlay>
  );
}

// ── Changing the cover by hand ────────────────────────────────────────────────

function CoverDialog({ a, onClose, onSaved }: { a: LibAlbum; onClose: () => void; onSaved: (msg: string) => void }) {
  const [tab, setTab] = useState<"upload" | "mb">("upload");
  const [embed, setEmbed] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [q, setQ] = useState(`${a.artist ?? ""} ${a.title ?? ""}`.trim());
  const [found, setFound] = useState<CoverCandidate[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);
  const pick = (f: File | null) => {
    setError(null);
    if (f && !/^image\/(jpeg|png)$/.test(f.type)) { setError("Choose a JPEG or PNG image"); return; }
    setFile(f);
    setPreview(f ? URL.createObjectURL(f) : null);
  };
  const search = async (e?: React.FormEvent) => {
    e?.preventDefault();
    setBusy(true); setError(null);
    try { setFound(await api.music.coverCandidates(a.id, q)); } catch (err) { setError((err as Error).message); }
    setBusy(false);
  };
  useEffect(() => { if (tab === "mb" && found === null) search(); }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps
  const run = async (fn: () => Promise<{ msg: string }>) => {
    setBusy(true); setError(null);
    try { onSaved((await fn()).msg); } catch (err) { setError((err as Error).message); setBusy(false); }
  };

  return (
    <Overlay title={`Cover for ${a.title ?? "this album"}`} onClose={onClose}>
      <div className="space-y-4 overflow-y-auto p-4">
        <div className="inline-flex rounded-lg border border-gray-200 p-0.5 dark:border-gray-700" role="tablist">
          {([["upload", "Upload a picture"], ["mb", "Choose from MusicBrainz"]] as const).map(([k, l]) => (
            <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
                    className={`rounded-md px-3 py-1.5 text-sm font-medium ${tab === k ? "bg-blue-600 text-white" : "text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"}`}>{l}</button>
          ))}
        </div>

        {tab === "upload" ? (
          <div className="space-y-3">
            <label htmlFor="cover-file"
                   onDragOver={(e) => e.preventDefault()}
                   onDrop={(e) => { e.preventDefault(); pick(e.dataTransfer.files?.[0] ?? null); }}
                   className="flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed border-gray-300 p-6 text-center text-sm text-gray-600 hover:border-blue-400 dark:border-gray-600 dark:text-gray-300">
              {preview ? <img src={preview} alt="New cover" className="h-40 w-40 rounded-lg object-cover" />
                       : <Upload className="h-6 w-6 text-gray-400" />}
              <span>{file ? file.name : "Choose or drop a JPEG or PNG (square works best)"}</span>
            </label>
            <input id="cover-file" type="file" accept="image/jpeg,image/png" className="sr-only" onChange={(e) => pick(e.target.files?.[0] ?? null)} />
          </div>
        ) : (
          <div className="space-y-3">
            <form onSubmit={search} className="flex gap-2">
              <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search MusicBrainz" placeholder="Artist and album, e.g. Grateful Dead Cornell" className={`${field} min-w-0 flex-1`} />
              <button type="submit" className={ghost} disabled={busy}>{busy && !found ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}Search</button>
            </form>
            {found && found.length === 0 && <p className="text-sm text-gray-500">Nothing found. Try fewer words, or upload a picture instead.</p>}
            {found && found.length > 0 && (
              <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {found.map((c) => (
                  <li key={c.mbid}>
                    <button type="button" disabled={busy} onClick={() => run(() => api.music.coverFromMusicBrainz(a.id, c.mbid, embed))}
                            className="block w-full overflow-hidden rounded-lg border border-gray-200 text-left hover:border-blue-500 disabled:opacity-50 dark:border-gray-700"
                            aria-label={`Use the cover of ${c.title} by ${c.artist}`}>
                      <CandidateArt src={c.cover} />
                      <span className="block p-1.5">
                        <span className="block truncate text-xs font-medium text-gray-800 dark:text-gray-200">{c.title}</span>
                        <span className="block truncate text-[11px] text-gray-500">{[c.artist, c.date.slice(0, 4), c.type].filter(Boolean).join(" · ")}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <label className="flex items-start gap-2 text-sm text-gray-700 dark:text-gray-300">
          <input type="checkbox" checked={embed} onChange={(e) => setEmbed(e.target.checked)} className="mt-0.5" />
          <span>Also put it inside each track, so the art travels with the files (Plex and phones show it too)</span>
        </label>
        {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-gray-200 p-3 dark:border-gray-700">
        {a.has_cover && (
          <button className={`${ghost} mr-auto`} disabled={busy} onClick={() => run(() => api.music.embedCover(a.id))}
                  title="Put the current cover inside the tracks">Put current cover in files</button>
        )}
        <button className={ghost} onClick={onClose}>Cancel</button>
        {tab === "upload" && (
          <button className={primary} disabled={!file || busy} onClick={() => file && run(() => api.music.uploadCover(a.id, file, embed))}>
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}Use this picture
          </button>
        )}
      </div>
    </Overlay>
  );
}

/** A MusicBrainz cover thumbnail; releases without art show a placeholder. */
function CandidateArt({ src }: { src: string }) {
  const [bad, setBad] = useState(false);
  return bad
    ? <span className="flex aspect-square w-full items-center justify-center bg-gray-100 text-xs text-gray-400 dark:bg-gray-800">No cover</span>
    : <img src={src} alt="" loading="lazy" onError={() => setBad(true)} className="aspect-square w-full object-cover" />;
}
