import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Gauge, Loader2, Sparkles, Table2, BarChart3 } from "lucide-react";
import { api, type Insights as Data } from "../api/client";
import { useEvent } from "../events";
import { BarList, ColumnChart, TableView, type Datum } from "../components/charts";
import { fmtSize } from "../utils";

// What MediaMate has done for the library, and what's left to gain.

const RANGES = [7, 30, 90] as const;

const size = (n: number) => (n ? fmtSize(n) : "0 B");
const count = (n: number) => n.toLocaleString();
const pct = (n: number) => `${Math.round(n * 100)}%`;
const day = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });

function Card({ title, subtitle, children, table }: {
  title: string; subtitle?: string; children: React.ReactNode; table?: React.ReactNode;
}) {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className="min-w-0 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4 shadow-sm">
      <div className="mb-3 flex items-start gap-2">
        <div className="flex-1">
          <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100">{title}</h2>
          {subtitle && <p className="text-xs text-gray-500 dark:text-gray-400">{subtitle}</p>}
        </div>
        {table && (
          <button onClick={() => setAsTable((v) => !v)} aria-pressed={asTable}
                  title={asTable ? "Show as chart" : "Show as table"}
                  className="rounded p-1.5 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 hover:text-gray-700 dark:hover:text-gray-200">
            {asTable ? <BarChart3 className="h-4 w-4" /> : <Table2 className="h-4 w-4" />}
          </button>
        )}
      </div>
      {asTable ? table : children}
    </section>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4">
      <div className="text-xs text-gray-500 dark:text-gray-400">{label}</div>
      <div className="mt-1 text-xl sm:text-2xl font-semibold text-gray-900 dark:text-gray-100">{value}</div>
      {note && <div className="text-xs text-gray-500 dark:text-gray-400">{note}</div>}
    </div>
  );
}

