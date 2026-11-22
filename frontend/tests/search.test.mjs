// Unit tests for src/lib/search.ts (library search with wildcards).
// Run: node --test tests/search.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { transformSync } from "esbuild";

const ts = readFileSync(new URL("../src/lib/search.ts", import.meta.url), "utf8");
const js = transformSync(ts, { loader: "ts", format: "esm" }).code;
const { makeMatcher } = await import(`data:text/javascript,${encodeURIComponent(js)}`);

const hit = (q, ...fields) => makeMatcher(q)(...fields);

test("plain words match in any order, ignoring case and separators", () => {
  assert.ok(hit("simpsons s11", "The.Simpsons.S11E01.1080p.mkv"));
  assert.ok(hit("S11 SIMPSONS", "the_simpsons-s11e01.mkv"));
  assert.ok(!hit("simpsons s12", "The.Simpsons.S11E01.mkv"));
});

test("* and ? are wildcards", () => {
  assert.ok(hit("Simpsons s11*", "The.Simpsons.S11E05.mkv"));
  assert.ok(hit("s1?e0?", "Show.S11E05.mkv"));
  assert.ok(!hit("s1?e0?", "Show.S11E15.mkv"));
  assert.ok(hit("simpsons*1080p", "The Simpsons S11E01 1080p"));
  assert.ok(!hit("simpsons*720p", "The Simpsons S11E01 1080p"));
  assert.ok(hit("*", "anything"), "a lone * matches everything");
});

test("special characters are literal", () => {
  assert.ok(hit("a$ap", "A$AP Rocky", "Don't Be Dumb"));
  assert.ok(hit("(2019)", "Movie (2019).mkv"));
  assert.ok(!hit("(2019)", "Movie 2019.mkv"));
});

test("quotes, exclusions and extra fields", () => {
  assert.ok(hit('"the office"', "The.Office.US.S01E01.mkv"));
  assert.ok(!hit('"office the"', "The.Office.US.S01E01.mkv"));
  assert.ok(!hit("simpsons -s11", "The.Simpsons.S11E01.mkv"));
  assert.ok(hit("simpsons -s11", "The.Simpsons.S12E01.mkv"));
  assert.ok(hit("simpsons s11", "S11E01.mkv", "The Simpsons Season 11"), "folder names count");
  assert.ok(hit("", "x") && hit("   ", "x"));
});
