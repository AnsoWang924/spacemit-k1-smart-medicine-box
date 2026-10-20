import unittest
import sys
from pathlib import Path

GATEWAY_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GATEWAY_DIR))

from prescription_correction_db import apply_closed_set_correction
from prescription_parser import parse_prescription_lines


def ocr_line(index: int, text: str, confidence: float = 0.98) -> dict:
    return {
        "index": index,
        "text": text,
        "confidence": confidence,
        "detConfidence": 0.99,
    }


class PrescriptionCorrectionTests(unittest.TestCase):
    def correct(self, lines: list[dict]) -> dict:
        return apply_closed_set_correction(lines, parse_prescription_lines(lines))

    def test_recognized_member_and_medicine_fill_complete_form_row(self) -> None:
        lines = [
            ocr_line(0, "姓名：王爷节"),
            ocr_line(1, "小柴胡颗粒"),
        ]

        result = self.correct(lines)

        self.assertEqual(result["member"]["name"], "王爷爷")
        self.assertEqual(result["member"]["correctionProfileId"], "wang-grandpa")
        self.assertEqual(len(result["medicines"]), 1)
        medicine = result["medicines"][0]
        self.assertEqual(
            {key: medicine[key] for key in ("name", "times", "amount", "unit", "stock")},
            {"name": "小柴胡颗粒", "times": "3", "amount": "1", "unit": "袋", "stock": "9"},
        )
        self.assertTrue(medicine["correctionApplied"])
        self.assertEqual(medicine["sourceLineIndexes"], [1])
        self.assertEqual([item["name"] for item in result["normalizedPrescription"]], ["小柴胡颗粒"])

    def test_only_observed_profile_medicines_are_added(self) -> None:
        result = self.correct([
            ocr_line(0, "姓名：王爷爷"),
            ocr_line(1, "小柴胡颗粒"),
        ])

        self.assertEqual([item["name"] for item in result["medicines"]], ["小柴胡颗粒"])
        self.assertEqual(
            [block["name"] for block in result["correction"]["blocks"] if block["present"]],
            ["小柴胡颗粒"],
        )
        self.assertTrue(all(
            block["sourceLineIndexes"] == []
            for block in result["correction"]["blocks"]
            if not block["present"]
        ))

    def test_unmatched_ocr_medicine_is_preserved_in_mixed_prescription(self) -> None:
        lines = [
            ocr_line(0, "姓名：王爷爷"),
            ocr_line(1, "小柴胡颗粒"),
            ocr_line(2, "阿莫西林胶囊"),
        ]

        result = self.correct(lines)

        self.assertEqual(
            [item["name"] for item in result["medicines"]],
            ["小柴胡颗粒", "阿莫西林胶囊"],
        )
        unknown = result["medicines"][1]
        self.assertNotIn("correctionApplied", unknown)
        self.assertEqual(unknown["sourceLineIndexes"], [2])

    def test_age_alone_does_not_autofill_member_identity(self) -> None:
        result = self.correct([
            ocr_line(0, "年龄：65"),
            ocr_line(1, "小柴胡颗粒"),
        ])

        self.assertIsNone(result["member"])
        self.assertEqual(result["medicines"][0]["name"], "小柴胡颗粒")

    def test_low_confidence_profile_text_does_not_trigger_regression(self) -> None:
        result = self.correct([
            ocr_line(0, "姓名：王爷爷", confidence=0.30),
            ocr_line(1, "小柴胡颗粒", confidence=0.30),
        ])

        self.assertIsNone(result["member"])
        self.assertFalse(result["correction"]["applied"])
        self.assertNotIn("correctionApplied", result["medicines"][0])

if __name__ == "__main__":
    unittest.main()
