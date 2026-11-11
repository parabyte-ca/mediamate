import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Loader2, Stethoscope, X } from "lucide-react";
import { api, type DoctorIssue, type DoctorState } from "../api/client";

// The Library doctor's dialog, shared by videos and music: the last report, Check now,
// Fix (one, or all of a kind) and the "fix automatically" switch.

export interface DoctorKindInfo<K extends string> {
  kind: K;
  title: string;
  hint: string;
  /** Button text for the whole section (default "Fix all"); null hides it. */
  fixAll?: string | null;
  /** Button text for one item (default "Fix"). */
  fixOne?: string;
  /** Ask before fixing n items; return null to skip asking. */
  confirm?: (n: number) => string | null;
  /** Instead of a Fix button: a link, e.g. to Search. */
  link?: (i: DoctorIssue<K>) => { to: string; label: string };
}

export interface DoctorApi<K extends string> {
  get: () => Promise<DoctorState<K>>;
  run: () => Promise<DoctorState<K>>;
  fix: (ids: string[]) => Promise<DoctorState<K> & { done: number; errors: string[]; started?: string | null }>;
}

const btn = "flex min-h-[36px] items-center gap-1.5 rounded-lg px-3 text-sm disabled:opacity-50";
const btnGhost = `${btn} border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800`;
const btnPrimary = `${btn} bg-blue-600 font-medium text-white hover:bg-blue-700`;
const small = "shrink-0 rounded-md border border-gray-300 dark:border-gray-600 px-2 py-1 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-50";

