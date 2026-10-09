"""Sample the finished demo, validate exports and persist a small evidence index."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/demo-video/full-workflow"
FFMPEG = "/tmp/furniscope-video-tools/node_modules/ffmpeg-static/ffmpeg"
manifest = json.loads((OUT / "manifest.json").read_text())
video = Path(manifest["video"])
checks = OUT / "verification"
checks.mkdir(exist_ok=True)


def run(args):
    return subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True, capture_output=True, text=True,
    )


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


assert sha(video) == manifest["sha256"], "Final video hash mismatch"
for generated in checks.iterdir():
    if re.fullmatch(r"(frame-\d+|sheet-\d+)\.png", generated.name):
        generated.unlink()
samples = []
for index, chapter in enumerate(manifest["chapters"]):
    # Each recorded clip leaves a reading pause before the checkpoint screenshot.
    at = chapter["at_seconds"] + max(0, chapter["duration_seconds"] - 1.6)
    target = checks / f"frame-{index:03d}.png"
    run(["-ss", str(at), "-i", str(video), "-frames:v", "1", str(target)])
    assert target.stat().st_size > 1000, f"Empty sample: {target}"
    samples.append({"index": index, "id": chapter["id"], "title": chapter["title"],
                    "at_seconds": at, "file": str(target.relative_to(OUT))})

# Contact sheets follow exactly the order in samples.json (left-to-right, top-to-bottom).
for start in range(0, len(samples), 9):
    count = min(9, len(samples) - start)
    run(["-framerate", "1", "-start_number", str(start), "-i", str(checks / "frame-%03d.png"),
         "-vf", f"scale=640:360,tile=3x3:nb_frames={count}",
         "-frames:v", "1", str(checks / f"sheet-{start // 9 + 1:02d}.png")])

pdfs = []
for pdf in sorted((OUT / "downloads").glob("*.pdf")):
    content = pdf.read_bytes()
    assert content.startswith(b"%PDF-") and b"%%EOF" in content[-100:], f"Invalid PDF envelope: {pdf}"
    pages = len(re.findall(rb"/Type\s*/Page\b", content))
    assert pages > 0
    pdfs.append({"file": str(pdf.relative_to(OUT)), "pages": pages, "sha256": sha(pdf)})

result = {
    "video": video.name,
    "sha256_verified": True,
    "duration_seconds": manifest["duration_seconds"],
    "resolution": manifest["resolution"],
    "sample_count": len(samples),
    "samples": samples,
    "pdfs": pdfs,
    "browser_page_errors": manifest["browser_errors"],
}
(checks / "samples.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({k: v for k, v in result.items() if k != "samples"}, ensure_ascii=False))
