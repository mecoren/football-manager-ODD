"""The FM27 gate: open for the attribute draft, closed for training and items.

FM27 reuses FM26's position weight table; only the display bias (2) and a
fitted CA intercept differ. The draft is a read-only preview, so publishing a
model for it cannot reach a save. Training and item paths write memory, so
their gate (``club_reader._rca_functions``) must stay shut - that asymmetry is
the contract these tests pin down.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from tools.club_reader import (
    GOALKEEPER_ATTRIBUTE_GROUPS, OUTFIELD_ATTRIBUTE_GROUPS, _rca_functions,
)
from tools.game_layout import FM24_LAYOUT, FM26_LAYOUT, FM27_LAYOUT
from tools.player_attribute_builder import build_player_attribute_draft
from tools.player_rca import (
    FM24_RCA_MODEL, FM26_RCA_MODEL, FM27_RCA_CA_OFFSET, FM27_RCA_MODEL,
    fm26_recommended_ca, fm27_recommended_ca,
    rca_ca_offset, rca_model_for_layout_key,
)

ROOT = Path(__file__).resolve().parents[1] / "src"
SLOT_FOR_KEY = {0: 0, 1: 3, 2: 2, 3: 13, 4: 5, 5: 7, 6: 6, 7: 10, 8: 9, 9: 12}
FM27_BIAS = 2


def _writable(groups) -> dict[int, str]:
    return {
        attribute_id: f"{group}:{name}"
        for group, fields in groups.items()
        for attribute_id, name in fields.items()
    }


OUTFIELD_WRITABLE = _writable(OUTFIELD_ATTRIBUTE_GROUPS)
GOALKEEPER_WRITABLE = _writable(GOALKEEPER_ATTRIBUTE_GROUPS)


def block(display: int = 1, bias: int = FM27_BIAS) -> bytes:
    raw = bytearray([5] * 54)
    for index in range(54):
        raw[index] = display * 5 - bias
    return bytes(raw)


def positions(slot: int, rating: int = 20) -> tuple[int, ...]:
    values = [1] * 15
    values[slot] = rating
    return tuple(values)


def draft_block(raw: bytes, draft: dict) -> bytes:
    """Rebuild the block the editor would write, at the FM27 display bias."""
    labels = _writable(
        GOALKEEPER_ATTRIBUTE_GROUPS if draft["group"] == "goalkeeper"
        else OUTFIELD_ATTRIBUTE_GROUPS
    )
    updated = bytearray(raw)
    for attribute_id, label in labels.items():
        updated[attribute_id - 0x0F] = draft["attributes"][label]["after"] * 5 - FM27_BIAS
    return bytes(updated)


def build(raw, ratings, target, *, writable, model=FM27_RCA_MODEL):
    return build_player_attribute_draft(
        raw, ratings, target,
        style="balanced", display_bias=FM27_BIAS, writable=writable, model=model,
    )


class RcaModelMappingTests(unittest.TestCase):
    def test_layout_keys_map_to_their_verified_models(self) -> None:
        self.assertEqual(rca_model_for_layout_key("fm24"), FM24_RCA_MODEL)
        self.assertEqual(rca_model_for_layout_key("fm26"), FM26_RCA_MODEL)
        self.assertEqual(rca_model_for_layout_key("fm27"), FM27_RCA_MODEL)

    def test_unknown_layout_keys_publish_no_model(self) -> None:
        for key in ("", None, "fm23", "fm30"):
            with self.subTest(key=key):
                self.assertEqual(rca_model_for_layout_key(key), "")

    def test_only_the_fm27_model_carries_a_ca_offset(self) -> None:
        self.assertEqual(rca_ca_offset(FM24_RCA_MODEL), 0.0)
        self.assertEqual(rca_ca_offset(FM26_RCA_MODEL), 0.0)
        self.assertEqual(rca_ca_offset(""), 0.0)
        self.assertEqual(rca_ca_offset(FM27_RCA_MODEL), FM27_RCA_CA_OFFSET)


class Fm27FormulaTests(unittest.TestCase):
    def test_fm27_is_the_shared_curve_shifted_by_its_fitted_offset(self) -> None:
        raw = block(display=12)
        ratings = positions(12)
        base = fm26_recommended_ca(raw, ratings, FM27_BIAS)
        fm27 = fm27_recommended_ca(raw, ratings)
        self.assertEqual(fm27["model"], FM27_RCA_MODEL)
        self.assertEqual(fm27["position_key"], base["position_key"])
        # The block itself is unchanged, so the measured average must be too:
        # only the calibration of that average into CA moves.
        self.assertEqual(fm27["weighted_average"], base["weighted_average"])
        self.assertAlmostEqual(
            float(fm27["recommended_ca_value"]),
            float(base["recommended_ca_value"]) + FM27_RCA_CA_OFFSET,
            places=6,
        )
        self.assertGreater(fm27["recommended_ca"], base["recommended_ca"])

    def test_display_bias_defaults_to_two_for_fm27(self) -> None:
        raw = block(display=12)
        ratings = positions(12)
        self.assertEqual(
            fm27_recommended_ca(raw, ratings)["recommended_ca"],
            fm27_recommended_ca(raw, ratings, FM27_BIAS)["recommended_ca"],
        )
        self.assertNotEqual(
            fm27_recommended_ca(raw, ratings)["recommended_ca"],
            fm27_recommended_ca(raw, ratings, 0)["recommended_ca"],
        )

    def test_the_offset_keeps_both_one_sided_ends_reachable(self) -> None:
        ratings = positions(12)
        floor = fm27_recommended_ca(block(display=1), ratings)
        ceiling = fm27_recommended_ca(block(display=20), ratings)
        self.assertEqual(floor["recommended_ca"], 1)
        self.assertEqual(ceiling["recommended_ca"], 200)


class Fm27DraftTests(unittest.TestCase):
    def test_every_position_key_hits_every_sampled_target(self) -> None:
        for position_key, slot in SLOT_FOR_KEY.items():
            writable = GOALKEEPER_WRITABLE if position_key == 0 else OUTFIELD_WRITABLE
            ratings = positions(slot)
            for target in (1, 6, 60, 199, 200):
                with self.subTest(position_key=position_key, target=target):
                    raw = block()
                    draft = build(raw, ratings, target, writable=writable)
                    self.assertTrue(draft["hit"], draft["warnings"])
                    self.assertEqual(draft["ca_model"], FM27_RCA_MODEL)
                    self.assertEqual(draft["formula_ca"], target)
                    settled = fm27_recommended_ca(draft_block(raw, draft), ratings)
                    self.assertEqual(settled["recommended_ca"], target)

    def test_an_unknown_model_is_still_refused(self) -> None:
        with self.assertRaises(ValueError):
            build(
                block(), positions(12), 100,
                writable=OUTFIELD_WRITABLE, model="fm30",
            )


class TrainingGateTests(unittest.TestCase):
    def test_fm24_and_fm26_still_publish_their_training_trio(self) -> None:
        for layout in (FM24_LAYOUT, FM26_LAYOUT):
            with self.subTest(layout=layout.key):
                self.assertEqual(len(_rca_functions(layout)), 3)

    def test_fm27_training_and_item_paths_stay_closed(self) -> None:
        # The draft is read-only. Training and the CA-spending items write
        # CA-bearing attributes into the save, so they keep refusing FM27 until
        # the fitted intercept has out-of-sample proof on a second save.
        with self.assertRaises(ValueError):
            _rca_functions(FM27_LAYOUT)

    def test_no_fm27_training_wrapper_exists(self) -> None:
        import tools.player_rca as module

        for name in ("fm27_training_ca_change", "fm27_ca_budget_attribute_change"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(module, name))


class EditorWiringTests(unittest.TestCase):
    def test_the_editor_publishes_the_model_through_the_mapping(self) -> None:
        source = (ROOT / "tools" / "club_reader.py").read_text(encoding="utf-8")
        matched = re.findall(r'"ca_model":\s*rca_model_for_layout_key\(', source)
        self.assertEqual(len(matched), 1, matched)
        self.assertNotIn('FM26_RCA_MODEL if layout.key.startswith("fm26")', source)


if __name__ == "__main__":
    unittest.main()
