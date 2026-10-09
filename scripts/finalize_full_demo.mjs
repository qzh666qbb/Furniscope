import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { spawnSync } from "node:child_process";

const root = path.resolve(new URL("..", import.meta.url).pathname);
const out = path.join(root, "artifacts/demo-video/full-workflow");
const ffmpeg = "/tmp/furniscope-video-tools/node_modules/ffmpeg-static/ffmpeg";
const record = JSON.parse(fs.readFileSync(path.join(out, "recording.json")));
if (!record.raw) throw Error("Recorder has not finished");
const segmentDir = path.join(out, "chapters");
fs.mkdirSync(segmentDir, { recursive: true });
const overridesPath = path.join(out, "clip-overrides.json");
const overrides = fs.existsSync(overridesPath) ? JSON.parse(fs.readFileSync(overridesPath)) : {};
const entries = record.clips.map(c => ({ ...c, ...overrides[c.id] }))
  .filter(c => c.status === "recorded" && !c.omit)
  .sort((a, b) => Number(a.id.slice(0, 2)) - Number(b.id.slice(0, 2)) || a.start - b.start);
const excluded = record.clips.filter(c => !entries.some(e => e.id === c.id))
  .map(c => ({ ...c, ...overrides[c.id] }));
for (const entry of excluded) {
  const cached = path.join(segmentDir, entry.id + ".mp4");
  if (fs.existsSync(cached)) {
    const archive = path.join(out, "excluded-clips");
    fs.mkdirSync(archive, { recursive: true });
    fs.renameSync(cached, path.join(archive, entry.id + ".mp4"));
  }
}
const run = args => {
  const result = spawnSync(ffmpeg, ["-hide_banner", "-loglevel", "error", "-y", ...args], { encoding: "utf8" });
  if (result.status !== 0) throw Error(result.stderr);
};
const durationOf = file => {
  const probe = spawnSync(ffmpeg, ["-hide_banner", "-i", file], { encoding: "utf8" });
  const match = probe.stderr.match(/Duration: (\d+):(\d+):([\d.]+)/);
  if (!match) throw Error(`Cannot determine duration: ${file}`);
  return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]);
};
let elapsed = 0;
const chapters = [];
const stamp = seconds => {
  const s = Math.floor(seconds);
  return [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60].map(n => String(n).padStart(2, "0")).join(":");
};
for (const entry of entries) {
  const dest = path.join(segmentDir, entry.id + ".mp4");
  if (!fs.existsSync(dest)) run(["-ss", String(entry.start), "-i", record.raw, "-t", String(entry.end - entry.start),
    "-an", "-r", "25", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
    "-pix_fmt", "yuv420p", "-movflags", "+faststart", dest]);
  const duration = durationOf(dest);
  chapters.push({ ...entry, at_seconds: elapsed, duration_seconds: duration, file: dest });
  elapsed += duration;
  console.log(`${entry.id}: ${duration.toFixed(1)}s`);
}
fs.writeFileSync(path.join(out, "concat.txt"), chapters.map(c => `file '${c.file.replaceAll("'", "'\\''")}'`).join("\n"));
fs.writeFileSync(path.join(out, "chapters.ffmeta"), ";FFMETADATA1\n" + chapters.map(c =>
  `[CHAPTER]\nTIMEBASE=1/1000\nSTART=${Math.round(c.at_seconds * 1000)}\nEND=${Math.round((c.at_seconds + c.duration_seconds) * 1000)}\ntitle=${c.title}\n`).join("\n"));
const final = path.join(out, "FurniScope-全功能操作演示.mp4");
run(["-f", "concat", "-safe", "0", "-i", path.join(out, "concat.txt"), "-i", path.join(out, "chapters.ffmeta"),
  "-map_metadata", "1", "-c", "copy", "-movflags", "+faststart", final]);
run(["-i", final, "-f", "null", "-"]);
const hash = file => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
const manifest = { generated_at: new Date().toISOString(), resolution: record.resolution,
  duration_seconds: durationOf(final), video: final, sha256: hash(final), chapters,
  excluded_clips: excluded, browser_errors: record.errors };
fs.writeFileSync(path.join(out, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
fs.writeFileSync(path.join(out, "CHAPTERS.md"),
  "# 全功能操作演示章节\n\n" + chapters.map(c => `- ${stamp(c.at_seconds)} — ${c.title}（${c.duration_seconds.toFixed(0)} 秒）`).join("\n") + "\n");
fs.writeFileSync(path.join(out, "SHA256SUMS.txt"), [final, ...chapters.map(c => c.file)]
  .map(f => `${hash(f)}  ${path.relative(out, f)}`).join("\n") + "\n");
console.log(JSON.stringify({ final, seconds: elapsed, sha256: manifest.sha256 }));
