"""Extract original product images from the HF catalog and map them to SKU labels."""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
import re

import fitz
from PIL import Image


SKU_PATTERN = re.compile(r"^HF-[A-Z][0-9A-Z-]{2,}$")


def extract(catalog: Path, output: Path) -> dict[str, str]:
    output.mkdir(parents=True, exist_ok=True)
    for stale in output.glob("HF-*.png"):
        stale.unlink()
    mapping: dict[str, str] = {}
    with fitz.open(catalog) as document:
        for page in document:
            labels = []
            for word in page.get_text("words"):
                value = str(word[4]).strip().upper()
                if SKU_PATTERN.match(value):
                    labels.append((value, fitz.Rect(word[:4])))
            candidates = []
            for image in page.get_images(full=True):
                xref, smask = image[0], image[1]
                for rect in page.get_image_rects(xref):
                    if rect.width * rect.height > 800:
                        candidates.append((xref, smask, rect))
            for sku, label in labels:
                above = [item for item in candidates if item[2].y1 <= label.y0 + 8]
                if not above:
                    continue
                center_x = (label.x0 + label.x1) / 2
                xref, smask, _rect = min(
                    above,
                    key=lambda item: abs((item[2].x0 + item[2].x1) / 2 - center_x)
                    + max(0, label.y0 - item[2].y1) * 0.35,
                )
                pixmap = fitz.Pixmap(document, xref)
                if smask:
                    if pixmap.alpha:
                        pixmap = fitz.Pixmap(pixmap, 0)
                    pixmap = fitz.Pixmap(pixmap, fitz.Pixmap(document, smask))
                if pixmap.colorspace and pixmap.colorspace.n > 3:
                    pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
                target = output / f"{sku}.webp"
                rendered = Image.open(BytesIO(pixmap.tobytes("png"))).convert("RGBA")
                rendered.thumbnail((800, 800), Image.Resampling.LANCZOS)
                rendered.save(target, "WEBP", quality=88, method=6)
                mapping[sku] = str(target)
    return mapping


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("HF catalog.pdf"))
    parser.add_argument("--output", type=Path, default=Path("frontend/public/assets/hf-products"))
    args = parser.parse_args()
    result = extract(args.catalog, args.output)
    print(f"extracted {len(result)} SKU images to {args.output}")
