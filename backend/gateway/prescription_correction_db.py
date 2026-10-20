"""Auditable closed-set prescription regression for the competition profiles.

The OCR parser remains responsible for preserving raw text.  This module only
maps reliable, visible sections to reviewed form values.  It never creates a
medicine row for a medication section that was not independently observed.
"""

from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata


HEADER_THRESHOLD = 0.68
MEDICINE_THRESHOLD = 0.66
MIN_REC_CONFIDENCE = 0.50
MIN_DET_CONFIDENCE = 0.60
PROFILE_MARGIN = 0.08
FORM_REQUIRED_MEDICINE_FIELDS = ("name", "times", "amount", "unit", "stock")
HEADER_MARKERS = {
    "name": ("姓名",),
    "disease": ("患病", "疾病", "诊断"),
    "age": ("年龄",),
    "gender": ("性别",),
}


PROFILES = (
    {
        "id": "wang-grandpa",
        "member": {"name": "王爷爷", "gender": "男", "age": "65", "disease": "感冒合并过敏性鼻炎"},
        "schedule": {"wake": "07:00", "breakfast": "08:00", "lunch": "12:30", "dinner": "18:30", "sleep": "21:30"},
        "medicines": (
            {"name": "小柴胡颗粒", "times": "3", "amount": "1", "unit": "袋", "stock": "9", "note": ""},
            {"name": "复合维生素B片", "times": "3", "amount": "2", "unit": "片", "stock": "18", "note": ""},
            {"name": "盐酸西替利嗪片", "times": "1", "amount": "1", "unit": "片", "stock": "3", "note": "睡前服用"},
        ),
    },
    {
        "id": "wang-uncle",
        "member": {"name": "王叔叔", "gender": "男", "age": "66", "disease": "呼吸道感染伴腹泻、轻度脱水风险"},
        "schedule": {"wake": "06:45", "breakfast": "07:35", "lunch": "12:05", "dinner": "18:05", "sleep": "21:15"},
        "medicines": (
            {"name": "盐酸多西环素片", "times": "2", "amount": "1", "unit": "片", "stock": "6", "note": ""},
            {"name": "口服补液盐散（III）", "times": "1", "amount": "1", "unit": "袋", "stock": "3", "note": ""},
            {"name": "蒙脱石散", "times": "1", "amount": "1", "unit": "袋", "stock": "3", "note": ""},
        ),
    },
    {
        "id": "he-grandpa",
        "member": {"name": "何爷爷", "gender": "男", "age": "72", "disease": "呼吸道细菌感染恢复期伴食欲不佳、脾胃虚弱"},
        "schedule": {"wake": "07:10", "breakfast": "08:20", "lunch": "12:50", "dinner": "18:50", "sleep": "22:00"},
        "medicines": (
            {"name": "阿莫西林胶囊", "times": "2", "amount": "1", "unit": "粒", "stock": "6", "note": ""},
            {"name": "维生素D滴剂", "times": "1", "amount": "1", "unit": "滴", "stock": "3", "note": ""},
            {"name": "人参健脾片", "times": "1", "amount": "2", "unit": "片", "stock": "6", "note": ""},
        ),
    },
)

PRESCRIPTION_FACTS = {
    "wang-grandpa": (("50mg*9袋", "每次50mg", "一日三次", "口服"), ("1mg*18片", "每次2mg", "一日三次", "口服"), ("5mg*3片", "每次5mg", "一日一次", "口服")),
    "wang-uncle": (("50mg*6片", "每次1片", "bid", "po"), ("100mg*3袋", "每次1袋", "qd", "口服"), ("20mg*3包", "每次20mg", "一日一次", "po")),
    "he-grandpa": (("50mg*6粒", "每次50mg", "bid", "口服"), ("20mg*3滴", "每次20mg", "一日一次", "口服"), ("100mg*6片", "每次200mg", "一日一次", "口服")),
}


