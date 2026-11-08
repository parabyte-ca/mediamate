import { useState, useEffect } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  ArrowUpCircle,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Clock,
  Command,
  Compass,
  Film,
  LogOut,
  MoreHorizontal,
  MonitorDown,
  Monitor,
  Moon,
  Search,
  Settings,
  Sun,
  TrendingUp,
  Tv,
} from "lucide-react";
import AppLogo from "./AppLogo";
import { PALETTES, useTheme, type Theme } from "../ThemeContext";
import { authStore } from "../auth";
import { api } from "../api/client";
import OnboardingWizard from "./OnboardingWizard";
import Tour, { TOUR_EVENT, tourSeen } from "./Tour";
import StatusPill from "./StatusPill";
import { PersonSwitcher, usePeople } from "../people";
import CommandPalette from "./CommandPalette";

const nav = [
  // mobile: a tab in the phone's bottom bar; the rest live under its "More" tab.
  // The desktop sidebar mirrors this: the tabs first, then a "More" group.
  { to: "/search",    label: "Search",    icon: Search,        mobile: true  },
  { to: "/watchlist", label: "Watchlist", icon: Tv,            mobile: true  },
  { to: "/browse",    label: "Browse",    icon: Compass,       mobile: true  },
  { to: "/activity",  label: "Activity",  icon: Activity,      mobile: true  },
  { to: "/library",   label: "Library",   icon: Film,          mobile: true  },
  { to: "/calendar",  label: "Calendar",  icon: CalendarDays,  mobile: false },
  { to: "/insights",  label: "Insights",  icon: TrendingUp,    mobile: false },
  { to: "/history",   label: "History",   icon: Clock,         mobile: false },
  { to: "/settings",  label: "Settings",  icon: Settings,      mobile: false },
  { to: "/updates",   label: "Updates",   icon: ArrowUpCircle, mobile: false },
];

const themeButtons: { value: Theme; icon: typeof Sun; title: string }[] = [
  { value: "light",  icon: Sun,     title: "Light theme"  },
  { value: "system", icon: Monitor, title: "System theme" },
  { value: "dark",   icon: Moon,    title: "Dark theme"   },
];

