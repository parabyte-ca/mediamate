import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Disc3, Loader2, Pause, Play, SkipBack, SkipForward, X } from "lucide-react";
import { api } from "../api/client";
import { fmtClock } from "./VideoPlayer";

/** One track in the play queue, with what the lock screen and car need to show it. */
export interface QueueTrack {
  id: number; title: string; artist: string | null; album: string | null; albumId: number;
  hasCover: boolean; duration: number | null;
}

interface PlayerApi {
  queue: QueueTrack[];
  index: number;
  playing: boolean;
  loading: boolean;
  current: QueueTrack | null;
  /** Play a library album from the start, or from one of its tracks. */
  playAlbum: (albumId: number, fromTrackId?: number) => Promise<void>;
  toggle: () => void;
  next: () => void;
  prev: () => void;
  stop: () => void;
}

const Ctx = createContext<PlayerApi | null>(null);

export function usePlayer(): PlayerApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePlayer outside PlayerProvider");
  return v;
}

/** Plays music anywhere in the app (it keeps going while you move between pages), and
 *  hands the song details and controls to the phone (lock screen, notification,
 *  Bluetooth and car displays) through the Media Session API. */
export function PlayerProvider({ children }: { children: React.ReactNode }) {
  const audio = useRef<HTMLAudioElement | null>(null);
  if (audio.current === null && typeof Audio !== "undefined") {
    audio.current = new Audio();
    audio.current.preload = "auto";
  }
  const [queue, setQueue] = useState<QueueTrack[]>([]);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);
  const [pos, setPos] = useState(0);
  const [dur, setDur] = useState(0);
  const current = queue[index] ?? null;

  const start = useCallback((q: QueueTrack[], i: number) => {
    const el = audio.current;
    if (!el || !q[i]) return;
    el.src = api.music.trackUrl(q[i].id);
    el.play().catch(() => setPlaying(false));
  }, []);

  const playAlbum = useCallback(async (albumId: number, fromTrackId?: number) => {
    setLoading(true);
    try {
      const a = await api.music.libraryAlbum(albumId);
      const q: QueueTrack[] = a.track_list.map((t) => ({
        id: t.id, title: t.title, artist: t.artist || a.artist, album: a.title, albumId: a.id,
        hasCover: a.has_cover, duration: t.duration_sec,
      }));
      const i = Math.max(0, fromTrackId ? q.findIndex((t) => t.id === fromTrackId) : 0);
      setQueue(q); setIndex(i); start(q, i);
    } finally {
      setLoading(false);
    }
  }, [start]);

  const go = useCallback((i: number) => {
    if (i < 0 || i >= queue.length) return;
    setIndex(i); start(queue, i);
  }, [queue, start]);
  const next = useCallback(() => go(index + 1), [go, index]);
  const prev = useCallback(() => {
    const el = audio.current;
    if (el && el.currentTime > 3) { el.currentTime = 0; return; }   // like every music app: restart first
    go(index - 1);
  }, [go, index]);
  const toggle = useCallback(() => {
    const el = audio.current;
    if (!el || !current) return;
    if (el.paused) el.play().catch(() => {}); else el.pause();
  }, [current]);
  const stop = useCallback(() => {
    const el = audio.current;
    if (el) { el.pause(); el.removeAttribute("src"); el.load(); }
    setQueue([]); setIndex(0); setPlaying(false);
    if ("mediaSession" in navigator) { navigator.mediaSession.metadata = null; navigator.mediaSession.playbackState = "none"; }
  }, []);

  // Audio element events.
  useEffect(() => {
    const el = audio.current;
    if (!el) return;
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onTime = () => {
      setPos(el.currentTime);
      if ("mediaSession" in navigator && el.duration && isFinite(el.duration)) {
        try { navigator.mediaSession.setPositionState({ duration: el.duration, position: Math.min(el.currentTime, el.duration), playbackRate: el.playbackRate }); }
        catch { /* not supported */ }
      }
    };
    const onMeta = () => setDur(isFinite(el.duration) ? el.duration : 0);
    const onEnded = () => { if (index + 1 < queue.length) go(index + 1); else setPlaying(false); };
    el.addEventListener("play", onPlay); el.addEventListener("pause", onPause);
    el.addEventListener("timeupdate", onTime); el.addEventListener("loadedmetadata", onMeta);
    el.addEventListener("ended", onEnded);
    return () => {
      el.removeEventListener("play", onPlay); el.removeEventListener("pause", onPause);
      el.removeEventListener("timeupdate", onTime); el.removeEventListener("loadedmetadata", onMeta);
      el.removeEventListener("ended", onEnded);
    };
  }, [index, queue, go]);

  // Song details and controls for the lock screen, Bluetooth and the car.
  useEffect(() => {
    if (!("mediaSession" in navigator) || !current) return;
    const art = current.hasCover
      ? [96, 256, 512].map((n) => ({ src: new URL(api.music.coverUrl(current.albumId), window.location.href).href, sizes: `${n}x${n}`, type: "image/jpeg" }))
      : [];
    navigator.mediaSession.metadata = new MediaMetadata({
      title: current.title, artist: current.artist ?? "", album: current.album ?? "", artwork: art,
    });
  }, [current]);
  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    navigator.mediaSession.playbackState = playing ? "playing" : current ? "paused" : "none";
  }, [playing, current]);
  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    const ms = navigator.mediaSession;
    const el = audio.current;
    const set = (a: MediaSessionAction, h: MediaSessionActionHandler | null) => { try { ms.setActionHandler(a, h); } catch { /* unsupported */ } };
    set("play", () => { el?.play().catch(() => {}); });
    set("pause", () => el?.pause());
    set("nexttrack", index + 1 < queue.length ? () => next() : null);
    set("previoustrack", queue.length ? () => prev() : null);
    set("seekto", (d) => { if (el && d.seekTime != null) el.currentTime = d.seekTime; });
    set("seekbackward", (d) => { if (el) el.currentTime = Math.max(0, el.currentTime - (d.seekOffset || 10)); });
    set("seekforward", (d) => { if (el) el.currentTime = el.currentTime + (d.seekOffset || 10); });
    set("stop", () => stop());
  }, [index, queue, next, prev, stop]);

  // Start loading the next track early, so albums play without a gap.
  useEffect(() => {
    const n = queue[index + 1];
    if (!n || !playing) return;
    const link = document.createElement("link");
    link.rel = "prefetch"; link.href = api.music.trackUrl(n.id);
    document.head.appendChild(link);
    return () => { link.remove(); };
  }, [queue, index, playing]);

  const value = useMemo<PlayerApi>(() => ({ queue, index, playing, loading, current, playAlbum, toggle, next, prev, stop }),
    [queue, index, playing, loading, current, playAlbum, toggle, next, prev, stop]);

  return (
    <Ctx.Provider value={value}>
      {children}
      {current && <PlayerBar pos={pos} dur={dur || current.duration || 0} onSeek={(t) => { if (audio.current) audio.current.currentTime = t; }} />}
    </Ctx.Provider>
  );
}