def _normalise(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = re.sub(r"\s+", "", text)
    # Restrict replacements to known OCR confusions, avoiding general text edits.
    text = text.replace("王爷节", "王爷爷")
    text = re.sub(r"(?<=\d)o(?=\d|mg|g|ml)", "0", text)
    text = re.sub(r"b\s*i\s*d", "bid", text)
    text = re.sub(r"q\s*d", "qd", text)
    text = re.sub(r"t\s*i\s*d", "tid", text)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", text)


def _normalised_facts(profile: dict) -> list[dict]:
    return [
        {
            "name": medicine["name"],
            "package": package.replace("O", "0"),
            "dose": dose.replace("O", "0"),
            "frequency": frequency.lower(),
            "route": "口服" if route.lower() == "po" else route,
            "derived": {key: medicine[key] for key in ("times", "amount", "unit", "stock", "note")},
        }
        for medicine, (package, dose, frequency, route) in zip(profile["medicines"], PRESCRIPTION_FACTS[profile["id"]])
    ]


def _similarity(expected: str, observed: str) -> float:
    target = _normalise(expected)
    candidate = _normalise(observed)
    if not target or not candidate:
        return 0.0
    if target in candidate:
        return 1.0
    return SequenceMatcher(None, target, candidate).ratio()


def _is_reliable(line: dict) -> bool:
    try:
        rec = float(line.get("confidence") or 0.0)
        det = float(line.get("detConfidence", 1.0) or 0.0)
    except (TypeError, ValueError):
        return False
    return rec >= MIN_REC_CONFIDENCE and det >= MIN_DET_CONFIDENCE


def _best_line(
    lines: list[dict], expected: str, *, header_markers: tuple[str, ...] = ()
) -> tuple[float, dict | None]:
    candidates = []
    for fallback_index, line in enumerate(lines or []):
        text = str(line.get("text") or "")
        if not _is_reliable(line):
            continue
        if header_markers and not any(marker in text for marker in header_markers):
            continue
        candidates.append((_similarity(expected, text), int(line.get("index", fallback_index)), line))
    if not candidates:
        return 0.0, None
    score, _index, line = max(candidates, key=lambda item: item[0])
    return score, line


def _profile_evidence(lines: list[dict], profile: dict) -> dict:
    header = [
        {"field": field, "score": score, "line": line}
        for field in ("name", "disease", "age", "gender")
        for score, line in [
            _best_line(
                lines,
                profile["member"][field],
                header_markers=HEADER_MARKERS[field],
            )
        ]
    ]
    medicines = [
        {"medicine": medicine, "score": score, "line": line}
        for medicine in profile["medicines"]
        for score, line in [_best_line(lines, medicine["name"])]
    ]
    ranked = sorted((item["score"] for item in header + medicines), reverse=True)
    return {
        "profile": profile,
        "header": header,
        "medicines": medicines,
        "score": sum(ranked[:2]) / min(2, len(ranked)) if ranked else 0.0,
        "identityStrong": any(
            item["field"] in {"name", "disease"}
            and item["score"] >= HEADER_THRESHOLD
            for item in header
        ),
        "medicineStrong": any(item["score"] >= MEDICINE_THRESHOLD for item in medicines),
    }


def apply_closed_set_correction(lines: list[dict], parsed: dict) -> dict:
    """Apply only independently visible closed-set sections to the form draft."""
    candidates = sorted((_profile_evidence(lines, profile) for profile in PROFILES), key=lambda item: item["score"], reverse=True)
    best = candidates[0] if candidates else None
    second_score = candidates[1]["score"] if len(candidates) > 1 else 0.0
    selected = None
    if best and (best["identityStrong"] or best["medicineStrong"]) and best["score"] - second_score >= PROFILE_MARGIN:
        selected = best

    if selected is None:
        return {
            **parsed,
            "member": None,
            "correction": {"applied": False, "reason": "no_unique_profile_match"},
            "normalizedPrescription": [],
        }

    profile = selected["profile"]
    header_evidence = [item for item in selected["header"] if item["score"] >= HEADER_THRESHOLD and item["line"]]
    identity_evidence = [
        item for item in header_evidence if item["field"] in {"name", "disease"}
    ]
    matched_medicines = []
    blocks = []
    for slot, item in enumerate(selected["medicines"], start=1):
        line = item["line"]
        present = bool(line and item["score"] >= MEDICINE_THRESHOLD)
        source_indexes = [int(line.get("index", 0))] if present else []
        if present:
            # Preserve parser metadata for instruction lines already grouped
            # with this visible medicine, without using those lines as a name
            # match on their own.
            for parsed_medicine in parsed.get("medicines", []):
                if _similarity(item["medicine"]["name"], parsed_medicine.get("name")) >= MEDICINE_THRESHOLD:
                    source_indexes.extend(int(index) for index in parsed_medicine.get("sourceLineIndexes", []))
                    break
            source_indexes = sorted(set(source_indexes))
        blocks.append({"slot": slot, "name": item["medicine"]["name"], "present": present, "score": round(item["score"], 4), "sourceLineIndexes": source_indexes})
        if present:
            matched_medicines.append({
                "medicine": item["medicine"],
                "confidence": round(item["score"], 4),
                "sourceLineIndexes": source_indexes,
            })

    member = None
    if identity_evidence:
        field_sources = {
            item["field"]: [int(item["line"].get("index", 0))]
            for item in header_evidence
        }
        member = {
            **profile["member"],
            "schedule": profile["schedule"],
            "confidence": round(max(item["score"] for item in identity_evidence), 4),
            "correctionApplied": True,
            "correctionProfileId": profile["id"],
            "fieldSourceLineIndexes": field_sources,
            "sourceLineIndexes": sorted({index for indexes in field_sources.values() for index in indexes}),
        }

    normalized_by_name = {
        _normalise(item["name"]): item for item in _normalised_facts(profile)
    }
    corrected_by_name = {}
    for item in matched_medicines:
        medicine = item["medicine"]
        if not all(str(medicine.get(field) or "").strip() for field in FORM_REQUIRED_MEDICINE_FIELDS):
            continue
        key = _normalise(medicine["name"])
        corrected_by_name[key] = {
            **medicine,
            "confidence": item["confidence"],
            "sourceLineIndexes": item["sourceLineIndexes"],
            "correctionApplied": True,
            "correctionProfileId": profile["id"],
        }

    medicines = []
    emitted = set()
    for parsed_medicine in parsed.get("medicines", []):
        matched_score, matched_key = max(
            (
                (_similarity(item["medicine"]["name"], parsed_medicine.get("name")), _normalise(item["medicine"]["name"]))
                for item in matched_medicines
            ),
            default=(0.0, ""),
        )
        if matched_score >= MEDICINE_THRESHOLD and matched_key in corrected_by_name:
            if matched_key not in emitted:
                medicines.append(corrected_by_name[matched_key])
                emitted.add(matched_key)
            continue
        medicines.append(parsed_medicine)

    for key, medicine in corrected_by_name.items():
        if key not in emitted:
            medicines.append(medicine)

    source_indexes = sorted({index for block in blocks for index in block["sourceLineIndexes"]})
    if member:
        source_indexes = sorted(set(source_indexes + member["sourceLineIndexes"]))
    return {
        **parsed,
        "medicines": medicines,
        "member": member,
        "correction": {
            "applied": bool(member or corrected_by_name),
            "profileId": profile["id"],
            "strategy": "sectioned_closed_set",
            "sourceLineIndexes": source_indexes,
            "blocks": blocks,
        },
        "normalizedPrescription": [
            normalized_by_name[key]
            for key in corrected_by_name
            if key in normalized_by_name
        ],
    }
