#!/usr/bin/env node
// Parity smoke test: drives the real app in Chromium against a running backend.
//
//   node tests/smoke.cjs [BASE_URL] [SCREENSHOT_DIR]
//
// Start the backend with MEDIA_PATHS pointing at a folder that holds at least one
// small h264 clip (the encode check needs ffmpeg), and vite or the built app at
// BASE_URL (default http://localhost:4173). Exits non-zero on the first failure.
const { chromium } = require("playwright");

const BASE = process.argv[2] || "http://localhost:4173";
const SHOTS = process.argv[3] || null;
const PAGES = ["search", "watchlist", "calendar", "browse", "activity", "library", "insights",
               "history", "settings", "updates"];

const results = [];
async function check(name, fn) {
  try {
    await fn();
    results.push(["ok", name]);
  } catch (e) {
    results.push(["FAIL", `${name}: ${e.message.split("\n")[0]}`]);
  }
}

async function api(path, init) {
  const r = await fetch(`${BASE}/api${path}`, init);
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.json();
}

(async () => {
  const browser = await chromium.launch();
  await fetch(`${BASE}/api/onboarding/complete`, { method: "POST" }).catch(() => {});

  for (const scheme of ["light", "dark"]) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, colorScheme: scheme });
  await ctx.addInitScript(() => localStorage.setItem('sm-tour', '2'));  // the tour is checked on its own
    const page = await ctx.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    for (const p of PAGES) {
      await check(`${scheme}: /${p} loads without errors`, async () => {
        errors.length = 0;
        await page.goto(`${BASE}/${p}`);
        await page.locator("main h1, main h2").first().waitFor({ timeout: 10000 });
        await page.waitForTimeout(300);
        if (errors.length) throw new Error(errors[0]);
        if (SHOTS) await page.screenshot({ path: `${SHOTS}/${scheme}-${p}.png` });
      });
    }
    await ctx.close();
  }

  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, acceptDownloads: true });
  await ctx.addInitScript(() => localStorage.setItem('sm-tour', '2'));  // the tour is checked on its own
  const page = await ctx.newPage();

  await check("/health reports a version", async () => {
    const h = await (await fetch(`${BASE}/health`)).json();
    if (h.status !== "ok" || !/^\d+\.\d+\.\d+$/.test(h.version)) throw new Error(JSON.stringify(h));
  });

  await check("/downloads redirects to /activity", async () => {
    await page.goto(`${BASE}/downloads`);
    await page.waitForURL("**/activity");
  });

  await check("theme buttons switch light and dark", async () => {
    await page.goto(`${BASE}/search`);
    await page.getByTitle("Dark theme").click();
    if (!(await page.evaluate(() => document.documentElement.classList.contains("dark")))) throw new Error("not dark");
    await page.getByTitle("Light theme").click();
    if (await page.evaluate(() => document.documentElement.classList.contains("dark"))) throw new Error("still dark");
  });

  await check("sidebar collapses and expands", async () => {
    await page.getByTitle("Collapse sidebar").click();
    await page.getByTitle("Expand sidebar").click();
  });

  await check("Ctrl+K palette jumps to a page", async () => {
    await page.keyboard.press("Control+k");
    const pal = page.getByRole("dialog", { name: "Command palette" });
    await pal.getByRole("combobox").fill("history");
    await page.keyboard.press("Enter");
    await page.waitForURL("**/history");
  });

  await check("backup export downloads a file", async () => {
    await page.goto(`${BASE}/settings`);
    const [dl] = await Promise.all([
      page.waitForEvent("download", { timeout: 15000 }),
      page.getByRole("button", { name: /Download backup/ }).click(),
    ]);
    if (!/\.(zip|db|json)$/.test(dl.suggestedFilename())) throw new Error(dl.suggestedFilename());
  });

  await check("library scan finds the clips, encode finishes as HEVC", async () => {
    await api("/media/scan", { method: "POST" });
    let files = [];
    for (let i = 0; i < 40 && !files.length; i++) {
      await new Promise((r) => setTimeout(r, 500));
      files = await api("/media/files");
    }
    const clip = files.find((f) => (f.codec || "").toLowerCase() === "h264");
    if (!clip) throw new Error(`no h264 clip found among ${files.length} files`);
    await api("/media/queue/resume", { method: "POST" });
    await api("/media/queue", { method: "POST", headers: { "content-type": "application/json" },
                                body: JSON.stringify({ file_id: clip.id, job_type: "encode" }) });
    let done = null;
    for (let i = 0; i < 120 && !done; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      const f = (await api("/media/files")).find((x) => x.id === clip.id);
      if (f && f.status === "error") throw new Error(f.error_msg || "encode failed");
      if (f && f.status === "done") done = f;
    }
    if (!done) throw new Error("encode did not finish in 2 minutes");
    if ((done.codec || "").toLowerCase() !== "hevc") throw new Error(`codec is ${done.codec}`);
    await page.goto(`${BASE}/activity`);
    await page.getByText(done.filename).first().waitFor({ timeout: 10000 });
  });

  await ctx.close();

  const phone = await browser.newContext({ viewport: { width: 390, height: 844 } });
  await phone.addInitScript(() => localStorage.setItem('sm-tour', '2'));  // the tour is checked on its own
  const mp = await phone.newPage();
  await check("phone: More tab reaches Updates", async () => {
    await mp.goto(`${BASE}/activity`);
    await mp.getByRole("button", { name: "More" }).click();
    await mp.getByRole("menuitem", { name: /Updates/ }).click();
    await mp.waitForURL("**/updates");
    if (await mp.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error("horizontal scroll");
  });
  await phone.close();
  await browser.close();

  for (const [status, name] of results) console.log(`${status.padEnd(4)} ${name}`);
  const failed = results.filter(([s]) => s === "FAIL").length;
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
})();
