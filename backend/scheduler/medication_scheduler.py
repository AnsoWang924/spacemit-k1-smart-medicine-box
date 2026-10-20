"""Pure-Python medication schedule engine for the competition demo.

This module deliberately never chooses dose or frequency.  Those values are
prescription input.  It only normalizes medicine names, enumerates slot
combinations, applies configured rules, picks a deterministic best plan and
emits both UI data and a rule-backed explanation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import combinations, product
import json
from math import prod
from pathlib import Path
from typing import Any


class SchedulerError(ValueError):
    """Input or rule data prevents automatic scheduling."""


@dataclass(frozen=True)
class Slot:
    id: str
    label: str
    schedule_field: str
    offset_minutes: int
    tie_break_order: int


class MedicationScheduler:
    def __init__(self, rules: dict[str, Any]):
        self.rules = rules
        self.slots = [Slot(**item) for item in rules["slots"]]
        self.slot_by_id = {slot.id: slot for slot in self.slots}
        self.catalog = {item["drug_id"]: item for item in rules["catalog"]}
        self.rule_by_drug = {item["drug_id"]: item for item in rules["drug_rules"]}
        self.alias_index = self._build_alias_index()
        self.pair_rules = rules.get("pair_rules", [])
        self.scoring = rules["scoring"]
        self.rule_version = f"medication-scheduler-rules-v{rules.get('schema_version', 1)}"
        self._validate_rule_database()

    @classmethod
    def from_file(cls, path: str | Path) -> "MedicationScheduler":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def _validate_rule_database(self) -> None:
        expected = self.rules["canonical_catalog_source"]["expected_count"]
        if len(self.catalog) != expected:
            raise SchedulerError(f"规则库药品目录应为{expected}种，当前为{len(self.catalog)}种")
        names = [item["canonical_name"] for item in self.catalog.values()]
        if len(names) != len(set(names)):
            raise SchedulerError("规则库存在重复标准药名")
        for drug_id, rule in self.rule_by_drug.items():
            if drug_id not in self.catalog:
                raise SchedulerError(f"单药规则引用未知药物: {drug_id}")
            for slot_id in rule["allowed_slots"]:
                if slot_id not in self.slot_by_id:
                    raise SchedulerError(f"单药规则引用未知节点: {slot_id}")
        for rule in self.pair_rules:
            if rule["drug_a"] not in self.catalog or rule["drug_b"] not in self.catalog:
                raise SchedulerError(f"两药规则引用未知药物: {rule['rule_id']}")
            if rule["same_slot_policy"] not in {"forbid", "prefer"}:
                raise SchedulerError(f"不支持的同节点规则: {rule['rule_id']}")

    @staticmethod
    def _normal_form(value: str) -> str:
        return (
            str(value)
            .strip()
            .replace(" ", "")
            .replace("（", "(")
            .replace("）", ")")
            .replace("Ⅲ", "III")
            .replace("三", "III")
        )

    def _build_alias_index(self) -> dict[str, str]:
        index: dict[str, str] = {}
        for drug_id, item in self.catalog.items():
            for name in [item["canonical_name"], *item.get("aliases", [])]:
                normalized = self._normal_form(name)
                previous = index.get(normalized)
                if previous and previous != drug_id:
                    raise SchedulerError(f"别名冲突: {name}")
                index[normalized] = drug_id
        return index

    def normalize_drug(self, name: str) -> str:
        drug_id = self.alias_index.get(self._normal_form(name))
        if not drug_id:
            raise SchedulerError(f"未收录药名，不能自动排程: {name}")
        item = self.catalog[drug_id]
        if item["scheduler_status"] != "configured" or drug_id not in self.rule_by_drug:
            raise SchedulerError(f"药品已识别但尚未配置排程规则，需人工确认: {item['canonical_name']}")
        return drug_id

    def _slot_minutes(self, schedule: dict[str, str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for slot in self.slots:
            value = schedule.get(slot.schedule_field)
            if not value:
                raise SchedulerError(f"缺少作息时间: {slot.schedule_field}")
            try:
                base = datetime.strptime(value, "%H:%M")
            except ValueError as exc:
                raise SchedulerError(f"作息时间格式必须为HH:MM: {slot.schedule_field}={value}") from exc
            result[slot.id] = base.hour * 60 + base.minute + slot.offset_minutes
        return result

    def _prepare_prescription(self, prescription: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not prescription:
            raise SchedulerError("处方为空")
        if len(prescription) > 12:
            raise SchedulerError("处方药品过多，需人工确认")
        prepared: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in prescription:
            drug_id = self.normalize_drug(item.get("name", ""))
            if drug_id in seen:
                raise SchedulerError(f"同一种药请合并为一条处方: {self.catalog[drug_id]['canonical_name']}")
            seen.add(drug_id)
            try:
                times = int(item["times_per_day"])
                amount = int(item["amount"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SchedulerError(f"药品处方缺少有效次数或数量: {item.get('name', '未命名药品')}") from exc
            if times < 1 or amount < 1:
                raise SchedulerError(f"每日次数和每次数量必须大于0: {item.get('name', '未命名药品')}")
            rule = self.rule_by_drug[drug_id]
            if times > len(rule["allowed_slots"]):
                raise SchedulerError(f"{self.catalog[drug_id]['canonical_name']}每日{times}次超出已配置可选节点数")
            prepared.append({
                "drug_id": drug_id,
                "name": self.catalog[drug_id]["canonical_name"],
                "spoken_name": self.catalog[drug_id]["spoken_name"],
                "times_per_day": times,
                "amount": amount,
                "unit": str(item.get("unit", "")).strip() or "次",
                "rule": rule,
            })
        return prepared

    def _candidate_slots(self, medicine: dict[str, Any]) -> list[tuple[str, ...]]:
        allowed = medicine["rule"]["allowed_slots"]
        return list(combinations(allowed, medicine["times_per_day"]))

    def _pair_rule_for(self, left: str, right: str) -> list[dict[str, Any]]:
        return [
            rule for rule in self.pair_rules
            if {rule["drug_a"], rule["drug_b"]} == {left, right}
        ]

    def _is_candidate_valid(
        self,
        medicines: list[dict[str, Any]],
        choices: tuple[tuple[str, ...], ...],
        slot_minutes: dict[str, int],
    ) -> tuple[bool, list[dict[str, Any]]]:
        violations: list[dict[str, Any]] = []
        by_drug = {medicine["drug_id"]: set(choice) for medicine, choice in zip(medicines, choices)}
        for index, medicine in enumerate(medicines):
            selected = choices[index]
            minimum = medicine["rule"].get("same_drug_min_gap_minutes")
            if minimum is not None:
                for left, right in combinations(selected, 2):
                    if abs(slot_minutes[left] - slot_minutes[right]) < minimum:
                        violations.append({"type": "same_drug_gap", "drug": medicine["drug_id"]})
        for left_index, left in enumerate(medicines):
            for right_index in range(left_index + 1, len(medicines)):
                right = medicines[right_index]
                common_slots = by_drug[left["drug_id"]] & by_drug[right["drug_id"]]
                for rule in self._pair_rule_for(left["drug_id"], right["drug_id"]):
                    if rule["same_slot_policy"] == "forbid" and common_slots:
                        violations.append({"type": "pair_same_slot", "rule_id": rule["rule_id"], "slots": sorted(common_slots)})
                    minimum = rule.get("min_separation_minutes")
                    if minimum is not None:
                        for left_slot in by_drug[left["drug_id"]]:
                            for right_slot in by_drug[right["drug_id"]]:
                                if abs(slot_minutes[left_slot] - slot_minutes[right_slot]) < minimum:
                                    violations.append({"type": "pair_gap", "rule_id": rule["rule_id"]})
        return not violations, violations

    def _score_candidate(
        self,
        medicines: list[dict[str, Any]],
        choices: tuple[tuple[str, ...], ...],
        slot_minutes: dict[str, int],
    ) -> tuple[int, tuple[int, ...]]:
        score = 0
        selected = {medicine["drug_id"]: set(choice) for medicine, choice in zip(medicines, choices)}
        for medicine, choice in zip(medicines, choices):
            preferred = set(medicine["rule"].get("preferred_slots", []))
            score += self.scoring.get("preferred_slot_weight", 0) * len(set(choice) & preferred)
            for left, right in combinations(choice, 2):
                score += self.scoring["same_drug_spacing_weight"] * abs(slot_minutes[left] - slot_minutes[right])
        for rule in self.pair_rules:
            if rule["same_slot_policy"] == "prefer" and rule["drug_a"] in selected and rule["drug_b"] in selected:
                if selected[rule["drug_a"]] & selected[rule["drug_b"]]:
                    score += self.scoring["prefer_merge_weight"]
        occupied_slots = set().union(*selected.values()) if selected else set()
        score += self.scoring["prefer_unused_slot_weight"] * len(occupied_slots)
        tie_break = tuple(
            self.slot_by_id[slot_id].tie_break_order
            for _drug_id, choice in sorted(
                ((medicine["drug_id"], choice) for medicine, choice in zip(medicines, choices)),
                key=lambda item: item[0],
            )
            for slot_id in choice
        )
        return score, tie_break

    def _reason_facts(self, medicines: list[dict[str, Any]], choices: tuple[tuple[str, ...], ...]) -> dict[str, list[dict[str, Any]]]:
        selected = {medicine["drug_id"]: set(choice) for medicine, choice in zip(medicines, choices)}
        facts: dict[str, list[dict[str, Any]]] = {}
        for medicine, choice in zip(medicines, choices):
            item_facts = [{"code": "frequency_satisfied", "times_per_day": medicine["times_per_day"]}]
            preferred_hit = sorted(
                set(choice) & set(medicine["rule"].get("preferred_slots", [])),
                key=lambda item: self.slot_by_id[item].tie_break_order,
            )
            if preferred_hit:
                item_facts.append({"code": "preferred_slot_selected", "slots": preferred_hit})
            if len(choice) > 1 and len(choice) == len(medicine["rule"]["allowed_slots"]):
                item_facts.append({"code": "all_allowed_slots_used", "slots": list(choice)})
            elif len(choice) > 1:
                item_facts.append({"code": "same_drug_spacing_selected", "slots": list(choice)})
            if len(medicine["rule"]["allowed_slots"]) == 1:
                item_facts.append({"code": "unique_allowed_slot", "slots": list(choice)})
            facts[medicine["drug_id"]] = item_facts
        for rule in self.pair_rules:
            if rule["drug_a"] not in selected or rule["drug_b"] not in selected:
                continue
            owner = rule["explanation_owner"]
            other = rule["drug_b"] if owner == rule["drug_a"] else rule["drug_a"]
            if rule["same_slot_policy"] == "forbid":
                facts[owner].append({"code": "separated_from_other_drug", "other_drug_id": other})
            elif rule["same_slot_policy"] == "prefer" and selected[rule["drug_a"]] & selected[rule["drug_b"]]:
                shared = sorted(selected[rule["drug_a"]] & selected[rule["drug_b"]], key=lambda item: self.slot_by_id[item].tie_break_order)
                facts[owner].append({"code": "merged_with_other_drug", "other_drug_id": other, "slots": shared})
        for medicine, choice in zip(medicines, choices):
            drug_id = medicine["drug_id"]
            owned_hard_rules = [
                rule for rule in self.pair_rules
                if rule["same_slot_policy"] == "forbid" and rule["explanation_owner"] == drug_id
                and rule["drug_a"] in selected and rule["drug_b"] in selected
            ]
            owns_hard_relation = bool(owned_hard_rules)
            if owns_hard_relation:
                blocked = set()
                for rule in owned_hard_rules:
                    other_id = rule["drug_b"] if drug_id == rule["drug_a"] else rule["drug_a"]
                    blocked.update(selected[other_id])
                remaining = set(medicine["rule"]["allowed_slots"]) - blocked
                if set(choice) == remaining and len(remaining) == 1:
                    facts[drug_id].append({
                        "code": "conflict_forced_slot",
                        "slots": list(choice),
                        "blocked_slots": sorted(blocked, key=lambda item: self.slot_by_id[item].tie_break_order),
                    })
            shared_with_other = any(
                selected[drug_id] & selected[other_id]
                for other_id in selected
                if other_id != drug_id
            )
            if (
                len(choice) == 1
                and len(medicine["rule"]["allowed_slots"]) > 1
                and not owns_hard_relation
                and not shared_with_other
            ):
                facts[drug_id].append({"code": "uses_unoccupied_slot", "slots": list(choice)})
        return facts

    def _primary_reason(
        self,
        medicine: dict[str, Any],
        choice: tuple[str, ...],
        item_facts: list[dict[str, Any]],
    ) -> str:
        """Render exactly one user-facing reason while preserving all facts in audit."""
        by_code: dict[str, list[dict[str, Any]]] = {}
        for fact in item_facts:
            by_code.setdefault(fact["code"], []).append(fact)

        # The order is intentional: a forced safety constraint explains a result
        # better than a preference that happened to be satisfied as a side effect.
        forced = by_code.get("conflict_forced_slot")
        if forced:
            other_ids = [fact["other_drug_id"] for fact in by_code.get("separated_from_other_drug", [])]
            other_names = "、".join(dict.fromkeys(self.catalog[item]["canonical_name"] for item in other_ids))
            slot_text = "、".join(self.slot_by_id[item].label for item in forced[0]["slots"])
            if other_names:
                return f"为避免与{other_names}在同一时段服用，系统将它安排在剩余可用的{slot_text}"
            return f"为满足当前冲突规则，系统将它安排在剩余可用的{slot_text}"

        if "same_drug_spacing_selected" in by_code:
            return "系统在可用时段中尽量拉开当天多次服药的间隔"

        merged = by_code.get("merged_with_other_drug")
        if merged:
            other = self.catalog[merged[0]["other_drug_id"]]["canonical_name"]
            slot_text = "、".join(self.slot_by_id[item].label for item in merged[0]["slots"])
            return f"可与{other}在{slot_text}合并提醒，方便按时服用"

        preferred = by_code.get("preferred_slot_selected")
        if preferred:
            slot_text = "、".join(self.slot_by_id[item].label for item in preferred[0]["slots"])
            return f"{slot_text}是本药在当前方案中的优先服用时段"

        all_slots = by_code.get("all_allowed_slots_used")
        if all_slots:
            slot_text = "、".join(self.slot_by_id[item].label for item in all_slots[0]["slots"])
            return f"每天需要安排{medicine['times_per_day']}次，已覆盖可用的{slot_text}时段"

        unique = by_code.get("unique_allowed_slot")
        if unique:
            slot_text = "、".join(self.slot_by_id[item].label for item in unique[0]["slots"])
            return f"根据本药的单药时间规则，{slot_text}是本次可用的服药时段"

        unused = by_code.get("uses_unoccupied_slot")
        if unused:
            slot_text = "、".join(self.slot_by_id[item].label for item in unused[0]["slots"])
            return f"{slot_text}是当前未被其他药物占用的可用时段"

        return "系统已在当前可用时段中完成安排"

    def _build_explanation(self, medicines: list[dict[str, Any]], choices: tuple[tuple[str, ...], ...], facts: dict[str, list[dict[str, Any]]]) -> str:
        sentences: list[str] = []
        for medicine, choice in zip(medicines, choices):
            labels = [self.slot_by_id[item].label for item in choice]
            dose_text = "、".join(f"{label}{medicine['amount']}{medicine['unit']}" for label in labels)
            reason = self._primary_reason(medicine, choice, facts[medicine["drug_id"]])
            sentences.append(f"{medicine['name']}安排在{dose_text}。{reason}。")
        # One medicine per paragraph keeps the right-side explanation easy to scan
        # and preserves the one-to-one link between a conclusion and its rule facts.
        return "\n\n".join(sentences)

    def schedule(self, request: dict[str, Any]) -> dict[str, Any]:
        medicines = self._prepare_prescription(request.get("prescription", []))
        slot_minutes = self._slot_minutes(request.get("schedule", {}))
        candidate_sets = [self._candidate_slots(medicine) for medicine in medicines]
        if prod(len(candidates) for candidates in candidate_sets) > 20_000:
            raise SchedulerError("处方组合过多，需人工确认")
        total_candidates = 0
        rejected: dict[str, int] = {}
        best: tuple[int, tuple[int, ...], tuple[tuple[str, ...], ...]] | None = None
        for choices in product(*candidate_sets):
            total_candidates += 1
            valid, violations = self._is_candidate_valid(medicines, choices, slot_minutes)
            if not valid:
                for violation in violations:
                    key = violation.get("rule_id", violation["type"])
                    rejected[key] = rejected.get(key, 0) + 1
                continue
            score, tie_break = self._score_candidate(medicines, choices, slot_minutes)
            candidate = (score, tuple(-item for item in tie_break), choices)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
        if best is None:
            return {
                "status": "needs_review",
                "reason": "无可行方案",
                "reasons": sorted(rejected) or ["no_feasible_schedule"],
                "ruleVersion": self.rule_version,
                "audit": {"total_candidates": total_candidates, "rejected": rejected},
            }
        score, _negated_tie_break, choices = best
        facts = self._reason_facts(medicines, choices)
        by_slot: dict[str, list[dict[str, Any]]] = {slot.id: [] for slot in self.slots}
        for medicine, choice in zip(medicines, choices):
            for slot_id in choice:
                by_slot[slot_id].append({"name": medicine["name"], "amount": str(medicine["amount"]), "unit": medicine["unit"]})
        schedule_rows = []
        for slot in sorted(self.slots, key=lambda item: slot_minutes[item.id]):
            if by_slot[slot.id]:
                by_slot[slot.id].sort(key=lambda med: med["name"])
                minute = slot_minutes[slot.id] % (24 * 60)
                schedule_rows.append({"slot": slot.label, "time": f"{minute // 60:02d}:{minute % 60:02d}", "meds": by_slot[slot.id]})
        trace = []
        for medicine, choice in zip(medicines, choices):
            trace.append({"drug_id": medicine["drug_id"], "name": medicine["name"], "selected_slots": [self.slot_by_id[item].label for item in choice], "facts": facts[medicine["drug_id"]]})
        return {
            "status": "ok",
            "ruleVersion": self.rule_version,
            "patient": request.get("patient", ""),
            "schedule": schedule_rows,
            "planExplanation": self._build_explanation(medicines, choices, facts),
            "trace": trace,
            "audit": {"total_candidates": total_candidates, "rejected": rejected, "selected_score": score},
        }
