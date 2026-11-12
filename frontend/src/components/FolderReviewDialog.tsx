import { useEffect, useState } from "react";
import { ArrowRight, Clapperboard, Loader2, Tv, X } from "lucide-react";
import { api, type FolderReviewRow } from "../api/client";

/** One-time check of where each Watchlist show and movie downloads to, against where
 *  Automatic would send it (by kind and who it's for). Mismatches start ticked. */
export default function FolderReviewDialog({ onClose, onDone }: { onClose: () => void; onDone: (msg: string) => void }) {
  const [rows, setRows] = useState<FolderReviewRow[] | null>(null);
  const [tick, setTick] = useState<Set<number>>(new Set());
  const [showAll, setShowAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.watchlist.folderReview()
      .then((r) => { setRows(r.items); setTick(new Set(r.items.filter((i) => i.differs && !i.confirmed).map((i) => i.id))); })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const pending = (rows ?? []).filter((r) => r.differs && !r.confirmed);
  const shown = showAll ? (rows ?? []) : pending;

  const apply = async (automatic: number[], keep: number[]) => {
    setBusy(true); setError(null);
    try {
      const r = await api.watchlist.applyFolderReview(automatic, keep);
      onDone(r.msg);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-4" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-labelledby="fr-title" onClick={(e) => e.stopPropagation()}
           className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-t-2xl bg-white shadow-xl dark:bg-gray-900 sm:rounded-2xl">
        <div className="flex items-start gap-3 border-b border-gray-200 p-4 dark:border-gray-700">
          <div className="min-w-0 flex-1">
            <h2 id="fr-title" className="text-lg font-semibold text-gray-900 dark:text-gray-100">Check download folders</h2>
            <p className="mt-0.5 text-sm text-gray-500 dark:text-gray-400">
              Automatic sends shows to a TV library and movies to a Movies library, on the side (regular or Mature) that fits who it's for.
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" className="rounded p-1 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"><X className="h-5 w-5" /></button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {error && <p className="mb-3 rounded bg-red-50 p-2 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300">{error}</p>}
          {rows === null && !error ? (
            <div className="flex items-center gap-2 text-sm text-gray-500"><Loader2 className="h-4 w-4 animate-spin" /> Checking…</div>
          ) : shown.length === 0 ? (
            <p className="text-sm text-gray-500 dark:text-gray-400">Everything already saves to the right place.</p>
          ) : (
            <ul className="divide-y divide-gray-100 dark:divide-gray-800">
              {shown.map((r) => (
                <li key={r.id} className="flex min-w-0 items-start gap-3 py-2.5">
                  <input type="checkbox" aria-label={`Use Automatic for ${r.title}`} disabled={!r.differs}
                         checked={tick.has(r.id)} className="mt-1 h-4 w-4 shrink-0 rounded border-gray-300 text-blue-600"
                         onChange={() => setTick((t) => { const n = new Set(t); if (n.has(r.id)) n.delete(r.id); else n.add(r.id); return n; })} />
                  {r.kind === "movie" ? <Clapperboard className="mt-0.5 h-4 w-4 shrink-0 text-gray-400" /> : <Tv className="mt-0.5 h-4 w-4 shrink-0 text-gray-400" />}
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">{r.title}</p>
                    <p className="flex flex-wrap items-center gap-1 text-xs text-gray-500 dark:text-gray-400">
                      <span className={r.differs ? "line-through decoration-red-400" : ""}>{r.current ?? "Default"}</span>
                      {r.differs && <><ArrowRight className="h-3 w-3" /><strong className="text-gray-700 dark:text-gray-200">{r.suggested ?? "Default"}</strong></>}
                      {r.confirmed && <span className="rounded bg-gray-100 px-1 dark:bg-gray-800">kept by you</span>}
                    </p>
                    {r.differs && r.reason && <p className="text-[11px] text-gray-400">{r.reason}</p>}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {rows && rows.length > pending.length && (
            <button onClick={() => setShowAll((v) => !v)} className="mt-2 text-xs font-medium text-blue-600 hover:underline dark:text-blue-400">
              {showAll ? "Show only the ones to check" : `Show all ${rows.length}`}
            </button>
          )}
        </div>

        <div className="flex flex-wrap justify-end gap-2 border-t border-gray-200 p-4 dark:border-gray-700">
          <button disabled={busy || pending.length === 0}
                  onClick={() => apply([], pending.map((r) => r.id))}
                  className="rounded-lg border border-gray-300 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800">
            Keep as they are
          </button>
          <button disabled={busy || tick.size === 0}
                  onClick={() => apply([...tick], pending.filter((r) => !tick.has(r.id)).map((r) => r.id))}
                  className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            Use Automatic for {tick.size} selected
          </button>
        </div>
      </div>
    </div>
  );
}
