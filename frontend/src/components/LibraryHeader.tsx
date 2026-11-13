import { createContext, useContext, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/** The Library page's frozen header: the title, tabs and each library's search and
 *  controls stay at the top while only the list scrolls. Its height is published as
 *  --lib-head so a list's column header can stick just below it. */

const SlotCtx = createContext<HTMLElement | null>(null);

/** The frozen header (title row plus whatever the library puts in it with
 *  <InLibraryHeader>), then the library's scrolling content. */
export function LibraryHeader({ title, children }: { title: React.ReactNode; children?: React.ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const [slot, setSlot] = useState<HTMLDivElement | null>(null);
  const [scrolled, setScrolled] = useState(false);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const root = document.documentElement;
    // The phone's top bar (sticky, hidden on wider screens) sits above the header.
    const bar = document.querySelector<HTMLElement>("[data-mobile-bar]");
    const set = () => {
      root.style.setProperty("--lib-head", `${Math.ceil(el.getBoundingClientRect().height)}px`);
      root.style.setProperty("--bar-h", `${bar ? Math.floor(bar.getBoundingClientRect().height) : 0}px`);
    };
    set();
    const ro = new ResizeObserver(set);
    ro.observe(el);
    if (bar) ro.observe(bar);
    const main = document.querySelector("main");
    const onScroll = () => setScrolled((main?.scrollTop ?? 0) > 4);
    main?.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => { ro.disconnect(); main?.removeEventListener("scroll", onScroll); root.style.removeProperty("--lib-head"); root.style.removeProperty("--bar-h"); };
  }, []);
  return (
    <SlotCtx.Provider value={slot}>
      <div ref={box} data-testid="library-header"
           className={`sticky top-[var(--bar-h,3rem)] z-20 -mx-4 mb-3 bg-gray-50/95 px-4 pb-1 pt-4 backdrop-blur dark:bg-gray-950/90 md:top-0 md:-mx-6 md:px-6 md:pt-6 ${scrolled ? "border-b border-gray-200 shadow-sm dark:border-gray-800" : "border-b border-transparent"}`}>
        <div className="mb-3 flex flex-wrap items-center gap-3">{title}</div>
        <div ref={setSlot} />
      </div>
      {children}
    </SlotCtx.Provider>
  );
}

/** Render children into the frozen header (or in place when there's no header). */
export function InLibraryHeader({ children }: { children: React.ReactNode }) {
  const slot = useContext(SlotCtx);
  return slot ? createPortal(children, slot) : <>{children}</>;
}

/** Classes for a column-header cell that sticks just under the frozen header. */
export const STICKY_TH = "sticky top-[calc(var(--lib-head,0px)+var(--bar-h,0px))] z-10 border-b border-gray-200 bg-white dark:border-gray-700 dark:bg-gray-900";
