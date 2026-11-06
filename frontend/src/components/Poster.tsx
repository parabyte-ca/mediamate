import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Tv, X } from "lucide-react";

/** The same image at a size fit for a preview (TMDB and TVMaze serve several). */
export function largePosterUrl(url: string): string {
  return url
    .replace(/(image\.tmdb\.org\/t\/p\/)w\d+\//, "$1w780/")
    .replace(/\/uploads\/images\/medium_portrait\//, "/uploads/images/original_untouched/");
}

/** Full-size poster over the page; Esc, the backdrop or X closes it. */
export function PosterLightbox({ url, title, onClose }: { url: string; title: string; onClose: () => void }) {
  const [src, setSrc] = useState(() => largePosterUrl(url));
  const [loaded, setLoaded] = useState(false);
  const closeBtn = useRef<HTMLButtonElement>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    closeBtn.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); close.current(); }
      if (e.key === "Tab") { e.preventDefault(); closeBtn.current?.focus(); }  // the only control
    };
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      opener?.focus?.();
    };
  }, []);

  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4" role="dialog" aria-modal="true"
         aria-label={`${title} poster`}
         onClick={(e) => { e.stopPropagation(); onClose(); }}>
      <div className="absolute inset-0 bg-black/80" aria-hidden />
      <figure className="relative flex max-h-full flex-col items-center" onClick={(e) => e.stopPropagation()}>
        <button ref={closeBtn} onClick={onClose} aria-label="Close preview"
                className="absolute -right-2 -top-2 z-10 rounded-full bg-white/90 p-1.5 text-gray-900 shadow hover:bg-white focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
          <X className="h-4 w-4" />
        </button>
        <img src={src} alt={`${title} poster`} referrerPolicy="no-referrer"
             onLoad={() => setLoaded(true)}
             onError={() => { if (src !== url) setSrc(url); }}
             style={{ backgroundImage: loaded ? undefined : `url("${url}")` }}
             className="max-h-[80vh] w-auto max-w-[min(90vw,32rem)] rounded-lg bg-gray-800 bg-cover bg-center object-contain shadow-2xl" />
        <figcaption className="mt-3 max-w-[min(90vw,32rem)] text-center text-sm font-medium text-white">{title}</figcaption>
      </figure>
    </div>,
    document.body,
  );
}

/** Show poster that degrades to a placeholder icon when missing or broken.
 *  Clicking it opens a larger preview unless `zoomable` is false. */
export default function Poster({ url, title, className = "h-16 w-11", zoomable = true, icon: Icon = Tv }: {
  url?: string | null;
  title: string;
  className?: string;
  zoomable?: boolean;
  /** Shown when there's no picture (a TV by default). */
  icon?: typeof Tv;
}) {
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);
  if (!url || failed) {
    return (
      <div
        className={`flex shrink-0 items-center justify-center rounded-md bg-gray-100 dark:bg-gray-800 text-gray-300 dark:text-gray-600 ${className}`}
        aria-hidden
      >
        <Icon className="h-1/3 w-1/3" />
      </div>
    );
  }
  const img = (
    <img
      src={url}
      alt={`${title} poster`}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className={`shrink-0 rounded-md object-cover bg-gray-100 dark:bg-gray-800 ${zoomable ? "h-full w-full" : className}`}
    />
  );
  if (!zoomable) return img;
  return (
    <>
      <button type="button" aria-label={`Enlarge ${title} poster`}
              onClick={(e) => { e.stopPropagation(); e.preventDefault(); setOpen(true); }}
              className={`shrink-0 cursor-zoom-in overflow-hidden rounded-md transition hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${className}`}>
        {img}
      </button>
      {open && <PosterLightbox url={url} title={title} onClose={() => setOpen(false)} />}
    </>
  );
}
