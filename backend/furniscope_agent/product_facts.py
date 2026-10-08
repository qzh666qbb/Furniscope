"""Shared, versioned vocabulary. Alias matches are suggestions, never facts."""
from __future__ import annotations

import json
import re

VOCABULARY_VERSION = "furniture-facts-v1"
FACT_GROUPS = {
    "material_codes": ("材质", [
        ("solid_wood", "实木", ["实木", "solid wood"]),
        ("metal", "金属", ["金属", "metal"]),
        ("fabric", "布艺", ["布艺", "fabric"]),
    ]),
    "process_codes": ("工艺", [
        ("upholstery", "软包工艺", ["软包", "upholstery", "upholstered"]),
        ("cnc", "数控加工", ["数控加工", "cnc"]),
        ("welding", "焊接", ["焊接", "welding"]),
    ]),
    "packaging_codes": ("包装", [
        ("flat_pack", "拆装包装", ["拆装包装", "flat pack", "flat-pack"]),
        ("ista_3a", "ISTA 3A 包装", ["ista 3a", "ista-3a"]),
    ]),
    "certification_codes": ("认证", [
        ("fsc", "FSC", ["fsc"]),
        ("carb_phase_2", "CARB Phase 2", ["carb phase 2", "carb phase ii"]),
    ]),
}


def vocabulary() -> dict:
    return {"version": VOCABULARY_VERSION, "groups": [
        {"attribute_code": field, "name": name, "capability_type": field.removesuffix("_codes"),
         "options": [{"code": code, "name": label} for code, label, _ in entries]}
        for field, (name, entries) in FACT_GROUPS.items()
    ]}


def validate_codes(field: str, value) -> list[str]:
    allowed = {code for code, _, _ in FACT_GROUPS[field][1]}
    if (not isinstance(value, list) or any(not isinstance(v, str) or v not in allowed for v in value)
            or len(value) != len(set(value))):
        raise ValueError(f"{field}须为不重复的标准代码列表")
    return value


def fact_suggestions(attributes: list[dict]) -> list[dict]:
    suggestions = []
    for attr in attributes:
        value = attr.get("attribute_value", attr.get("value"))
        raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        for sentence in re.split(r"[。；;\n]", raw):
            # Do not promote negative, uncertain or hypothetical mentions. Users
            # can still enter a verified fact explicitly via the vocabulary picker.
            if re.search(r"未|无|不|非|待|拟|可能|计划|\b(no|not|without|pending|may|planned)\b", sentence, re.I):
                continue
            for field, (_, entries) in FACT_GROUPS.items():
                for code, name, aliases in entries:
                    terms = [code, *aliases] if attr["attribute_code"] == field else aliases
                    if not any(re.search(
                        rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])" if term.isascii() else re.escape(term),
                        sentence, re.I) for term in terms):
                        continue
                    suggestions.append({
                        "attribute_code": field, "code": code, "name": name,
                        "source_attribute": attr["attribute_code"],
                        "source_locator": attr.get("source_locator"),
                        "evidence_text": sentence.strip()[:1000], "status": "suggested",
                    })
    return suggestions


def fact_is_confirmed(fact: dict) -> bool:
    """Legacy model confidence is not a human review."""
    return (fact.get("confirmation_status") == "confirmed"
            and (fact.get("source_type") not in {"document", "image", "inferred"}
                 or bool((fact.get("source_locator") or {}).get("confirmed_by"))))
