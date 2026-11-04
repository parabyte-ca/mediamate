import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight, X } from "lucide-react";

// A short guided tour for new users (household members): what each part of the
// app is for, highlighted one at a time. Targets are elements with data-tour="…";
// when one isn't on screen (a phone has no sidebar), the step is shown in the middle.

export const TOUR_KEY = "sm-tour";
/** Bump when the tour changes enough that everyone should see it again. */
export const TOUR_VERSION = "2";
export const TOUR_EVENT = "screenmate:tour";

type Step = {
  /** data-tour values to try, first visible one wins (desktop vs phone). */
  targets?: string[];
  title: string;
  body: string;
};

const STEPS: Step[] = [
  {
    title: "Welcome to ScreenMate",
    body: "ScreenMate finds shows and movies, downloads them, and gets them ready to watch. Here's a quick look around.",
  },
  {
    targets: ["nav-search"],
    title: "Search",
    body: "Look up a show or movie, then pick a result to download it.",
  },
  {
    targets: ["nav-watchlist"],
    title: "Watchlist",
    body: "Follow a show and new episodes download by themselves. Add a movie and it downloads once it's out. TV shows and Movies have their own tabs.",
  },
  {
    targets: ["nav-browse", "nav-more"],
    title: "Browse",
    body: "See what's airing and what's coming to cinemas. Add a show, or a movie that isn't out yet: it downloads when it's released digitally.",
  },
  {
    targets: ["nav-calendar", "nav-more"],
    title: "Calendar",
    body: "See what's airing and what's coming up. On a phone, Calendar, Insights, History and Settings are under More.",
  },
  {
    targets: ["nav-activity"],
    title: "Activity",
    body: "Watch downloads and processing as they happen. Tap one to see its details.",
  },
  {
    targets: ["status"],
    title: "At a glance",
    body: "A quick count of what's running right now, and anything that needs a look.",
  },
  {
    targets: ["nav-library"],
    title: "Library",
    body: "Everything ScreenMate has found. Tap a picture to play it.",
  },
  {
    targets: ["jump"],
    title: "Jump to anything",
    body: "Search shows, files, pages and actions from one box. On a keyboard, press Ctrl+K (⌘K on a Mac).",
  },
  {
    targets: ["themes"],
    title: "Make it yours",
    body: "Pick colours and light or dark. It's saved on this device. You'll also find it in Settings → Appearance (under More on a phone).",
  },
  {
    title: "You're all set",
    body: "Replay this tour any time from Settings → Appearance, or search for “tour”.",
  },
];

export function tourSeen(): boolean {
  try { return localStorage.getItem(TOUR_KEY) === TOUR_VERSION; } catch { return true; }
}

function markSeen() {
  try { localStorage.setItem(TOUR_KEY, TOUR_VERSION); } catch { /* private mode: it may show again */ }
}

/** Ask the tour to start (Settings button, command palette). */
export function startTour() {
  window.dispatchEvent(new Event(TOUR_EVENT));
}

