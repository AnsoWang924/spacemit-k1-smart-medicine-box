"""Deterministic, side-effect-free parsing of prescription OCR lines."""

from __future__ import annotations

import re
import unicodedata


_DOSAGE_FORM = (
    r"(?:胶囊|口服液|混悬液|注射液|滴眼液|滴剂|喷雾剂|颗粒|糖浆|乳膏|软膏|"
    r"贴剂|栓剂|散剂|粉针剂|片剂|丸剂|药水|片|丸|散|栓)"
)
_MEDICINE_NAME_RE = re.compile(
    r"([A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff·()（）\-]{1,48}"
    + _DOSAGE_FORM
    + r"(?:\([A-Za-z0-9]+\))?)"
)
_NUMBER = r"(?:\d+(?:\.\d+)?|[一二两三四五六七八九十半]+)"
_DOSE_RE = re.compile(
    r"(?:每次|一次|单次)\s*(" + _NUMBER + r"\s*(?:mg|g|ml|毫克|克|毫升|片|粒|袋|支|丸|滴|揿|喷|包))",
    re.IGNORECASE,
)
_FREQUENCY_PATTERNS = [
    re.compile(r"(?:每日|每天|一日|一天)\s*" + _NUMBER + r"\s*次"),
    re.compile(r"每\s*" + _NUMBER + r"\s*小时(?:\s*(?:一次|1次))?"),
    re.compile(r"(?:早晚|早中晚)(?:各)?\s*" + _NUMBER + r"\s*次"),
    re.compile(r"(?:睡前|晨起|饭前|饭后|餐前|餐后)(?:\s*(?:一次|1次))?"),
    re.compile(r"必要时"),
    re.compile(r"(?<![A-Za-z])(?:qid|tid|bid|qd|qhs|hs|prn)(?![A-Za-z])", re.IGNORECASE),
]
_ROUTE_RE = re.compile(r"(?<![A-Za-z])po(?![A-Za-z])|口服|舌下含服|外用|皮下注射|静脉滴注", re.IGNORECASE)
_LEADING_LABEL_RE = re.compile(
    r"^(?:(?:药品|药物)?名称|药品|药物|处方)[:：]?",
    re.IGNORECASE,
)
_LEADING_INDEX_RE = re.compile(r"^\s*(?:\d+|[一二三四五六七八九十]+)[.、)）]\s*")
_INSTRUCTION_MARKER_RE = re.compile(
    r"(?:用法|用量|每次|一次|单次|每日|每天|一日|一天|口服|外用|睡前|晨起|饭前|饭后|餐前|餐后)"
)


def _clean_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"\s+", "", text)


def _extract_name(text: str) -> str:
    candidate = _LEADING_INDEX_RE.sub("", text)
    candidate = _LEADING_LABEL_RE.sub("", candidate)
    for marker in _INSTRUCTION_MARKER_RE.finditer(candidate):
        prefix = candidate[:marker.start()]
        if marker.group(0) in {"口服", "外用"} and not _MEDICINE_NAME_RE.search(prefix):
            continue
        candidate = prefix
        break
    match = _MEDICINE_NAME_RE.search(candidate)
    return match.group(1) if match else ""


def _extract_dose(text: str) -> str:
    match = _DOSE_RE.search(text)
    return re.sub(r"\s+", "", match.group(1)) if match else ""


def _extract_frequency(text: str) -> str:
    for pattern in _FREQUENCY_PATTERNS:
        match = pattern.search(text)
        if match:
            return re.sub(r"\s+", "", match.group(0))
    return ""


def _extract_route(text: str) -> str:
    match = _ROUTE_RE.search(text)
    if not match:
        return ""
    return "口服" if match.group(0).lower() == "po" else match.group(0)


def _warning(code: str, message: str, indexes: list[int]) -> dict:
    return {
        "code": code,
        "message": message,
        "sourceLineIndexes": sorted(set(int(index) for index in indexes)),
    }


def parse_prescription_lines(lines: list[dict]) -> dict:
    """Return conservative medicine candidates without inventing missing fields."""
    medicines: list[dict] = []
    warnings: list[dict] = []
    current: dict | None = None

    for fallback_index, line in enumerate(lines or []):
        index = int(line.get("index", fallback_index))
        text = _clean_text(line.get("text"))
        if not text:
            continue
        confidence = max(0.0, min(1.0, float(line.get("confidence") or 0.0)))
        if confidence < 0.5:
            warnings.append(_warning("low_confidence_line", "该行识别置信度较低，需要人工核对", [index]))

        name = _extract_name(text)
        dose = _extract_dose(text)
        frequency = _extract_frequency(text)
        route = _extract_route(text)
        if name:
            current = {
                "name": name,
                "dose": dose,
                "frequency": frequency,
                "route": route,
                "confidence": confidence,
                "sourceLineIndexes": [index],
                "_confidences": [confidence],
            }
            medicines.append(current)
            continue

        if (dose or frequency) and current is None:
            warnings.append(
                _warning(
                    "orphan_instruction",
                    "识别到用法用量，但未找到可关联的药品名称",
                    [index],
                )
            )
            continue
        if current is None:
            continue
        if dose and not current["dose"]:
            current["dose"] = dose
        if frequency and not current["frequency"]:
            current["frequency"] = frequency
        if route and not current["route"]:
            current["route"] = route
        if dose or frequency or route:
            current["sourceLineIndexes"].append(index)
            current["_confidences"].append(confidence)

    if not medicines:
        warnings.append(
            _warning(
                "no_structured_medicine",
                "未从 OCR 文本中提取出可确认的药品条目",
                [int(line.get("index", index)) for index, line in enumerate(lines or [])],
            )
        )

    for medicine in medicines:
        confidences = medicine.pop("_confidences")
        medicine["confidence"] = round(sum(confidences) / len(confidences), 4)
        medicine["sourceLineIndexes"] = sorted(set(medicine["sourceLineIndexes"]))
        if not medicine["dose"]:
            warnings.append(
                _warning(
                    "missing_dose",
                    f"{medicine['name']} 缺少明确的每次剂量",
                    medicine["sourceLineIndexes"],
                )
            )
        if not medicine["frequency"]:
            warnings.append(
                _warning(
                    "missing_frequency",
                    f"{medicine['name']} 缺少明确的服用频次",
                    medicine["sourceLineIndexes"],
                )
            )

    return {"medicines": medicines, "warnings": warnings}
