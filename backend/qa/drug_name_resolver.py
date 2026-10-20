"""Shared, conservative medicine-name resolution for the PC production path."""
from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENDOR_DIR = ROOT / "vendor"
if VENDOR_DIR.is_dir():
    sys.path.insert(0, str(VENDOR_DIR))

from pypinyin import lazy_pinyin


NON_UNIQUE_ALIASES = {
    "退烧药", "止痛药", "降压药", "抗过敏药", "感冒药", "消炎药",
    "益生菌", "感冒胶囊", "维生素片", "钙片",
}
FUZZY_MIN_SCORE = 0.72
FUZZY_MIN_PINYIN = 0.70
FUZZY_MIN_MARGIN = 0.08

_PROFILE_SIGNATURE = None
_PROFILES = ()


def normalize_drug_message(text: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:'\"“”‘’（）()\[\]【】]+", "", str(text or ""))


def all_names(card: dict) -> list[str]:
    return [
        name for name in [card.get("drug_name"), card.get("generic_name"), *card.get("aliases", [])]
        if name and name not in NON_UNIQUE_ALIASES
    ]


def find_latin_alias(question: str, cards: list[dict]) -> tuple[dict | None, str | None]:
    query = str(question or "")
    hits = []
    for card in cards:
        for alias in card.get("latin_aliases", []):
            if not alias:
                continue
            match = re.search(r"(?<![A-Za-z])" + re.escape(alias) + r"(?![A-Za-z])", query, re.IGNORECASE)
            if match:
                hits.append((match.start(), -len(alias), card, alias))
    if not hits:
        return None, None
    hits.sort(key=lambda item: (item[0], item[1]))
    return hits[0][2], hits[0][3]


def _profiles(cards: list[dict]) -> tuple:
    global _PROFILE_SIGNATURE, _PROFILES
    signature = tuple((card.get("drug_name"), tuple(all_names(card))) for card in cards)
    if signature == _PROFILE_SIGNATURE:
        return _PROFILES
    profiles = []
    for card in cards:
        for name in all_names(card):
            canonical = normalize_drug_message(name)
            if len(canonical) < 3:
                continue
            tokens = tuple(lazy_pinyin(canonical))
            profiles.append((
                card,
                name,
                canonical,
                tokens,
                " ".join(tokens),
                range(max(2, len(canonical) - 1), len(canonical) + 2),
            ))
    _PROFILE_SIGNATURE = signature
    _PROFILES = tuple(profiles)
    return _PROFILES


def prepare_fuzzy_alias_profiles(cards: list[dict]) -> None:
    _profiles(cards)


def _trim_question_tail(fragment: str) -> str:
    # Do not let “怎么/有什么用” become part of the proposed medicine name.
    fragment = re.split(r"怎么|如何|能不能|是否|有什么|什么", fragment, maxsplit=1)[0]
    return fragment.rstrip("怎如能可吗")


def fuzzy_candidate(question: str, cards: list[dict]) -> tuple[dict | None, str | None, dict | None]:
    """Return the best conservative fuzzy medicine match, if any."""
    text = normalize_drug_message(question)
    if not re.search(r"[\u4e00-\u9fff]", text):
        return None, None, None
    text_tokens = lazy_pinyin(text)
    best_by_card = {}
    for card, name, canonical, canonical_tokens, canonical_pinyin, lengths in _profiles(cards):
        windows = []
        for start in range(len(text)):
            for length in lengths:
                fragment = text[start:start + length]
                if len(fragment) < 2 or re.search(r"怎么|如何|能不能|是否|有什么", fragment):
                    continue
                fragment_tokens = text_tokens[start:start + length]
                rough = sum(a == b for a, b in zip(canonical_tokens, fragment_tokens)) / max(
                    len(canonical_tokens), len(fragment_tokens)
                )
                windows.append((rough, start, length, fragment))
        windows.sort(reverse=True)
        for _, start, length, raw_fragment in windows[:2]:
            fragment = _trim_question_tail(raw_fragment)
            if len(fragment) < 3:
                continue
            raw_chars = difflib.SequenceMatcher(a=canonical, b=fragment, autojunk=False).ratio()
            char_score = max(0.0, raw_chars - 0.20 * abs(len(canonical) - len(fragment)))
            fragment_pinyin = " ".join(lazy_pinyin(fragment))
            raw_pinyin = difflib.SequenceMatcher(
                a=canonical_pinyin, b=fragment_pinyin, autojunk=False
            ).ratio()
            pinyin_score = max(0.0, raw_pinyin - 0.20 * abs(len(canonical) - len(fragment)))
            score = 0.80 * pinyin_score + 0.20 * char_score
            candidate = {
                "candidate": card["drug_name"],
                "matchedAlias": name,
                "heardFragment": fragment,
                "characterSimilarity": round(char_score, 4),
                "pinyinSimilarity": round(pinyin_score, 4),
                "confidence": round(score, 4),
            }
            current = best_by_card.get(card["drug_name"])
            if current is None or candidate["confidence"] > current["confidence"]:
                best_by_card[card["drug_name"]] = candidate
    ranked = sorted(best_by_card.items(), key=lambda item: item[1]["confidence"], reverse=True)
    if not ranked:
        return None, None, None
    card_name, best = ranked[0]
    second = ranked[1][1]["confidence"] if len(ranked) > 1 else 0.0
    best["secondCandidateScore"] = round(second, 4)
    best["margin"] = round(best["confidence"] - second, 4)
    best["autoAccepted"] = bool(
        best["confidence"] >= FUZZY_MIN_SCORE
        and best["pinyinSimilarity"] >= FUZZY_MIN_PINYIN
        and best["margin"] >= FUZZY_MIN_MARGIN
    )
    card = next(item for item in cards if item["drug_name"] == card_name)
    return card, best["matchedAlias"], best


def canonical_question(question: str, candidate: dict, canonical_name: str) -> str:
    fragment = candidate.get("heardFragment") or ""
    if fragment and fragment in question:
        return question.replace(fragment, canonical_name, 1)
    return question


def resolved_name_correction(question: str, cards: list[dict]) -> dict | None:
    """Describe a silent Latin/pinyin correction without changing dialogue state."""
    latin_card, latin_alias = find_latin_alias(question, cards)
    if latin_card is not None:
        return {
            "source": "latin_alias",
            "entered_name": latin_alias,
            "canonical_name": latin_card["drug_name"],
            "card": latin_card,
        }
    if any(name and name in str(question or "") for card in cards for name in all_names(card)):
        return None
    fuzzy_card, _, fuzzy_detail = fuzzy_candidate(question, cards)
    if fuzzy_card is not None and fuzzy_detail and fuzzy_detail.get("autoAccepted"):
        return {
            "source": "pinyin_fuzzy",
            "entered_name": fuzzy_detail.get("heardFragment") or "",
            "canonical_name": fuzzy_card["drug_name"],
            "card": fuzzy_card,
        }
    return None
