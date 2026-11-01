"""Edit mode and the build that has no U21 international counters.

Edit mode is the only switch that unlocks direct player writes. Two things
matter and both are asserted here: the setting is off until someone turns it
on, and the write endpoints refuse on their own instead of trusting the
frontend to hide the button.

The second half covers the FM27 counter quartet. Rounds 112/119 left the
senior pair unset because the reader demanded all four offsets, while FM27
keeps no U21 pair at all. The layout now declares that absence, so the senior
counters publish with the U21 values as None rather than the whole block being
withheld over two offsets that cannot exist.
"""
from __future__ import annotations

import dataclasses
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fm_odds_web
from tools import app_settings
from tools.game_layout import FM24_LAYOUT, FM26_LAYOUT, FM27_LAYOUT
from tools.player_details_fm26 import read_fm26_international_stats


def _reader(layout, *, caps=0, goals=0, u21_caps=0, u21_goals=0):
    buffer = bytearray(0x400)
    struct.pack_into("<H", buffer, 0x3BC, caps)
    struct.pack_into("<H", buffer, 0x3BE, goals)
    buffer[0x3C0] = u21_caps
    buffer[0x3C1] = u21_goals
    return SimpleNamespace(
        layout=layout,
        bytes=lambda address, size: bytes(buffer[address:address + size]),
    )


