import { useEffect, useState } from "react";
import { CheckCircle2, Cpu, Loader2, RefreshCw, Save, Trash2, XCircle } from "lucide-react";
import { api, type MediaSettings as MS } from "../api/client";
import { fmtSize } from "../utils";

// Settings → Media engine (FileSplitter's Settings tab): where media lives,
// encoder, scene detection, splitting, the saved-space counter and the trash.

const input = "w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:border-blue-500 focus:outline-none";
const btn = "flex min-h-[36px] items-center gap-1.5 rounded-lg px-3 text-sm disabled:opacity-50";
const btnGhost = `${btn} border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800`;
const btnPrimary = `${btn} bg-blue-600 font-medium text-white hover:bg-blue-700`;

// Editable keys and how to read them back from the form.
const NUMERIC = [
  "x265_crf", "max_workers", "ffprobe_timeout", "scene_threshold", "min_scene_duration", "black_min_duration",
  "black_pix_th", "split_min_duration", "split_min_size", "split_min_episode_gap", "split_black_min_duration",
  "split_freeze_min_duration", "split_episode_count",
] as const;
const TEXT = [
  "x265_preset", "target_resolution", "encoder_backend", "scene_method", "split_scene_method", "split_keywords",
  "track_cleanup", "keep_audio_langs", "keep_sub_langs",
] as const;

type Form = Record<string, string>;

function Num({ label, hint, k, form, set, step = "1" }: { label: string; hint?: string; k: string; form: Form; set: (k: string, v: string) => void; step?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">{label}</span>
      {hint && <span className="mb-1 block text-xs text-gray-500 dark:text-gray-400">{hint}</span>}
      <input className={input} type="number" step={step} value={form[k] ?? ""} onChange={(e) => set(k, e.target.value)} />
    </label>
  );
}

function Pick({ label, hint, k, form, set, options }: { label: string; hint?: string; k: string; form: Form; set: (k: string, v: string) => void; options: [string, string][] }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">{label}</span>
      {hint && <span className="mb-1 block text-xs text-gray-500 dark:text-gray-400">{hint}</span>}
      <select className={input} value={form[k] ?? ""} onChange={(e) => set(k, e.target.value)}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
}

