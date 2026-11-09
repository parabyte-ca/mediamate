import { useEffect, useRef, useState } from "react";
import { Check, Copy, Film, Globe, Home, Loader2, Minus, Play, Plus, Share2, Shuffle, X } from "lucide-react";
import { api, type ShareLink, type TeaserInfo } from "../api/client";
import { fmtClock } from "./VideoPlayer";

const DAYS: { v: 0 | 1 | 7 | 30; label: string }[] = [
  { v: 1, label: "1 day" }, { v: 7, label: "7 days" }, { v: 30, label: "30 days" }, { v: 0, label: "Until I remove it" },
];

async function shareOrCopy(url: string, title: string): Promise<"shared" | "copied"> {
  if (navigator.share && /Android|iPhone|iPad/.test(navigator.userAgent)) {
    try { await navigator.share({ title, url }); return "shared"; } catch { /* cancelled: fall back to copying */ }
  }
  await navigator.clipboard.writeText(url);
  return "copied";
}

/** Make the teaser on the server and wait for it, reporting progress. */
async function makeTeaser(id: number, starts: number[], onPct: (n: number) => void, cancelled: () => boolean): Promise<void> {
  let st = await api.shares.teaserBuild(id, starts);
  while (st.state === "queued" || st.state === "building") {
    if (cancelled()) throw new Error("cancelled");
    onPct(st.pct);
    await new Promise((r) => setTimeout(r, 1500));
    st = await api.shares.teaserStatus(st.key);
  }
  if (st.state === "error") throw new Error(st.error || "Couldn't make the teaser");
  onPct(100);
}

function Progress({ pct, label }: { pct: number; label: string }) {
  return (
    <div className="mt-2" role="status" aria-live="polite">
      <div className="flex justify-between text-xs text-gray-600 dark:text-gray-300"><span>{label}</span><span className="tabular-nums">{pct}%</span></div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
        <div className="h-full rounded-full bg-blue-600 transition-all" style={{ width: `${Math.max(4, pct)}%` }} />
      </div>
    </div>
  );
}

/** Share a video or album: a household link (opens it in MediaMate) or a public link
 *  (anyone with it can watch or listen in the browser, until it expires). */
