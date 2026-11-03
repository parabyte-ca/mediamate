import { useEffect, useState } from "react";
import {
  ArrowDown, ArrowUp, CheckCircle2, Edit2, Eye, FolderSearch, Loader2, Play, Plus, Save, Trash2, X,
} from "lucide-react";
import {
  api,
  type OrganiseSettings, type PathMappingRow, type PathTestResult, type PipelineAction, type PipelineActionType,
  type PipelineMatch, type PipelinePreview, type PipelineRule, type PipelineRuleInput,
  type PipelineSettings as PSettings, type PipelineTemplate,
} from "../api/client";
import { fmtSize } from "../utils";

// Settings → Auto pipeline: what happens to a file after it downloads.

const STEP_ORDER: PipelineActionType[] = ["trim", "encode", "split", "organise"];
const STEP_LABEL: Record<PipelineActionType, string> = {
  trim: "Trim intro", encode: "Encode to x265", split: "Split scenes", organise: "Rename & organise",
};
const RESOLUTIONS = ["original", "480p", "720p", "1080p", "1440p", "2160p"];

const input = "w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none";
const btn = "flex min-h-[36px] items-center gap-1.5 rounded-lg px-3 text-sm";
const btnGhost = `${btn} border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800`;
const btnPrimary = `${btn} bg-blue-600 font-medium text-white hover:bg-blue-700 disabled:opacity-50`;

function describeMatch(m: PipelineMatch): string {
  const parts: string[] = [];
  if (m.source && m.source !== "any") parts.push(m.source === "download" ? "downloads" : "library scans");
  if (m.codecs?.length) parts.push(m.codecs.join("/"));
  if (m.min_height) parts.push(`≥${m.min_height}p`);
  if (m.max_height) parts.push(`≤${m.max_height}p`);
  if (m.min_size_mb) parts.push(`≥${m.min_size_mb} MB`);
  if (m.min_duration_min) parts.push(`≥${m.min_duration_min} min`);
  if (m.max_duration_min) parts.push(`≤${m.max_duration_min} min`);
  if (m.keywords?.length) parts.push(`name has ${m.keywords.join(" or ")}`);
  if (m.anthology != null) parts.push(m.anthology ? "anthologies" : "not anthologies");
  if (m.path_prefix) parts.push(`in ${m.path_prefix}`);
  return parts.length ? parts.join(" · ") : "every file";
}

function stepChip(a: PipelineAction) {
  const extra = a.type === "encode" && a.resolution && a.resolution !== "original" ? ` → ${a.resolution}` : "";
  return `${STEP_LABEL[a.type]}${extra}`;
}

const csv = (v: string) => v.split(",").map((x) => x.trim()).filter(Boolean);
const num = (v: string) => (v.trim() === "" ? null : Number(v));

// ── Rule editor ───────────────────────────────────────────────────────────────