class EditModeSettingTests(unittest.TestCase):
    def test_edit_mode_is_off_until_it_is_turned_on(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with patch.object(app_settings, "SETTINGS_PATH", path):
                self.assertFalse(app_settings.load_settings()["edit_mode"])
                self.assertFalse(app_settings.edit_mode_enabled())

                saved = app_settings.set_edit_mode(True)

                self.assertTrue(saved["edit_mode"])
                self.assertTrue(app_settings.load_settings()["edit_mode"])
                self.assertTrue(app_settings.edit_mode_enabled())

                app_settings.set_edit_mode(False)
                self.assertFalse(app_settings.edit_mode_enabled())

    def test_edit_mode_does_not_disturb_the_other_cheat_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with patch.object(app_settings, "SETTINGS_PATH", path):
                app_settings.set_disable_fa_penalties(True)
                app_settings.set_edit_mode(True)
                settings = app_settings.load_settings()
                self.assertTrue(settings["disable_fa_penalties"])
                self.assertTrue(settings["edit_mode"])
                self.assertFalse(settings["god_mode"])


class PlayerWriteGateTests(unittest.TestCase):
    def _state(self):
        state = fm_odds_web.LocalOddsState.__new__(fm_odds_web.LocalOddsState)
        state.lock = threading.RLock()
        state.memory_lock = fm_odds_web.ForegroundPriorityLock()
        state.last_operation_performance = {}
        state._bind_current_save = lambda: "scope-a"
        return state

    def test_writes_are_refused_while_edit_mode_is_off(self) -> None:
        state = self._state()
        called = []
        with patch("fm_odds_web.edit_mode_enabled", return_value=False):
            with self.assertRaises(ValueError) as raised:
                state._world_player_action_result(
                    7, "world_player_test", lambda: called.append(True),
                )
        self.assertIn("修改模式", str(raised.exception))
        self.assertEqual(called, [])

    def test_writes_run_when_edit_mode_is_on(self) -> None:
        state = self._state()
        with (
            patch("fm_odds_web.edit_mode_enabled", return_value=True),
            patch("fm_odds_web.set_active_save_id"),
            patch(
                "fm_odds_web.read_world_player_profile",
                side_effect=lambda player_id: {"id": player_id, "name": "测试球员"},
            ),
            patch("fm_odds_web.apply_player_aliases"),
        ):
            result = state._world_player_action_result(
                7, "world_player_test", lambda: {"changed": True},
            )
        self.assertEqual(result["result"], {"changed": True})

    def test_every_write_family_checks_the_gate(self) -> None:
        # The gate has to cover the scalar/attribute writer and the two
        # dedicated families as well, or enabling the switch would only half
        # protect the game's memory.
        source = Path(__file__).resolve().parents[1].joinpath(
            "src", "fm_odds_web.py",
        ).read_text(encoding="utf-8")
        for method in (
            "def _world_player_action_result(",
            "def edit_world_player(",
            "def edit_world_player_names(",
            "def edit_world_player_contract(",
        ):
            with self.subTest(method=method):
                body = source.split(method, 1)[1][:400]
                self.assertIn("self._require_edit_mode()", body)


class InternationalCounterTests(unittest.TestCase):
    def test_fm27_publishes_the_senior_pair_and_reports_u21_as_unknown(self) -> None:
        stats = read_fm26_international_stats(
            _reader(FM27_LAYOUT, caps=169, goals=12), 0,
        )
        self.assertEqual(stats["international_appearances"], 169)
        self.assertEqual(stats["international_goals"], 12)
        self.assertIsNone(stats["u21_international_appearances"])
        self.assertIsNone(stats["u21_international_goals"])
        self.assertTrue(stats["u21_counters_absent"])
        self.assertEqual(stats["source"], "fm26_native_international_counters")

    def test_fm26_still_reads_all_four_counters(self) -> None:
        stats = read_fm26_international_stats(
            _reader(FM26_LAYOUT, caps=50, goals=5, u21_caps=7, u21_goals=2), 0,
        )
        self.assertEqual(stats["international_appearances"], 50)
        self.assertEqual(stats["u21_international_appearances"], 7)
        self.assertEqual(stats["u21_international_goals"], 2)
        self.assertNotIn("u21_counters_absent", stats)

    def test_a_build_that_keeps_u21_counters_still_needs_them(self) -> None:
        partial = dataclasses.replace(
            FM26_LAYOUT,
            player_u21_international_appearances_offset=None,
            player_u21_international_goals_offset=None,
        )
        self.assertIsNone(
            read_fm26_international_stats(_reader(partial, caps=50, goals=5), 0),
        )

    def test_a_contradictory_layout_publishes_nothing(self) -> None:
        # Declaring the pair absent while also carrying offsets for it means the
        # layout disagrees with itself; the reader must not pick a side.
        contradictory = dataclasses.replace(
            FM27_LAYOUT,
            player_u21_international_appearances_offset=0x3C0,
            player_u21_international_goals_offset=0x3C1,
        )
        self.assertIsNone(
            read_fm26_international_stats(_reader(contradictory, caps=1), 0),
        )

    def test_an_unreadable_senior_counter_still_withholds_the_block(self) -> None:
        unreadable = dataclasses.replace(
            FM27_LAYOUT, player_international_goals_offset=None,
        )
        self.assertIsNone(
            read_fm26_international_stats(_reader(unreadable, caps=1), 0),
        )

    def test_only_fm27_declares_the_u21_pair_absent(self) -> None:
        self.assertTrue(FM27_LAYOUT.player_u21_international_counters_absent)
        self.assertFalse(FM24_LAYOUT.player_u21_international_counters_absent)
        self.assertFalse(FM26_LAYOUT.player_u21_international_counters_absent)


class EditModeFrontendContractTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1] / "src" / "web"
        self.script = (root / "app.js").read_text(encoding="utf-8")
        self.html = (root / "index.html").read_text(encoding="utf-8")

    def test_settings_offer_the_switch_with_a_warning(self) -> None:
        self.assertIn('id="edit-mode"', self.html)
        self.assertIn('data-i18n="settings.edit_mode"', self.html)
        self.assertIn('data-i18n="settings.edit_mode_detail"', self.html)

    def test_the_switch_posts_to_the_settings_endpoint(self) -> None:
        self.assertIn("/api/settings/edit-mode", self.script)
        self.assertIn('$("#edit-mode")?.addEventListener("change"', self.script)

    def test_the_switch_is_on_the_default_tab_and_reachable_from_a_profile(self) -> None:
        groups = self.script.split("const SETTINGS_TAB_GROUPS", 1)[1].split(
            "function activateSettingsTab", 1,
        )[0]
        general = groups.split('key:"general"', 1)[1].split('key:"markets"', 1)[0]
        self.assertIn('".edit-mode-setting"', general)
        # "general" is the tab the dialog opens on by default, so the switch is
        # visible without hunting. A toast alone is still not enough - the dialog
        # restores the last used tab, so the profile button opens the right one.
        self.assertIn("function openSettingsWithTab(tabKey)", self.script)
        self.assertIn('openSettingsWithTab("general");', self.script)

    def test_the_profile_button_only_appears_while_the_mode_is_on(self) -> None:
        self.assertIn("function editModeEnabled()", self.script)
        self.assertIn("kind === \"player\" && editModeEnabled()", self.script)
        self.assertIn("data-open-player-editor", self.script)
        # Opening the dialog re-checks the flag, so a stale page cannot reach
        # the editor after the mode was switched off elsewhere.
        open_body = self.script.split("function openWorldPlayerEditor", 1)[1][:400]
        self.assertIn("settings.edit_mode_required", open_body)


if __name__ == "__main__":
    unittest.main()