export default function Insights() {
  const [days, setDays] = useState<(typeof RANGES)[number]>(30);
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => api.insights(days).then((d) => { setData(d); setError(null); }).catch((e) => setError((e as Error).message));
  useEffect(() => { load(); }, [days]); // eslint-disable-line react-hooks/exhaustive-deps
  useEvent("journey.updated", (j: { status?: string }) => { if (j?.status === "done") load(); });

  if (error) return <p className="p-6 text-sm text-red-600">{error}</p>;
  if (!data) return <div className="flex justify-center p-10"><Loader2 className="h-5 w-5 animate-spin text-blue-600" /></div>;

  const t = data.totals;
  const savings: Datum[] = data.savings_by_day.map((d) => ({
    key: d.date, label: day(d.date), value: d.saved_bytes, detail: `${d.encodes} encode${d.encodes === 1 ? "" : "s"}`,
  }));
  const downloads: Datum[] = data.downloads_by_day.map((d) => ({
    key: d.date, label: day(d.date), value: d.count, detail: size(d.bytes),
  }));
  const codecs: Datum[] = data.codec_mix.map((c) => ({
    key: c.codec, label: c.label, value: c.bytes, detail: `${count(c.files)} file${c.files === 1 ? "" : "s"}`,
  }));
  const res: Datum[] = data.resolution_mix.map((r) => ({
    key: r.label, label: r.label, value: r.bytes, detail: `${count(r.files)} file${r.files === 1 ? "" : "s"}`,
  }));
  const speed: Datum[] = data.speed.map((s) => ({
    key: s.encoder, label: s.label, value: s.realtime_x, detail: `${s.jobs} job${s.jobs === 1 ? "" : "s"}`,
  }));
  const f = data.forecast;
  const realtime = (n: number) => `${n.toFixed(n < 10 ? 1 : 0)}× real time`;

  return (
    <div className="mx-auto max-w-6xl p-4 md:p-6">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h1 className="mr-auto text-xl font-bold text-gray-900 dark:text-gray-100">Insights</h1>
        <div className="flex rounded-lg border border-gray-200 dark:border-gray-700 p-0.5" role="group" aria-label="Time range">
          {RANGES.map((r) => (
            <button key={r} onClick={() => setDays(r)} aria-pressed={days === r}
                    className={`rounded-md px-3 py-1 text-sm ${days === r ? "bg-blue-600 text-white" : "text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800"}`}>
              {r} days
            </button>
          ))}
        </div>
      </div>

      {/* Hero + KPI row */}
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-[1.3fr_repeat(4,1fr)]">
        <div className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4 col-span-2 lg:col-span-1">
          <div className="text-xs text-gray-500 dark:text-gray-400">Space saved by encoding, all time</div>
          <div className="mt-1 text-5xl font-semibold tracking-tight text-gray-900 dark:text-gray-100">{size(t.saved_bytes)}</div>
        </div>
        <Stat label={`Saved, last ${days} days`} value={size(t.saved_bytes_period)} note={`${count(t.encodes_period)} encodes`} />
        <Stat label={`Downloaded, last ${days} days`} value={count(t.downloads_period)} note={size(t.downloaded_bytes_period)} />
        <Stat label="Library" value={size(t.library_bytes)} note={`${count(t.library_files)} files`} />
        <Stat label="Library already in HEVC" value={pct(t.hevc_share)} note="share of total size" />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="min-w-0 lg:col-span-2">
          <Card title="Space saved per day" subtitle={`Finished encodes, last ${days} days`}
                table={<TableView data={[...savings].reverse()} valueLabel="Saved" format={size} />}>
            {t.saved_bytes_period === 0
              ? <p className="py-8 text-center text-sm text-gray-500">No encodes finished in this period.</p>
              : <ColumnChart data={savings} format={size} ariaLabel={`Space saved per day over the last ${days} days`} />}
          </Card>
        </div>

        {/* Forecast */}
        <section className="rounded-xl border border-blue-200 dark:border-blue-900 bg-blue-50 dark:bg-blue-950/30 p-4">
          <h2 className="flex items-center gap-1.5 text-sm font-semibold text-blue-900 dark:text-blue-100"><Sparkles className="h-4 w-4" />What's left to gain</h2>
          {f.files === 0 ? (
            <p className="mt-2 text-sm text-blue-900/80 dark:text-blue-100/80">Everything in your library is HEVC or done. Nice.</p>
          ) : (
            <>
              <p className="mt-2 text-3xl font-semibold text-blue-950 dark:text-blue-50">~{size(f.est_saved_bytes)}</p>
              <p className="mt-1 text-sm text-blue-900/80 dark:text-blue-100/80">
                {count(f.files)} file{f.files === 1 ? " isn't" : "s aren't"} in HEVC yet ({size(f.bytes)}). Encoding {f.files === 1 ? "it" : "them"} would save
                about {pct(f.ratio)}, based on {data.speed.length ? "your past encodes" : "typical results"}
                {f.est_hours != null ? <> — roughly <b>{f.est_hours < 1 ? "under an hour" : `${f.est_hours} hours`}</b> on the {f.encoder === "nvenc" ? "GPU" : "CPU"}.</> : "."}
              </p>
              <div className="mt-3 flex flex-wrap gap-2 text-sm">
                <Link to="/library?status=pending" className="rounded-lg bg-blue-600 px-3 py-1.5 font-medium text-white hover:bg-blue-700">Open in Library</Link>
                <Link to="/settings" className="rounded-lg border border-blue-300 dark:border-blue-800 px-3 py-1.5 text-blue-800 dark:text-blue-200 hover:bg-blue-100 dark:hover:bg-blue-900/40">Automate with a rule</Link>
              </div>
            </>
          )}
        </section>

        <Card title="Encoding speed" subtitle="Seconds of video encoded per second of work"
              table={<TableView data={speed} valueLabel="Speed" format={realtime} />}>
          {speed.length === 0
            ? <p className="py-6 text-center text-sm text-gray-500"><Gauge className="mx-auto mb-1 h-5 w-5 text-gray-300" />Speeds appear after your first encodes.</p>
            : <BarList data={speed} format={realtime} ariaLabel="Encoding speed by encoder" />}
          {speed.length === 1 && (
            <p className="mt-3 text-xs text-gray-500 dark:text-gray-400">
              {speed[0].key === "cpu" ? "Encodes on an NVIDIA GPU are typically 5–10× faster — see Settings → Media engine." : "Only GPU encodes so far."}
            </p>
          )}
        </Card>

        <Card title="Library by codec" subtitle="Share of total size"
              table={<TableView data={codecs} valueLabel="Size" format={size} />}>
          {codecs.length ? <BarList data={codecs} format={size} ariaLabel="Library size by codec" />
                         : <p className="py-6 text-center text-sm text-gray-500">Scan your library to see its make-up.</p>}
        </Card>

        <Card title="Library by resolution" subtitle="Share of total size"
              table={<TableView data={res} valueLabel="Size" format={size} />}>
          {res.length ? <BarList data={res} format={size} ariaLabel="Library size by resolution" />
                      : <p className="py-6 text-center text-sm text-gray-500">Scan your library to see its make-up.</p>}
        </Card>

        <Card title="Downloads per day" subtitle={`Torrents sent to qBittorrent, last ${days} days`}
              table={<TableView data={[...downloads].reverse()} valueLabel="Downloads" format={count} />}>
          {t.downloads_period === 0
            ? <p className="py-8 text-center text-sm text-gray-500">No downloads in this period.</p>
            : <ColumnChart data={downloads} format={(n) => `${count(n)} download${n === 1 ? "" : "s"}`} axisFormat={count}
                           height={160} ariaLabel={`Downloads per day over the last ${days} days`} />}
        </Card>

        <Card title="Biggest savings">
          {data.top_savings.length === 0 ? <p className="py-6 text-center text-sm text-gray-500">Nothing encoded yet.</p> : (
            <ol className="divide-y divide-gray-100 dark:divide-gray-800 text-sm">
              {data.top_savings.map((s, i) => (
                <li key={i} className="flex items-center gap-3 py-2">
                  <span className="w-4 text-xs text-gray-400">{i + 1}</span>
                  <span className="min-w-0 flex-1 truncate text-gray-800 dark:text-gray-200" title={s.filename}>{s.filename}</span>
                  <span className="shrink-0 font-medium tabular-nums text-gray-900 dark:text-gray-100">−{size(s.saved_bytes)}</span>
                </li>
              ))}
            </ol>
          )}
        </Card>
      </div>
    </div>
  );
}
