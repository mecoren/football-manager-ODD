"""The attribute draft has to land on the requested CA, every time.

The solver is pure maths over the verified RCA formula, so these tests build
raw 54-byte blocks the same way ``test_player_rca`` does and then ask the real
formula function to confirm the result. A draft that only looks plausible is
not enough: the whole point of the feature is that the attributes and the CA
agree once the editor saves both.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from tools.club_reader import (
    GOALKEEPER_ATTRIBUTE_GROUPS,
    OUTFIELD_ATTRIBUTE_GROUPS,
)
from tools.player_attribute_builder import (
    ATTRIBUTE_STYLES,
    attribute_style_catalog,
    build_player_attribute_draft,
    style_keys_for_group,
)
from tools.player_rca import fm24_recommended_ca

ROOT = Path(__file__).resolve().parents[1] / "src"
SLOT_NAMES = (
    "GK", "SW", "DL", "DC", "DR", "DM", "ML", "MC", "MR",
    "AML", "AMC", "AMR", "ST", "WBL", "WBR",
)
# One representative slot per FMRTE position key.
SLOT_FOR_KEY = {0: 0, 1: 3, 2: 2, 3: 13, 4: 5, 5: 7, 6: 6, 7: 10, 8: 9, 9: 12}
TARGETS = (1, 2, 6, 25, 50, 80, 120, 160, 199, 200)


def _writable(groups) -> dict[int, str]:
    return {
        attribute_id: f"{group}:{name}"
        for group, fields in groups.items()
        for attribute_id, name in fields.items()
    }


OUTFIELD_WRITABLE = _writable(OUTFIELD_ATTRIBUTE_GROUPS)
GOALKEEPER_WRITABLE = _writable(GOALKEEPER_ATTRIBUTE_GROUPS)


def block(values: dict[int, int] | None = None, bias: int = 0) -> bytes:
    """Build a raw attribute block from display values (default: all ones)."""
    raw = bytearray([5] * 54)
    for attribute_id, display in (values or {}).items():
        raw[attribute_id - 0x0F] = display * 5 - bias
    return bytes(raw)


def positions(slot: int | None = None, rating: int = 20, **named: int) -> tuple[int, ...]:
    values = [1] * 15
    if slot is not None:
        values[slot] = rating
    for name, value in named.items():
        values[SLOT_NAMES.index(name.upper())] = value
    return tuple(values)


def draft_block(raw: bytes, draft: dict, bias: int = 0) -> bytes:
    """Rebuild the block the editor would write for a draft."""
    labels = _writable(
        GOALKEEPER_ATTRIBUTE_GROUPS if draft["group"] == "goalkeeper"
        else OUTFIELD_ATTRIBUTE_GROUPS
    )
    updated = bytearray(raw)
    for attribute_id, label in labels.items():
        updated[attribute_id - 0x0F] = draft["attributes"][label]["after"] * 5 - bias
    return bytes(updated)


def build(raw, ratings, target, *, writable, style="balanced", bias=0, **kwargs):
    return build_player_attribute_draft(
        raw, ratings, target,
        style=style, display_bias=bias, writable=writable, **kwargs,
    )


class AttributeDraftTargetTests(unittest.TestCase):
    def test_every_position_key_hits_every_sampled_target(self) -> None:
        for position_key, slot in SLOT_FOR_KEY.items():
            writable = GOALKEEPER_WRITABLE if position_key == 0 else OUTFIELD_WRITABLE
            ratings = positions(slot)
            for target in TARGETS:
                with self.subTest(position_key=position_key, target=target):
                    raw = block()
                    draft = build(raw, ratings, target, writable=writable)
                    self.assertTrue(draft["hit"], draft["warnings"])
                    self.assertEqual(draft["formula_ca"], target)
                    settled = fm24_recommended_ca(draft_block(raw, draft), ratings)
                    self.assertEqual(settled["recommended_ca"], target)

    def test_target_199_never_spills_into_the_200_branch(self) -> None:
        ratings = positions(12)
        draft = build(block(), ratings, 199, writable=OUTFIELD_WRITABLE)
        self.assertEqual(draft["formula_ca"], 199)
        self.assertLess(draft["weighted_average"], 16.0)

    def test_target_one_lands_in_the_floor_segment(self) -> None:
        draft = build(block(), positions(12), 1, writable=OUTFIELD_WRITABLE)
        self.assertEqual(draft["formula_ca"], 1)
        self.assertLessEqual(draft["weighted_average"], 6.0)

    def test_target_200_lands_in_the_ceiling_segment(self) -> None:
        draft = build(block(), positions(12), 200, writable=OUTFIELD_WRITABLE)
        self.assertEqual(draft["formula_ca"], 200)
        self.assertGreater(draft["weighted_average"], 16.0)

    def test_an_existing_strong_player_is_rebuilt_to_a_lower_target(self) -> None:
        strong = {
            attribute_id: 16 for attribute_id in OUTFIELD_WRITABLE
        }
        ratings = positions(12)
        self.assertGreater(
            fm24_recommended_ca(block(strong), ratings)["recommended_ca"], 120,
        )
        draft = build(block(strong), ratings, 60, writable=OUTFIELD_WRITABLE)
        self.assertEqual(draft["formula_ca"], 60)
        self.assertTrue(draft["hit"])


class AttributeDraftByteTests(unittest.TestCase):
    def test_fm27_bias_round_trips_through_the_written_raw_value(self) -> None:
        raw = block(bias=2)
        ratings = positions(12)
        draft = build(raw, ratings, 150, writable=OUTFIELD_WRITABLE, bias=2)
        updated = draft_block(raw, draft, bias=2)
        for attribute_id in OUTFIELD_WRITABLE:
            value = updated[attribute_id - 0x0F]
            self.assertLessEqual(value, 100)
            self.assertEqual((value + 2) // 5, draft["attributes"][OUTFIELD_WRITABLE[attribute_id]]["after"])
        self.assertEqual(
            fm24_recommended_ca(updated, ratings, 2)["recommended_ca"], 150,
        )

    def test_the_weaker_foot_bytes_are_left_alone(self) -> None:
        raw = block({0x27: 20, 0x28: 1})
        draft = build(raw, positions(12), 140, writable=OUTFIELD_WRITABLE)
        updated = draft_block(raw, draft)
        self.assertEqual(updated[0x27 - 0x0F], raw[0x27 - 0x0F])
        self.assertEqual(updated[0x28 - 0x0F], raw[0x28 - 0x0F])
        self.assertEqual(draft["feet"], {
            "left": 20, "right": 1, "used": 1, "trusted": True,
        })

    def test_an_unreadable_foot_byte_degrades_instead_of_lying(self) -> None:
        raw = bytearray(block())
        raw[0x27 - 0x0F] = 200
        draft = build(bytes(raw), positions(12), 140, writable=OUTFIELD_WRITABLE)
        self.assertFalse(draft["hit"])
        self.assertFalse(draft["feet"]["trusted"])
        self.assertIn("formula_unreadable", [row["code"] for row in draft["warnings"]])


class AttributeDraftPositionTests(unittest.TestCase):
    def test_tied_positions_blend_the_shape_but_keep_one_formula_key(self) -> None:
        ratings = positions(DC=20, DR=20)
        draft = build(block(), ratings, 120, writable=OUTFIELD_WRITABLE)
        self.assertTrue(draft["mixed"])
        self.assertEqual(draft["position_key"], 1)
        self.assertEqual(len(draft["positions_used"]), 2)
        self.assertEqual(
            sorted(row["slot"] for row in draft["positions_used"]), ["DC", "DR"],
        )
        self.assertEqual(draft["formula_ca"], 120)

    def test_a_higher_blended_position_still_wins_the_formula_key(self) -> None:
        ratings = positions(12, 20, **{"AMC": 12, "AMR": 12})
        draft = build(block(), ratings, 122, writable=OUTFIELD_WRITABLE)
        self.assertFalse(draft["mixed"])
        self.assertEqual(draft["position_key"], 9)
        self.assertEqual([row["slot"] for row in draft["positions_used"]], ["ST"])
        self.assertEqual(draft["formula_ca"], 122)

    def test_a_sweeper_only_player_reports_the_formula_gap(self) -> None:
        ratings = [0] * 15
        ratings[1] = 20
        draft = build(block(), tuple(ratings), 60, writable=OUTFIELD_WRITABLE)
        self.assertEqual(draft["position_key"], 0)
        self.assertIn("sweeper_fallback", [row["code"] for row in draft["warnings"]])


class AttributeDraftStyleTests(unittest.TestCase):
    def test_a_style_reshapes_without_changing_the_level(self) -> None:
        ratings = positions(7)
        technical = build(block(), ratings, 140, writable=OUTFIELD_WRITABLE, style="technical")
        defensive = build(block(), ratings, 140, writable=OUTFIELD_WRITABLE, style="defensive")
        self.assertEqual(technical["formula_ca"], defensive["formula_ca"])
        self.assertGreater(
            technical["attributes"]["技术:技术"]["after"],
            defensive["attributes"]["技术:技术"]["after"],
        )
        self.assertGreater(
            defensive["attributes"]["技术:抢断"]["after"],
            technical["attributes"]["技术:抢断"]["after"],
        )

    def test_every_style_settles_on_the_requested_ca(self) -> None:
        ratings = positions(12)
        for style in ATTRIBUTE_STYLES:
            with self.subTest(style=style):
                draft = build(block(), ratings, 130, writable=OUTFIELD_WRITABLE, style=style)
                self.assertEqual(draft["formula_ca"], 130)

    def test_the_finishing_style_is_hidden_from_goalkeepers(self) -> None:
        self.assertNotIn("finishing", style_keys_for_group(True))
        self.assertIn("finishing", style_keys_for_group(False))
        catalog = {row["key"]: row["goalkeeper"] for row in attribute_style_catalog()}
        self.assertFalse(catalog["finishing"])
        self.assertTrue(catalog["balanced"])

    def test_the_free_attributes_carry_the_style(self) -> None:
        # 才华/侵略性/体质/意志力 never enter the CA formula, so the style has
        # to be visible there or it would be cancelled by the calibration.
        ratings = positions(7)
        defensive = build(block(), ratings, 130, writable=OUTFIELD_WRITABLE, style="defensive")
        genius = build(block(), ratings, 130, writable=OUTFIELD_WRITABLE, style="genius")
        self.assertGreater(
            defensive["attributes"]["精神:侵略性"]["after"],
            genius["attributes"]["精神:侵略性"]["after"],
        )
        self.assertGreater(
            genius["attributes"]["精神:才华"]["after"],
            defensive["attributes"]["精神:才华"]["after"],
        )


class AttributeDraftContractTests(unittest.TestCase):
    def test_the_draft_is_deterministic(self) -> None:
        ratings = positions(12)
        first = build(block(), ratings, 157, writable=OUTFIELD_WRITABLE)
        second = build(block(), ratings, 157, writable=OUTFIELD_WRITABLE)
        self.assertEqual(first, second)

    def test_a_step_budget_of_zero_reports_the_nearest_settlement(self) -> None:
        draft = build(
            block(), positions(12), 199, writable=OUTFIELD_WRITABLE, max_steps=0,
        )
        if not draft["hit"]:
            self.assertIn("unreachable", [row["code"] for row in draft["warnings"]])
            self.assertIsInstance(draft["formula_ca"], int)

    def test_bad_arguments_are_rejected(self) -> None:
        ratings = positions(12)
        with self.assertRaises(ValueError):
            build(block()[:53], ratings, 100, writable=OUTFIELD_WRITABLE)
        with self.assertRaises(ValueError):
            build(block(), ratings, 0, writable=OUTFIELD_WRITABLE)
        with self.assertRaises(ValueError):
            build(block(), ratings, 201, writable=OUTFIELD_WRITABLE)
        with self.assertRaises(ValueError):
            build(block(), ratings, 100, writable=OUTFIELD_WRITABLE, style="nope")
        with self.assertRaises(ValueError):
            build(block(), ratings, 100, writable={}, style="balanced")
        with self.assertRaises(ValueError):
            build(block(), ratings, 100, writable=OUTFIELD_WRITABLE, model="fm30")

    def test_the_module_never_touches_the_game_process(self) -> None:
        source = (ROOT / "tools" / "player_attribute_builder.py").read_text(encoding="utf-8")
        for token in ("open_process(", "write_process_memory(", "ReadProcessMemory"):
            self.assertNotIn(token, source)
        # The reader layer owns the FM offsets; the solver must stay importable
        # without a live save.
        self.assertNotIn("import club_reader", source)
        self.assertNotIn("from tools.club_reader", source)


if __name__ == "__main__":
    unittest.main()
