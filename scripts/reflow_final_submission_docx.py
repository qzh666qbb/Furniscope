#!/usr/bin/env python3
"""Reflow the final submission DOCX and add complete admin screenshots."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import struct
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"

W = f"{{{W_NS}}}"
R = f"{{{R_NS}}}"
REL = f"{{{REL_NS}}}"
WP = f"{{{WP_NS}}}"
A = f"{{{A_NS}}}"
PIC = f"{{{PIC_NS}}}"

EMU_PER_INCH = 914400
MAX_LANDSCAPE_WIDTH_EMU = int(5.05 * EMU_PER_INCH)
TARGET_FIGURE_WIDTHS = {
    "图3-16-2 市场决策-AI智能选品详情": 6.5,
    "图3-17-4 竞品监控工作台-价格趋势（第2/2段）": 4.0,
    "图3-17-5 竞品监控工作台-版本对比": 4.0,
    "图3-18-2 市场决策-评论主题原文证据": 4.0,
    "图3-18-3 评论舆情工作台": 4.0,
    "图3-21-1 经营配置工作台-排序策略": 4.0,
    "图3-21-2 经营配置工作台-企业事实": 4.0,
    "图3-21-3 经营配置工作台-准入条件": 4.0,
    "图3-21-4 经营配置工作台-版本与导出": 4.0,
    "图3-23-1 竞品监控工作台-价格趋势（第1/2段）": 4.0,
    "图3-23-2 竞品监控工作台-价格趋势（第2/2段）": 4.0,
    "图3-24-1 自动化投递-渠道配置与投递记录": 6.0,
    "图3-24-2 自动化投递-新增渠道弹窗": 4.0,
    "图3-24-3 自动化投递-编辑渠道弹窗": 4.0,
    "图3-28 三条业务链路与企业资产底座关系": 6.0,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_namespaces(xml_bytes: bytes) -> None:
    import io

    for _, item in ET.iterparse(io.BytesIO(xml_bytes), events=("start-ns",)):
        prefix, uri = item
        if prefix != "xml":
            try:
                ET.register_namespace(prefix, uri)
            except ValueError:
                pass


def paragraph_text(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.iter(W + "t")).strip()


def set_paragraph_text(element: ET.Element, value: str) -> None:
    text_nodes = list(element.iter(W + "t"))
    if not text_nodes:
        run = ET.SubElement(element, W + "r")
        text_node = ET.SubElement(run, W + "t")
        text_node.text = value
        return
    text_nodes[0].text = value
    for node in text_nodes[1:]:
        node.text = ""


def ensure_ppr(paragraph: ET.Element) -> ET.Element:
    ppr = paragraph.find(W + "pPr")
    if ppr is None:
        ppr = ET.Element(W + "pPr")
        paragraph.insert(0, ppr)
    return ppr


def set_spacing(paragraph: ET.Element, *, before: int, after: int) -> None:
    ppr = ensure_ppr(paragraph)
    spacing = ppr.find(W + "spacing")
    if spacing is None:
        spacing = ET.SubElement(ppr, W + "spacing")
    spacing.set(W + "before", str(before))
    spacing.set(W + "after", str(after))


def ensure_keep_next(paragraph: ET.Element) -> None:
    ppr = ensure_ppr(paragraph)
    if ppr.find(W + "keepNext") is None:
        ET.SubElement(ppr, W + "keepNext")
    if ppr.find(W + "keepLines") is None:
        ET.SubElement(ppr, W + "keepLines")


def image_extent(paragraph: ET.Element) -> tuple[int, int] | None:
    extent = paragraph.find(f".//{WP}extent")
    if extent is None:
        return None
    return int(extent.get("cx", "0")), int(extent.get("cy", "0"))


def set_image_extent(paragraph: ET.Element, width: int, height: int) -> None:
    for extent in paragraph.findall(f".//{WP}extent"):
        extent.set("cx", str(width))
        extent.set("cy", str(height))
    for extent in paragraph.findall(f".//{A}xfrm/{A}ext"):
        extent.set("cx", str(width))
        extent.set("cy", str(height))


def png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError(f"Expected a PNG image: {path}")
    return struct.unpack(">II", data[16:24])


def next_relationship_id(root: ET.Element) -> str:
    largest = 0
    for relation in root.findall(REL + "Relationship"):
        value = relation.get("Id", "")
        if value.startswith("rId") and value[3:].isdigit():
            largest = max(largest, int(value[3:]))
    return f"rId{largest + 1}"


def next_drawing_id(root: ET.Element) -> int:
    largest = 0
    for node in root.findall(f".//{WP}docPr"):
        value = node.get("id", "")
        if value.isdigit():
            largest = max(largest, int(value))
    return largest + 1


def clone_image_paragraph(
    template: ET.Element,
    *,
    relation_id: str,
    width: int,
    height: int,
    drawing_id: int,
    image_name: str,
    alt_text: str,
) -> ET.Element:
    paragraph = copy.deepcopy(template)
    set_image_extent(paragraph, width, height)
    set_spacing(paragraph, before=0, after=100)

    blip = paragraph.find(f".//{A}blip")
    if blip is None:
        raise RuntimeError("Image template does not contain a DrawingML blip")
    blip.set(R + "embed", relation_id)

    source_rect = paragraph.find(f".//{A}srcRect")
    if source_rect is not None:
        source_rect.attrib.clear()

    doc_pr = paragraph.find(f".//{WP}docPr")
    if doc_pr is not None:
        doc_pr.set("id", str(drawing_id))
        doc_pr.set("name", image_name)
        doc_pr.set("descr", alt_text)

    picture_pr = paragraph.find(f".//{PIC}cNvPr")
    if picture_pr is not None:
        picture_pr.set("id", str(drawing_id))
        picture_pr.set("name", image_name)
        picture_pr.set("descr", alt_text)

    return paragraph


def build_caption(template: ET.Element, text: str) -> ET.Element:
    paragraph = copy.deepcopy(template)
    set_paragraph_text(paragraph, text)
    ensure_keep_next(paragraph)
    set_spacing(paragraph, before=100, after=40)
    return paragraph


def locate_body_index(body: ET.Element, text: str) -> int:
    for index, child in enumerate(list(body)):
        if paragraph_text(child) == text:
            return index
    raise RuntimeError(f"Could not locate paragraph: {text}")


def serialize_xml(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def write_docx(
    source: Path,
    destination: Path,
    *,
    document_xml: bytes,
    relationships_xml: bytes,
    new_media: dict[str, bytes],
) -> None:
    with zipfile.ZipFile(source, "r") as source_zip:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as output_zip:
            for info in source_zip.infolist():
                if info.filename == "word/document.xml":
                    output_zip.writestr(info, document_xml)
                elif info.filename == "word/_rels/document.xml.rels":
                    output_zip.writestr(info, relationships_xml)
                else:
                    output_zip.writestr(info, source_zip.read(info.filename))
            for name, data in new_media.items():
                output_zip.writestr(name, data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("document", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument(
        "--admin-enterprises",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--admin-create-modal",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--admin-models",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--admin-model-modal",
        type=Path,
        required=True,
    )
    args = parser.parse_args()

    document = args.document.resolve()
    if not document.exists():
        raise FileNotFoundError(document)
    image_specs = [
        (
            "图3-27-2 管理后台-企业用户与租户生命周期",
            args.admin_enterprises.resolve(),
            "admin-enterprise-users.png",
        ),
        (
            "图3-27-3 管理后台-开通企业用户",
            args.admin_create_modal.resolve(),
            "admin-create-enterprise-modal.png",
        ),
        (
            "图3-27-4 管理后台-租户预测模型",
            args.admin_models.resolve(),
            "admin-forecast-models.png",
        ),
        (
            "图3-27-5 管理后台-模型更新说明",
            args.admin_model_modal.resolve(),
            "admin-model-update-modal.png",
        ),
    ]
    for _, path, _ in image_specs:
        if not path.exists():
            raise FileNotFoundError(path)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = (
        args.backup.resolve()
        if args.backup
        else document.with_name(f"{document.stem}.{timestamp}-before-layout-admin{document.suffix}")
    )
    if backup.exists():
        raise FileExistsError(backup)
    shutil.copy2(document, backup)

    with zipfile.ZipFile(document, "r") as archive:
        document_bytes = archive.read("word/document.xml")
        relationships_bytes = archive.read("word/_rels/document.xml.rels")
        existing_names = set(archive.namelist())

    collect_namespaces(document_bytes)
    collect_namespaces(relationships_bytes)
    document_root = ET.fromstring(document_bytes)
    relationships_root = ET.fromstring(relationships_bytes)
    body = document_root.find(W + "body")
    if body is None:
        raise RuntimeError("DOCX document body is missing")

    section_start = locate_body_index(body, "产品功能与使用说明")
    section_end = locate_body_index(body, "技术架构及调用模型说明")
    try:
        admin_caption_index = locate_body_index(body, "图3-27 平台管理员登录页")
        admin_assets_already_present = False
    except RuntimeError:
        admin_caption_index = locate_body_index(body, "图3-27-1 平台管理员登录页")
        admin_assets_already_present = any(
            paragraph_text(child).startswith("图3-27-2 ") for child in list(body)
        )
    children = list(body)
    admin_caption = children[admin_caption_index]
    admin_image = children[admin_caption_index + 1]
    if image_extent(admin_image) is None:
        raise RuntimeError("Expected the administrator login image after its caption")

    set_paragraph_text(admin_caption, "图3-27-1 平台管理员登录页")
    ensure_keep_next(admin_caption)
    set_spacing(admin_caption, before=100, after=40)

    caption_template = admin_caption
    image_template = admin_image
    drawing_id = next_drawing_id(document_root)
    new_media: dict[str, bytes] = {}
    inserted_nodes: list[ET.Element] = []
    inserted_assets: list[dict[str, object]] = []
    if admin_assets_already_present:
        for caption, image_path, media_basename in image_specs:
            pixel_width, pixel_height = png_dimensions(image_path)
            width = MAX_LANDSCAPE_WIDTH_EMU
            height = round(width * pixel_height / pixel_width)
            inserted_assets.append(
                {
                    "caption": caption,
                    "source": os.path.relpath(image_path, document.parent),
                    "source_sha256": sha256(image_path),
                    "pixels": [pixel_width, pixel_height],
                    "display_emu": [width, height],
                    "media": f"word/media/{media_basename}",
                }
            )

    for caption, image_path, media_basename in ([] if admin_assets_already_present else image_specs):
        relation_id = next_relationship_id(relationships_root)
        media_name = f"word/media/{media_basename}"
        suffix = 1
        while media_name in existing_names or media_name in new_media:
            media_name = f"word/media/{Path(media_basename).stem}-{suffix}.png"
            suffix += 1

        relation = ET.SubElement(relationships_root, REL + "Relationship")
        relation.set("Id", relation_id)
        relation.set(
            "Type",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
        )
        relation.set("Target", f"media/{Path(media_name).name}")

        pixel_width, pixel_height = png_dimensions(image_path)
        width = MAX_LANDSCAPE_WIDTH_EMU
        height = round(width * pixel_height / pixel_width)
        image_name = Path(media_name).name
        inserted_nodes.append(build_caption(caption_template, caption))
        inserted_nodes.append(
            clone_image_paragraph(
                image_template,
                relation_id=relation_id,
                width=width,
                height=height,
                drawing_id=drawing_id,
                image_name=image_name,
                alt_text=caption,
            )
        )
        drawing_id += 1
        new_media[media_name] = image_path.read_bytes()
        inserted_assets.append(
            {
                "caption": caption,
                "source": os.path.relpath(image_path, document.parent),
                "source_sha256": sha256(image_path),
                "pixels": [pixel_width, pixel_height],
                "display_emu": [width, height],
                "media": media_name,
            }
        )

    insertion_index = admin_caption_index + 2
    for offset, node in enumerate(inserted_nodes):
        body.insert(insertion_index + offset, node)

    section_end += len(inserted_nodes)
    resized_images = 0
    figure_images = 0
    for child in list(body)[section_start:section_end]:
        extent = image_extent(child)
        if extent is None:
            style = child.find(f"{W}pPr/{W}pStyle")
            if style is not None and style.get(W + "val") in {"5", "6"}:
                ensure_keep_next(child)
            continue

        figure_images += 1
        width, height = extent
        if width and height and width / height >= 1.45 and width > MAX_LANDSCAPE_WIDTH_EMU:
            new_height = round(height * MAX_LANDSCAPE_WIDTH_EMU / width)
            set_image_extent(child, MAX_LANDSCAPE_WIDTH_EMU, new_height)
            resized_images += 1
        set_spacing(child, before=0, after=100)

    for child in list(body)[section_start:section_end]:
        text = paragraph_text(child)
        if text.startswith("图3-"):
            ensure_keep_next(child)
            set_spacing(child, before=100, after=40)

    targeted_resizes: list[dict[str, object]] = []
    body_children = list(body)
    for index, child in enumerate(body_children[:-1]):
        caption = paragraph_text(child)
        target_inches = TARGET_FIGURE_WIDTHS.get(caption)
        if target_inches is None:
            continue
        image_paragraph = body_children[index + 1]
        extent = image_extent(image_paragraph)
        if extent is None:
            raise RuntimeError(f"Expected image after caption: {caption}")
        old_width, old_height = extent
        target_width = int(target_inches * EMU_PER_INCH)
        target_height = round(old_height * target_width / old_width)
        set_image_extent(image_paragraph, target_width, target_height)
        targeted_resizes.append(
            {
                "caption": caption,
                "from_emu": [old_width, old_height],
                "to_emu": [target_width, target_height],
            }
        )

    with tempfile.NamedTemporaryFile(
        prefix=f".{document.stem}.",
        suffix=".docx",
        dir=document.parent,
        delete=False,
    ) as temp_stream:
        temp_path = Path(temp_stream.name)
    try:
        write_docx(
            document,
            temp_path,
            document_xml=serialize_xml(document_root),
            relationships_xml=serialize_xml(relationships_root),
            new_media=new_media,
        )
        with zipfile.ZipFile(temp_path, "r") as verification_zip:
            bad_member = verification_zip.testzip()
            if bad_member:
                raise RuntimeError(f"Corrupt DOCX member after update: {bad_member}")
        os.replace(temp_path, document)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    manifest = args.manifest.resolve() if args.manifest else document.parent / "LAYOUT_ADMIN_UPDATE_MANIFEST.json"
    manifest_data = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "document": document.name,
        "document_sha256": sha256(document),
        "backup": backup.name,
        "backup_sha256": sha256(backup),
        "product_section_figure_images": figure_images,
        "resized_landscape_images": resized_images,
        "landscape_max_width_inches": 5.05,
        "admin_images_present": len(inserted_assets),
        "admin_images_inserted_this_run": 0 if admin_assets_already_present else len(inserted_assets),
        "admin_assets": inserted_assets,
        "targeted_resizes": targeted_resizes,
    }
    manifest.write_text(
        json.dumps(manifest_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest_data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
