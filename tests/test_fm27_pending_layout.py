from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tools import initial_data_audit
from tools.domain_errors import Fm27LayoutPendingError
from tools.game_layout import (
    FM26_LAYOUT,
    FM27_EXE_SHA256,
    FM27_LAYOUT,
    FM27_PENDING_NOTICE,
    FM27_STEAM_PLUGIN_SHA256,
    LAYOUT_BY_HASH,
    SUPPORTED_LAYOUTS,
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


class FM27PendingAttachRefusalTests(unittest.TestCase):
    """A pending row must refuse as pending, never as a process mismatch.

    ``connect_current_save`` takes the ``include_internal`` discovery fast
    path, which hands ``select_game_process`` the row's ``layout`` directly.
    A pending FM27 row carries ``layout=None``, and that used to trip the
    identity guard and surface "does not match the requested target".
    """

    def _pending_selection(self) -> tuple:
        with (
            patch.object(
                initial_data_audit, "running_supported_processes",
                return_value=[_pending_row()],
            ),
            patch.object(initial_data_audit, "_PREFERRED_LAYOUT_KEY", ""),
            patch.object(initial_data_audit, "_PREFERRED_PROCESS_PID", None),
        ):
            status = initial_data_audit.process_selection_status(include_internal=True)
        return status["_selected_process"]

    def test_connect_fast_path_refuses_pending_fm27_as_pending(self):
        selected = self._pending_selection()
        self.assertEqual(selected[0], 42)
        self.assertIsNone(selected[2])
        with pytest.raises(Fm27LayoutPendingError) as caught:
            initial_data_audit.select_game_process("fm27", 42, selected=selected)
        self.assertEqual(
            caught.value.message_key, "error.game_layout.fm27_pending"
        )
        self.assertNotIn("does not match", str(caught.value))

    def test_pending_refusal_keeps_runtime_error_compatibility(self):
        self.assertIsInstance(
            Fm27LayoutPendingError(FM27_PENDING_NOTICE), RuntimeError
        )

    def test_genuine_pid_mismatch_still_reports_a_mismatch(self):
        with pytest.raises(ValueError, match="does not match the requested target"):
            initial_data_audit.select_game_process(
                "fm27", 42, selected=(7, r"C:\Games\FM27\fm.exe", FM26_LAYOUT),
            )

    def test_genuine_layout_key_mismatch_still_reports_a_mismatch(self):
        with pytest.raises(ValueError, match="does not match the requested target"):
            initial_data_audit.select_game_process(
                "fm27", 42, selected=(42, r"C:\Games\FM27\fm.exe", FM26_LAYOUT),
            )

    def test_select_process_refusal_carries_the_same_semantic_key(self):
        with (
            patch.object(
                initial_data_audit, "running_supported_processes",
                return_value=[_pending_row()],
            ),
            patch.object(initial_data_audit, "_PREFERRED_LAYOUT_KEY", "fm27"),
            patch.object(initial_data_audit, "_PREFERRED_PROCESS_PID", None),
        ):
            with pytest.raises(Fm27LayoutPendingError) as caught:
                initial_data_audit.select_process()
        self.assertEqual(
            caught.value.message_key, "error.game_layout.fm27_pending"
        )


class FM27PendingLayoutTests(unittest.TestCase):
    def test_fm27_is_registered_for_live_reads(self):
        """FM27 is a supported layout now that its offsets are verified.

        The pending refusal path above stays as the fail-closed route for any
        future build whose executable hash is known but whose layout is not.
        """
        self.assertIn(FM27_EXE_SHA256, LAYOUT_BY_HASH)
        self.assertIs(LAYOUT_BY_HASH[FM27_EXE_SHA256], FM27_LAYOUT)
        self.assertTrue(any(layout.key == "fm27" for layout in SUPPORTED_LAYOUTS))

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
            layout.actual_player_vtable_rvas, (0x4D27228, 0x4D27708, 0x4D27758)
        )
        self.assertEqual(
            layout.player_and_non_player_vtable_rvas,
            (0x4F75A08, 0x4F75F38),
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
        self.assertEqual(layout.player_person_offset, 0x288)
        self.assertEqual(layout.player_and_non_player_person_offset, 0x380)
        self.assertEqual(layout.player_ca_offset, 0x264)
        self.assertEqual(layout.player_pa_offset, 0x266)
        self.assertEqual(layout.player_ca_bytes, 2)

    def test_fm27_display_bias_is_round_to_nearest_not_floor(self):
        """FM27 attribute bytes are not multiples of 5, unlike FM24/FM26.

        Measured over 800 live players: mean|CA error| is 10.900 at bias 0,
        3.780 at bias 2 and 3.100 at bias 3 (signed +0.45, i.e. unbiased).
        """
        self.assertEqual(FM27_LAYOUT.attribute_display_bias, 3)

    def test_human_manager_fields_are_resolved(self):
        """Both manager fields, resolved by the vtable + person-UID constraint.

        The primary vtable at the manager object's +0 must be 0x4CC086C, and
        +0x440 must be the person sub-object: it carries sub-vtable 0x4CC08D8
        and a genuine person UID at its +0x0C. Neither value may be set without
        the other - the consumer pairs them to read the manager UID.
        """
        self.assertEqual(FM27_LAYOUT.human_manager_vtable_rvas, (0x4CC086C,))
        self.assertEqual(FM27_LAYOUT.manager_person_offset, 0x440)
        self.assertFalse(FM27_LAYOUT.human_manager_vtable_on_person)

    def test_human_manager_team_offset_resolves_the_managed_team(self):
        """FM27 has no manager contract, so the team comes off the manager object.

        Verified on the live 27.0 session with two independent pointers agreeing
        on one club: manager+0x60 -> team 406 (北京国安, club 0x27372CF9A00) and
        manager+0x548 -> the same club object, which resolves to vtable
        0x4CDA1F8 (== club_vtable_rva) with uid 406 and the club name. The
        contract route yields nothing in FM27 because person+0xA8/+0xB0/+0xC8
        are all zero, so this offset is the only path.
        """
        self.assertEqual(FM27_LAYOUT.human_manager_team_offset, 0x60)
        # FM24/FM26 keep the contract route; the new field must stay inert there.
        self.assertIsNone(FM26_LAYOUT.human_manager_team_offset)

    def test_fixture_pool_is_resolved(self):
        """0x56F6AB0 was accepted by the product's own pool validator.

        Scanned from the FIXTURE_DEL_ARRAY signature; the run read 7021
        fixtures and 24321 results, and the FM26-relative guess was rejected.
        """
        self.assertEqual(FM27_LAYOUT.fixture_pool_rva, 0x56F6AB0)

    def test_staff_ability_block_is_resolved(self):
        """person - 0x100 holds the 18 coaching bytes on a raw 0..100 scale.

        Measured over 37 live staff rows: 666/666 non-zero, spanning 5..98,
        which the consumer converts to a full 1..20 display range. The offset
        is person-relative and the staff subobject also precedes the person, so
        it needs no shift.
        """
        self.assertEqual(FM27_LAYOUT.staff_ability_complete_object_offset, 0x100)
        self.assertTrue(FM27_LAYOUT.staff_abilities_verified)

    def test_fm27_save_identity_fails_closed_instead_of_using_fm26_roots(self):
        """An empty savegame_root_rvas must not fall back to FM26's RVAs.

        ``read_savegame_identity`` used ``or SAVEGAME_ROOT_RVAS``, so an empty
        tuple silently read FM26's 0x04E35F60/0x04E44950 inside FM27 and
        published arbitrary bytes as a save id, which corrupts the save scope
        keys built from it. It must fail closed instead.
        """
        from tools.save_memory import read_savegame_identity

        reader = SimpleNamespace(
            layout=FM27_LAYOUT, ptr=MagicMock(), u32=MagicMock(),
        )
        self.assertIsNone(read_savegame_identity(reader))
        reader.ptr.assert_not_called()
        reader.u32.assert_not_called()

    def test_human_manager_discovery_gate_is_capability_based(self):
        """The direct manager scan must key on the verified vtable, not the version.

        It used ``layout.key not in {"fm24", "fm26"}``, which excluded FM27 even
        though its human-manager root AOB and vtable (0x4CC086C) are verified.
        Without the manager, the connection handshake has no identity evidence
        and fails with "无法确认当前存档身份" on a cold start.
        """
        import inspect

        from tools import preview_cup_odds

        source = inspect.getsource(
            preview_cup_odds._discover_human_managers_direct,
        )
        self.assertNotIn('not in {"fm24", "fm26"}', source)
        self.assertIn("human_manager_vtable_rvas", source)
        # A layout with no verified manager vtable must still be refused.
        self.assertEqual(FM27_LAYOUT.human_manager_vtable_rvas, (0x4CC086C,))

    def test_player_fields_use_fm26_offsets_on_a_person_prefixed_row(self):
        """Round 105: the row is FM26's, only the table pointer moved.

        The FM27 table hands out PERSON pointers, and the person sits at
        +0x288 inside a 0x3C8 row, so the player subobject is at person-0x288
        and every player-relative field keeps its FM26 offset. The round-25
        "FM27 hoists the person block to the front" rule added 0x140 to each
        player offset; that pointed one full row ahead (person+0x140 is the
        next row's player block), which is what made 0x290/0x29F/0x36E look
        plausible, self-consistent and session-unstable all at once.

        Verified live in one session over 17268 ACTUAL_PLAYER rows:
          * module pointer at person-0x288: 17268/17268, always vtable 0x4D27228
          * legal 1..20 x 15 position matrix at person-0x138: 17268/17268
          * CA/PA at person-0x24/-0x22 in 1..200 with CA<=PA: 17268/17268
          * 54 attribute bytes at person-0x129 all in 1..100, zero zeros
          * 32 known real players: positions 28/32, height 27/32 (the old
            0x290/0x36E scored 6/32 and 9/32 on the same players)
          * FMRTE RCA over 121 rows: CA at person-0x24 mean|err| 2.49 vs 26.46
            for the old person+0x3A4
        """
        layout = FM27_LAYOUT
        self.assertEqual(layout.player_person_offset, 0x288)
        self.assertEqual(layout.player_positions_offset, 0x150)
        self.assertEqual(layout.player_attributes_offset, 0x15F)
        self.assertEqual(layout.player_height_offset, 0x22E)
        self.assertEqual(layout.player_ca_offset, 0x264)
        self.assertEqual(layout.player_pa_offset, 0x266)
        self.assertEqual(layout.player_season_stats_root_offset, 0x108)
        self.assertEqual(layout.person_flags_offset, 0x18)
        self.assertEqual(layout.person_hidden_attributes_offset, 0x70)
        # Never measured, so deliberately absent rather than guessed.
        self.assertIsNone(layout.player_season_stats_total_slot_offset)
        self.assertIsNone(layout.player_season_stats_block_size)

    def test_condition_fields_that_are_read_with_bare_arithmetic_are_set(self):
        """_player reads these with `address + offset` when roster_summary is None.

        Leaving any of them None raised
        "unsupported operand type(s) for +: 'int' and 'NoneType'" on
        /api/world-players/detail. They are FM26's player-relative values, which
        the round-105 base fix restores; the old 0x3A2/0x398/0x39C/0x3AC/0x238
        were the same fields of the row at person+0x3C8.
        """
        layout = FM27_LAYOUT
        self.assertEqual(layout.player_world_reputation_offset, 0x262)
        self.assertEqual(layout.player_sharpness_offset, 0x258)
        self.assertEqual(layout.player_fitness_offset, 0x25C)
        self.assertEqual(layout.player_morale_offset, 0x26C)
        self.assertEqual(layout.player_injury_list_offset, 0xF8)

    def test_player_fatigue_stays_unset(self):
        """FM27's 0x25A does not hold a condition value.

        Sampling 300 live players: only 192/300 fell inside 0..10000 (median 90,
        max 65530), while sharpness 0x258 and fitness 0x25C were 300/300. The
        original measurement was taken at person+0x39A, which round 105 showed
        is this same 0x25A field one row along. The offset is also WRITTEN by
        adjust_player_training_condition, so a guess would corrupt unrelated
        bytes. It must stay None.
        """
        self.assertIsNone(FM27_LAYOUT.player_fatigue_offset)

    def test_savegame_root_stays_empty_instead_of_guessing(self):
        """FM27 has no rankable savegame root; see the layout note.

        Two module-data globals point at save-class objects and both are
        rejected, and SAVEGAME_ID_OFFSET/SESSION_NONCE_OFFSET are module
        constants rather than layout fields, so a moved pair cannot be expressed
        here at all. Registration no longer depends on it.
        """
        self.assertEqual(FM27_LAYOUT.savegame_root_rvas, ())

    def test_pending_notice_explains_the_attach_refusal(self):
        self.assertIn("Football Manager 27", FM27_PENDING_NOTICE)
        self.assertIn("not verified", FM27_PENDING_NOTICE)


if __name__ == "__main__":
    unittest.main()