export default function ShareDialog({ kind, id, title, householdPath, onClose }: {
  kind: "video" | "album"; id: number; title: string; householdPath: string; onClose: () => void;
}) {
  const [days, setDays] = useState<0 | 1 | 7 | 30>(7);
  const [link, setLink] = useState<ShareLink | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const home = `${window.location.origin}${householdPath}`;
  const [info, setInfo] = useState<TeaserInfo | null>(null);
  const [mode, setMode] = useState<"teaser" | "whole">("whole");
  const [starts, setStarts] = useState<number[]>([]);

  useEffect(() => {
    if (kind !== "video") return;
    api.shares.teaserInfo(id).then((t) => {
      setInfo(t);
      if (t.adult) { setMode("teaser"); setStarts(t.starts); }
    }).catch(() => { /* no teaser option */ });
  }, [kind, id]);
  const teaser = !!info?.adult && mode === "teaser";
  const [makePct, setMakePct] = useState<number | null>(null);

  const send = async (url: string, what: string) => {
    try { setDone((await shareOrCopy(url, title)) === "shared" ? `${what} shared` : `${what} copied`); }
    catch { setErr("Couldn't copy. Press and hold the link to copy it."); }
  };
  const create = async () => {
    setBusy(true); setErr(null);
    try {
      if (teaser) {
        setMakePct(0);
        await makeTeaser(id, starts, setMakePct, () => false);
      }
      const l = await api.shares.create(kind, id, days, teaser ? starts : undefined); setLink(l); await send(l.url, "Link"); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); setMakePct(null); }
  };
  const btn = "flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium";

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 sm:items-center sm:p-4" role="dialog" aria-modal aria-label={`Share ${title}`}>
      <div className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-t-2xl bg-white p-5 shadow-xl dark:bg-gray-900 sm:rounded-2xl">
        <div className="mb-4 flex items-start gap-2">
          <Share2 className="mt-0.5 h-5 w-5 shrink-0 text-blue-600" />
          <h2 className="min-w-0 flex-1 break-words font-semibold text-gray-900 dark:text-gray-100">Share “{title}”</h2>
          <button onClick={onClose} aria-label="Close" className="rounded p-1 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"><X className="h-4 w-4" /></button>
        </div>

        <section className="rounded-xl border border-gray-200 p-3 dark:border-gray-700">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold text-gray-800 dark:text-gray-200"><Home className="h-4 w-4" />Household</h3>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">Opens it in MediaMate. Works for anyone in the house (or signed in from outside).</p>
          <button onClick={() => send(home, "Household link")} className={`${btn} mt-2 w-full border border-gray-300 text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800`}>
            <Copy className="h-4 w-4" />Copy household link
          </button>
        </section>

        <section className="mt-3 rounded-xl border border-gray-200 p-3 dark:border-gray-700">
          <h3 className="flex items-center gap-1.5 text-sm font-semibold text-gray-800 dark:text-gray-200"><Globe className="h-4 w-4" />Anyone with the link</h3>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
            They can {kind === "video" ? "watch it" : "listen to it"} in their browser without logging in, until the link expires. You can remove it any time in Settings → Sharing.
          </p>
          {!link ? (
            <>
              {info?.adult && (
                <>
                  <div className="mt-2 grid grid-cols-2 gap-1.5" role="radiogroup" aria-label="What to share">
                    {([["teaser", `Teaser (${Math.round(info.clip_sec * starts.length)} sec)`], ["whole", "Whole video"]] as const).map(([v, label]) => (
                      <button key={v} role="radio" aria-checked={mode === v} onClick={() => setMode(v)}
                              className={`rounded-lg border px-2 py-1.5 text-sm ${mode === v ? "border-blue-600 bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300" : "border-gray-300 text-gray-600 dark:border-gray-600 dark:text-gray-300"}`}>
                        {label}
                      </button>
                    ))}
                  </div>
                  {teaser && <TeaserPicker id={id} info={info} starts={starts} onChange={setStarts} />}
                </>
              )}
              <p className="mt-3 text-xs font-medium text-gray-600 dark:text-gray-300">Link lasts</p>
              <div className="mt-1 grid grid-cols-2 gap-1.5" role="radiogroup" aria-label="Link lasts">
                {DAYS.map((d) => (
                  <button key={d.v} role="radio" aria-checked={days === d.v} onClick={() => setDays(d.v)}
                          className={`rounded-lg border px-2 py-1.5 text-sm ${days === d.v ? "border-blue-600 bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300" : "border-gray-300 text-gray-600 dark:border-gray-600 dark:text-gray-300"}`}>
                    {d.label}
                  </button>
                ))}
              </div>
              <button onClick={create} disabled={busy} className={`${btn} mt-2 w-full bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-60`}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Share2 className="h-4 w-4" />}
                {busy && teaser ? "Making the teaser…" : teaser ? "Create teaser link" : "Create link"}
              </button>
              {makePct !== null && <Progress pct={makePct} label="Making the teaser" />}
            </>
          ) : (
            <div className="mt-2 space-y-2">
              <input readOnly value={link.url} onFocus={(e) => e.target.select()} aria-label="Shared link"
                     className="w-full rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 font-mono text-xs text-gray-700 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200" />
              <button onClick={() => send(link.url, "Link")} className={`${btn} w-full border border-gray-300 text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-800`}>
                <Copy className="h-4 w-4" />Copy again
              </button>
              <p className="text-xs text-gray-500 dark:text-gray-400">
                {link.expires_at ? `Works until ${new Date(link.expires_at + "Z").toLocaleString()}.` : "Works until you remove it."}
              </p>
            </div>
          )}
        </section>

        {done && <p className="mt-3 flex items-center gap-1.5 text-sm text-green-600 dark:text-green-400"><Check className="h-4 w-4" />{done}</p>}
        {err && <p className="mt-3 text-sm text-red-600 dark:text-red-400">{err}</p>}
      </div>
    </div>
  );
}

/** The teaser's moments: a frame and time each, nudged back or forward, plus a
 *  preview of the finished clip. */
