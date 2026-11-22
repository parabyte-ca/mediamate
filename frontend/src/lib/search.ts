/** Library search: every word must match, in any order.
 *  - `*` matches any run of characters, `?` exactly one ("s11*", "s0?e01", "simpsons*1080p")
 *  - "quoted words" must appear together as a phrase
 *  - `-word` leaves out anything that matches it
 *  Dots, dashes and underscores count as spaces, and case doesn't matter, so
 *  "simpsons s11" finds "The.Simpsons.S11E01.mkv". */

const SEP = /[\s._\-–—]+/g;

/** Text as the search sees it: lower case, separators turned into single spaces. */
export function normalise(text: string): string {
  return ` ${text.toLowerCase().replace(SEP, " ").trim()} `;
}

type Term = { re: RegExp; exclude: boolean };

function termRegex(raw: string): RegExp | null {
  const t = raw.toLowerCase().replace(SEP, " ").trim();
  if (!t || /^[*?]+$/.test(t)) return null;                 // "*" alone matches everything: ignore
  const body = t.split("").map((ch) =>
    ch === "*" ? ".*" : ch === "?" ? "." : ch.replace(/[.+^${}()|[\]\\]/g, "\\$&")).join("");
  return new RegExp(body);
}

/** Turn a search box's text into a test. An empty search matches everything. */
export function makeMatcher(query: string): (...fields: (string | null | undefined)[]) => boolean {
  const terms: Term[] = [];
  const re = /(-?)"([^"]*)"|(\S+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(query))) {
    let exclude = m[1] === "-";
    let text = m[2] ?? m[3] ?? "";
    if (m[3] && text.startsWith("-") && text.length > 1) { exclude = true; text = text.slice(1); }
    const rx = termRegex(text);
    if (rx) terms.push({ re: rx, exclude });
  }
  if (!terms.length) return () => true;
  return (...fields) => {
    const hay = normalise(fields.filter(Boolean).join(" "));
    return terms.every((t) => t.re.test(hay) !== t.exclude);
  };
}

/** The tip shown under/beside library search boxes. */
export const SEARCH_TIP = 'Use * and ? as wildcards (s11*, s0?e01), "quotes" for a phrase, and -word to leave something out.';