export default function Layout() {
  const { theme, setTheme, palette, setPalette } = useTheme();
  const navigate = useNavigate();
  const [version, setVersion] = useState<string | null>(null);
  const [updateAvailable, setUpdateAvailable] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const { pathname } = useLocation();
  const more = nav.filter((n) => !n.mobile);
  const onMorePage = more.some((n) => pathname.startsWith(n.to));
  useEffect(() => setMoreOpen(false), [pathname]);

  useEffect(() => {
    fetch("/health")
      .then((r) => r.json())
      .then((d) => {
        setVersion(d.version ?? null);
        // An installed app (phones especially) can keep running the screens it cached before
        // an upgrade. When the server is newer than this build, fetch the new one and reload once.
        const built = import.meta.env.VITE_APP_VERSION as string | undefined;
        const key = `mm-reloaded-for-${d.version}`;
        let seen = true;                     // no storage: never risk a reload loop
        try { seen = !!sessionStorage.getItem(key); if (!seen) sessionStorage.setItem(key, "1"); } catch { /* keep seen */ }
        if (built && d.version && built !== d.version && !seen) {
          const reload = () => window.location.reload();
          if ("serviceWorker" in navigator) {
            navigator.serviceWorker.getRegistration()
              .then((reg) => reg?.update())
              .catch(() => {})
              .finally(() => setTimeout(reload, 500));
          } else reload();
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    api.updates.status()
      .then((s) => setUpdateAvailable(s.update_available))
      .catch(() => {});
  }, []);

  const [onboarding, setOnboarding] = useState(false);

  // "Install app" appears when the browser offers it (Chrome/Edge/Android; iOS uses Share → Add to Home Screen).
  const [installEvt, setInstallEvt] = useState<(Event & { prompt: () => Promise<void> }) | null>(null);
  useEffect(() => {
    const onPrompt = (e: Event) => { e.preventDefault(); setInstallEvt(e as Event & { prompt: () => Promise<void> }); };
    const onInstalled = () => setInstallEvt(null);
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);
  const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

  // The guided tour: once per device, after the setup wizard if that's showing;
  // replayed from Settings → Appearance or the command palette.
  const [touring, setTouring] = useState(false);
  const [wizardChecked, setWizardChecked] = useState(false);

  useEffect(() => {
    api.onboarding.status().then((s) => setOnboarding(s.needed)).catch(() => {})
      .finally(() => setWizardChecked(true));
  }, []);

  const { people, me } = usePeople();
  const whoKnown = people.length > 0 && (people.length < 2 || !!me);   // the "who's using this device?" question is answered
  useEffect(() => {
    if (!wizardChecked || onboarding || !whoKnown || tourSeen()) return;
    const t = setTimeout(() => setTouring(true), 800);
    return () => clearTimeout(t);
  }, [wizardChecked, onboarding, whoKnown]);

  useEffect(() => {
    const start = () => { setMoreOpen(false); setTouring(true); };
    window.addEventListener(TOUR_EVENT, start);
    return () => window.removeEventListener(TOUR_EVENT, start);
  }, []);

  const [lowSpace, setLowSpace] = useState<{ name: string; free: number }[]>([]);

  useEffect(() => {
    const load = () =>
      api.paths.list()
        .then((ps) =>
          setLowSpace(
            ps.filter((p) => p.low_space).map((p) => ({ name: p.name, free: (p.free_bytes ?? 0) / 1e9 })),
          ),
        )
        .catch(() => {});
    load();
    const id = setInterval(load, 5 * 60_000);
    return () => clearInterval(id);
  }, []);

  const handleLogout = () => {
    authStore.clearToken();
    navigate("/login", { replace: true });
  };

  const [collapsed, setCollapsed] = useState<boolean>(() => {
    const saved = localStorage.getItem("ar-sidebar");
    if (saved !== null) return saved === "true";
    return window.innerWidth < 768;
  });

  const toggle = () =>
    setCollapsed((v) => {
      const next = !v;
      localStorage.setItem("ar-sidebar", String(next));
      return next;
    });

  // relative: absolutely positioned children (e.g. sr-only inputs) stay inside the app
  // instead of stretching the page, which made the whole page scroll on phones.
  return (
    <div className="relative flex h-screen overflow-hidden bg-gray-50 dark:bg-gray-950">
      {onboarding && <OnboardingWizard onClose={() => setOnboarding(false)} />}
      <CommandPalette />
      <Tour open={touring} onClose={() => setTouring(false)} many={people.length > 1} />

      {/* ── Sidebar (md and above) ──────────────────────────────────────────── */}
      <aside
        className={`hidden md:flex flex-col border-r border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-sm transition-all duration-200 shrink-0 ${
          collapsed ? "w-16" : "w-56"
        }`}
      >
        {/* Logo row */}
        <div
          className={`flex items-center border-b border-gray-100 dark:border-gray-800 ${
            collapsed ? "justify-center px-0 py-5" : "gap-2 px-4 py-5"
          }`}
        >
          <AppLogo className="h-8 w-8" />
          {!collapsed && (
            <>
              <span className="flex-1 text-lg font-bold text-gray-900 dark:text-gray-100">
                MediaMate
              </span>
              <button
                onClick={toggle}
                title="Collapse sidebar"
                className="rounded-md p-1 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
            </>
          )}
        </div>

        {/* Who's using this device */}
        <div className={collapsed ? "flex justify-center pt-3" : "px-3 pt-3"}>
          <PersonSwitcher compact={collapsed} />
        </div>

        {/* Live status pill */}
        <div className={collapsed ? "pt-3" : "px-3 pt-3"} data-tour="status">
          <StatusPill compact={collapsed} />
        </div>

        {/* Command palette */}
        <div className={collapsed ? "pt-2" : "px-3 pt-2"}>
          <button
            onClick={() => window.dispatchEvent(new Event("mediamate:palette"))}
            title={`Search or jump to… (${isMac ? "⌘" : "Ctrl+"}K)`}
            data-tour="jump"
            className={`flex w-full items-center rounded-lg border border-gray-200 dark:border-gray-700 text-sm text-gray-500 dark:text-gray-400 hover:border-blue-300 dark:hover:border-blue-700 ${
              collapsed ? "mx-auto h-8 w-8 justify-center" : "gap-2 px-3 py-1.5"
            }`}
          >
            <Command className="h-3.5 w-3.5 shrink-0" />
            {!collapsed && (
              <>
                <span className="flex-1 truncate text-left">Jump to…</span>
                <kbd className="text-[10px] text-gray-400">{isMac ? "⌘K" : "Ctrl K"}</kbd>
              </>
            )}
          </button>
        </div>

        {/* Expand button (collapsed state only) */}
        {collapsed && (
          <button
            onClick={toggle}
            title="Expand sidebar"
            className="mx-auto mt-2 rounded-md p-1.5 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        )}

        {/* Navigation */}
        <nav className={`flex-1 space-y-1 py-4 ${collapsed ? "px-2" : "px-3"}`}>
          {nav.map(({ to, label, icon: Icon, mobile }, i) => (
            <div key={to}>
            {!mobile && nav[i - 1]?.mobile && (
              collapsed
                ? <div className="my-2 border-t border-gray-200 dark:border-gray-700" aria-hidden />
                : <p className="mt-4 mb-1 border-t border-gray-200 dark:border-gray-700 px-3 pt-3 text-[11px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">More</p>
            )}
            <NavLink
              key={to}
              to={to}
              data-tour={`nav-${to.slice(1)}`}
              title={collapsed ? label : undefined}
              className={({ isActive }) =>
                `relative flex items-center rounded-lg py-2.5 text-sm font-medium transition-colors ${
                  collapsed ? "justify-center px-0" : "gap-3 px-3"
                } ${
                  isActive
                    ? "bg-blue-50 dark:bg-blue-950 text-blue-700 dark:text-blue-400"
                    : "text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-900 dark:hover:text-gray-100"
                }`
              }
            >
              <span className="relative shrink-0">
                <Icon className="h-4 w-4" />
                {to === "/updates" && updateAvailable && (
                  <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-blue-500 ring-2 ring-white dark:ring-gray-900" />
                )}
              </span>
              {!collapsed && label}
            </NavLink>
            </div>
          ))}
        </nav>

        {/* Footer: theme toggle + version */}
        <div
          data-tour="themes"
          className={`pb-4 ${
            collapsed ? "flex flex-col items-center gap-1.5 px-2" : "px-4"
          }`}
        >
          {!collapsed && (
            <div className="mb-2 flex items-center gap-1.5" role="radiogroup" aria-label="Colour theme">
              {PALETTES.map((p) => (
                <button
                  key={p.id}
                  role="radio"
                  aria-checked={palette === p.id}
                  title={`${p.name} (${p.note})`}
                  onClick={() => setPalette(p.id)}
                  style={{ background: p.swatch }}
                  className={`h-4 w-4 rounded-full ring-offset-2 ring-offset-white dark:ring-offset-gray-900 transition-shadow ${
                    palette === p.id ? "ring-2 ring-gray-400 dark:ring-gray-500" : "hover:ring-2 hover:ring-gray-200 dark:hover:ring-gray-700"
                  } ${p.id === "graphite" ? "dark:outline dark:outline-1 dark:outline-gray-600" : ""}`}
                />
              ))}
            </div>
          )}
          <div className={`flex ${collapsed ? "flex-col gap-1" : "gap-1 mb-2"}`}>
            {themeButtons.map(({ value, icon: Icon, title }) => (
              <button
                key={value}
                title={title}
                onClick={() => setTheme(value)}
                className={`rounded-md p-1.5 transition-colors ${
                  theme === value
                    ? "bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-400"
                    : "text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-600 dark:hover:text-gray-300"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
              </button>
            ))}
            {installEvt && (
              <button
                title="Install MediaMate as an app"
                onClick={async () => { await installEvt.prompt(); setInstallEvt(null); }}
                className="rounded-md p-1.5 text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-blue-600"
              >
                <MonitorDown className="h-3.5 w-3.5" />
              </button>
            )}
            {authStore.getToken() && (
              <button
                title="Sign out"
                onClick={handleLogout}
                className="rounded-md p-1.5 text-gray-400 dark:text-gray-500 hover:bg-red-50 dark:hover:bg-red-950 hover:text-red-500 transition-colors"
              >
                <LogOut className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
          {!collapsed && version && (
            <p className="text-xs text-gray-400 dark:text-gray-600">v{version}</p>
          )}
        </div>
      </aside>

      {/* ── Main content ────────────────────────────────────────────────────── */}
      {/* main-scroll: on mobile adds padding to clear the bottom tab bar     */}
      {/* + iOS safe area; on md+ resets to zero (see index.css)              */}
      <main className="flex-1 overflow-y-auto overflow-x-hidden main-scroll">
        <div className="sticky top-0 z-30 flex items-center justify-center gap-2 border-b border-gray-200 dark:border-gray-800 bg-gray-50/90 dark:bg-gray-950/90 px-4 py-2 backdrop-blur md:hidden">
          <PersonSwitcher compact />
          <span data-tour="status"><StatusPill /></span>
          <button aria-label="Search or jump to…" data-tour="jump" onClick={() => window.dispatchEvent(new Event("mediamate:palette"))}
                  className="rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-1.5 text-gray-500">
            <Command className="h-3.5 w-3.5" />
          </button>
        </div>
        {lowSpace.length > 0 && (
          <div className="flex items-center gap-2 border-b border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950/40 px-4 py-2 text-xs text-amber-800 dark:text-amber-300">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            <span className="flex-1">
              Low disk space: {lowSpace.map((p) => `${p.name} (${p.free.toFixed(1)} GB free)`).join(", ")}.
            </span>
            <NavLink to="/settings" className="font-medium underline">Settings</NavLink>
          </div>
        )}
        <Outlet />
      </main>

      {/* ── Bottom tab bar (mobile only, hidden on md+) ─────────────────────── */}
      <nav
        data-mobile-tabs
        className="fixed bottom-0 left-0 right-0 z-40 flex md:hidden bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-700 shadow-[0_-1px_3px_rgba(0,0,0,0.08)]"
        style={{ paddingBottom: "env(safe-area-inset-bottom, 0px)" } as React.CSSProperties}
      >
        {nav.filter((n) => n.mobile).map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            data-tour={`nav-${to.slice(1)}`}
            className={({ isActive }) =>
              `flex flex-1 flex-col items-center justify-center gap-0.5 py-2 text-[10px] font-medium min-h-[52px] transition-colors ${
                isActive
                  ? "text-blue-600 dark:text-blue-400"
                  : "text-gray-500 dark:text-gray-400"
              }`
            }
          >
            <span className="relative">
              <Icon className="h-5 w-5" />
              {to === "/updates" && updateAvailable && (
                <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-blue-500 ring-2 ring-white dark:ring-gray-900" />
              )}
            </span>
            {label}
          </NavLink>
        ))}
        <button
          type="button"
          onClick={() => setMoreOpen((o) => !o)}
          data-tour="nav-more"
          aria-expanded={moreOpen}
          aria-controls="more-sheet"
          className={`flex flex-1 flex-col items-center justify-center gap-0.5 py-2 text-[10px] font-medium min-h-[52px] transition-colors ${
            moreOpen || onMorePage ? "text-blue-600 dark:text-blue-400" : "text-gray-500 dark:text-gray-400"
          }`}
        >
          <span className="relative">
            <MoreHorizontal className="h-5 w-5" />
            {updateAvailable && (
              <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-blue-500 ring-2 ring-white dark:ring-gray-900" />
            )}
          </span>
          More
        </button>
      </nav>

      {/* ── "More" sheet (mobile): the pages that don't fit in the tab bar ──── */}
      {moreOpen && (
        <div className="fixed inset-0 z-30 md:hidden" onClick={() => setMoreOpen(false)}>
          <div className="absolute inset-0 bg-black/30" aria-hidden />
          <div
            id="more-sheet"
            role="menu"
            aria-label="More pages"
            onClick={(e) => e.stopPropagation()}
            className="absolute inset-x-0 bottom-0 rounded-t-2xl bg-white dark:bg-gray-900 border-t border-gray-200 dark:border-gray-700 p-2 shadow-xl"
            style={{ paddingBottom: "calc(env(safe-area-inset-bottom, 0px) + 60px)" } as React.CSSProperties}
          >
            {more.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                role="menuitem"
                className={({ isActive }) =>
                  `flex items-center gap-3 rounded-lg px-4 py-3 text-sm font-medium ${
                    isActive
                      ? "bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300"
                      : "text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800"
                  }`
                }
              >
                <Icon className="h-5 w-5" />
                <span className="flex-1">{label}</span>
                {to === "/updates" && updateAvailable && (
                  <span className="rounded-full bg-blue-600 px-2 py-0.5 text-[11px] font-semibold text-white">New</span>
                )}
              </NavLink>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