function visible(el: Element): boolean {
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

function findTarget(keys?: string[]): HTMLElement | null {
  for (const k of keys ?? []) {
    for (const el of document.querySelectorAll<HTMLElement>(`[data-tour="${k}"]`)) {
      if (visible(el)) return el;
    }
  }
  return null;
}

const PAD = 6;          // space around the highlighted element
const GAP = 12;         // space between it and the card
const CARD_W = 320;

type Box = { top: number; left: number; width: number; height: number };

export default function Tour({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [i, setI] = useState(0);
  const [box, setBox] = useState<Box | null>(null);
  const [vw, setVw] = useState(() => window.innerWidth);
  const [vh, setVh] = useState(() => window.innerHeight);
  const cardRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const step = STEPS[i];
  const last = i === STEPS.length - 1;

  useEffect(() => { if (open) setI(0); }, [open]);

  const finish = useCallback(() => {
    markSeen();
    onClose();
  }, [onClose]);

  // Find and follow the highlighted element.
  useLayoutEffect(() => {
    if (!open) return;
    let frame = 0;
    const measure = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        setVw(window.innerWidth);
        setVh(window.innerHeight);
        const el = findTarget(step.targets);
        if (!el) { setBox(null); return; }
        const r = el.getBoundingClientRect();
        setBox({ top: r.top - PAD, left: r.left - PAD, width: r.width + PAD * 2, height: r.height + PAD * 2 });
      });
    };
    findTarget(step.targets)?.scrollIntoView({ block: "nearest", inline: "nearest" });
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open, i, step.targets]);

  // Keyboard: Esc skips, arrows move. Focus goes to the card for screen readers.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); finish(); }
      else if (e.key === "ArrowRight") { e.preventDefault(); last ? finish() : setI((n) => n + 1); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); setI((n) => Math.max(0, n - 1)); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, last, finish]);

  useEffect(() => { if (open) cardRef.current?.focus(); }, [open, i]);

  if (!open) return null;

  const done = () => { finish(); navigate("/search"); };
  const phone = vw < 640;

  // Card position: beside the target where there's room, else below/above it; on
  // phones a sheet docked above the bottom tab bar.
  let cardStyle: React.CSSProperties;
  if (!box) {
    cardStyle = { top: "50%", left: "50%", transform: "translate(-50%, -50%)", width: Math.min(CARD_W, vw - 32) };
  } else if (phone) {
    const targetLow = box.top > vh / 2;
    cardStyle = targetLow
      ? { left: 16, right: 16, bottom: Math.max(16, vh - box.top + GAP) }
      : { left: 16, right: 16, top: box.top + box.height + GAP };
  } else if (box.left + box.width + GAP + CARD_W < vw - 16) {
    cardStyle = { left: box.left + box.width + GAP, top: Math.min(Math.max(16, box.top), vh - 220), width: CARD_W };
  } else {
    const below = box.top + box.height + GAP;
    cardStyle = below + 200 < vh
      ? { top: below, left: Math.min(Math.max(16, box.left), vw - CARD_W - 16), width: CARD_W }
      : { bottom: vh - box.top + GAP, left: Math.min(Math.max(16, box.left), vw - CARD_W - 16), width: CARD_W };
  }

  return (
    <div className="fixed inset-0 z-[60]" aria-hidden={false}>
      {/* Dim everything except the highlighted element (or everything, for a centred step). */}
      {box ? (
        <div
          className="pointer-events-none fixed rounded-xl ring-2 ring-blue-500 transition-all duration-200 motion-reduce:transition-none"
          style={{ ...box, boxShadow: "0 0 0 9999px rgb(0 0 0 / 0.55)" }}
        />
      ) : (
        <div className="fixed inset-0 bg-black/55" />
      )}
      {/* Clicks outside the card do nothing, so the tour isn't lost by accident. */}
      <div className="fixed inset-0" onClick={(e) => e.stopPropagation()} />

      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="tour-title"
        aria-describedby="tour-body"
        tabIndex={-1}
        className="fixed rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4 shadow-2xl outline-none"
        style={cardStyle}
      >
        <div className="mb-1 flex items-start gap-2">
          <h2 id="tour-title" className="flex-1 font-semibold text-gray-900 dark:text-gray-100">{step.title}</h2>
          <button onClick={finish} aria-label="Skip tour"
                  className="rounded-md p-1 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-600 dark:hover:text-gray-300">
            <X className="h-4 w-4" />
          </button>
        </div>
        <p id="tour-body" aria-live="polite" className="text-sm text-gray-600 dark:text-gray-300">{step.body}</p>

        <div className="mt-4 flex items-center gap-2">
          <span className="text-xs tabular-nums text-gray-400" aria-label={`Step ${i + 1} of ${STEPS.length}`}>
            {i + 1} of {STEPS.length}
          </span>
          <div className="flex-1" />
          {i === 0 ? (
            <button onClick={finish}
                    className="rounded-lg px-3 py-1.5 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800">
              Skip
            </button>
          ) : (
            <button onClick={() => setI(i - 1)}
                    className="flex items-center gap-1 rounded-lg px-3 py-1.5 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800">
              <ArrowLeft className="h-3.5 w-3.5" />Back
            </button>
          )}
          <button onClick={() => (last ? done() : setI(i + 1))}
                  className="flex items-center gap-1 rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700">
            {i === 0 ? "Show me" : last ? "Done" : "Next"}
            {!last && <ArrowRight className="h-3.5 w-3.5" />}
          </button>
        </div>
      </div>
    </div>
  );
}
