from __future__ import annotations

import unittest
from unittest.mock import patch

import pytest

from tools import initial_data_audit
from tools.game_layout import (
    FM27_EXE_SHA256,
    FM27_LAYOUT,
    FM27_PENDING_NOTICE,
    FM27_STEAM_PLUGIN_SHA256,
    LAYOUT_BY_HASH,
)


def _pending_row() -> dict:
    return {
        "pid": 42, "path": r"C:\Games\FM27\fm.exe", "thread_count": 5,
        "layout": None, "key": "fm27", "display_name": FM27_LAYOUT.display_name,
        "version": "27", "distribution": "steam",
        "file_version": "6000.3.18", "build_version": "27.0",
        "build_label": "27.0", "unity_version": "6000.3.18",
        "unity_label": "6000.3.18", "pending": True,
    }


class FM27VersionSwitchTests(unittest.TestCase):
    def test_selecting_pending_fm27_records_preference_without_pinning(self):
        with (
            patch.object(
                initial_data_audit, "running_supported_processes",
                return_value=[_pending_row()],
            ),
            patch.object(initial_data_audit, "select_game_process") as pin,
            patch.object(initial_data_audit, "_PREFERRED_LAYOUT_KEY", ""),
            patch.object(initial_data_audit, "_PREFERRED_PROCESS_PID", None),
            patch.object(initial_data_audit, "_PROCESS_LAYOUT_CACHE", None),
        ):
            status = initial_data_audit.select_game_layout("fm27")
            pin.assert_not_called()
            self.assertEqual(status["selected"], "fm27")
            self.assertEqual(
                initial_data_audit._PREFERRED_LAYOUT_KEY, "fm27"
            )
            self.assertIsNone(initial_data_audit._PREFERRED_PROCESS_PID)

    def test_select_process_refuses_pending_fm27_with_notice(self):
        with (
            patch.object(
                initial_data_audit, "running_supported_processes",
                return_value=[_pending_row()],
            ),
            patch.object(initial_data_audit, "_PREFERRED_LAYOUT_KEY", "fm27"),
            patch.object(initial_data_audit, "_PREFERRED_PROCESS_PID", None),
        ):
            with pytest.raises(RuntimeError, match="layout is not verified"):
                initial_data_audit.select_process()

    def test_process_selection_status_reports_pending_flag(self):
        with patch.object(
            initial_data_audit, "running_supported_processes",
            return_value=[_pending_row()],
        ):
            status = initial_data_audit.process_selection_status()
        self.assertEqual(status["selected"], "fm27")
        entry = next(
            item for item in status["available"] if item["key"] == "fm27"
        )
        self.assertTrue(entry["pending"])


class FM27PendingLayoutTests(unittest.TestCase):
    def test_fm27_is_not_registered_for_live_reads(self):
        self.assertNotIn(FM27_EXE_SHA256, LAYOUT_BY_HASH)
        for layout in LAYOUT_BY_HASH.values():
            self.assertNotEqual(layout.key, "fm27")

    def test_identity_hashes_are_recorded_exactly(self):
        self.assertEqual(
            FM27_EXE_SHA256,
            "2A7318C6984BCB3A19FF3304432B63462581CEE33BA203F9A0A118F11C69E27E",
        )
        self.assertEqual(
            FM27_STEAM_PLUGIN_SHA256,
            "BC940E472418FC5C214208D7CF30FECF600F689B6F9D71C11BA19A2DF0FE057F",
        )

    def test_statically_resolved_probe_addresses_are_recorded_exactly(self):
        layout = FM27_LAYOUT
        self.assertEqual(layout.fixture_vtable_rva, 0x4CB69A8)
        self.assertEqual(layout.fixture_result_vtable_rva, 0x4AFDE68)
        self.assertEqual(layout.season_result_vtable_rva, 0x4AFC318)
        self.assertEqual(layout.team_vtable_rva, 0x4CE1108)
        self.assertEqual(layout.national_team_vtable_rva, 0x4DC2288)
        self.assertEqual(layout.nation_vtable_rva, 0x4CEC0B8)
        self.assertEqual(layout.club_vtable_rva, 0x4CDA1F8)
        self.assertEqual(layout.competition_vtable_rva, 0x4CDFAD8)
        self.assertEqual(
            layout.actual_player_vtable_rvas, (0x4D27708, 0x4D27758)
        )
        self.assertEqual(
            layout.player_and_non_player_vtable_rvas,
            (0x4F75F38,),
        )
        self.assertEqual(layout.staff_person_vtable_rva, 0x4D8BE78)
        self.assertEqual(layout.game_date_rva, 0x56AD378)
        self.assertEqual(layout.database_root_probe_rva, 0x2E8749A)
        self.assertEqual(layout.database_root_rel32_offset, 7)
        self.assertEqual(layout.database_root_instruction_size, 11)
        self.assertEqual(layout.human_manager_root_probe_rva, 0xEB7253)
        self.assertEqual(layout.human_manager_root_rel32_offset, 3)
        self.assertEqual(layout.human_manager_root_instruction_size, 7)

    def test_verified_person_and_ability_fields_are_recorded(self):
        layout = FM27_LAYOUT
        self.assertEqual(layout.person_full_name_offset, 0x40)
        self.assertEqual(layout.person_first_name_offset, 0x50)
        self.assertEqual(layout.person_last_name_offset, 0x58)
        self.assertEqual(layout.person_nationality_offset, 0x68)
        self.assertEqual(layout.person_date_of_birth_offset, 0x88)
        self.assertFalse(layout.person_date_of_birth_day_year)
        self.assertEqual(layout.player_person_offset, 0)
        self.assertEqual(layout.player_and_non_player_person_offset, 0)
        self.assertEqual(layout.player_ca_offset, 0x3A4)
        self.assertEqual(layout.player_pa_offset, 0x3A6)
        self.assertEqual(layout.player_ca_bytes, 2)

    def test_unverified_structural_offsets_stay_disabled(self):
        layout = FM27_LAYOUT
        for offset in (
            layout.player_positions_offset,
            layout.player_attributes_offset,
            layout.manager_person_offset,
        ):
            self.assertEqual(offset, 0)
        self.assertIsNone(layout.person_flags_offset)
        self.assertIsNone(layout.person_hidden_attributes_offset)
        self.assertIsNone(layout.player_season_stats_root_offset)
        self.assertEqual(layout.human_manager_vtable_rvas, ())
        self.assertFalse(layout.staff_abilities_verified)
        self.assertIsNone(layout.fixture_pool_rva)
        self.assertEqual(layout.savegame_root_rvas, ())

    def test_pending_notice_explains_the_attach_refusal(self):
        self.assertIn("Football Manager 27", FM27_PENDING_NOTICE)
        self.assertIn("not verified", FM27_PENDING_NOTICE)


if __name__ == "__main__":
    unittest.main()
