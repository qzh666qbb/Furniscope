import { readdir, stat } from "node:fs/promises";
import path from "node:path";

const assetsDirectory = path.resolve("dist/client/assets");
const budgets = {
  MarketWorkspace: 20 * 1024,
  MarketInsightsPages: 80 * 1024,
  MarketDatasets: 55 * 1024,
  CompetitorTracking: 35 * 1024,
  MarketSignals: 12 * 1024,
};

const files = await readdir(assetsDirectory);
const results = [];

for (const [prefix, limit] of Object.entries(budgets)) {
  const filename = files.find((file) => file.startsWith(`${prefix}-`) && file.endsWith(".js"));
  if (!filename) throw new Error(`Missing market bundle: ${prefix}`);
  const size = (await stat(path.join(assetsDirectory, filename))).size;
  results.push(`${prefix} ${(size / 1024).toFixed(1)} KiB / ${(limit / 1024).toFixed(0)} KiB`);
  if (size > limit) {
    throw new Error(`${prefix} exceeds its performance budget: ${size} > ${limit} bytes`);
  }
}

console.log(`Market page bundle budgets passed:\n${results.join("\n")}`);