export default function MediaSettings() {
  const [s, setS] = useState<MS | null>(null);
  const [form, setForm] = useState<Form>({});
  const [paths, setPaths] = useState<string[]>([]);
  const [tpdb, setTpdb] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [rechecking, setRechecking] = useState(false);
  const [trash, setTrash] = useState<{ size_bytes: number; file_count: number } | null>(null);
  const [saved, setSaved] = useState<number | null>(null);
  const [savedInput, setSavedInput] = useState("");

  const load = async () => {
    const [settings, t, stats] = await Promise.all([api.media.settings(), api.media.trash(), api.media.stats()]);
    setS(settings);
    setPaths(settings.media_paths);
    setForm(Object.fromEntries([...NUMERIC, ...TEXT].map((k) => [k, String((settings as unknown as Record<string, unknown>)[k] ?? "")])));
    setTrash(t);
    setSaved(stats.total_saved_bytes);
  };
  useEffect(() => { load().catch(() => {}); }, []);

  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));
  const nvenc = form.encoder_backend === "nvenc";
  const presets = nvenc ? s?.nvenc_presets ?? [] : s?.cpu_presets ?? [];

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const body: Record<string, unknown> = {};  // folders are edited in Settings → Folders
      for (const k of NUMERIC) if (form[k] !== "") body[k] = Number(form[k]);
      for (const k of TEXT) body[k] = form[k];
      if (tpdb) body.tpdb_api_key = tpdb;
      await api.media.saveSettings(body);
      setTpdb("");
      await load();
      setMsg("Saved — new jobs use these settings.");
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const recheck = async () => {
    setRechecking(true);
    try {
      const r = await api.media.recheckGpu();
      setS((cur) => cur && { ...cur, nvenc_available: r.nvenc_available, nvenc_last_error: r.nvenc_last_error, nvenc_probe_done: true });
    } finally {
      setRechecking(false);
    }
  };

  if (!s) return <Loader2 className="h-5 w-5 animate-spin text-blue-600" />;

  return (
    <div className="space-y-6">
      {/* Media folders come from Settings → Folders */}
      <div>
        <h3 className="mb-1 text-sm font-semibold text-gray-900 dark:text-gray-100">Media folders</h3>
        <p className="mb-2 text-xs text-gray-500 dark:text-gray-400">
          The Library scans the folders ticked <b>Scan &amp; encode</b> in{" "}
          <a href="#folders" className="font-medium text-blue-600 hover:underline dark:text-blue-400">Folders</a>.
          Each one becomes a category.
        </p>
        {paths.length ? (
          <ul className="space-y-1">
            {paths.map((p) => (
              <li key={p} className="rounded bg-gray-50 dark:bg-gray-800 px-2 py-1 font-mono text-xs text-gray-700 dark:text-gray-200">{p}</li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-amber-700 dark:text-amber-400">No folders are scanned yet. Tick “Scan &amp; encode” on a folder in Folders.</p>
        )}
      </div>

      {/* Encoder */}
      <div>
        <h3 className="mb-2 text-sm font-semibold text-gray-900 dark:text-gray-100">Encoder</h3>
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-gray-200 dark:border-gray-700 px-3 py-2 text-sm">
          <Cpu className="h-4 w-4 text-gray-400" />
          {!s.nvenc_probe_done ? (
            <span className="text-gray-500">Checking for an NVIDIA GPU…</span>
          ) : s.nvenc_available ? (
            <span className="flex items-center gap-1 text-green-700 dark:text-green-400"><CheckCircle2 className="h-4 w-4" />GPU detected (NVENC)</span>
          ) : (
            <span className="flex items-center gap-1 text-gray-600 dark:text-gray-300"><XCircle className="h-4 w-4 text-gray-400" />No GPU — encoding on the CPU</span>
          )}
          <button className={`${btnGhost} ml-auto`} onClick={recheck} disabled={rechecking}>
            <RefreshCw className={`h-4 w-4 ${rechecking ? "animate-spin" : ""}`} />Recheck GPU
          </button>
          {s.nvenc_last_error && !s.nvenc_available && (
            <p className="w-full text-xs text-gray-500 dark:text-gray-400" title={s.nvenc_last_error}>Last GPU check: {s.nvenc_last_error}</p>
          )}
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Pick label="Encoder" k="encoder_backend" form={form} set={set}
                hint={nvenc && !s.nvenc_available ? "No GPU detected — jobs will fall back to the CPU." : "GPU is 5–10× faster; quality is slightly lower at the same number."}
                options={[["cpu", "CPU (libx265)"], ["nvenc", "NVIDIA GPU (hevc_nvenc)"]]} />
          <Num label={nvenc ? "Quality (CQ)" : "Quality (CRF)"} k="x265_crf" form={form} set={set}
               hint={nvenc ? "Lower = better and bigger. 22–24 looks like CRF 28." : "Lower = better and bigger. 18 high · 28 good · 35 small."} />
          <Pick label="Speed preset" k="x265_preset" form={form} set={set} options={presets.map((p) => [p, p])}
                hint="Slower presets make smaller files at the same quality." />
          <Pick label="Default output resolution" k="target_resolution" form={form} set={set}
                options={s.resolutions.map((r) => [r, r === "original" ? "Keep original" : r])} />
          <Num label="Simultaneous jobs" k="max_workers" form={form} set={set} hint="ffmpeg jobs running at once. Applies immediately." />
          <Num label="ffprobe timeout (seconds)" k="ffprobe_timeout" form={form} set={set} hint="Raise this for slow network storage." />
        </div>
      </div>

      {/* Track clean-up */}
      <div>
        <h3 className="mb-1 text-sm font-semibold text-gray-900 dark:text-gray-100">Audio &amp; subtitle tracks</h3>
        <p className="mb-2 text-xs text-gray-500 dark:text-gray-400">
          When encoding, keep only the languages you watch. The original-language audio (the file's main track) is always
          kept, and so are forced subtitles and tracks with no language label. Often saves another 10–20 %.
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <Pick label="Clean up tracks when encoding" k="track_cleanup" form={form} set={set}
                options={[["true", "Yes: keep the languages below"], ["false", "No: keep every track"]]} />
          <div className="hidden sm:block" />
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Audio languages to keep</span>
            <span className="mb-1 block text-xs text-gray-500 dark:text-gray-400">Plus the original language. Codes or names: eng, fre, jpn…</span>
            <input className={input} value={form.keep_audio_langs ?? ""} onChange={(e) => set("keep_audio_langs", e.target.value)} placeholder="eng" />
          </label>
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Subtitle languages to keep</span>
            <span className="mb-1 block text-xs text-gray-500 dark:text-gray-400">Plus forced subtitles (foreign-language parts).</span>
            <input className={input} value={form.keep_sub_langs ?? ""} onChange={(e) => set("keep_sub_langs", e.target.value)} placeholder="eng" />
          </label>
        </div>
      </div>

      {/* Anthologies & splitting */}
      <div>
        <h3 className="mb-1 text-sm font-semibold text-gray-900 dark:text-gray-100">Anthologies & splitting</h3>
        <p className="mb-2 text-xs text-gray-500 dark:text-gray-400">
          A file is an anthology when it's long <i>or</i> big <i>and</i> its name has one of the keywords. Episode-named files (S01E02) never are.
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <Num label="Long: at least (seconds)" k="split_min_duration" form={form} set={set} hint="5400 = 90 minutes" />
          <Num label="Big: at least (bytes)" k="split_min_size" form={form} set={set} hint={`${fmtSize(Number(form.split_min_size) || 0)}`} />
          <label className="block sm:col-span-2">
            <span className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">Keywords</span>
            <input className={input} value={form.split_keywords ?? ""} onChange={(e) => set("split_keywords", e.target.value)} placeholder="Vol, Compilation, Anthology" />
          </label>
          <Pick label="Split by" k="split_scene_method" form={form} set={set}
                options={[["combined", "Black or frozen frames (recommended)"], ["black", "Black frames"], ["title", "Frozen frames (title cards)"], ["select", "Scene changes"]]} />
          <Num label="Shortest part (seconds)" k="split_min_episode_gap" form={form} set={set} hint="Cuts closer together than this are ignored." />
          <Num label="Black gap at least (seconds)" k="split_black_min_duration" form={form} set={set} step="0.1" />
          <Num label="Freeze at least (seconds)" k="split_freeze_min_duration" form={form} set={set} step="0.1" />
          <Num label="Default number of parts" k="split_episode_count" form={form} set={set} hint="0 = detect automatically" />
          <label className="block">
            <span className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">ThePornDB API key</span>
            <span className="mb-1 block text-xs text-gray-500 dark:text-gray-400">
              Optional: names split scenes by matching frames online. {s.tpdb_api_key_set ? "A key is saved." : "Not set."}
            </span>
            <input className={input} type="password" autoComplete="new-password" value={tpdb} onChange={(e) => setTpdb(e.target.value)}
                   placeholder={s.tpdb_api_key_set ? "•••••••• (leave blank to keep)" : ""} />
          </label>
        </div>
        <details className="mt-3">
          <summary className="cursor-pointer text-sm text-gray-600 dark:text-gray-300">Scene detection (advanced)</summary>
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            <Pick label="Method" k="scene_method" form={form} set={set}
                  options={[["auto", "Auto (frame difference, then black)"], ["select", "Frame difference"], ["black", "Fade to black"]]} />
            <Num label="Sensitivity" k="scene_threshold" form={form} set={set} step="0.05" hint="0 = everything is a scene, 1 = nothing. 0.4 is a good start." />
            <Num label="Minimum scene length (seconds)" k="min_scene_duration" form={form} set={set} />
            <Num label="Black frame length (seconds)" k="black_min_duration" form={form} set={set} step="0.1" />
            <Num label="Black pixel threshold" k="black_pix_th" form={form} set={set} step="0.01" />
          </div>
        </details>
      </div>

      <div className="flex items-center gap-3">
        <button className={btnPrimary} onClick={save} disabled={saving}>
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}Save media settings
        </button>
        {msg && <span className="text-sm text-gray-600 dark:text-gray-300">{msg}</span>}
      </div>

      {/* Saved counter & trash */}
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3">
          <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Space saved by encoding</h4>
          <p className="my-1 text-2xl font-semibold text-green-700 dark:text-green-400">{saved ? fmtSize(saved) : "0 B"}</p>
          <div className="flex gap-2">
            <input className={input} type="number" placeholder="Set to (GB)" value={savedInput} onChange={(e) => setSavedInput(e.target.value)} aria-label="Set saved total in GB" />
            <button className={btnGhost} disabled={savedInput === ""}
                    onClick={async () => { const r = await api.media.setSavedBytes(Math.round(Number(savedInput) * 1e9)); setSaved(r.total_saved_bytes); setSavedInput(""); }}>Set</button>
            <button className={btnGhost}
                    onClick={async () => { if (confirm("Reset the saved-space counter to zero?")) { await api.media.setSavedBytes(0); setSaved(0); } }}>Reset</button>
          </div>
        </div>
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 p-3">
          <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Trash</h4>
          <p className="my-1 text-sm text-gray-600 dark:text-gray-300">
            {trash ? `${fmtSize(trash.size_bytes) === "—" ? "0 B" : fmtSize(trash.size_bytes)} in ${trash.file_count} file${trash.file_count === 1 ? "" : "s"}` : "…"}
          </p>
          <p className="mb-2 text-xs text-gray-500 dark:text-gray-400">Deleted files wait here 30 days, then go for good.</p>
          <button className={`${btnGhost} text-red-600 dark:text-red-400`} disabled={!trash?.file_count}
                  onClick={async () => {
                    if (!confirm("Permanently delete everything in the trash?")) return;
                    const r = await api.media.emptyTrash();
                    setTrash({ size_bytes: 0, file_count: 0 });
                    setMsg(`Freed ${fmtSize(r.freed_bytes)}.`);
                  }}>
            <Trash2 className="h-4 w-4" />Empty trash
          </button>
        </div>
      </div>
    </div>
  );
}
