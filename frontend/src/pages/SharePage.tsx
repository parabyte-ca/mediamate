import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Disc3, Loader2, Pause, Play, SkipBack, SkipForward } from "lucide-react";
import { api, type PublicShare } from "../api/client";
import VideoPlayer, { fmtClock } from "../components/VideoPlayer";

/** A shared link (/s/<token>): watch a video or listen to an album without logging in. */
export default function SharePage() {
  const { token = "" } = useParams();
  const [meta, setMeta] = useState<PublicShare | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ok18, setOk18] = useState(false);

  useEffect(() => {
    const m = document.createElement("meta");
    m.name = "robots"; m.content = "noindex, nofollow";
    document.head.appendChild(m);
    return () => { document.head.removeChild(m); };
  }, []);
  useEffect(() => {
    api.publicShare.meta(token).then((m) => { setMeta(m); document.title = `${m.title} · MediaMate`; })
      .catch((e) => setError((e as Error).message));
  }, [token]);

  return (
    <div className="min-h-screen bg-gray-50 px-4 py-6 text-gray-900 dark:bg-gray-950 dark:text-gray-100">
      <div className="mx-auto max-w-3xl">
        <p className="mb-4 text-sm font-semibold text-blue-600 dark:text-blue-400">MediaMate</p>
        {error ? (
          <div className="rounded-xl border border-gray-200 bg-white p-8 text-center dark:border-gray-700 dark:bg-gray-900">
            <p className="font-medium">{error}</p>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">Ask whoever sent it for a new link.</p>
          </div>
        ) : !meta ? (
          <Loader2 className="mx-auto h-6 w-6 animate-spin text-gray-400" />
        ) : meta.adult && !ok18 ? (
          <div className="rounded-xl border border-gray-200 bg-white p-8 text-center dark:border-gray-700 dark:bg-gray-900">
            <p className="text-lg font-semibold">This video is for adults</p>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">It contains explicit content. Continue only if you're 18 or older.</p>
            <button onClick={() => setOk18(true)} className="mt-4 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700">I'm 18 or older, continue</button>
          </div>
        ) : (
          <>
            <h1 className="mb-1 break-words text-xl font-bold">{meta.title}</h1>
            {meta.teaser && (
              <span className="mb-2 inline-block rounded-full bg-pink-100 px-2 py-0.5 text-xs font-medium text-pink-700 dark:bg-pink-950/50 dark:text-pink-300">
                Teaser · {Math.round(meta.duration_sec ?? 30)} sec
              </span>
            )}
            <p className="mb-4 text-xs text-gray-500 dark:text-gray-400">
              {meta.expires_at ? `This link works until ${new Date(meta.expires_at + "Z").toLocaleString()}.` : "Shared with you."}
            </p>
            {meta.kind === "video" && meta.teaser && meta.ready === false ? (
              <TeaserWait token={token} onReady={setMeta} />
            ) : meta.kind === "video" && meta.teaser ? (
              <video src={api.publicShare.video(token)} controls playsInline className="w-full rounded-lg bg-black" aria-label={meta.title} />
            ) : meta.kind === "video" ? (
              <VideoPlayer original={api.publicShare.video(token)} converted={(from) => api.publicShare.play(token, from)}
                           duration={meta.duration_sec ?? 0}
                           buttonClass="flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800" />
            ) : (
              <AlbumPlayer token={token} meta={meta} />
            )}
          </>
        )}
      </div>
    </div>
  );
}

function AlbumPlayer({ token, meta }: { token: string; meta: PublicShare }) {
  const tracks = meta.tracks ?? [];
  const audio = useRef<HTMLAudioElement>(null);
  const [cur, setCur] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [cover, setCover] = useState(!!meta.has_cover);
  const go = (n: number) => { if (n >= 1 && n <= tracks.length) setCur(n); };
  useEffect(() => { if (cur != null) audio.current?.play().catch(() => {}); }, [cur]);

  return (
    <div className="overflow-hidden rounded-xl border border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900">
      <div className="flex items-center gap-4 p-4">
        {cover ? (
          <img src={api.publicShare.cover(token)} alt="" onError={() => setCover(false)} className="h-24 w-24 shrink-0 rounded-lg object-cover sm:h-32 sm:w-32" />
        ) : (
          <div className="flex h-24 w-24 shrink-0 items-center justify-center rounded-lg bg-gray-100 dark:bg-gray-800 sm:h-32 sm:w-32"><Disc3 className="h-8 w-8 text-gray-400" /></div>
        )}
        <div className="min-w-0 flex-1">
          <p className="text-sm text-gray-500 dark:text-gray-400">{tracks.length} tracks</p>
          <div className="mt-2 flex items-center gap-2">
            <button aria-label="Previous track" onClick={() => go((cur ?? 1) - 1)} className="rounded-full p-2 hover:bg-gray-100 dark:hover:bg-gray-800"><SkipBack className="h-5 w-5" /></button>
            <button aria-label={playing ? "Pause" : "Play"} onClick={() => { if (cur == null) go(1); else if (playing) audio.current?.pause(); else audio.current?.play(); }}
                    className="rounded-full bg-blue-600 p-3 text-white hover:bg-blue-700">{playing ? <Pause className="h-5 w-5" /> : <Play className="h-5 w-5" />}</button>
            <button aria-label="Next track" onClick={() => go((cur ?? 0) + 1)} className="rounded-full p-2 hover:bg-gray-100 dark:hover:bg-gray-800"><SkipForward className="h-5 w-5" /></button>
          </div>
        </div>
      </div>
      <audio ref={audio} src={cur != null ? api.publicShare.track(token, cur) : undefined} controls={cur != null}
             onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => go((cur ?? 0) + 1)}
             className={cur != null ? "w-full px-4 pb-3" : "hidden"} />
      <ol className="divide-y divide-gray-100 border-t border-gray-200 dark:divide-gray-800 dark:border-gray-700">
        {tracks.map((t) => (
          <li key={t.n}>
            <button onClick={() => go(t.n)} className={`flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-800 ${cur === t.n ? "font-semibold text-blue-600 dark:text-blue-400" : ""}`}>
              <span className="w-6 shrink-0 text-right tabular-nums text-gray-400">{t.n}</span>
              <span className="min-w-0 flex-1 truncate">{t.title}</span>
              {t.duration_sec ? <span className="shrink-0 tabular-nums text-xs text-gray-400">{fmtClock(t.duration_sec)}</span> : null}
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** A teaser link opened before its clip is made: check again every 5 seconds. */
function TeaserWait({ token, onReady }: { token: string; onReady: (m: PublicShare) => void }) {
  useEffect(() => {
    const t = setInterval(() => {
      api.publicShare.meta(token).then((m) => { if (m.ready !== false) onReady(m); }).catch(() => { /* keep waiting */ });
    }, 5000);
    return () => clearInterval(t);
  }, [token, onReady]);
  return (
    <div className="flex items-center justify-center gap-2 rounded-lg bg-gray-100 p-8 text-sm text-gray-600 dark:bg-gray-800 dark:text-gray-300">
      <Loader2 className="h-4 w-4 animate-spin" />Getting the teaser ready…
    </div>
  );
}
