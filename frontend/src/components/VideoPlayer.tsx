import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Film } from "lucide-react";

export function fmtClock(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

/** Plays the original file when the browser can; otherwise (or on request) a version
 *  MediaMate converts on the fly. The converted stream has no timeline, so the player
 *  shows its own seek bar and restarts the conversion from the chosen point. Used by
 *  the Library preview and by shared links. */
export default function VideoPlayer({ original, converted, duration, extra, buttonClass }: {
  original: string;
  converted: (from: number) => string;
  duration: number;
  extra?: ReactNode;
  buttonClass: string;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  // On close, stop the download straight away so a converting preview frees its slot
  // on the server (only two can convert at once).
  useEffect(() => () => {
    const v = videoRef.current;
    if (v) { v.pause(); v.removeAttribute("src"); v.load(); }
  }, []);
  const [mode, setMode] = useState<"original" | "converted">("original");
  const [offset, setOffset] = useState(0);        // where the conversion started (s)
  const [pos, setPos] = useState(0);              // current position in the file (s)
  const [note, setNote] = useState<string | null>(null);

  const convert = useCallback((from: number, why?: string) => {
    setMode("converted");
    setOffset(from);
    setPos(from);
    if (why) setNote(why);
  }, []);

  // The original plays but shows no picture (e.g. HEVC in a browser without it): convert.
  useEffect(() => {
    if (mode !== "original") return;
    const v = videoRef.current;
    if (!v) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const onMeta = () => {
      timer = setTimeout(() => {
        if (v.videoWidth === 0) convert(v.currentTime, "This browser can't show this file's video, so MediaMate is converting it.");
      }, 2500);
    };
    v.addEventListener("loadedmetadata", onMeta);
    return () => { v.removeEventListener("loadedmetadata", onMeta); if (timer) clearTimeout(timer); };
  }, [mode, convert]);

  const src = mode === "original" ? original : converted(offset);

  return (
    <>
      <video
        key={src}
        ref={videoRef}
        src={src}
        controls
        autoPlay
        playsInline
        className="max-h-[65vh] w-full rounded bg-black"
        onError={() => mode === "original"
          ? convert(videoRef.current?.currentTime ?? 0, "This browser can't play this file directly, so MediaMate is converting it.")
          : setNote("MediaMate couldn't convert this file.")}
        onTimeUpdate={(e) => mode === "converted" && setPos(offset + e.currentTarget.currentTime)}
      />

      {mode === "converted" && duration > 0 && (
        <div className="mt-3 flex items-center gap-3 text-xs tabular-nums text-gray-600 dark:text-gray-300">
          <span>{fmtClock(pos)}</span>
          <input type="range" min={0} max={Math.floor(duration)} step={1} value={Math.min(pos, duration)}
                 aria-label="Seek" className="flex-1 accent-blue-600"
                 onChange={(e) => setPos(Number(e.target.value))}
                 onMouseUp={(e) => convert(Number((e.target as HTMLInputElement).value))}
                 onTouchEnd={(e) => convert(Number((e.target as HTMLInputElement).value))}
                 onKeyUp={(e) => convert(Number((e.target as HTMLInputElement).value))} />
          <span>{fmtClock(duration)}</span>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-gray-500 dark:text-gray-400">
          {note ?? (mode === "original" ? "Playing the original file." : "Converted for your browser as it plays.")}
        </p>
        <div className="flex gap-2">
          {mode === "original" ? (
            <button className={buttonClass} title="Use this if there's no picture or no sound"
                    onClick={() => convert(videoRef.current?.currentTime ?? 0, "Converted for your browser as it plays.")}>
              <Film className="h-4 w-4" />Compatible playback
            </button>
          ) : (
            <button className={buttonClass} onClick={() => { setMode("original"); setNote(null); }}>Original file</button>
          )}
          {extra}
        </div>
      </div>
    </>
  );
}