function RuleModal({ initial, onClose, onSaved }: {
  initial: PipelineRuleInput & { id?: number };
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(initial.name);
  const [m, setM] = useState<PipelineMatch>(initial.match);
  const [steps, setSteps] = useState<Record<PipelineActionType, PipelineAction | null>>(() => {
    const s: Record<PipelineActionType, PipelineAction | null> = { trim: null, encode: null, split: null, organise: null };
    for (const a of initial.actions) s[a.type] = a;
    return s;
  });
  const [preview, setPreview] = useState<PipelinePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const actions = STEP_ORDER.map((t) => steps[t]).filter(Boolean) as PipelineAction[];
  const rule: PipelineRuleInput = { name: name.trim(), enabled: initial.enabled, match: m, actions };
  const setMatch = (patch: Partial<PipelineMatch>) => { setM((cur) => ({ ...cur, ...patch })); setPreview(null); };

  const runPreview = async () => {
    setError(null);
    try { setPreview(await api.pipeline.preview(rule)); } catch (e) { setError((e as Error).message); }
  };
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      if (initial.id) await api.pipeline.updateRule(initial.id, rule);
      else await api.pipeline.createRule(rule);
      onSaved();
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true">
      <div className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-xl bg-white dark:bg-gray-900 shadow-xl">
        <div className="flex items-center border-b border-gray-200 dark:border-gray-700 px-5 py-3">
          <h3 className="flex-1 font-semibold text-gray-900 dark:text-gray-100">{initial.id ? "Edit rule" : "New rule"}</h3>
          <button onClick={onClose} aria-label="Close" className="rounded p-1 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"><X className="h-4 w-4" /></button>
        </div>
        <div className="space-y-4 overflow-y-auto px-5 py-4 text-sm">
          <label className="block">
            <span className="mb-1 block font-medium text-gray-700 dark:text-gray-300">Name</span>
            <input className={input} value={name} onChange={(e) => setName(e.target.value)} placeholder="Shrink to x265" />
          </label>

          <fieldset className="space-y-3">
            <legend className="mb-1 font-medium text-gray-700 dark:text-gray-300">When a file…</legend>
            <label className="block">
              <span className="mb-1 block text-xs text-gray-500">comes from</span>
              <select className={input} value={m.source ?? "any"} onChange={(e) => setMatch({ source: e.target.value as PipelineMatch["source"] })}>
                <option value="any">a download or a library scan</option>
                <option value="download">a finished download</option>
                <option value="scan">a library scan</option>
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-gray-500">has codec (comma-separated, blank = any)</span>
              <input className={input} value={(m.codecs ?? []).join(", ")} placeholder="h264, mpeg4"
                     onChange={(e) => setMatch({ codecs: csv(e.target.value) })} />
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block"><span className="mb-1 block text-xs text-gray-500">min height (px)</span>
                <input className={input} type="number" value={m.min_height ?? ""} placeholder="e.g. 1500"
                       onChange={(e) => setMatch({ min_height: num(e.target.value) })} /></label>
              <label className="block"><span className="mb-1 block text-xs text-gray-500">max height (px)</span>
                <input className={input} type="number" value={m.max_height ?? ""}
                       onChange={(e) => setMatch({ max_height: num(e.target.value) })} /></label>
              <label className="block"><span className="mb-1 block text-xs text-gray-500">min size (MB)</span>
                <input className={input} type="number" value={m.min_size_mb ?? ""}
                       onChange={(e) => setMatch({ min_size_mb: num(e.target.value) })} /></label>
              <label className="block"><span className="mb-1 block text-xs text-gray-500">min length (min)</span>
                <input className={input} type="number" value={m.min_duration_min ?? ""}
                       onChange={(e) => setMatch({ min_duration_min: num(e.target.value) })} /></label>
            </div>
            <label className="block">
              <span className="mb-1 block text-xs text-gray-500">file name contains any of (comma-separated)</span>
              <input className={input} value={(m.keywords ?? []).join(", ")}
                     onChange={(e) => setMatch({ keywords: csv(e.target.value) })} />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-gray-500">anthology</span>
              <select className={input} value={m.anthology == null ? "" : String(m.anthology)}
                      onChange={(e) => setMatch({ anthology: e.target.value === "" ? null : e.target.value === "true" })}>
                <option value="">doesn't matter</option>
                <option value="true">only anthologies</option>
                <option value="false">never anthologies</option>
              </select>
            </label>
          </fieldset>

          <fieldset>
            <legend className="mb-2 font-medium text-gray-700 dark:text-gray-300">…do these steps, in order</legend>
            <div className="space-y-2">
              {STEP_ORDER.map((t, i) => (
                <div key={t} className="flex items-center gap-3 rounded-lg border border-gray-200 dark:border-gray-700 px-3 py-2">
                  <span className="w-4 text-xs text-gray-400">{i + 1}</span>
                  <label className="flex flex-1 items-center gap-2">
                    <input type="checkbox" checked={!!steps[t]}
                           disabled={(t === "split" && !!steps.organise) || (t === "organise" && !!steps.split)}
                           title={t === "split" || t === "organise" ? "Split and Rename & organise can't be in the same rule" : undefined}
                           onChange={(e) => { setSteps((s) => ({ ...s, [t]: e.target.checked ? { type: t } : null })); setPreview(null); }} />
                    <span className="text-gray-800 dark:text-gray-200">{STEP_LABEL[t]}</span>
                  </label>
                  {t === "encode" && steps.encode && (
                    <select className="rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1 text-xs"
                            value={steps.encode.resolution ?? "original"} aria-label="Resolution"
                            onChange={(e) => setSteps((s) => ({ ...s, encode: { type: "encode", resolution: e.target.value } }))}>
                      {RESOLUTIONS.map((r) => <option key={r} value={r}>{r === "original" ? "keep resolution" : r}</option>)}
                    </select>
                  )}
                </div>
              ))}
            </div>
            <p className="mt-2 text-xs text-gray-500">Encode quality and speed use the global encoder settings. Trim cuts the first 3.5 s.
              Rename &amp; organise moves the file into your TV or Movies folder with a Plex-style name (set up below the rules).</p>
          </fieldset>

          {preview && (
            <div className="rounded-lg bg-blue-50 dark:bg-blue-950/40 p-3 text-xs text-blue-800 dark:text-blue-200">
              In your library right now this matches <b>{preview.count}</b> file{preview.count === 1 ? "" : "s"} ({fmtSize(preview.total_size_bytes)})
              {preview.est_saved_bytes > 0 && <> — encoding would save about <b>{fmtSize(preview.est_saved_bytes)}</b></>}.
              {preview.items.slice(0, 5).map((i) => <div key={i.file_id} className="truncate opacity-80">• {i.filename}</div>)}
            </div>
          )}
          {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}
        </div>
        <div className="flex gap-2 border-t border-gray-200 dark:border-gray-700 px-5 py-3">
          <button className={btnGhost} onClick={runPreview} disabled={!actions.length}><Eye className="h-4 w-4" />Preview</button>
          <button className={`${btnPrimary} ml-auto`} onClick={save} disabled={busy || !name.trim() || !actions.length}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}Save rule
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Rename & organise ─────────────────────────────────────────────────────────

function OrganiseSection() {
  const [s, setS] = useState<OrganiseSettings | null>(null);
  const [sample, setSample] = useState("The.Bear.S03E01.1080p.WEB.h264-GROUP.mkv");
  const [example, setExample] = useState<{ ok: boolean; target?: string; error?: string; audience?: "general" | "mature"; reason?: string } | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState(false);

  useEffect(() => { api.pipeline.organise().then(setS).catch(() => {}); }, []);
  useEffect(() => {
    if (!s) return;
    const t = setTimeout(() => {
      const { warnings: _w, ...unsaved } = s;
      api.pipeline.organiseExample(sample, unsaved).then(setExample).catch(() => setExample(null));
    }, 250);
    return () => clearTimeout(t);
  }, [s, sample]);

  if (!s) return null;
  const set = (k: keyof OrganiseSettings, v: string) => { setS({ ...s, [k]: v }); setSaved(null); };
  const save = async () => {
    const { warnings: _w, ...data } = s;
    setS(await api.pipeline.saveOrganise(data));
    setSaved("Saved");
  };
  const field = (k: "organise_tv_dir" | "organise_movie_dir" | "organise_tv_mature_dir" | "organise_movie_mature_dir", label: string, ph: string) => (
    <label className="block">
      <span className="mb-1 block text-xs text-gray-500">{label}</span>
      <input className={input} value={s[k]} placeholder={ph} onChange={(e) => set(k, e.target.value)} />
      {s.warnings?.[k] && <span className="mt-1 block text-xs text-amber-700 dark:text-amber-400">{s.warnings[k]}</span>}
    </label>
  );

  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold text-gray-900 dark:text-gray-100">Rename &amp; organise</h3>
      <p className="mb-3 text-xs text-gray-500 dark:text-gray-400">
        Used by the <b>Rename &amp; organise</b> rule step and the Library's <b>Rename &amp; organise</b> action.
        Files get Plex-style names in these folders (paths as ScreenMate sees them). Downloads are removed from
        qBittorrent first (stops seeding, keeps the file). Matching subtitles move too; nothing is ever overwritten.
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {field("organise_tv_dir", "TV folder", "/mnt/video/TV Shows")}
        {field("organise_movie_dir", "Movies folder", "/mnt/video/Movies")}
        {field("organise_tv_mature_dir", "Mature TV folder (optional)", "/mnt/video/Mature TV Shows")}
        {field("organise_movie_mature_dir", "Mature movies folder (optional)", "/mnt/video/Mature Movies")}
      </div>
      <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
        With Mature folders set, each show or movie goes by its age rating (from TMDB):
        up to <b>PG-13 / TV-14</b> in the regular folders; <b>R, TV-MA</b> and anything unrated in the Mature ones.
        A show's <b>Who it's for</b> on the Watchlist overrides this. In Plex, share only the regular libraries
        with the kids' accounts.
      </p>
      {s.ratings_need_key && (
        <p className="mt-2 rounded-lg bg-amber-50 dark:bg-amber-950/40 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
          Ratings need a TMDB API key (Settings → Integrations). Until one is added, everything without a
          Watchlist choice goes to the Mature folders.
        </p>
      )}
      <button className="mt-2 text-xs text-blue-600 hover:underline dark:text-blue-400" onClick={() => setAdvanced((a) => !a)}>
        {advanced ? "Hide" : "Change"} naming templates
      </button>
      {advanced && (
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
          <label className="block"><span className="mb-1 block text-xs text-gray-500">TV template</span>
            <input className={`${input} font-mono text-xs`} value={s.organise_tv_template}
                   onChange={(e) => set("organise_tv_template", e.target.value)} /></label>
          <label className="block"><span className="mb-1 block text-xs text-gray-500">Movie template</span>
            <input className={`${input} font-mono text-xs`} value={s.organise_movie_template}
                   onChange={(e) => set("organise_movie_template", e.target.value)} /></label>
          <p className="text-xs text-gray-500 sm:col-span-2">
            Fields: <code>{"{show}"}</code> <code>{"{season:02d}"}</code> <code>{"{episode:02d}"}</code> <code>{"{title}"}</code> <code>{"{year}"}</code> <code>{"{ext}"}</code>. Use / for folders.
          </p>
        </div>
      )}
      <div className="mt-3 rounded-lg border border-gray-200 dark:border-gray-700 p-3">
        <label className="block"><span className="mb-1 block text-xs text-gray-500">Try a file name</span>
          <input className={input} value={sample} onChange={(e) => setSample(e.target.value)} /></label>
        {example && (
          <p className={`mt-2 break-all text-xs ${example.ok ? "text-green-700 dark:text-green-400" : "text-amber-700 dark:text-amber-400"}`}>
            {example.ok ? <>→ <span className="font-mono">{example.target}</span>
              {example.reason && <span className="ml-1 text-gray-500 dark:text-gray-400">({example.audience === "mature" ? "Mature" : "regular"}: {example.reason})</span>}</> : example.error}
          </p>
        )}
      </div>
      <div className="mt-3 flex items-center gap-3">
        <button className={btnPrimary} onClick={save}><Save className="h-4 w-4" />Save</button>
        {saved && <span className="flex items-center gap-1 text-xs text-green-700 dark:text-green-400"><CheckCircle2 className="h-3.5 w-3.5" />{saved}</span>}
      </div>
    </div>
  );
}