/** Room at the bottom of a page so the player bar never covers its last row. */
export function PlayerSpacer() {
  const p = useContext(Ctx);
  return p?.current ? <div aria-hidden className="h-20" /> : null;
}

function PlayerBar({ pos, dur, onSeek }: { pos: number; dur: number; onSeek: (t: number) => void }) {
  const { current, playing, toggle, next, prev, stop, queue, index } = usePlayer();
  if (!current) return null;
  const btn = "rounded-full p-2 text-gray-700 hover:bg-gray-100 disabled:opacity-30 dark:text-gray-200 dark:hover:bg-gray-800";
  return (
    <div role="region" aria-label="Music player"
         className="fixed inset-x-0 z-40 border-t border-gray-200 bg-white/95 px-3 py-2 shadow-lg backdrop-blur dark:border-gray-700 dark:bg-gray-900/95 md:inset-x-auto md:right-4 md:w-[26rem] md:rounded-2xl md:border"
         style={{ bottom: "var(--player-bottom)" } as React.CSSProperties}>
      <div className="flex items-center gap-2">
        {current.hasCover
          ? <img src={api.music.coverUrl(current.albumId)} alt="" className="h-10 w-10 shrink-0 rounded object-cover" />
          : <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded bg-gray-100 dark:bg-gray-800"><Disc3 className="h-5 w-5 text-gray-400" /></div>}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100" data-testid="now-playing">{current.title}</p>
          <p className="truncate text-xs text-gray-500 dark:text-gray-400">{[current.artist, current.album].filter(Boolean).join(" · ")}</p>
        </div>
        <button className={btn} onClick={prev} aria-label="Previous track"><SkipBack className="h-4 w-4" /></button>
        <button className={`${btn} bg-blue-600 text-white hover:bg-blue-700 dark:text-white dark:hover:bg-blue-700`} onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
          {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
        </button>
        <button className={btn} onClick={next} disabled={index + 1 >= queue.length} aria-label="Next track"><SkipForward className="h-4 w-4" /></button>
        <button className={btn} onClick={stop} aria-label="Close player"><X className="h-4 w-4" /></button>
      </div>
      <div className="mt-1 flex items-center gap-2 text-[11px] tabular-nums text-gray-500">
        <span>{fmtClock(pos)}</span>
        <input type="range" min={0} max={Math.max(1, Math.floor(dur))} value={Math.min(Math.floor(pos), Math.floor(dur) || 0)}
               onChange={(e) => onSeek(Number(e.target.value))} aria-label="Position" className="min-w-0 flex-1 accent-blue-600" />
        <span>{fmtClock(dur)}</span>
        <span className="hidden sm:inline">{index + 1}/{queue.length}</span>
      </div>
    </div>
  );
}

/** A small round play button for album tiles and rows. */
export function PlayAlbumButton({ albumId, label, className = "" }: { albumId: number; label: string; className?: string }) {
  const p = usePlayer();
  const [busy, setBusy] = useState(false);
  const mine = p.current?.albumId === albumId;
  return (
    <button type="button" aria-label={mine && p.playing ? `Pause ${label}` : `Play ${label}`}
            onClick={async (e) => {
              e.stopPropagation();
              if (mine) { p.toggle(); return; }
              setBusy(true);
              try { await p.playAlbum(albumId); } finally { setBusy(false); }
            }}
            className={`flex items-center justify-center rounded-full bg-blue-600 text-white shadow hover:bg-blue-700 ${className}`}>
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : mine && p.playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
    </button>
  );
}
