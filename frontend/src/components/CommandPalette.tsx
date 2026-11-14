import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity, ArrowUpCircle, CalendarDays, Clock, Compass, CornerDownLeft, Film, FolderSearch, Monitor, Moon,
  Palette, Pause, Play, Radar, Search, Settings, Sun, Trash2, TrendingUp, Tv, type LucideIcon,
} from "lucide-react";
import { api, type Journey, type MediaFile, type WatchlistItem } from "../api/client";
import { PALETTES, useTheme } from "../ThemeContext";
import { startTour } from "./Tour";

// Ctrl/⌘+K (or "/") opens a palette that finds pages, actions, shows, library
// files and recent activity. "g" then a letter jumps straight to a page.

interface Command {
  id: string;
  group: string;
  label: string;
  hint?: string;
  icon: LucideIcon;
  keywords?: string;
  run: () => void | Promise<unknown>;
}

export const GO_KEYS: Record<string, { to: string; label: string; icon: LucideIcon }> = {
  s: { to: "/search", label: "Search", icon: Search },
  w: { to: "/watchlist", label: "Watchlist", icon: Tv },
  c: { to: "/calendar", label: "Calendar", icon: CalendarDays },
  b: { to: "/browse", label: "Browse shows & movies", icon: Compass },
  a: { to: "/activity", label: "Activity", icon: Activity },
  l: { to: "/library", label: "Library", icon: Film },
  i: { to: "/insights", label: "Insights", icon: TrendingUp },
  h: { to: "/history", label: "History", icon: Clock },
  ",": { to: "/settings", label: "Settings", icon: Settings },
  u: { to: "/updates", label: "Updates", icon: ArrowUpCircle },
};

/** Exact substrings score 800+; letter-by-letter (subsequence) matches score below
 *  400, less the more spread out they are; 0 = no match. */
export function score(query: string, text: string): number {
  const q = query.toLowerCase().trim();
  if (!q) return 1;
  const t = text.toLowerCase();
  const exact = t.indexOf(q);
  if (exact >= 0) return 1000 - exact + (exact === 0 || /\W/.test(t[exact - 1]) ? 200 : 0);
  let s = 0, ti = 0, run = 0, first = -1, letters = 0;
  for (const ch of q) {
    if (ch === " ") continue;
    const found = t.indexOf(ch, ti);
    if (found < 0) return 0;
    if (first < 0) first = found;
    run = found === ti ? run + 1 : 0;
    s += 10 + run * 5 + (found === 0 || /\W/.test(t[found - 1]) ? 15 : 0);
    ti = found + 1;
    letters++;
  }
  const spread = ti - first - letters;  // characters skipped between matched letters
  return Math.max(1, Math.min(399, s - spread * 4));
}