export default function DoctorDialog<K extends string>({ title, kinds, source, autoFixLabel, onClose, onChanged, onKeep }: {
  title: string;
  kinds: DoctorKindInfo<K>[];
  source: DoctorApi<K>;
  autoFixLabel: string;
  onClose: () => void;
  onChanged: () => void;
  /** "Keep here" on a row marked keep: remember the choice; the row goes away. */
  onKeep?: (issue: DoctorIssue<K>) => Promise<string>;
}) {
  const [state, setState] = useState<DoctorState<K> | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { source.get().then(setState).catch((e) => setMsg(e.message)); }, [source]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);

  const check = async () => {
    setBusy("check"); setMsg(null);
    try {
      const s = await source.run();
      setState(s);
      const fixed = s.report?.fixed.length ?? 0;
      setMsg(`${s.report?.total ? `${s.report.total} to look at` : "Nothing to fix"}${fixed ? ` · ${fixed} fixed automatically` : ""}`);
      onChanged();
    } catch (e) { setMsg((e as Error).message); } finally { setBusy(null); }
  };
  const fix = async (ids: string[], key: string, info?: DoctorKindInfo<K>) => {
    const ask = info?.confirm?.(ids.length);
    if (ask && !confirm(ask)) return;
    setBusy(key); setMsg(null);
    try {
      const r = await source.fix(ids);
      setState(r);
      setMsg(r.started ? `${r.started}: running in the background. Progress shows above the albums.`
        + (r.errors.length ? ` · ${r.errors[0]}` : "")
        : `Fixed ${r.done}${r.errors.length ? ` · ${r.errors.length} couldn't be: ${r.errors[0]}` : ""}`);
      onChanged();
    } catch (e) { setMsg((e as Error).message); } finally { setBusy(null); }
  };

  const report = state?.report;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" role="dialog" aria-modal="true" aria-label={title}>
      <div className="w-full max-w-3xl rounded-xl bg-white shadow-xl dark:bg-gray-900">
        <div className="flex items-center gap-2 border-b border-gray-200 px-4 py-3 dark:border-gray-700">
          <h3 className="min-w-0 flex-1 truncate font-semibold text-gray-900 dark:text-gray-100">{title}</h3>
          <button onClick={onClose} aria-label="Close" className="rounded p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800 dark:hover:text-gray-200">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="p-4">
          <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
            <p className="mr-auto text-gray-600 dark:text-gray-300">
              {report ? <>Last checked {new Date(report.checked_at).toLocaleString()}. Checks run every Saturday.</> : "Not checked yet."}
            </p>
            <button className={btnPrimary} onClick={check} disabled={!!busy}>
              {busy === "check" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Stethoscope className="h-4 w-4" />}Check now
            </button>
          </div>
          <label className="mb-3 flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
            <input type="checkbox" checked={!!state?.auto_fix} disabled={!state}
                   onChange={async (e) => { const on = e.target.checked; await api.doctor.setAutoFix(on); setState((s) => (s ? { ...s, auto_fix: on } : s)); }} />
            {autoFixLabel}
          </label>
          {msg && <p role="status" className="mb-3 rounded-lg bg-blue-50 px-3 py-2 text-xs text-blue-800 dark:bg-blue-950/40 dark:text-blue-200">{msg}</p>}

          <div className="max-h-[55vh] space-y-3 overflow-y-auto">
            {report && report.issues.length === 0 && (
              <p className="flex items-center gap-2 rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-800 dark:border-green-900 dark:bg-green-950/30 dark:text-green-300">
                <CheckCircle2 className="h-4 w-4" />Your library looks healthy.
              </p>
            )}
            {kinds.map((info) => {
              const list = report?.issues.filter((i) => i.kind === info.kind) ?? [];
              if (!list.length) return null;
              const fixable = list.filter((i) => i.fixable !== false && i.bulk !== false);
              return (
                <section key={info.kind} className="rounded-lg border border-gray-200 dark:border-gray-700">
                  <header className="flex items-center gap-2 border-b border-gray-100 px-3 py-2 dark:border-gray-800">
                    <div className="mr-auto min-w-0">
                      <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{info.title} <span className="text-gray-400">({list.length})</span></p>
                      <p className="text-xs text-gray-500 dark:text-gray-400">{info.hint}</p>
                    </div>
                    {info.fixAll !== null && !info.link && fixable.length > 0 && (
                      <button className={`${btnGhost} shrink-0 whitespace-nowrap`} disabled={!!busy} onClick={() => fix(fixable.map((i) => i.id), info.kind, info)}>
                        {busy === info.kind ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                        {info.fixAll ?? "Fix all"}{fixable.length < list.length ? ` (${fixable.length})` : ""}
                      </button>
                    )}
                  </header>
                  <ul className="divide-y divide-gray-100 dark:divide-gray-800">
                    {list.slice(0, 100).map((i) => {
                      const link = info.link?.(i);
                      return (
                        <li key={i.id} className="flex items-start gap-2 px-3 py-2 text-xs">
                          <div className="min-w-0 flex-1">
                            <p className="truncate font-mono text-gray-800 dark:text-gray-200" title={i.path}>{i.path}</p>
                            <p className="break-words text-gray-500 dark:text-gray-400">{i.detail}</p>
                            {i.error && <p className="text-red-600 dark:text-red-400">{i.error}</p>}
                          </div>
                          {i.keep_here && onKeep && (
                            <button className={small} disabled={!!busy} title="Leave it where it is and stop asking"
                                    onClick={async () => {
                                      setBusy(`keep-${i.id}`); setMsg(null);
                                      try {
                                        setMsg(await onKeep(i));
                                        setState((st) => st && st.report ? { ...st, report: { ...st.report, issues: st.report.issues.filter((x) => x.id !== i.id), total: Math.max(0, st.report.total - 1) } } : st);
                                        onChanged();
                                      } catch (e) { setMsg((e as Error).message); } finally { setBusy(null); }
                                    }}>
                              {busy === `keep-${i.id}` ? <Loader2 className="h-3 w-3 animate-spin" /> : "Keep here"}
                            </button>
                          )}
                          {link ? (
                            <Link to={link.to} className={small}>{link.label}</Link>
                          ) : i.fixable !== false && (
                            <button className={small} disabled={!!busy} title={i.fix} onClick={() => fix([i.id], i.id, info)}>
                              {busy === i.id ? <Loader2 className="h-3 w-3 animate-spin" /> : info.fixOne ?? "Fix"}
                            </button>
                          )}
                        </li>
                      );
                    })}
                    {list.length > 100 && <li className="px-3 py-2 text-xs text-gray-500">…and {list.length - 100} more (Fix all includes them).</li>}
                  </ul>
                </section>
              );
            })}
            {report && report.fixed.length > 0 && (
              <details className="rounded-lg border border-gray-200 px-3 py-2 text-xs text-gray-600 dark:border-gray-700 dark:text-gray-300">
                <summary className="cursor-pointer">Fixed ({report.fixed.length})</summary>
                <ul className="mt-2 space-y-1">
                  {report.fixed.slice(-100).map((i) => <li key={i.id} className="truncate" title={i.path}>{i.label}: {i.path} — {i.result}</li>)}
                </ul>
              </details>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