// ── Library preview ───────────────────────────────────────────────────────────

function LibraryPreview({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<PipelinePreview | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [msg, setMsg] = useState<string | null>(null);

  const load = () => api.pipeline.preview().then((d) => { setData(d); setPicked(new Set(d.items.map((i) => i.file_id))); });
  useEffect(() => { load(); }, []);

  const apply = async () => {
    const r = await api.pipeline.apply([...picked]);
    setMsg(`Queued ${r.queued} file${r.queued === 1 ? "" : "s"} — follow them on the Activity page.`);
    load();
  };
  const selected = data?.items.filter((i) => picked.has(i.file_id)) ?? [];

  return (
    <div className="mt-4 rounded-lg border border-gray-200 dark:border-gray-700">
      <div className="flex flex-wrap items-center gap-2 border-b border-gray-200 dark:border-gray-700 px-3 py-2 text-sm">
        <span className="font-medium text-gray-800 dark:text-gray-200">Dry run on your library</span>
        {data && (
          <span className="text-xs text-gray-500">
            {data.count} match · {fmtSize(data.total_size_bytes)}
            {data.est_saved_bytes > 0 && ` · ~${fmtSize(data.est_saved_bytes)} saved`}
          </span>
        )}
        <button onClick={onClose} aria-label="Close preview" className="ml-auto rounded p-1 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"><X className="h-4 w-4" /></button>
      </div>
      {!data ? (
        <Loader2 className="m-4 h-4 w-4 animate-spin text-blue-600" />
      ) : data.items.length === 0 ? (
        <p className="p-4 text-sm text-gray-500">No library files match your rules (finished, skipped and already-queued files are left out). Run a library scan to find files.</p>
      ) : (
        <>
          <ul className="max-h-72 divide-y divide-gray-100 dark:divide-gray-800 overflow-y-auto text-sm">
            {data.items.map((i) => (
              <li key={i.file_id} className="flex items-center gap-3 px-3 py-2">
                <input type="checkbox" aria-label={`Include ${i.filename}`} checked={picked.has(i.file_id)}
                       onChange={(e) => setPicked((s) => { const n = new Set(s); if (e.target.checked) n.add(i.file_id); else n.delete(i.file_id); return n; })} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-gray-900 dark:text-gray-100">{i.filename}</p>
                  <p className="text-xs text-gray-500">{i.codec} · {i.height}p · {fmtSize(i.size_bytes)} · {i.rule_name}: {i.actions.join(" → ")}</p>
                </div>
                {i.est_saved_bytes > 0 && <span className="shrink-0 text-xs text-green-600 dark:text-green-400">−{fmtSize(i.est_saved_bytes)}</span>}
              </li>
            ))}
          </ul>
          <div className="flex items-center gap-2 border-t border-gray-200 dark:border-gray-700 px-3 py-2">
            {msg && <span className="flex items-center gap-1 text-xs text-green-700 dark:text-green-400"><CheckCircle2 className="h-3.5 w-3.5" />{msg}</span>}
            <button className={`${btnPrimary} ml-auto`} disabled={!selected.length} onClick={apply}>
              <Play className="h-4 w-4" />Apply to {selected.length} file{selected.length === 1 ? "" : "s"}
            </button>
          </div>
        </>
      )}
      <p className="px-3 pb-2 text-xs text-gray-400">Estimates use the average saving of your past encodes (or ~{Math.round((data?.encode_ratio ?? 0.45) * 100)} % before you have any).</p>
    </div>
  );
}

// ── Section ───────────────────────────────────────────────────────────────────

export default function PipelineSettings() {
  const [settings, setSettings] = useState<PSettings | null>(null);
  const [rules, setRules] = useState<PipelineRule[]>([]);
  const [templates, setTemplates] = useState<PipelineTemplate[]>([]);
  const [editing, setEditing] = useState<(PipelineRuleInput & { id?: number }) | null>(null);
  const [mappings, setMappings] = useState<PathMappingRow[]>([]);
  const [mapMsg, setMapMsg] = useState<string | null>(null);
  const [testPath, setTestPath] = useState("");
  const [testResult, setTestResult] = useState<PathTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [showPreview, setShowPreview] = useState(false);

  const loadRules = () => api.pipeline.rules().then(setRules).catch(() => {});
  useEffect(() => {
    api.pipeline.settings().then(setSettings).catch(() => {});
    api.pipeline.templates().then(setTemplates).catch(() => {});
    api.pipeline.mappings().then(setMappings).catch(() => {});
    loadRules();
  }, []);

  const toggle = async (key: keyof PSettings) => {
    if (!settings) return;
    setSettings(await api.pipeline.saveSettings({ [key]: !settings[key] }));
  };
  const move = async (idx: number, dir: -1 | 1) => {
    const ids = rules.map((r) => r.id);
    [ids[idx], ids[idx + dir]] = [ids[idx + dir], ids[idx]];
    setRules(await api.pipeline.reorder(ids));
  };
  const setEnabled = async (r: PipelineRule) => {
    await api.pipeline.updateRule(r.id, { ...r, enabled: !r.enabled });
    loadRules();
  };
  const remove = async (r: PipelineRule) => {
    if (!confirm(`Delete the rule "${r.name}"?`)) return;
    await api.pipeline.deleteRule(r.id);
    loadRules();
  };
  const saveMappings = async () => {
    setMapMsg(null);
    try {
      setMappings(await api.pipeline.saveMappings(mappings.filter((m) => m.remote_prefix || m.local_prefix)));
      setMapMsg("Saved");
    } catch (e) {
      setMapMsg((e as Error).message);
    }
  };
  const runTest = async () => {
    setTesting(true);
    try { setTestResult(await api.pipeline.testPath(testPath)); } catch (e) { setMapMsg((e as Error).message); }
    finally { setTesting(false); }
  };

  const Toggle = ({ k, label, hint }: { k: keyof PSettings; label: string; hint: string }) => (
    <label className="flex items-start gap-3">
      <input type="checkbox" className="mt-1" checked={!!settings?.[k]} onChange={() => toggle(k)} />
      <span>
        <span className="block text-sm font-medium text-gray-800 dark:text-gray-200">{label}</span>
        <span className="block text-xs text-gray-500 dark:text-gray-400">{hint}</span>
      </span>
    </label>
  );

  return (
    <div className="space-y-6">
      <p className="text-sm text-gray-600 dark:text-gray-400">
        When a download finishes, ScreenMate checks your rules from top to bottom and runs the first one that matches —
        then refreshes Plex/Jellyfin and notifies you. Progress shows on the Activity page.
      </p>

      <div className="space-y-3">
        <Toggle k="pipeline_enabled" label="Run rules on finished downloads" hint="Turn off to pause all automatic processing." />
        <Toggle k="pipeline_remove_torrent" label="Remove the torrent before processing (files are kept)"
                hint="Changing a file while it's still seeding breaks the torrent. If off, ScreenMate waits until the torrent is removed." />
        <Toggle k="pipeline_on_scan" label="Also run rules on new files found by a library scan"
                hint="Off by default so a first scan doesn't queue your whole library — use the dry run below instead." />
      </div>

      {/* Rules */}
      <div>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Rules</h3>
          <button className={`${btnGhost} ml-auto`} onClick={() => setShowPreview((v) => !v)} disabled={!rules.length}>
            <Eye className="h-4 w-4" />Dry run on library
          </button>
          <button className={btnPrimary}
                  onClick={() => setEditing({ name: "", enabled: true, match: { source: "any" }, actions: [{ type: "encode" }] })}>
            <Plus className="h-4 w-4" />Add rule
          </button>
        </div>
        {rules.length === 0 ? (
          <div className="rounded-lg border border-dashed border-gray-300 dark:border-gray-600 p-4">
            <p className="mb-3 text-sm text-gray-600 dark:text-gray-400">No rules yet. Start from one of these:</p>
            <div className="grid gap-2 sm:grid-cols-2">
              {templates.map((t) => (
                <button key={t.name} onClick={() => setEditing({ name: t.name, enabled: true, match: t.match, actions: t.actions })}
                        className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 text-left hover:border-blue-400 hover:bg-blue-50/50 dark:hover:bg-blue-950/30">
                  <span className="block text-sm font-medium text-gray-900 dark:text-gray-100">{t.name}</span>
                  <span className="block text-xs text-gray-500 dark:text-gray-400">{t.description}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <ol className="divide-y divide-gray-100 dark:divide-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
            {rules.map((r, i) => (
              <li key={r.id} className={`flex items-center gap-3 px-3 py-2.5 ${r.enabled ? "" : "opacity-60"}`}>
                <input type="checkbox" checked={r.enabled} onChange={() => setEnabled(r)} aria-label={`Enable ${r.name}`} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-gray-900 dark:text-gray-100">{i + 1}. {r.name}</p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    {describeMatch(r.match)} → {r.actions.map(stepChip).join(" → ")}
                  </p>
                </div>
                <div className="flex shrink-0 items-center">
                  <button disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up" className="rounded p-1.5 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-30"><ArrowUp className="h-3.5 w-3.5" /></button>
                  <button disabled={i === rules.length - 1} onClick={() => move(i, 1)} aria-label="Move down" className="rounded p-1.5 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-30"><ArrowDown className="h-3.5 w-3.5" /></button>
                  <button onClick={() => setEditing(r)} aria-label="Edit" className="rounded p-1.5 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"><Edit2 className="h-3.5 w-3.5" /></button>
                  <button onClick={() => remove(r)} aria-label="Delete" className="rounded p-1.5 text-gray-400 hover:bg-red-50 dark:hover:bg-red-950 hover:text-red-500"><Trash2 className="h-3.5 w-3.5" /></button>
                </div>
              </li>
            ))}
          </ol>
        )}
        {showPreview && <LibraryPreview onClose={() => setShowPreview(false)} />}
      </div>

      <OrganiseSection />

      {/* Path mappings */}
      <div>
        <h3 className="mb-1 text-sm font-semibold text-gray-900 dark:text-gray-100">Path mapping</h3>
        <p className="mb-2 text-xs text-gray-500 dark:text-gray-400">
          Only needed if qBittorrent sees your downloads under a different path than ScreenMate does
          (e.g. qBittorrent saves to <code>/downloads</code>, mounted here as <code>/mnt/nas/downloads</code>).
        </p>
        <div className="space-y-2">
          {mappings.map((m, i) => (
            <div key={i} className="flex items-center gap-2">
              <input className={input} placeholder="qBittorrent path, e.g. /downloads" value={m.remote_prefix}
                     onChange={(e) => setMappings((ms) => ms.map((x, j) => (j === i ? { ...x, remote_prefix: e.target.value } : x)))} />
              <span className="text-gray-400">→</span>
              <input className={input} placeholder="ScreenMate path, e.g. /mnt/nas/downloads" value={m.local_prefix}
                     onChange={(e) => setMappings((ms) => ms.map((x, j) => (j === i ? { ...x, local_prefix: e.target.value } : x)))} />
              <button aria-label="Remove mapping" onClick={() => setMappings((ms) => ms.filter((_, j) => j !== i))}
                      className="rounded p-1.5 text-gray-400 hover:bg-red-50 dark:hover:bg-red-950 hover:text-red-500"><Trash2 className="h-4 w-4" /></button>
            </div>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button className={btnGhost} onClick={() => setMappings((ms) => [...ms, { remote_prefix: "", local_prefix: "" }])}><Plus className="h-4 w-4" />Add mapping</button>
          <button className={btnPrimary} onClick={saveMappings}><Save className="h-4 w-4" />Save mappings</button>
          {mapMsg && <span className="text-xs text-gray-500">{mapMsg}</span>}
        </div>

        <div className="mt-4 flex gap-2">
          <input className={input} placeholder="Test a path as qBittorrent reports it, e.g. /downloads/Show.S01E01"
                 value={testPath} onChange={(e) => setTestPath(e.target.value)} onKeyDown={(e) => e.key === "Enter" && testPath && runTest()} />
          <button className={btnGhost} disabled={!testPath || testing} onClick={runTest}>
            {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <FolderSearch className="h-4 w-4" />}Test
          </button>
        </div>
        {testResult && (
          <div className={`mt-2 rounded-lg p-3 text-xs ${testResult.exists ? "bg-green-50 dark:bg-green-950/40 text-green-800 dark:text-green-200" : "bg-red-50 dark:bg-red-950/40 text-red-800 dark:text-red-200"}`}>
            <p>Maps to <code>{testResult.mapped}</code> — {testResult.exists ? `found ${testResult.video_files} video file(s)` : "not found here"}.</p>
            {testResult.files.map((f) => (
              <p key={f.path} className="mt-1 truncate">
                • {f.filename ?? f.path}: {f.error ? f.error : f.rule_name ? `${f.rule_name} → ${f.actions?.join(" → ")}` : "no rule matches"}
              </p>
            ))}
          </div>
        )}
      </div>

      {editing && <RuleModal initial={editing} onClose={() => setEditing(null)} onSaved={loadRules} />}
    </div>
  );
}