function isTyping(e: KeyboardEvent) {
  const el = e.target as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

export default function CommandPalette() {
  const navigate = useNavigate();
  const { setTheme, setPalette } = useTheme();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [toast, setToast] = useState<string | null>(null);
  const [shows, setShows] = useState<WatchlistItem[]>([]);
  const [files, setFiles] = useState<MediaFile[]>([]);
  const [journeys, setJourneys] = useState<Journey[]>([]);
  const [paused, setPaused] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  // Global shortcuts.
  useEffect(() => {
    let gAt = 0;
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
        return;
      }
      if (isTyping(e) || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "/" && !open) {
        e.preventDefault();
        setOpen(true);
        return;
      }
      if (e.key === "g") { gAt = Date.now(); return; }
      if (gAt && Date.now() - gAt < 1200 && GO_KEYS[e.key]) {
        e.preventDefault();
        navigate(GO_KEYS[e.key].to);
      }
      gAt = 0;
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("mediamate:palette", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mediamate:palette", onOpen);
    };
  }, [navigate, open]);

  // Fresh data each time it opens; failures just leave a group empty.
  useEffect(() => {
    if (!open) return;
    setQ("");
    setSel(0);
    window.setTimeout(() => inputRef.current?.focus(), 0);
    api.watchlist.list().then(setShows).catch(() => {});
    api.media.files().then(setFiles).catch(() => {});
    api.activity.list("all", 30).then((r) => setJourneys(r.items)).catch(() => {});
    api.media.stats().then((s) => setPaused(s.paused)).catch(() => {});
  }, [open]);

  const close = () => setOpen(false);
  const say = (m: string) => { setToast(m); window.setTimeout(() => setToast(null), 3000); };
  const act = (fn: () => Promise<unknown>, done: string) => async () => {
    try { await fn(); say(done); } catch (e) { say((e as Error).message); }
  };

  const commands = useMemo<Command[]>(() => {
    const go = (to: string) => () => navigate(to);
    const list: Command[] = [];
    const query = q.trim();
    if (query) {
      list.push({ id: "search", group: "Search", label: `Search shows & movies for “${query}”`, icon: Search,
                  run: go(`/search?q=${encodeURIComponent(query)}`) });
      if (query.length >= 2) {
        list.push({ id: "search-music", group: "Search", label: `Search music for “${query}”`, icon: Search,
                    run: go(`/search?type=music&q=${encodeURIComponent(query)}`) });
      }
    }
    for (const [key, p] of Object.entries(GO_KEYS)) {
      list.push({ id: `go-${p.to}`, group: "Go to", label: p.label, hint: `g ${key}`, icon: p.icon, run: go(p.to) });
    }
    list.push(
      { id: "scan-library", group: "Actions", label: "Scan the library for new files", icon: FolderSearch,
        keywords: "media files rescan", run: act(() => api.media.scan(), "Library scan started") },
      { id: "scan-watchlist", group: "Actions", label: "Check the watchlist for new episodes now", icon: Radar,
        keywords: "scan episodes grab", run: act(() => api.watchlist.scanAll(), "Watchlist scan started") },
      paused
        ? { id: "resume", group: "Actions", label: "Resume the processing queue", icon: Play, keywords: "unpause pause start encode jobs",
            run: act(() => api.media.resume(), "Queue resumed") }
        : { id: "pause", group: "Actions", label: "Pause the processing queue", icon: Pause, keywords: "stop hold resume encode jobs",
            run: act(() => api.media.pause(), "Queue paused") },
      { id: "clear", group: "Actions", label: "Clear finished items from Activity", icon: Trash2,
        run: act(() => api.activity.clearFinished(), "Finished items cleared") },
      { id: "theme-dark", group: "Actions", label: "Dark theme", icon: Moon, keywords: "appearance", run: () => setTheme("dark") },
      { id: "theme-light", group: "Actions", label: "Light theme", icon: Sun, keywords: "appearance", run: () => setTheme("light") },
      { id: "theme-system", group: "Actions", label: "Theme: follow the system", icon: Monitor, keywords: "appearance", run: () => setTheme("system") },
      { id: "tour", group: "Actions", label: "Take the tour", icon: Compass,
        keywords: "help guide tour getting started welcome how", run: startTour },
      ...PALETTES.map((p) => ({
        id: `palette-${p.id}`, group: "Actions", label: `Colour theme: ${p.name}`, icon: Palette,
        keywords: `appearance colour color theme ${p.note}`, run: () => setPalette(p.id),
      })),
    );
    if (query.length >= 2) {
      for (const s of shows) {
        list.push({ id: `show-${s.id}`, group: "Shows", label: s.title,
                    hint: `S${String(s.season).padStart(2, "0")}E${String(s.episode).padStart(2, "0")}`,
                    icon: Tv, run: go("/watchlist") });
      }
      for (const j of journeys) {
        list.push({ id: `journey-${j.id}`, group: "Activity", label: j.title, hint: j.status, icon: Activity,
                    run: go(`/activity?open=${j.id}`) });
      }
      for (const f of files) {
        list.push({ id: `file-${f.id}`, group: "Library", label: f.filename, hint: f.category, icon: Film,
                    run: go(`/library?q=${encodeURIComponent(f.filename)}`) });
      }
    }
    return list;
  }, [q, shows, files, journeys, paused, navigate, setTheme, setPalette]); // eslint-disable-line react-hooks/exhaustive-deps

  const results = useMemo(() => {
    const query = q.trim();
    if (!query) return commands;
    const scored = commands
      // Torrent search sits below a clear name match ("lib" → Library) but above weak
      // scattered matches, so free text still searches torrents by default.
      .map((c) => ({ c, s: c.id === "search" ? 500 : Math.max(score(query, c.label), score(query, c.keywords ?? "") * 0.8) }))
      .filter((x) => x.s > 0);
    // Keep groups together, best groups first, at most 6 per data group.
    const best: Record<string, number> = {};
    for (const x of scored) best[x.c.group] = Math.max(best[x.c.group] ?? 0, x.s);
    const perGroup: Record<string, number> = {};
    return scored
      .sort((a, b) => (best[b.c.group] - best[a.c.group]) || (b.s - a.s))
      .filter((x) => (perGroup[x.c.group] = (perGroup[x.c.group] ?? 0) + 1) <= 6)
      .map((x) => x.c);
  }, [commands, q]);

  useEffect(() => setSel(0), [q]);
  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${sel}"]`)?.scrollIntoView({ block: "nearest" });
  }, [sel]);

  const choose = (c: Command | undefined) => {
    if (!c) return;
    close();
    c.run();
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setSel((i) => Math.min(results.length - 1, i + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setSel((i) => Math.max(0, i - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); choose(results[sel]); }
    else if (e.key === "Escape") { e.preventDefault(); close(); }
  };

  return (
    <>
      {open && (
        <div className="fixed inset-0 z-[70] flex items-start justify-center bg-black/40 p-4 pt-[12vh]" onMouseDown={close}>
          <div role="dialog" aria-modal="true" aria-label="Command palette" onMouseDown={(e) => e.stopPropagation()}
               className="w-full max-w-xl overflow-hidden rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-2xl">
            <div className="flex items-center gap-2 border-b border-gray-200 dark:border-gray-700 px-4">
              <Search className="h-4 w-4 shrink-0 text-gray-400" />
              <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKeyDown}
                     placeholder="Search shows, files, actions — or type a torrent search…"
                     role="combobox" aria-expanded="true" aria-controls="palette-results"
                     aria-activedescendant={results[sel] ? `palette-${results[sel].id}` : undefined}
                     className="h-12 flex-1 bg-transparent text-sm text-gray-900 dark:text-gray-100 outline-none placeholder:text-gray-400" />
              <kbd className="rounded border border-gray-200 dark:border-gray-700 px-1.5 text-[10px] text-gray-400">Esc</kbd>
            </div>
            <ul id="palette-results" ref={listRef} role="listbox" className="max-h-[50vh] overflow-y-auto py-1">
              {results.length === 0 && <li className="px-4 py-6 text-center text-sm text-gray-500">No matches</li>}
              {results.map((c, i) => (
                <li key={c.id}>
                  {(i === 0 || results[i - 1].group !== c.group) && (
                    <div className="px-4 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wide text-gray-400">{c.group}</div>
                  )}
                  <button id={`palette-${c.id}`} data-index={i} role="option" aria-selected={i === sel}
                          onMouseMove={() => setSel(i)} onClick={() => choose(c)}
                          className={`flex w-full items-center gap-3 px-4 py-2 text-left text-sm ${i === sel ? "bg-blue-50 dark:bg-blue-950/60 text-blue-800 dark:text-blue-200" : "text-gray-700 dark:text-gray-200"}`}>
                    <c.icon className="h-4 w-4 shrink-0 opacity-70" />
                    <span className="min-w-0 flex-1 truncate">{c.label}</span>
                    {c.hint && <span className="shrink-0 text-xs text-gray-400">{c.hint}</span>}
                    {i === sel && <CornerDownLeft className="h-3.5 w-3.5 shrink-0 opacity-60" />}
                  </button>
                </li>
              ))}
            </ul>
            <div className="flex gap-3 border-t border-gray-200 dark:border-gray-700 px-4 py-2 text-[11px] text-gray-400">
              <span>↑↓ to move</span><span>↵ to open</span><span>g then a letter jumps to a page</span>
            </div>
          </div>
        </div>
      )}
      {toast && (
        <div role="status" className="fixed bottom-20 left-1/2 z-[70] -translate-x-1/2 rounded-lg bg-gray-900 px-4 py-2 text-sm text-white shadow-lg md:bottom-6 dark:bg-gray-100 dark:text-gray-900">
          {toast}
        </div>
      )}
    </>
  );
}
