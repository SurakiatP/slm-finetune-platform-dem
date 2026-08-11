#!/usr/bin/env node
/**
 * i18n Health Check
 *
 * Reports:
 *   1. Unused keys (defined in translations.ts but never referenced in src/)
 *   2. Missing keys (referenced in src/ but absent from translations.ts)
 *   3. Locale parity gaps (key exists in EN but not TH, or vice versa)
 *
 * Usage:
 *   node scripts/check-i18n.mjs            # report only (exit 0)
 *   node scripts/check-i18n.mjs --strict   # exit 1 on any issue
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, extname } from "node:path";

const ROOT = process.cwd();
const TRANSLATIONS = join(ROOT, "src/i18n/translations.ts");
const SRC = join(ROOT, "src");
const STRICT = process.argv.includes("--strict");

// ---------- 1. Extract EN + TH key sets ----------
const file = readFileSync(TRANSLATIONS, "utf8");
const enStart = file.indexOf("en:");
const thStart = file.indexOf("th:", enStart);
if (enStart < 0 || thStart < 0) {
  console.error("[i18n] Could not locate `en:` / `th:` blocks in translations.ts");
  process.exit(1);
}
const enBlock = file.slice(enStart, thStart);
const thBlock = file.slice(thStart);

const keyRe = /"([a-zA-Z0-9_.\-]+)"\s*:/g;
function extractKeys(block) {
  const set = new Set();
  let m;
  while ((m = keyRe.exec(block))) set.add(m[1]);
  keyRe.lastIndex = 0;
  return set;
}
const enKeys = extractKeys(enBlock);
const thKeys = extractKeys(thBlock);
const allKeys = new Set([...enKeys, ...thKeys]);

// ---------- 2. Walk src/ and gather code ----------
const SKIP_DIRS = new Set(["node_modules", "dist", "build", ".git"]);
const files = [];
function walk(dir) {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIRS.has(entry)) continue;
    const p = join(dir, entry);
    const s = statSync(p);
    if (s.isDirectory()) walk(p);
    else if ([".ts", ".tsx", ".js", ".jsx"].includes(extname(p)) && p !== TRANSLATIONS) {
      files.push(p);
    }
  }
}
walk(SRC);

const haystack = files.map((f) => readFileSync(f, "utf8")).join("\n");

// ---------- 3a. Find dynamic prefix patterns: t(`a.${x}.b`) ----------
const dynamicRe = /t\(`([a-zA-Z0-9_.\-]*)\$\{[^}]+\}([a-zA-Z0-9_.\-]*)`\)/g;
const dynamicPatterns = [];
let dm;
while ((dm = dynamicRe.exec(haystack))) {
  dynamicPatterns.push({ prefix: dm[1], suffix: dm[2] });
}

function isUsed(k) {
  const escaped = k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`["'\`]${escaped}["'\`]`);
  if (re.test(haystack)) return true;
  return dynamicPatterns.some(
    (p) => k.startsWith(p.prefix) && k.endsWith(p.suffix) && k.length > p.prefix.length + p.suffix.length,
  );
}

// ---------- 3b. Find referenced keys via t("...") that don't exist ----------
const tCallRe = /\bt\(\s*["'`]([a-zA-Z0-9_.\-]+)["'`]\s*\)/g;
const referenced = new Set();
let rm;
while ((rm = tCallRe.exec(haystack))) referenced.add(rm[1]);

const unused = [...allKeys].filter((k) => !isUsed(k)).sort();
const missing = [...referenced].filter((k) => !allKeys.has(k)).sort();
const enOnly = [...enKeys].filter((k) => !thKeys.has(k)).sort();
const thOnly = [...thKeys].filter((k) => !enKeys.has(k)).sort();

// ---------- 4. Report ----------
console.log(`\ni18n health check`);
console.log(`  EN keys:        ${enKeys.size}`);
console.log(`  TH keys:        ${thKeys.size}`);
console.log(`  referenced:     ${referenced.size}`);
console.log(`  unused:         ${unused.length}`);
console.log(`  missing:        ${missing.length}`);
console.log(`  EN-only:        ${enOnly.length}`);
console.log(`  TH-only:        ${thOnly.length}`);

function dump(label, list) {
  if (!list.length) return;
  console.log(`\n${label}:`);
  for (const k of list) console.log(`  - ${k}`);
}
dump("Unused keys", unused);
dump("Missing keys (used in code, not defined)", missing);
dump("EN-only keys (no TH translation)", enOnly);
dump("TH-only keys (no EN translation)", thOnly);

const hasIssues = unused.length || missing.length || enOnly.length || thOnly.length;
if (hasIssues && STRICT) {
  console.error(`\n[strict] i18n issues detected — failing.`);
  process.exit(1);
}
process.exit(0);