function TeaserPicker({ id, info, starts, onChange }: {
  id: number; info: TeaserInfo; starts: number[]; onChange: (s: number[]) => void;
}) {
  const [preview, setPreview] = useState<string | null>(null);
  const [pct, setPct] = useState<number | null>(null);
  const [perr, setPerr] = useState<string | null>(null);
  const run = useRef(0);
  const doPreview = async () => {
    const mine = ++run.current;
    setPreview(null); setPerr(null); setPct(0);
    try {
      await makeTeaser(id, starts, (n) => mine === run.current && setPct(n), () => mine !== run.current);
      if (mine === run.current) setPreview(api.shares.teaserUrl(id, starts));
    } catch (e) {
      if (mine === run.current && (e as Error).message !== "cancelled") setPerr((e as Error).message);
    } finally {
      if (mine === run.current) setPct(null);
    }
  };
  const stop = () => { run.current++; setPct(null); };
  const dur = info.duration_sec ?? 0;
  const max = Math.max(0, dur - info.clip_sec);
  const step = dur > 600 ? 30 : dur > 120 ? 10 : 2;
  const move = (i: number, by: number) => {
    stop(); setPreview(null);
    onChange(starts.map((s, j) => (j === i ? Math.min(max, Math.max(0, Math.round(s + by))) : s)));
  };
  const shuffle = () => {
    stop(); setPreview(null);
    const n = starts.length, lo = dur * 0.05, span = Math.max(0, max - lo) / n;
    onChange(Array.from({ length: n }, (_, i) => Math.round(lo + i * span + Math.random() * span * 0.9)));
  };
  const nudge = "rounded p-1 text-gray-500 hover:bg-gray-100 disabled:opacity-30 dark:hover:bg-gray-800";

  return (
    <div className="mt-2 rounded-lg bg-gray-50 p-2 dark:bg-gray-800/60">
      <p className="text-xs text-gray-500 dark:text-gray-400">
        {starts.length} moments of {Math.round(info.clip_sec)} sec, joined with short fades, with sound. Move any of them, then preview.
      </p>
      <ul className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3" aria-label="Teaser moments">
        {starts.map((s, i) => (
          <li key={i} className="min-w-0">
            <img src={api.shares.frameUrl(id, s)} alt={`Moment ${i + 1} at ${fmtClock(s)}`} loading="lazy"
                 className="aspect-video w-full rounded bg-gray-200 object-cover dark:bg-gray-700" />
            <div className="mt-0.5 flex items-center justify-between">
              <button className={nudge} onClick={() => move(i, -step)} disabled={s <= 0} aria-label={`Moment ${i + 1} earlier`}><Minus className="h-3.5 w-3.5" /></button>
              <span className="text-xs tabular-nums text-gray-700 dark:text-gray-200">{fmtClock(s)}</span>
              <button className={nudge} onClick={() => move(i, step)} disabled={s >= max} aria-label={`Moment ${i + 1} later`}><Plus className="h-3.5 w-3.5" /></button>
            </div>
          </li>
        ))}
      </ul>
      <div className="mt-2 flex gap-1.5">
        <button onClick={shuffle} className="flex flex-1 items-center justify-center gap-1 rounded-lg border border-gray-300 px-2 py-1.5 text-xs text-gray-700 hover:bg-white dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-900">
          <Shuffle className="h-3.5 w-3.5" />Other moments
        </button>
        <button onClick={doPreview} disabled={pct !== null} className="flex flex-1 items-center justify-center gap-1 rounded-lg border border-gray-300 px-2 py-1.5 text-xs text-gray-700 hover:bg-white dark:border-gray-600 dark:text-gray-200 dark:hover:bg-gray-900">
          {pct !== null ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}Preview teaser
        </button>
      </div>
      {pct !== null && (
        <>
          <Progress pct={pct} label="Making the teaser" />
          <button onClick={stop} className="mt-1 text-xs text-gray-500 underline dark:text-gray-400">Stop waiting</button>
        </>
      )}
      {perr && (
        <p className="mt-2 text-xs text-red-600 dark:text-red-400">
          {perr} <button onClick={doPreview} className="underline">Try again</button>
        </p>
      )}
      {preview && (
        <div className="mt-2">
          <video key={preview} src={preview} controls autoPlay playsInline className="w-full rounded bg-black" aria-label="Teaser preview"
                 onError={() => { setPreview(null); setPerr("Couldn't play the teaser."); }} />
          <p className="mt-1 flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400"><Film className="h-3.5 w-3.5" />This is what people with the link will see.</p>
        </div>
      )}
    </div>
  );
}
