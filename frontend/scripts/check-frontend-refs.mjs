#!/usr/bin/env node
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "src");
const names = [
  "listings",
  "marketRows",
  "locationHash",
  "setLocationHash",
  "taskInsight",
  "resultTask",
  "resultReport",
  "resultView",
  "buildInsightHash",
  "LatestAnalysisSnapshot",
  "InsightResultsPanel",
  "InsightDetailPage",
];

const strip = (source) => source
  .replace(/\/\*[\s\S]*?\*\//g, " ")
  .replace(/\/\/.*$/gm, " ")
  .replace(/`(?:\\.|[^`\\])*`/g, '""')
  .replace(/"(?:\\.|[^"\\])*"/g, '""')
  .replace(/'(?:\\.|[^'\\])*'/g, '""');

const declared = (source, name) => new RegExp(
  String.raw`(?:\b(?:const|let|var)\s+(?:${name}\b|\[[^\]]*?\b${name}\b|\{[^}]*?\b${name}\b)|\bfunction\s+${name}\b|\bimport\s+(?:\{[^}]*\b${name}\b|\*\s+as\s+${name}\b|\b${name}\b))`,
).test(source);

const used = (source, name) => new RegExp(String.raw`(?<![.\w$])${name}(?![\w$:])`).test(source);

const walk = (dir) => readdirSync(dir).flatMap((entry) => {
  const full = path.join(dir, entry);
  return statSync(full).isDirectory() ? walk(full) : [full];
});

const errors = [];
for (const file of walk(root).filter((item) => /\.(jsx?|mjs)$/.test(item))) {
  const raw = readFileSync(file, "utf8");
  const code = strip(raw);
  for (const name of names) {
    if (name === "checked" && !file.endsWith("CompetitorTracking.jsx")) continue;
    if (used(code, name) && !declared(code, name)) {
      errors.push(`${path.relative(root, file)} uses '${name}' but never declares it`);
    }
  }
}

if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
console.log("Frontend identifier check passed.");
