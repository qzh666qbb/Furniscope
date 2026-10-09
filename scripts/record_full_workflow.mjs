import { chromium } from "../frontend/node_modules/playwright/index.mjs";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";

const root = path.resolve(new URL("..", import.meta.url).pathname);
const out = path.join(root, "artifacts/demo-video/full-workflow");
fs.mkdirSync(path.join(out, "raw"), { recursive: true });
fs.mkdirSync(path.join(out, "evidence"), { recursive: true });
fs.mkdirSync(path.join(out, "downloads"), { recursive: true });
const readme = fs.readFileSync(path.join(root, "README.md"), "utf8");
const credential = label => readme.match(new RegExp(`\\| ${label} \\| \`([^\`]+)\``))?.[1];
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 },
  recordVideo: { dir: path.join(out, "raw"), size: { width: 1920, height: 1080 } },
  locale: "zh-CN", acceptDownloads: true,
});
const p = await context.newPage();
const started = Date.now();
const video = p.video();
const clips = [];
const errors = [];
p.setDefaultTimeout(12000);
p.on("pageerror", e => errors.push({ at: Date.now(), message: String(e) }));
p.on("dialog", d => d.dismiss());
p.on("download", async d => {
  await d.saveAs(path.join(out, "downloads", d.suggestedFilename()));
  console.log(JSON.stringify({ download: d.suggestedFilename() }));
});
const hold = ms => new Promise(resolve => setTimeout(resolve, ms));
const snap = async (selector = "body") => (await p.locator(selector).ariaSnapshot()).slice(0, 24000);
const click = async loc => {
  await loc.scrollIntoViewIfNeeded();
  await loc.hover();
  await hold(250);
  await loc.click();
  await hold(1000);
};
const type = async (loc, text) => {
  await loc.fill("");
  await loc.pressSequentially(text, { delay: 45 });
  await hold(700);
};
const nav = async (hash, heading) => {
  await p.goto(`http://127.0.0.1:4173/#${hash}`);
  if (heading) await p.getByText(heading, { exact: false }).first().waitFor();
  await hold(1300);
};
const scroll = async (amount = 650, selector) => {
  if (selector) await p.locator(selector).hover(); else await p.mouse.move(1350, 800);
  await p.mouse.wheel(0, amount);
  await hold(1800);
};
const shot = async name => p.screenshot({ path: path.join(out, "evidence", `${name}.png`) });
const persist = () => fs.writeFileSync(path.join(out, "recording.json"), JSON.stringify({
  started, clips, errors, raw: null, resolution: "1920x1080",
}, null, 2));
const clip = async (id, title, fn) => {
  const item = { id, title, start: (Date.now() - started) / 1000, status: "recording" };
  clips.push(item);
  try {
    await fn();
    await hold(1800);
    item.status = "recorded";
    item.url = p.url();
    await shot(id);
  } catch (e) {
    item.status = "failed";
    item.error = String(e);
    await shot(`${id}-error`);
    throw e;
  } finally {
    item.end = (Date.now() - started) / 1000;
    persist();
  }
};
const api = async endpoint => {
  const token = await p.evaluate(() => sessionStorage.getItem("furniscope-access-token"));
  const res = await fetch(`http://127.0.0.1:8016${endpoint}`, { headers: { Authorization: `Bearer ${token}` } });
  const body = await res.json();
  if (!res.ok) throw Error(JSON.stringify(body));
  return body.data ?? body;
};
const finish = async () => {
  await context.close();
  const raw = await video.path();
  await browser.close();
  fs.writeFileSync(path.join(out, "recording.json"), JSON.stringify({
    started, clips, errors, raw, resolution: "1920x1080",
  }, null, 2));
  console.log(JSON.stringify({ finished: true, raw, clips: clips.length }));
};
Object.assign(globalThis, { p, context, browser, root, out, credential, hold, snap, click, type, nav, scroll, shot, clip, api, finish, fs });
const input = readline.createInterface({ input: process.stdin });
console.log("RECORDER_READY");
for await (const line of input) {
  if (!line.trim()) continue;
  try {
    const { code } = JSON.parse(line);
    fs.appendFileSync(path.join(out, "commands.jsonl"), `${JSON.stringify({ code })}\n`);
    const result = await new (Object.getPrototypeOf(async function () {}).constructor)(code)();
    console.log(JSON.stringify({ ok: true, result }));
  } catch (e) {
    console.log(JSON.stringify({ ok: false, error: String(e) }));
  }
}
