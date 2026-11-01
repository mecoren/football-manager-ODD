from __future__ import annotations

from dataclasses import dataclass, replace
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
import mmap
from pathlib import Path
import struct
from typing import Any


FM26_EXE_SHA256 = "3653C97F9CCEC2BE28EDC4FAAE67304B5B6C26733F2F07DEA3E7C591D3B9FF73"
# The Steam EXE hash is shared across minor FM26 updates. Keep the loaded
# plugin identity separate so an older game_plugin.dll cannot be paired with
# the 26.3.2 layout (notably its different game-date RVA).
FM26_STEAM_PLUGIN_SHA256 = "EB6C86FAB56E051FE41482C7B93D8CB0AF716A1CC2C6197CE1BABBFF68372BB8"
FM26_GAME_DATE_PATTERN = (
    0x0F, 0xB7, 0x03, 0x25, 0xFF, 0x01, 0x00, 0x00,
    0x66, 0x83, 0xF8, 0x01, 0x75, 0x08, 0x8B, 0x05,
    None, None, None, None, 0x89, 0x03,
)
FM24_EXE_SHA256 = "E1059EEE82FA7832188831521A3FA633EC3260DD98D03DA48B16661147E3AB48"
FM24_240_EXE_SHA256 = "1473E5C3A778CB255A30C7A43D8346E27B031B47C42E93533097E049C110D58E"
FM24_241_EXE_SHA256 = "ED5D0F9C3D06194FA5D19E5A2C14EF8F9C6AB8A9FF7E77220594E8A600A75F5F"
FM24_EPIC_EXE_SHA256 = "33E8A2E48C4986F2A15E11C4C93ACA1199029D66B19BD765403EB22DEA265378"
FM24_XGP_PE_TIMESTAMP = 0x67FF9A0A
FM24_XGP_IMAGE_SIZE = 0x1CD9F000
FM26_XGP_PE_TIMESTAMP = 0x6A229DA9
FM26_XGP_IMAGE_SIZE = 0x1EDF4000
# Steam FM27 (Unity 6000.3.18f1-custom). The fm.exe hash identifies the
# running process; the plugin hash pins the game_plugin.dll build whose
# statically resolved RVAs are recorded in FM27_LAYOUT. The game-date AOB
# is byte-identical to FM26's and still matches exactly one location in
# that plugin build, but the constant stays separate so the two games can
# diverge in a future update without touching FM26 resolution.
FM27_EXE_SHA256 = "2A7318C6984BCB3A19FF3304432B63462581CEE33BA203F9A0A118F11C69E27E"
FM27_STEAM_PLUGIN_SHA256 = "BC940E472418FC5C214208D7CF30FECF600F689B6F9D71C11BA19A2DF0FE057F"
FM27_GAME_DATE_PATTERN = FM26_GAME_DATE_PATTERN
FM27_PENDING_NOTICE = (
    "Football Manager 27 detected, but its FM27 memory layout is not "
    "verified yet; FMODD refuses to attach without build-specific offsets"
)


@dataclass(frozen=True)
class GameLayout:
    key: str
    display_name: str
    executable_sha256: str
    module_name: str
    distribution: str
    fixture_vtable_rva: int
    team_vtable_rva: int
    national_team_vtable_rva: int
    nation_vtable_rva: int
    club_vtable_rva: int
    competition_vtable_rva: int
    actual_player_vtable_rvas: tuple[int, ...]
    player_and_non_player_vtable_rvas: tuple[int, ...]
    player_person_offset: int
    player_and_non_player_person_offset: int
    # Optional since round 88: FM27 leaves these unset until its
    # display-backed position/attribute/height fields are relocated.
    player_positions_offset: int | None
    player_attributes_offset: int | None
    player_ca_offset: int
    player_pa_offset: int
    player_ca_bytes: int
    attribute_display_bias: int
    manager_person_offset: int
    human_manager_vtable_rvas: tuple[int, ...]
    human_manager_vtable_on_person: bool = False
    # A national Team points at a generation-specific Nation container.  On
    # verified builds this field links that container to the canonical nation
    # object whose stable UID is used by nation_mappings.
    national_team_nation_offset: int | None = None
    nationality_vtable_rva: int | None = None
    # Generation-specific, read-only database directory probes.  The patterns
    # are resolved inside the selected module and the RIP-relative target is a
    # table of pointers to FM's native database containers.  AOBs remain
    # dynamic across distributions; no session address is persisted.
    database_root_pattern: tuple[int | None, ...] = ()
    database_root_probe_rva: int | None = None
    database_root_rel32_offset: int | None = None
    database_root_instruction_size: int | None = None
    human_manager_root_pattern: tuple[int | None, ...] = ()
    human_manager_root_probe_rva: int | None = None
    human_manager_root_rel32_offset: int | None = None
    human_manager_root_instruction_size: int | None = None
    savegame_root_rvas: tuple[int, ...] = ()
    savegame_id_absolute_address: int | None = None
    savegame_id_requires_legacy_validation: bool = False
    match_engine_phase_rva: int | None = None
    match_engine_mode_rva: int | None = None
    match_engine_active_state_rva: int | None = None
    play_fixture_manager_pointer_rva: int | None = None
    play_fixture_manager_vtable_rva: int | None = None
    match_playback_module_name: str | None = None
    match_playback_module_timestamp: int | None = None
    match_playback_module_image_size: int | None = None
    match_setup_type_info_rva: int | None = None
    redbull_hook_rva: int | None = None
    referee_hook_rva: int | None = None
    ca_growth_hook_rva: int | None = None
    club_policy_departure_pattern_rva: int | None = None
    club_policy_salary_pattern_rva: int | None = None
    club_policy_salary_promise_pattern_rva: int | None = None
    youth_generation_gate_hook_rva: int | None = None
    youth_generation_pa_hook_rva: int | None = None
    youth_generation_state_hook_rva: int | None = None
    youth_son_hook_rva: int | None = None
    result_home_goals_offset: int = 0x64
    result_away_goals_offset: int = 0x69
    result_home_outcome_offset: int = 0x6E
    result_away_outcome_offset: int = 0x6F
    result_event_root_offset: int = 0x78
    result_event_record_size: int = 0x10
    result_event_record_format: str = "fm26"
    fixture_stage_index_offset: int | None = None
    fixture_group_index_offset: int | None = None
    fixture_round_number_offset: int | None = None
    competition_actual_offset: int | None = None
    actual_competition_stages_offset: int | None = None
    competition_stage_type_offset: int | None = None
    competition_stage_index_offset: int | None = None
    cup_stage_teams_offset: int | None = None
    cup_stage_round_ties_offset: int | None = None
    fixture_result_vtable_rva: int | None = None
    fixture_pool_rva: int | None = None
    season_result_vtable_rva: int | None = None
    game_date_rva: int | None = None
    match_session_pointer_rva: int | None = None
    match_session_vtable_rva: int | None = None
    game_match_session_vtable_rva: int | None = None
    player_height_offset: int | None = None
    player_weight_offset: int | None = None
    player_sharpness_offset: int | None = None
    player_fatigue_offset: int | None = None
    player_fitness_offset: int | None = None
    player_morale_offset: int | None = None
    player_injury_list_offset: int | None = None
    player_home_reputation_offset: int | None = None
    player_current_reputation_offset: int | None = None
    player_world_reputation_offset: int | None = None
    # Build-gated current-season aggregate stored on db::PLAYER.  These
    # offsets are intentionally disabled unless the exact distribution/build
    # has been verified against the in-game statistics screen.
    player_season_stats_root_offset: int | None = None
    player_season_stats_total_slot_offset: int | None = None
    player_season_stats_block_size: int | None = None
    player_career_stats_offset: int | None = None
    # Native international appearance/goal counters.  These are separate
    # from club season/career containers and remain disabled unless the
    # exact build profile has a verified mapping.
    player_international_appearances_offset: int | None = None
    player_international_goals_offset: int | None = None
    player_u21_international_appearances_offset: int | None = None
    player_u21_international_goals_offset: int | None = None
    player_international_counter_width: int = 2
    player_u21_international_counter_width: int = 1
    # Some builds keep no U21 counter pair at all. Declaring that here lets the
    # reader publish the senior pair on its own and report the U21 values as
    # unknown, instead of withholding all four because two of them do not
    # exist. It is a structural claim about the object, so it is per-layout and
    # must be backed by an exhaustive scan of the counter window, not by a
    # missing value.
    player_u21_international_counters_absent: bool = False
    team_reputation_offset: int | None = None
    # Native db::TEAM squad classification (first team, U21, U18, etc.).
    # Keep disabled for builds where the field has not been verified.
    team_type_offset: int | None = None
    competition_reputation_offset: int | None = None
    competition_reputation_bytes: int | None = None
    person_nationality_offset: int | None = None
    person_date_of_birth_offset: int | None = None
    person_date_of_birth_day_year: bool = False
    person_hidden_attributes_offset: int | None = None
    person_relationships_offset: int | None = None
    person_languages_offset: int | None = None
    # Native Person.PreviousClub pointer. Keep build-gated because this is a
    # Person field and is unrelated to db::PLAYER's +0x108 season-stat root.
    person_previous_club_offset: int | None = None
    staff_language_write_verified: bool = False
    # FM26 PersonFlags contains the native Female bit at this verified field.
    # FM24 remains None until an equivalent build-specific mapping is verified.
    person_flags_offset: int | None = None
    person_flags_bytes: int = 8
    person_full_name_offset: int = 0x40
    person_first_name_offset: int = 0x50
    person_last_name_offset: int = 0x58
    person_common_name_offset: int = 0x60
    staff_person_vtable_rva: int | None = None
    staff_complete_object_offset: int = 0x100
    # The coaching-ability block may use a different complete-object base.
    # FM24 stores it at person-0xF0 while chairman/controller fields remain
    # based at person-0x100. FM26 uses person-0x100 for both.
    staff_ability_complete_object_offset: int | None = None
    # Builds where a human manager references the managed team directly on the
    # manager object, instead of reaching it through a player-style contract
    # (FM27 has no manager contract: person+0xA8/+0xB0/+0xC8 are all zero).
    # None keeps the FM24/FM26 contract route unchanged.
    human_manager_team_offset: int | None = None
    team_manager_person_offset: int = 0x100
    staff_domestic_reputation_offset: int | None = None
    staff_current_reputation_offset: int | None = None
    staff_world_reputation_offset: int | None = None
    staff_ca_offset: int | None = None
    staff_pa_offset: int | None = None
    staff_coaching_license_offset: int | None = None
    staff_job_type_offset: int | None = None
    staff_contract_type_offset: int | None = None
    staff_wage_offset: int | None = None
    staff_contract_start_offset: int | None = None
    staff_contract_expiry_offset: int | None = None
    staff_abilities_verified: bool = False
    loan_contract_vtable_rva: int | None = None
    player_move_contract_pool_rva: int | None = None
    player_move_loan_contract_pool_rva: int | None = None
    player_move_loan_contract_constructor_rva: int | None = None
    player_move_contract_allocate_rva: int | None = None
    player_move_contract_release_rva: int | None = None
    player_move_contract_factory_global_rva: int | None = None
    player_move_contract_factory_target_rva: int | None = None
    player_move_main_contract_vtable_rva: int | None = None
    player_move_terminate_contract_rva: int | None = None
    player_move_prepare_loan_rva: int | None = None
    player_move_club_method_rva: int | None = None
    player_move_fm24_source_cleanup_rva: int | None = None
    player_move_fm24_transfer_rva: int | None = None
    player_move_fm24_transaction_allocator_rva: int | None = None
    player_move_fm24_transaction_constructor_rva: int | None = None
    player_move_fm24_transaction_initializer_rva: int | None = None
    player_move_fm24_transaction_prepare_rva: int | None = None
    player_move_fm24_transaction_commit_rva: int | None = None
    player_move_fm24_transaction_transition_rva: int | None = None
    player_move_fm24_transaction_outer_submit_rva: int | None = None
    player_move_fm24_transaction_entry_rva: int | None = None
    player_move_fm24_transaction_vtable_rva: int | None = None
    player_move_fm24_context_root_rva: int | None = None
    player_move_fm24_context_vtable_rva: int | None = None
    player_move_fm24_contract_factory_rva: int | None = None
    player_move_fm24_loan_constructor_rva: int | None = None
    future_transfer_manager_vtable_rva: int | None = None
    future_transfer_full_offer_vtable_rva: int | None = None
    future_transfer_loan_offer_vtable_rva: int | None = None
    # Club detail fields recovered from FMRTE 24.4.2/26.3.2 metadata.
    # These are deliberately explicit per layout so unsupported distributions
    # can leave the profile disabled instead of inheriting an unverified map.
    club_detail_offset: int | None = None
    club_detail2_offset: int | None = None
    club_training_ground_offset: int | None = None
    club_year_founded_offset: int | None = None
    club_training_facilities_offset: int | None = None
    club_youth_facilities_offset: int | None = None
    club_junior_coaching_offset: int | None = None
    club_youth_recruitment_offset: int | None = None
    club_average_attendance_offset: int | None = None
    club_minimum_attendance_offset: int | None = None
    club_maximum_attendance_offset: int | None = None
    club_season_ticket_holders_offset: int | None = None
    club_social_media_followers_offset: int | None = None
    club_supporters_profile_offset: int | None = None
    club_supporters_distribution_offset: int | None = None
    club_chairman_status_offset: int | None = None
    club_status_offset: int | None = None
    club_morale_offset: int | None = None
    club_ownership_offset: int | None = None
    club_finance_offset: int | None = None
    finance_balance_offset: int | None = None
    finance_sugar_daddy_offset: int | None = None
    finance_remaining_transfer_budget_offset: int | None = None
    finance_season_transfer_budget_offset: int | None = None
    finance_wage_budget_offset: int | None = None
    finance_wage_used_offset: int | None = None
    finance_max_wage_offset: int | None = None
    finance_average_ticket_price_offset: int | None = None
    finance_transfer_revenue_percentage_offset: int | None = None
    finance_income_statement_period_offsets: tuple[int, int, int, int] | None = None
    finance_monthly_summary_offset: int | None = None
    club_loans_offset: int | None = None
    club_debt_record_size: int | None = None
    club_sponsors_root_offset: int | None = None
    club_sponsor_record_size: int | None = None
    team_details_offset: int | None = None
    team_details_club_vision_offset: int | None = None
    club_vision_culture_offset: int | None = None
    club_culture_record_size: int | None = None
    club_culture_vtable_rva: int | None = None
    club_culture_constructor_rva: int | None = None
    club_culture_initialize_rva: int | None = None
    # Club affiliation evidence recovered from FMRTE 24.4.2 build 47 and
    # 26.3.2 build 40.  The club owns a vector of pointers at +0x118; each
    # pointer targets a 0x38-byte affiliation record shared by both clubs.
    club_affiliations_offset: int | None = None
    club_affiliation_record_size: int | None = None
    # Native affiliation creation is build-specific.  The manager owns the
    # serializer-visible global registry while each Club owns a local vector.
    club_affiliation_manager_rva: int | None = None
    club_affiliation_create_rva: int | None = None
    club_affiliation_global_vector_offset: int | None = None
    club_affiliation_create_pattern: tuple[int | None, ...] = ()
    club_affiliation_manager_call_pattern: tuple[int | None, ...] = ()
    club_affiliation_manager_rel32_offset: int | None = None
    club_affiliation_manager_instruction_size: int | None = None
    club_affiliation_create_call_rel32_offset: int | None = None
    club_affiliation_create_call_instruction_size: int | None = None
    club_nation_offset: int | None = None
    nation_men_container_offset: int | None = None
    nation_youth_rating_offset: int | None = None
    club_director_of_football_offset: int | None = None
    club_foreground_color_offset: int | None = None
    club_background_color_offset: int | None = None
    club_allow_custom_logo_offset: int | None = None
    stadium_vtable_rva: int | None = None
    stadium_capacity_offset: int | None = None
    stadium_seating_capacity_offset: int | None = None
    stadium_used_capacity_offset: int | None = None
    stadium_expansion_capacity_offset: int | None = None
    stadium_pitch_condition_offset: int | None = None
    stadium_pitch_type_offset: int | None = None
    stadium_name_offset: int = 0x40
    stadium_item_id_offset: int | None = None
    stadium_id_offset: int | None = None
    stadium_id2_offset: int | None = None
    stadium_location_offset: int | None = None
    stadium_build_date_offset: int | None = None
    stadium_rebuild_date_offset: int | None = None
    stadium_all_seater_capacity_offset: int | None = None
    stadium_nearby_offset: int | None = None
    stadium_owner_offset: int | None = None
    stadium_latitude_offset: int | None = None
    stadium_longitude_offset: int | None = None
    stadium_state_offset: int | None = None
    stadium_extinct_offset: int | None = None
    stadium_pitch_deterioration_offset: int | None = None
    stadium_pitch_recovery_offset: int | None = None
    stadium_extras_offset: int | None = None
    stadium_national_team_use_offset: int | None = None
    stadium_national_u21_use_offset: int | None = None
    stadium_national_u19_use_offset: int | None = None
    stadium_pitch_relaid_date_offset: int | None = None
    stadium_pitch_relay_required_date_offset: int | None = None
    stadium_seat_color_offset: int | None = None
    chairman_business_offset: int | None = None
    chairman_interference_offset: int | None = None
    chairman_patience_offset: int | None = None
    chairman_resources_offset: int | None = None
    chairman_base_adjustment: int = 0
    game_version: str | None = None

    def module(self, process: Any) -> Any:
        # A connected read session already resolved and verified this module
        # for the same PID/layout generation. Reusing that immutable snapshot
        # avoids another Toolhelp module enumeration for every legacy writer.
        from tools.game_session import active_game_module

        active = active_game_module(process, self)
        if active is not None:
            return active
        from fm_collector.win32 import find_module

        return find_module(process, self.module_name)


FM26_LAYOUT = GameLayout(
    key="fm26",
    display_name="Football Manager 26",
    game_version="26.3.2",
    executable_sha256=FM26_EXE_SHA256,
    module_name="game_plugin.dll",
    distribution="steam",
    nation_men_container_offset=0x108,
    nation_youth_rating_offset=0x864,
    fixture_vtable_rva=0x449EF98,
    fixture_result_vtable_rva=0x4332188,
    fixture_pool_rva=0x4E370F0,
    season_result_vtable_rva=0x4330778,
    team_vtable_rva=0x44C13A8,
    national_team_vtable_rva=0x4592528,
    nation_vtable_rva=0x44CDD68,
    national_team_nation_offset=0x170,
    nationality_vtable_rva=0x44BF9B8,
    club_vtable_rva=0x44BA518,
    competition_vtable_rva=0x44BFE68,
    game_date_rva=0x4DF3C18,
    manager_person_offset=0x450,
    human_manager_vtable_rvas=(0x44A51CC,),
    database_root_pattern=(
        0x48, 0x83, 0xC7, 0x28, 0x4C, 0x8B, 0x0D,
        None, None, None, None,
    ),
    database_root_probe_rva=0x2C033AB,
    database_root_rel32_offset=7,
    database_root_instruction_size=11,
    human_manager_root_pattern=(
        0x48, 0x8B, 0x0D, None, None, None, None,
        0x48, 0x8B, 0x41, 0x18, 0x48, 0x8B, 0x49, 0x20,
        0x48, 0x29, 0xC1, 0x48, 0xC1, 0xF9, 0x03, 0x48, 0x39, 0xCE,
    ),
    human_manager_root_probe_rva=0xD0BFE3,
    human_manager_root_rel32_offset=3,
    human_manager_root_instruction_size=7,
    club_policy_departure_pattern_rva=0x168915B,
    club_policy_salary_pattern_rva=0x219B599,
    club_policy_salary_promise_pattern_rva=0x2DBB791,
    fixture_stage_index_offset=0x38,
    fixture_group_index_offset=0x39,
    fixture_round_number_offset=0x3B,
    competition_actual_offset=0xB0,
    actual_competition_stages_offset=0x1C0,
    competition_stage_type_offset=0x48,
    competition_stage_index_offset=0x4E,
    cup_stage_teams_offset=0x98,
    cup_stage_round_ties_offset=0xA0,
    savegame_root_rvas=(0x04E35F60, 0x04E44950),
    match_engine_phase_rva=0x4E46428,
    match_engine_mode_rva=0x4E47B0C,
    match_engine_active_state_rva=0x4E47B08,
    play_fixture_manager_pointer_rva=0x4E35E28,
    play_fixture_manager_vtable_rva=0x4480808,
    match_playback_module_name="GameAssembly.dll",
    match_playback_module_timestamp=0x6A229ED8,
    match_playback_module_image_size=0x35D9000,
    match_setup_type_info_rva=0x2E4E9F8,
    ca_growth_hook_rva=0x3174E3C,
    youth_generation_gate_hook_rva=0xEE5F2E,
    youth_generation_pa_hook_rva=0x1711767,
    youth_son_hook_rva=0x2D05180,
    actual_player_vtable_rvas=(0x4509828,),
    player_and_non_player_vtable_rvas=(0x4785308,),
    player_person_offset=0x288,
    player_and_non_player_person_offset=0x380,
    player_positions_offset=0x150,
    player_attributes_offset=0x15F,
    player_ca_offset=0x264,
    player_pa_offset=0x266,
    player_ca_bytes=2,
    attribute_display_bias=0,
    player_height_offset=0x22E,
    player_sharpness_offset=0x258,
    player_fatigue_offset=0x25A,
    player_fitness_offset=0x25C,
    player_morale_offset=0x26C,
    player_injury_list_offset=0xF8,
    player_season_stats_root_offset=0x108,
    player_season_stats_total_slot_offset=0x48,
    player_season_stats_block_size=0x78,
    player_career_stats_offset=0x110,
    player_international_appearances_offset=0x3BC,
    player_international_goals_offset=0x3BE,
    player_u21_international_appearances_offset=0x3C0,
    player_u21_international_goals_offset=0x3C1,
    player_international_counter_width=2,
    player_u21_international_counter_width=1,
    player_world_reputation_offset=0x262,
    team_reputation_offset=0xA8,
    team_type_offset=0x28,
    competition_reputation_offset=0x188,
    competition_reputation_bytes=1,
    person_nationality_offset=0x68,
    person_languages_offset=0xF0,
    person_flags_offset=0x18,
    person_date_of_birth_offset=0x88,
    person_hidden_attributes_offset=0x70,
    person_relationships_offset=0x78,
    person_previous_club_offset=0x108,
    staff_person_vtable_rva=0x4565018,
    staff_domestic_reputation_offset=None,
    staff_current_reputation_offset=None,
    staff_world_reputation_offset=None,
    staff_ca_offset=None,
    staff_pa_offset=None,
    staff_coaching_license_offset=0x13C,
    staff_job_type_offset=0x26,
    staff_contract_type_offset=0xC3,
    staff_wage_offset=0x20,
    staff_contract_start_offset=0x44,
    staff_contract_expiry_offset=0x48,
    staff_abilities_verified=True,
    staff_ability_complete_object_offset=0x100,
    loan_contract_vtable_rva=0x45A1268,
    player_move_contract_pool_rva=0x4E373A0,
    player_move_loan_contract_pool_rva=0x4E45C98,
    player_move_loan_contract_constructor_rva=0x1AA7700,
    player_move_contract_allocate_rva=0xDC1CD0,
    player_move_contract_release_rva=0xDC1E30,
    player_move_contract_factory_global_rva=0x4E48470,
    player_move_contract_factory_target_rva=0x346CC50,
    player_move_main_contract_vtable_rva=0x4334DF8,
    player_move_terminate_contract_rva=0x9E83C0,
    player_move_prepare_loan_rva=0x140CAE0,
    player_move_club_method_rva=0xEEF950,
    club_detail_offset=0xB0,
    club_detail2_offset=0x100,
    club_training_ground_offset=0x140,
    club_year_founded_offset=0xD0,
    club_training_facilities_offset=0x118,
    club_youth_facilities_offset=0x123,
    club_junior_coaching_offset=0x124,
    club_youth_recruitment_offset=0x125,
    club_average_attendance_offset=0x68,
    club_minimum_attendance_offset=0x6C,
    club_maximum_attendance_offset=0x70,
    club_season_ticket_holders_offset=0xB8,
    club_social_media_followers_offset=0xF0,
    club_supporters_profile_offset=0x10C,
    club_supporters_distribution_offset=0xFE,
    club_chairman_status_offset=0xD6,
    club_status_offset=0x16A,
    club_morale_offset=0x119,
    club_ownership_offset=0x70,
    club_finance_offset=0x150,
    finance_balance_offset=0x14,
    # FMRTE 26.3.2 build 40 runtime Aft.Aib resolves to +0x3C.
    finance_sugar_daddy_offset=0x3C,
    finance_remaining_transfer_budget_offset=0x7CC,
    finance_season_transfer_budget_offset=0x7D0,
    finance_wage_budget_offset=0x810,
    finance_wage_used_offset=0x81C,
    finance_max_wage_offset=0x814,
    finance_average_ticket_price_offset=0x870,
    finance_transfer_revenue_percentage_offset=0x7D8,
    finance_income_statement_period_offsets=(0x280, 0x398, 0x4B0, 0x5D0),
    finance_monthly_summary_offset=0x40,
    club_loans_offset=0x48,
    club_debt_record_size=0x1C,
    team_details_offset=0xA0,
    team_details_club_vision_offset=0xC8,
    club_vision_culture_offset=0x78,
    club_culture_record_size=0x40,
    club_culture_vtable_rva=0x4342788,
    club_culture_constructor_rva=0x193D240,
    club_affiliations_offset=0x118,
    club_affiliation_record_size=0x38,
    club_affiliation_manager_rva=0x4E49578,
    club_affiliation_create_rva=0xBA2570,
    club_affiliation_global_vector_offset=0x808,
    club_nation_offset=0xD8,
    club_director_of_football_offset=0x148,
    club_foreground_color_offset=0xA0,
    club_background_color_offset=0xA8,
    club_allow_custom_logo_offset=0x162,
    stadium_vtable_rva=0x45C03F8,
    stadium_capacity_offset=0x7C,
    stadium_seating_capacity_offset=0x80,
    stadium_used_capacity_offset=0x84,
    stadium_expansion_capacity_offset=0x8C,
    stadium_pitch_condition_offset=0xA8,
    stadium_pitch_type_offset=0xAD,
    stadium_name_offset=0x40,
    stadium_item_id_offset=0x08,
    stadium_id_offset=0x0C,
    stadium_id2_offset=0x10,
    stadium_location_offset=0x60,
    stadium_build_date_offset=0x30,
    stadium_rebuild_date_offset=0x34,
    stadium_all_seater_capacity_offset=0x88,
    stadium_nearby_offset=0x68,
    stadium_owner_offset=0x90,
    stadium_latitude_offset=0x70,
    stadium_longitude_offset=0x74,
    stadium_state_offset=0xAA,
    stadium_extinct_offset=0xAB,
    stadium_pitch_deterioration_offset=0xAC,
    stadium_pitch_recovery_offset=0xAE,
    stadium_extras_offset=0xB6,
    stadium_national_team_use_offset=0xB1,
    stadium_national_u21_use_offset=0xB2,
    stadium_national_u19_use_offset=0xB3,
    stadium_pitch_relaid_date_offset=0x28,
    stadium_pitch_relay_required_date_offset=0x2C,
    stadium_seat_color_offset=0x78,
    chairman_business_offset=0x11,
    chairman_interference_offset=0x16,
    chairman_patience_offset=0x19,
    chairman_resources_offset=0x1B,
    chairman_base_adjustment=0,
)


# Steam FM2024 24.4.2+2081827. Only fields proven against the live process are
# enabled here. The current-date global is resolved by the trainer-validated
# ``44 8B 1D ?? ?? ?? ?? 48 FF ?? 31 ?? EB`` RIP-relative signature.
# ``sicomps::FIXTURE_RESULT`` is the short-lived detailed result archive.
# ``db::SCORELINE::BASIC_SCORELINE`` persists simulated matches even when the
# user does not watch them; its FM24 field layout was verified against 1200
# completed records in a live save.
FM24_LAYOUT = GameLayout(
    key="fm24",
    display_name="Football Manager 2024",
    executable_sha256=FM24_EXE_SHA256,
    module_name="fm.exe",
    distribution="steam",
    nation_men_container_offset=0x108,
    nation_youth_rating_offset=0x78C,
    fixture_vtable_rva=0x5A25F58,
    team_vtable_rva=0x5A78848,
    national_team_vtable_rva=0x5A6CAB8,
    nation_vtable_rva=0x5A6CD98,
    club_vtable_rva=0x5A51F38,
    competition_vtable_rva=0x5A54A38,
    actual_player_vtable_rvas=(0x5A4E958,),
    player_and_non_player_vtable_rvas=(0x5C7E798,),
    player_person_offset=0x278,
    player_and_non_player_person_offset=0x368,
    player_positions_offset=0x208,
    player_attributes_offset=0x217,
    player_ca_offset=0x200,
    player_pa_offset=0x202,
    player_ca_bytes=2,
    attribute_display_bias=0,
    game_date_rva=0x631D5BC,
    manager_person_offset=0x450,
    human_manager_vtable_rvas=(0x5A6A388,),
    human_manager_vtable_on_person=True,
    club_policy_departure_pattern_rva=0x32668D3,
    club_policy_salary_pattern_rva=0x3262089,
    database_root_pattern=(
        0x48, 0x8D, 0xB9, None, None, None, None,
        0x4C, 0x8B, 0x0D, None, None, None, None,
        0x48, 0x8D, 0x0D,
    ),
    database_root_probe_rva=0x3BDC363,
    database_root_rel32_offset=10,
    database_root_instruction_size=14,
    human_manager_root_pattern=(
        0x48, 0x8B, 0x35, None, None, None, None,
        0x48, 0x8B, 0x56, 0x18, 0x4C, 0x8B, 0x76, 0x20,
        0x49, 0x29, 0xD6, 0xB0, 0x01, 0x49, 0x83, 0xFE, 0x10,
    ),
    human_manager_root_probe_rva=0x3A278B1,
    human_manager_root_rel32_offset=3,
    human_manager_root_instruction_size=7,
    # Retired in V1.9.1c: the value changes during normal FM24 sessions and
    # cannot serve as a permanent save identity. Local saves use the verified
    # GAME_SAVE_PROVIDER_LOCAL path/label pair instead.
    savegame_id_absolute_address=None,
    fixture_result_vtable_rva=0x5608078,
    fixture_pool_rva=0x6429A68,
    season_result_vtable_rva=0x56218F8,
    match_session_pointer_rva=0x6374480,
    match_session_vtable_rva=0x5836A90,
    game_match_session_vtable_rva=0x5604F08,
    redbull_hook_rva=0x43B3FDF,
    referee_hook_rva=0x1A0AC338,
    ca_growth_hook_rva=0x4F7C463,
    youth_generation_gate_hook_rva=0x217CB60,
    youth_generation_pa_hook_rva=0x21271A2,
    youth_generation_state_hook_rva=0x36238D5,
    youth_son_hook_rva=0x3618E59,
    player_height_offset=0x14E,
    player_weight_offset=0x14C,
    player_sharpness_offset=0x1F4,
    player_fatigue_offset=0x1F6,
    player_fitness_offset=0x1F8,
    player_morale_offset=0x25F,
    player_injury_list_offset=0xF8,
    player_home_reputation_offset=0x1FA,
    player_current_reputation_offset=0x1FC,
    player_world_reputation_offset=0x1FE,
    player_season_stats_root_offset=0x108,
    player_season_stats_total_slot_offset=0x48,
    player_season_stats_block_size=0x78,
    player_career_stats_offset=0x110,
    player_international_appearances_offset=0x3DC,
    player_international_goals_offset=0x3DE,
    player_u21_international_appearances_offset=0x3DD,
    player_u21_international_goals_offset=0x3DF,
    player_international_counter_width=1,
    player_u21_international_counter_width=1,
    team_reputation_offset=0xA8,
    team_type_offset=0x28,
    competition_reputation_offset=0x180,
    competition_reputation_bytes=2,
    person_nationality_offset=0x70,
    person_languages_offset=0x120,
    person_date_of_birth_offset=0x44,
    person_date_of_birth_day_year=True,
    person_hidden_attributes_offset=0x78,
    person_relationships_offset=0x80,
    person_previous_club_offset=0x138,
    person_full_name_offset=0x48,
    person_first_name_offset=0x58,
    person_last_name_offset=0x60,
    person_common_name_offset=0x68,
    staff_person_vtable_rva=0x5A4C358,
    team_manager_person_offset=0xF8,
    staff_domestic_reputation_offset=0xD8,
    staff_current_reputation_offset=0xDA,
    staff_world_reputation_offset=0xDC,
    staff_ca_offset=0xDE,
    staff_pa_offset=0xE0,
    staff_coaching_license_offset=0x16A,
    staff_job_type_offset=0x1C,
    staff_contract_type_offset=0xB3,
    staff_wage_offset=0x18,
    staff_contract_start_offset=0x3C,
    staff_contract_expiry_offset=0x40,
    staff_abilities_verified=True,
    staff_ability_complete_object_offset=0xF0,
    result_away_goals_offset=0x68,
    result_home_outcome_offset=0x6C,
    result_away_outcome_offset=0x6D,
    # FM24 stores a compact <scorer, flags, minute> record per goal in the
    # first vector of result+0x70. FM26 instead uses 16-byte inline records
    # behind result+0x78.
    result_event_root_offset=0x70,
    result_event_record_size=0x08,
    result_event_record_format="fm24",
    fixture_stage_index_offset=0x38,
    fixture_group_index_offset=0x39,
    fixture_round_number_offset=0x3A,
    competition_actual_offset=0xB0,
    actual_competition_stages_offset=0x1C0,
    competition_stage_type_offset=0x48,
    competition_stage_index_offset=0x4C,
    cup_stage_teams_offset=0xA0,
    cup_stage_round_ties_offset=0xA8,
    loan_contract_vtable_rva=0x5C78638,
    player_move_contract_pool_rva=0x6378C80,
    player_move_main_contract_vtable_rva=0x5667C88,
    player_move_fm24_source_cleanup_rva=0x224F2A0,
    player_move_fm24_transfer_rva=0x22515B0,
    player_move_fm24_transaction_allocator_rva=0x2A0A420,
    player_move_fm24_transaction_constructor_rva=0x49DAB60,
    player_move_fm24_transaction_initializer_rva=0x49DAD30,
    player_move_fm24_transaction_prepare_rva=0x2A3DBE0,
    player_move_fm24_transaction_commit_rva=0x394D590,
    player_move_fm24_transaction_transition_rva=0x2A3C980,
    player_move_fm24_transaction_outer_submit_rva=0x3954C90,
    player_move_fm24_transaction_entry_rva=0x39D4F90,
    player_move_fm24_transaction_vtable_rva=0x5C85388,
    player_move_fm24_context_root_rva=0x642B960,
    player_move_fm24_context_vtable_rva=0x5B08480,
    player_move_fm24_contract_factory_rva=0x2341640,
    player_move_fm24_loan_constructor_rva=0x48870C0,
    club_detail_offset=0xB0,
    club_detail2_offset=0x100,
    club_training_ground_offset=0x140,
    club_year_founded_offset=0xD8,
    club_training_facilities_offset=0x120,
    club_youth_facilities_offset=0x12B,
    club_junior_coaching_offset=0x12C,
    club_youth_recruitment_offset=0x12D,
    club_average_attendance_offset=0x68,
    club_minimum_attendance_offset=0x6C,
    club_maximum_attendance_offset=0x70,
    club_season_ticket_holders_offset=0xC0,
    club_social_media_followers_offset=0xF8,
    club_supporters_profile_offset=0x114,
    club_supporters_distribution_offset=0x106,
    club_chairman_status_offset=0xDE,
    club_status_offset=0x16A,
    club_morale_offset=0x121,
    club_ownership_offset=0x78,
    club_finance_offset=0x150,
    finance_balance_offset=0x14,
    # FMRTE 24.4.2 BasicFinances.SugarDaddy: Aeo.Ahk resolves lookup key
    # 679 to the byte field at +0x3C. Shared by the exact Steam/Epic layouts.
    finance_sugar_daddy_offset=0x3C,
    finance_remaining_transfer_budget_offset=0x7CC,
    finance_season_transfer_budget_offset=0x7D0,
    finance_wage_budget_offset=0x810,
    finance_wage_used_offset=0x81C,
    finance_max_wage_offset=0x814,
    finance_average_ticket_price_offset=0x870,
    finance_transfer_revenue_percentage_offset=0x7D8,
    finance_income_statement_period_offsets=(0x280, 0x398, 0x4B0, 0x5D0),
    finance_monthly_summary_offset=0x40,
    club_loans_offset=0x50,
    club_debt_record_size=0x1C,
    club_sponsors_root_offset=0x130,
    club_sponsor_record_size=0x18,
    team_details_offset=0xA0,
    team_details_club_vision_offset=0xC0,
    club_vision_culture_offset=0x78,
    club_culture_record_size=0x40,
    club_culture_vtable_rva=0x574AA78,
    club_culture_constructor_rva=0x2E9A6D0,
    club_culture_initialize_rva=0x294D110,
    club_affiliations_offset=0x118,
    club_affiliation_record_size=0x38,
    club_affiliation_manager_rva=0x642ECD0,
    club_affiliation_create_rva=0x23E63A0,
    club_affiliation_global_vector_offset=0x680,
    club_nation_offset=0xD8,
    club_director_of_football_offset=0x148,
    club_foreground_color_offset=0xA0,
    club_background_color_offset=0xA8,
    club_allow_custom_logo_offset=0x162,
    stadium_vtable_rva=0x5A78248,
    stadium_capacity_offset=0x6C,
    stadium_seating_capacity_offset=0x70,
    stadium_used_capacity_offset=0x74,
    stadium_expansion_capacity_offset=0x7C,
    stadium_pitch_condition_offset=0x98,
    stadium_pitch_type_offset=0x9D,
    stadium_name_offset=0x38,
    stadium_item_id_offset=0x08,
    stadium_id_offset=0x0C,
    stadium_id2_offset=0x10,
    stadium_location_offset=0x50,
    stadium_build_date_offset=0x28,
    stadium_rebuild_date_offset=0x2C,
    stadium_all_seater_capacity_offset=0x78,
    stadium_nearby_offset=0x58,
    stadium_owner_offset=0x80,
    stadium_latitude_offset=0x60,
    stadium_longitude_offset=0x64,
    stadium_state_offset=0x9A,
    stadium_extinct_offset=0x9B,
    stadium_pitch_deterioration_offset=0x9C,
    stadium_pitch_recovery_offset=0x9E,
    stadium_extras_offset=0xA6,
    stadium_national_team_use_offset=0xA1,
    stadium_national_u21_use_offset=0xA2,
    stadium_national_u19_use_offset=0xA3,
    stadium_pitch_relaid_date_offset=0x20,
    stadium_pitch_relay_required_date_offset=0x24,
    stadium_seat_color_offset=0x68,
    chairman_business_offset=0x19,
    chairman_interference_offset=0x1E,
    chairman_patience_offset=0x21,
    chairman_resources_offset=0x23,
    chairman_base_adjustment=0x08,
)


FM24_EPIC_LAYOUT = replace(
    FM24_LAYOUT,
    # Epic 24.4.2 (SHA256 33E8...5378) was checked read-only on 2026-09-09.
    # These roots, object fields, pools, vtables, and Hook entries match the
    # Steam build byte-for-byte. Distribution-specific high-level transaction
    # entrypoints remain disabled below.
    person_previous_club_offset=0x138,
    club_policy_departure_pattern_rva=None,
    club_policy_salary_pattern_rva=None,
    team_type_offset=0x28,
    database_root_probe_rva=0x3BDC363,
    human_manager_root_probe_rva=0x3A278B1,
    display_name="Football Manager 2024 (Epic)",
    executable_sha256=FM24_EPIC_EXE_SHA256,
    distribution="epic",
    club_affiliation_manager_rva=None,
    club_affiliation_create_rva=None,
    club_affiliation_global_vector_offset=0x680,
    club_affiliation_create_pattern=(
        0x41, 0x57, 0x41, 0x56, 0x41, 0x55, 0x41, 0x54,
        0x56, 0x57, 0x55, 0x53, 0x48, 0x83, 0xEC, 0x48,
        0x45, 0x89, 0xCE, 0x44, 0x89, 0xC7, 0x48, 0x89,
        0xCB, 0x48, 0x8B, 0x49, 0x10, 0xE8,
        None, None, None, None,
        0x49, 0x89, 0xC4, 0x48, 0x8B, 0x4B, 0x10, 0x89,
        0xFA, 0xE8, None, None, None, None, 0x4D, 0x85, 0xE4,
    ),
    club_affiliation_manager_call_pattern=(
        0xC7, 0x44, 0x24, 0x20, 0x00, 0x00, 0x00, 0x00,
        0x48, 0x8D, 0x0D, None, None, None, None,
        0x41, 0xB8, 0xFF, 0xFF, 0xFF, 0xFF, 0x41, 0xB1, 0x01,
        0xE8, None, None, None, None, 0x48, 0x85, 0xC0,
    ),
    club_affiliation_manager_rel32_offset=11,
    club_affiliation_manager_instruction_size=15,
    club_affiliation_create_call_rel32_offset=25,
    club_affiliation_create_call_instruction_size=29,
    club_culture_vtable_rva=0x574AA78,
    club_culture_constructor_rva=0x2E9A6D0,
    club_culture_initialize_rva=0x294D110,
    stadium_vtable_rva=0x5A78248,
    savegame_id_absolute_address=None,
    savegame_id_requires_legacy_validation=False,
    fixture_pool_rva=0x6429A68,
    referee_hook_rva=0x19EEAA14,
    ca_growth_hook_rva=0x4F7C463,
    youth_generation_gate_hook_rva=0x217CB60,
    youth_generation_pa_hook_rva=0x21271A2,
    youth_generation_state_hook_rva=0x36238D5,
    youth_son_hook_rva=0x3618E59,
    player_move_contract_pool_rva=0x6378C80,
    player_move_main_contract_vtable_rva=0x5667C88,
    player_move_fm24_source_cleanup_rva=None,
    player_move_fm24_transfer_rva=None,
    player_move_fm24_contract_factory_rva=None,
    player_move_fm24_loan_constructor_rva=None,
    player_move_fm24_transaction_allocator_rva=None,
    player_move_fm24_transaction_constructor_rva=None,
    player_move_fm24_transaction_initializer_rva=None,
    player_move_fm24_transaction_prepare_rva=None,
    player_move_fm24_transaction_commit_rva=None,
    player_move_fm24_transaction_transition_rva=None,
    player_move_fm24_transaction_outer_submit_rva=None,
    player_move_fm24_transaction_entry_rva=None,
    player_move_fm24_transaction_vtable_rva=None,
    player_move_fm24_context_root_rva=None,
    player_move_fm24_context_vtable_rva=None,
)


# Steam FM2024 24.4.0.0. RTTI, date and hook RVAs were collected from the
# exact executable above. Record field offsets are shared with 24.4.2; every
# version-dependent address that differs remains explicit in this layout.
FM24_240_LAYOUT = replace(
    FM24_LAYOUT,
    person_previous_club_offset=None,
    club_policy_departure_pattern_rva=None,
    club_policy_salary_pattern_rva=None,
    team_type_offset=None,
    database_root_probe_rva=None,
    human_manager_root_probe_rva=None,
    club_affiliation_manager_rva=None,
    club_affiliation_create_rva=None,
    club_affiliation_global_vector_offset=None,
    club_culture_vtable_rva=None,
    club_culture_constructor_rva=None,
    club_culture_initialize_rva=None,
    display_name="Football Manager 2024 Steam 24.4.0",
    executable_sha256=FM24_240_EXE_SHA256,
    stadium_vtable_rva=None,
    savegame_id_absolute_address=None,
    fixture_pool_rva=None,
    competition_reputation_offset=None,
    competition_reputation_bytes=None,
    competition_actual_offset=None,
    actual_competition_stages_offset=None,
    competition_stage_type_offset=None,
    competition_stage_index_offset=None,
    cup_stage_teams_offset=None,
    cup_stage_round_ties_offset=None,
    finance_sugar_daddy_offset=None,
    finance_season_transfer_budget_offset=None,
    finance_wage_budget_offset=None,
    finance_wage_used_offset=None,
    finance_max_wage_offset=None,
    finance_average_ticket_price_offset=None,
    finance_transfer_revenue_percentage_offset=None,
    finance_monthly_summary_offset=None,
    club_loans_offset=None,
    club_debt_record_size=None,
    club_sponsors_root_offset=None,
    club_sponsor_record_size=None,
    team_vtable_rva=0x5A78828,
    national_team_vtable_rva=0x5A6CA98,
    nation_vtable_rva=0x5A6CD78,
    player_and_non_player_vtable_rvas=(0x5C7E778,),
    human_manager_vtable_rvas=(0x5A6A368,),
    game_match_session_vtable_rva=None,
    redbull_hook_rva=0x43B3C8F,
    referee_hook_rva=0x1A56A559,
    ca_growth_hook_rva=None,
    youth_generation_gate_hook_rva=None,
    youth_generation_pa_hook_rva=None,
    youth_generation_state_hook_rva=None,
    youth_son_hook_rva=None,
    player_move_contract_pool_rva=None,
    player_move_main_contract_vtable_rva=None,
    player_move_fm24_source_cleanup_rva=None,
    player_move_fm24_transfer_rva=None,
    player_move_fm24_contract_factory_rva=None,
    player_move_fm24_loan_constructor_rva=None,
    player_move_fm24_transaction_allocator_rva=None,
    player_move_fm24_transaction_constructor_rva=None,
    player_move_fm24_transaction_initializer_rva=None,
    player_move_fm24_transaction_prepare_rva=None,
    player_move_fm24_transaction_commit_rva=None,
    player_move_fm24_transaction_transition_rva=None,
    player_move_fm24_transaction_outer_submit_rva=None,
    player_move_fm24_transaction_entry_rva=None,
    player_move_fm24_transaction_vtable_rva=None,
    player_move_fm24_context_root_rva=None,
    player_move_fm24_context_vtable_rva=None,
)


# FM2024 24.4.1.0 custom-directory build. Its collected RTTI, date, result,
# player/staff and match-session addresses match the verified 24.4.2 layout.
# The referee hook is build-specific and therefore remains explicit here.
FM24_241_LAYOUT = replace(
    FM24_LAYOUT,
    person_previous_club_offset=None,
    club_policy_departure_pattern_rva=None,
    club_policy_salary_pattern_rva=None,
    team_type_offset=None,
    database_root_probe_rva=None,
    human_manager_root_probe_rva=None,
    club_affiliation_manager_rva=None,
    club_affiliation_create_rva=None,
    club_affiliation_global_vector_offset=None,
    club_culture_vtable_rva=None,
    club_culture_constructor_rva=None,
    club_culture_initialize_rva=None,
    display_name="Football Manager 2024 Steam 24.4.1 (Beta)",
    executable_sha256=FM24_241_EXE_SHA256,
    stadium_vtable_rva=None,
    savegame_id_absolute_address=None,
    fixture_pool_rva=None,
    competition_reputation_offset=None,
    competition_reputation_bytes=None,
    competition_actual_offset=None,
    actual_competition_stages_offset=None,
    competition_stage_type_offset=None,
    competition_stage_index_offset=None,
    cup_stage_teams_offset=None,
    cup_stage_round_ties_offset=None,
    finance_sugar_daddy_offset=None,
    finance_season_transfer_budget_offset=None,
    finance_wage_budget_offset=None,
    finance_wage_used_offset=None,
    finance_max_wage_offset=None,
    finance_average_ticket_price_offset=None,
    finance_transfer_revenue_percentage_offset=None,
    finance_monthly_summary_offset=None,
    club_loans_offset=None,
    club_debt_record_size=None,
    club_sponsors_root_offset=None,
    club_sponsor_record_size=None,
    referee_hook_rva=0x19DA4E2A,
    ca_growth_hook_rva=None,
    youth_generation_gate_hook_rva=None,
    youth_generation_pa_hook_rva=None,
    youth_generation_state_hook_rva=None,
    youth_son_hook_rva=None,
    player_move_contract_pool_rva=None,
    player_move_main_contract_vtable_rva=None,
    player_move_fm24_source_cleanup_rva=None,
    player_move_fm24_transfer_rva=None,
    player_move_fm24_contract_factory_rva=None,
    player_move_fm24_loan_constructor_rva=None,
    player_move_fm24_transaction_allocator_rva=None,
    player_move_fm24_transaction_constructor_rva=None,
    player_move_fm24_transaction_initializer_rva=None,
    player_move_fm24_transaction_prepare_rva=None,
    player_move_fm24_transaction_commit_rva=None,
    player_move_fm24_transaction_transition_rva=None,
    player_move_fm24_transaction_outer_submit_rva=None,
    player_move_fm24_transaction_entry_rva=None,
    player_move_fm24_transaction_vtable_rva=None,
    player_move_fm24_context_root_rva=None,
    player_move_fm24_context_vtable_rva=None,
)


# FM2024 XGP Content build. The protected executable cannot be hashed from
# disk, so selection uses the mapped PE timestamp and SizeOfImage. RTTI and
# hook RVAs were resolved from the live image; record offsets remain the
# FM2024 layout. Match-session state stays disabled until its global pointer
# has been independently located.
FM24_XGP_LAYOUT = replace(
    FM24_LAYOUT,
    person_previous_club_offset=None,
    club_policy_departure_pattern_rva=None,
    club_policy_salary_pattern_rva=None,
    team_type_offset=None,
    database_root_probe_rva=None,
    human_manager_root_probe_rva=None,
    club_affiliation_manager_rva=None,
    club_affiliation_create_rva=None,
    club_affiliation_global_vector_offset=None,
    club_culture_vtable_rva=None,
    club_culture_constructor_rva=None,
    club_culture_initialize_rva=None,
    display_name="Football Manager 2024 (XGP Beta)",
    executable_sha256="",
    distribution="xgp",
    stadium_vtable_rva=None,
    ca_growth_hook_rva=None,
    youth_generation_gate_hook_rva=None,
    youth_generation_pa_hook_rva=None,
    youth_generation_state_hook_rva=None,
    youth_son_hook_rva=None,
    player_move_contract_pool_rva=None,
    player_move_main_contract_vtable_rva=None,
    player_move_fm24_source_cleanup_rva=None,
    player_move_fm24_transfer_rva=None,
    player_move_fm24_contract_factory_rva=None,
    player_move_fm24_loan_constructor_rva=None,
    player_move_fm24_transaction_allocator_rva=None,
    player_move_fm24_transaction_constructor_rva=None,
    player_move_fm24_transaction_initializer_rva=None,
    player_move_fm24_transaction_prepare_rva=None,
    player_move_fm24_transaction_commit_rva=None,
    player_move_fm24_transaction_transition_rva=None,
    player_move_fm24_transaction_outer_submit_rva=None,
    player_move_fm24_transaction_entry_rva=None,
    player_move_fm24_transaction_vtable_rva=None,
    player_move_fm24_context_root_rva=None,
    player_move_fm24_context_vtable_rva=None,
    savegame_id_absolute_address=None,
    fixture_pool_rva=None,
    # No independent BasicFinances.SugarDaddy field evidence for XGP.
    finance_sugar_daddy_offset=None,
    fixture_vtable_rva=0x5A2B288,
    fixture_result_vtable_rva=0x561A1C8,
    # Keep protected XGP persistent results disabled until its RTTI probe is
    # independently collected; never reuse the Steam vtable in another image.
    season_result_vtable_rva=None,
    team_vtable_rva=0x5A7DBC8,
    national_team_vtable_rva=0x5A71E28,
    nation_vtable_rva=0x5A72108,
    club_vtable_rva=0x5A57298,
    competition_vtable_rva=0x5A59D98,
    actual_player_vtable_rvas=(0x5A53CB8,),
    player_and_non_player_vtable_rvas=(0x5C84098,),
    staff_person_vtable_rva=0x5A516B8,
    human_manager_vtable_rvas=(0x5A6F6F8,),
    game_date_rva=0x633D36C,
    match_session_pointer_rva=None,
    match_session_vtable_rva=0x5847280,
    game_match_session_vtable_rva=None,
    redbull_hook_rva=0x430FE9F,
    referee_hook_rva=0x19688732,
    loan_contract_vtable_rva=None,
)


FM26_XGP_TEMPLATE = replace(
    FM26_LAYOUT,
    national_team_nation_offset=None,
    nationality_vtable_rva=None,
    club_policy_departure_pattern_rva=None,
    club_policy_salary_pattern_rva=None,
    club_policy_salary_promise_pattern_rva=None,
    club_affiliation_manager_rva=None,
    club_affiliation_create_rva=None,
    club_affiliation_global_vector_offset=None,
    club_culture_vtable_rva=None,
    club_culture_constructor_rva=None,
    club_culture_initialize_rva=None,
    database_root_probe_rva=None,
    human_manager_root_probe_rva=None,
    display_name="Football Manager 26 (XGP)",
    game_version="0.9.9957.0",
    executable_sha256="",
    distribution="xgp",
    stadium_vtable_rva=None,
    fixture_pool_rva=None,
    finance_sugar_daddy_offset=None,
    fixture_vtable_rva=0,
    fixture_result_vtable_rva=None,
    season_result_vtable_rva=None,
    team_vtable_rva=0,
    national_team_vtable_rva=0,
    nation_vtable_rva=0,
    club_vtable_rva=0,
    competition_vtable_rva=0,
    actual_player_vtable_rvas=(),
    loan_contract_vtable_rva=None,
    player_and_non_player_vtable_rvas=(),
    human_manager_vtable_rvas=(),
    staff_person_vtable_rva=None,
    person_previous_club_offset=None,
    player_season_stats_root_offset=None,
    player_season_stats_total_slot_offset=None,
    player_season_stats_block_size=None,
    game_date_rva=None,
    savegame_root_rvas=(),
    match_engine_phase_rva=None,
    match_engine_mode_rva=0x4D7F99C,
    match_engine_active_state_rva=None,
    play_fixture_manager_pointer_rva=None,
    play_fixture_manager_vtable_rva=None,
    player_move_contract_pool_rva=None,
    player_move_loan_contract_pool_rva=None,
    player_move_loan_contract_constructor_rva=None,
    player_move_contract_allocate_rva=None,
    player_move_contract_release_rva=None,
    player_move_contract_factory_global_rva=None,
    player_move_contract_factory_target_rva=None,
    player_move_main_contract_vtable_rva=None,
    player_move_terminate_contract_rva=None,
    player_move_prepare_loan_rva=None,
    player_move_club_method_rva=None,
    future_transfer_manager_vtable_rva=None,
    future_transfer_full_offer_vtable_rva=None,
    future_transfer_loan_offer_vtable_rva=None,
    ca_growth_hook_rva=None,
    youth_generation_gate_hook_rva=None,
    youth_generation_pa_hook_rva=None,
    youth_generation_state_hook_rva=None,
    youth_son_hook_rva=None,
)


# Football Manager 27 (Steam). Read-only static probe of the local install
# (Unity 6000.3.18f1-custom, game_plugin.dll 433986560 bytes, SHA256
# FM27_STEAM_PLUGIN_SHA256). The FM26 RTTI class names, the game-date AOB
# and the human-manager marker each match exactly one location in that
# build, so the values below were resolved statically from the plugin file
# without launching the game:
#   fixture          .?AVFIXTURE@sicomps@@                 0x4CB69A8
#   fixture_result   .?AVFIXTURE_RESULT@sicomps@@          0x4AFDE68
#   season_result    .?AVBASIC_SCORELINE@SCORELINE@db@@    0x4AFC318
#   team             .?AVTEAM@db@@                         0x4CE1108
#   national_team    .?AVNATIONAL_TEAM@db@@                0x4DC2288
#   nation           .?AVNATIONAL_TEAM_CONTAINER@db@@      0x4CEC0B8
#   club             .?AVCLUB@db@@                         0x4CDA1F8
#   competition      .?AVCOMP@db@@                         0x4CDFAD8
#   player           .?AVACTUAL_PLAYER@db@@                0x4D27228, 0x4D27708, 0x4D27758
#   player_non_player .?AVACTUAL_PLAYER_AND_NON_PLAYER@db@@ 0x4F75A08, 0x4F75EE8, 0x4F75F38
#   staff            .?AVACTUAL_NON_PLAYER@db@@            0x4D8BE28, 0x4D8BE78
#   game date        FM26_GAME_DATE_PATTERN                0x56AD378
#   database root    FM26 database_root_pattern            probe 0x2E8749A -> 0x57036D0
#   human mgr root   FM26 human_manager_root_pattern       probe 0xEB7253 -> 0x56F3008
#
# Everything else stays unresolved. This layout IS registered: the structural
# offsets below were verified against a running 27.0 build, so an attached read
# session is allowed. Components that are still unmeasured keep their dataclass
# defaults (None), which the readers guard, instead of carrying a guess.
FM27_LAYOUT = GameLayout(
    key="fm27",
    display_name="Football Manager 27",
    game_version="27.0",
    executable_sha256=FM27_EXE_SHA256,
    module_name="game_plugin.dll",
    distribution="steam",
    fixture_vtable_rva=0x4CB69A8,
    fixture_result_vtable_rva=0x4AFDE68,
    # Found by scanning the module data window for the FIXTURE_DEL_ARRAY pool
    # signature and then confirmed with the product's own validator
    # (refresh_memory_core._read_fixture_pool_at_root): 7021 fixtures and 24321
    # results read. Exactly one candidate matched in the window, and the
    # FM26-relative guess was rejected, so the scan was necessary.
    fixture_pool_rva=0x56F6AB0,
    season_result_vtable_rva=0x4AFC318,
    team_vtable_rva=0x4CE1108,
    national_team_vtable_rva=0x4DC2288,
    nation_vtable_rva=0x4CEC0B8,
    club_vtable_rva=0x4CDA1F8,
    competition_vtable_rva=0x4CDFAD8,
    # Row-object evidence from the live person table (scripts/
    # verify_fm27_layout.py + probe round 9): rows are mostly db::PERSON
    # (0x4CC2578) with ACTUAL_PLAYER rows on 0x4D27758/0x4D27708 (equal
    # copies), ACTUAL_NON_PLAYER rows on 0x4D8BE78 and
    # ACTUAL_PLAYER_AND_NON_PLAYER rows on 0x4F75F38. The never-observed copies
    # stay excluded.
    #
    # Round 105 adds the player-SUBOBJECT vtables, because the reader is now
    # handed a player pointer (person-0x288) rather than the person pointer the
    # table yields, and that subobject carries its own vtable: 0x4D27228 for
    # ACTUAL_PLAYER (17268/17268 live rows) and 0x4F75A08 for the hybrid class
    # (198/200 sampled). Round 9's note that "0x4D27228 never appears as a row
    # vtable" remains true - it is a subobject vtable, never a person vtable -
    # so adding it here cannot misclassify a table entry.
    actual_player_vtable_rvas=(0x4D27228, 0x4D27708, 0x4D27758),
    player_and_non_player_vtable_rvas=(0x4F75A08, 0x4F75F38),
    staff_person_vtable_rva=0x4D8BE78,
    # Verified against 37 live ACTUAL_NON_PLAYER staff rows: at person - 0x100
    # the 18 STAFF_COACHING_FIELDS bytes are non-zero in 666/666 reads and span
    # 5..98 raw, which the consumer's (v + 2) // 5 conversion maps onto a full
    # 1..20 display range (all 20 values present). The offset is person-relative
    # and the staff subobject also precedes the person, so FM26's 0x100 holds
    # unchanged. A second clean-looking block at person - 0x7DB reads
    # 1..20 directly, but that scale would collapse to 1..4 under the consumer's
    # conversion, so it is not the staff ability block.
    staff_ability_complete_object_offset=0x100,
    staff_abilities_verified=True,
    game_date_rva=0x56AD378,
    # Same generation as FM26: both root AOBs match exactly one location in
    # the FM27 plugin. The resolved pointer-table globals were confirmed to
    # hold live non-null heap pointers against a running 27.0 session via
    # scripts/verify_fm27_layout.py.
    database_root_pattern=FM26_LAYOUT.database_root_pattern,
    database_root_probe_rva=0x2E8749A,
    database_root_rel32_offset=7,
    database_root_instruction_size=11,
    human_manager_root_pattern=FM26_LAYOUT.human_manager_root_pattern,
    human_manager_root_probe_rva=0xEB7253,
    human_manager_root_rel32_offset=3,
    human_manager_root_instruction_size=7,
    # Observed directly on the 27.0 singleton resolved through the root AOB.
    human_manager_vtable_rvas=(0x4CC086C,),
    # Person fields verified against real players (probe rounds 10-19):
    # +0x0C entity UID, +0x40 full name, +0x50 first name, +0x58 last name,
    # +0x68 pointer to db::NATION, +0x88 DOB (year<<16 | day-of-year).
    # Edmond Tapsoba resolved to 1999-d33 (born 1999-02-02).
    person_full_name_offset=0x40,
    person_first_name_offset=0x50,
    person_last_name_offset=0x58,
    person_nationality_offset=0x68,
    person_date_of_birth_offset=0x88,
    person_date_of_birth_day_year=False,
    # CORRECTED (round 105). The table's pointers address the PERSON
    # sub-object, which sits at +0x288 inside a 0x3C8 row - the very same
    # [player 0x288][person 0x140] split as FM26. Everything below was read
    # live in one session across 17268 ACTUAL_PLAYER rows:
    #   * the qword at person-0x288 is a module .rdata pointer in 17268/17268
    #     rows and is always 0x4D27228, the ACTUAL_PLAYER player-subobject
    #     vtable listed in the static RTTI dump;
    #   * the 15-byte block at person-0x138 is a legal 1..20 position matrix in
    #     17268/17268 rows, and no other person class scores above 0;
    #   * CA/PA at person-0x24 / person-0x22 are 1..200 in 17268/17268 rows
    #     with CA<=PA in 17268/17268;
    #   * the 54 attribute bytes at person-0x129 are 1..100 with zero zeros.
    # The earlier reading - person at +0, with 0x288 described as "a shared
    # container, uid 0x7FF9 across every player" - was the NEXT row's player
    # subobject: person+0x288+0x140 == person+0x3C8, exactly one row past the
    # object being read. That is also why the 0x4D27228 vtable appeared "never
    # as a row vtable": it is the subobject vtable, and the subobject it
    # belongs to is one row along.
    player_person_offset=0x288,
    # Same evidence for the hybrid class: person-0x380 carries vtable
    # 0x4F75A08 and a legal position matrix at +0x150 past it in 198/200
    # sampled rows. Identical to FM26's 0x380.
    player_and_non_player_person_offset=0x380,
    # CA/PA: back to FM26's player-relative 0x264/0x266 (round 105). The old
    # 0x3A4/0x3A6 pair was person+0x3A4 == the NEXT row's player+0x264, which
    # is why it looked so healthy: a realistic CA<=PA distribution over 300
    # named players, an age-consistent 2031 curve, everything except the right
    # player. Discriminated by the FMRTE RCA model over 121 sampled rows:
    #   CA at person-0x24 (own row)   mean|err| 2.49, signed +0.26, |err|<=5 113/121
    #   CA at person+0x3A4 (next row) mean|err| 26.46, signed -6.45, |err|<=5 24/121
    # Note CA<=PA held 121/121 in BOTH reads, so that invariant never had any
    # discriminating power here and must not be used as evidence again.
    player_ca_offset=0x264,
    player_pa_offset=0x266,
    player_ca_bytes=2,
    attribute_display_bias=2,
    # Historical evidence only. Kept for the audit trail; every offset quoted
    # in this block is person+0x140+FM26, i.e. one row past the target, so the
    # numbers describe the neighbouring player, not this one.
    # Positions / attributes / height (probe rounds 24-55; group order
    # re-verified against 800 live players):
    #   +0x290 15 u8 position ratings, same slot order as FM26 (GK first).
    #          Evidence: 2390/2392 rows are 15 values in 1..20 with exactly one
    #          20; slot 0 at 20 matches the goalkeeping attribute group of
    #          +0x29F in 170/170 cases with no keeper outside it; the FMRTE
    #          position-key table applied to this block agrees with the stored
    #          CA for DC (slot 3) and ST (slot 12).
    #   +0x29F 54 u8 attributes keyed by the internal ids 0x0F..0x44, the same
    #          block FM26 keeps at 0x15F and in the same order: index = id -
    #          0x0F (identity). Mbappe reads finishing 19 (0x11), heading 8
    #          (0x12), technique 19 (0x26), flair 19 (0x29), passing 17 (0x16),
    #          corners 13 (0x2A), long throws 4 (0x2D) and 1-3 on every
    #          goalkeeping id.
    #   +0x36E height in cm (u8).
    #
    # The identity order was confirmed twice over 800 live players, and the
    # older "two groups disagree with the profile" issue is RETRACTED:
    #   - the verified FMRTE RCA weights reproduce the stored CA under the
    #     identity mapping with mean|error| 3.1 and signed +0.45, while any
    #     permutation of the 18 goalkeeping/physical ids costs 27.7..50.7 and a
    #     global +1 index shift costs 37.5. A hill-climb over those 18 ids
    #     found no permutation that generalised to a held-out half.
    #   - the keeper flag (position slot 0 == 20) is independent of the CA
    #     model: for the 76 keepers all of 0x1A..0x1F/0x22/0x24/0x2E..0x30 read
    #     5.6..10.8 display points HIGHER than for the 724 outfielders, while
    #     0x0F/0x10/0x11/0x14/0x15/0x18 read 6.8..7.5 points LOWER. That is
    #     correct attribute behaviour and agrees with the FM26-verified names
    #     already in the tree (0x2F is "rushing out", the sole entry of
    #     GOALKEEPER_HIGH_ATTRIBUTE_IDS). Profile-screen reading is not a
    #     reliable oracle; stored CA and the keeper flag are.
    # Ruled out while investigating: duplicated blocks (3229/3229 signatures
    # unique), whole-row shift (index +-40 finds nothing), byte shift inside
    # the row (no other p in 0x240..0x3B0 validates the position/attribute/CA
    # triple), a UID back-link (no u32 or u64 in 0x288..0x3C8 matches another
    # row's +0x0C uid), and a contiguous run holding his physical values in
    # either raw encoding anywhere in the 0x4000 object window.
    #
    # attribute_display_bias is 2, NOT 0 and NOT 3. Two separate measurements
    # were needed to get here:
    #   * bias 0 is ruled out because FM27 attribute bytes are not restricted to
    #     multiples of 5 (per id the multiple-of-5 share runs from ~20% for
    #     0x20/0x21/0x23/0x25/0x26/0x2A..0x2D/0x31..0x39/0x3D/0x43/0x44 up to
    #     100% for 0x2E/0x2F/0x30/0x38/0x3C/0x3F/0x42), so floor(raw/5)
    #     truncates where a rounded read recovers the display value.
    #   * bias 3 came from an INDIRECT objective (mean|CA error| over the FMRTE
    #     RCA model: 10.900 at 0, 3.780 at 2, 3.100 at 3). That objective has
    #     its own approximation error, so it moved the optimum by one step.
    # Round 114 replaced it with a DIRECT oracle: the game's own save file
    # (rhiever/fmsave, build 27.0.0+2486695, same session and same game date),
    # joined to live memory on person uid == save `unique_id`. Over 14509
    # matched players the transform clamp((raw + 2) // 5) reproduces the save's
    # value for all 52 named attributes with **0 mismatches in 754468
    # comparisons**; bias 3 reproduces only 79..98% of them, because it differs
    # exactly when raw % 5 == 2. The six ids that looked bias-agnostic
    # (0x2E/0x2F/0x30/0x38/0x3C/0x3F) store exact multiples of 5 and score 100%
    # under every candidate, which is why the earlier search could not separate
    # them. Bias 2 also fixes the WRITE path: _raw_attribute_target now writes
    # raw = target*5 - 2, which displays as `target`, instead of target*5 - 3,
    # which displays one point low.
    #
    # Blind spots: no position weights 0x29/0x2E/0x2F/0x30/0x38/0x3B/0x3C/0x3E/
    # 0x3F/0x40/0x41/0x42, so the CA objective could never constrain them. The
    # round-114 oracle named all of them from data: 0x38 dirtiness, 0x3B
    # consistency, 0x3E important matches, 0x3F injury proneness, 0x40
    # versatility. The only two ids it cannot name are 0x27/0x28, which both
    # read 1..100 with 100 as the modal value (23% and 77% of rows) - the two
    # foot ratings the product deliberately does not publish.
    #
    # Row structure, CORRECTED in round 105. The round-25 reading - "FM27 hoists
    # the person block to the front, [person 0x140][player 0x288]" - was wrong
    # by exactly one row. FM27 lays the object out exactly like FM26,
    # [player 0x288][person 0x140], but the table's pointer addresses the
    # PERSON, so person+0x140 is not the player block of this row: it is the
    # player block of the row that starts at person+0x3C8. Everything the
    # hoist rule produced was therefore the neighbour's field, which is why it
    # all looked sane, reproduced CA, and drifted between sessions.
    #   - person-relative fields keep their FM26 offsets unchanged because the
    #     person subobject is still the person subobject: names 0x40/0x50/0x58,
    #     nationality 0x68, hidden attributes 0x70, relationships 0x78, DOB
    #     0x88, languages 0xF0, previous club 0x108, flags 0x18, international
    #     counters at 0x134/0x136 (u16) and 0x138/0x139 (u8).
    #   - player-relative fields keep their FM26 offsets unchanged too, because
    #     the base now moves instead: positions 0x150, attributes 0x15F, height
    #     0x22E, CA/PA 0x264/0x266, injury list 0xF8, season-stats root 0x108,
    #     career stats 0x110, sharpness/fatigue/fitness 0x258/0x25A/0x25C,
    #     world reputation 0x262, morale 0x26C.
    # NOTE ON SCOPE: those two bullets describe where the FIELDS live in the
    # object, not which ones this layout declares. Four of them are deliberately
    # left unset - player_career_stats_offset, person_relationships_offset,
    # person_languages_offset, person_previous_club_offset - because "the qword
    # lands in the heap" is much weaker evidence than a DOB that decodes to a
    # real date, and none of them is needed to register FM27. The measurements
    # for them are still in the audit above so a future attempt does not have to
    # start over.
    #   - the player subobject vtable is 0x4D27228 (hybrid: 0x4F75A08); it shows
    #     up at person-0x288 (person-0x380) in 100% (99%) of the respective
    #     rows and is what the reader must accept once it is handed a player
    #     pointer rather than a person pointer.
    # Full-population audit (rounds 109/111): all 17268 ACTUAL_PLAYER rows, no
    # sampling, read at player = person-0x288. Each field is scored on its own
    # acceptance test rather than on "looks reasonable":
    #   positions 0x150        15 bytes in 1..20, peak 20      17268/17268
    #   attributes 0x15F       54 bytes in 1..100, no zeros    17268/17268
    #   height 0x22E           150..210                         17268/17268
    #   CA/PA 0x264/0x266      1..200 with CA<=PA               17268/17268
    #   sharpness 0x258        0..10000                         17268/17268
    #   fitness 0x25C          0..10000                         17268/17268
    #   fatigue 0x25A          0..10000                         14242/17268
    #   world reputation 0x262 0..10000                         17268/17268
    #   morale 0x26C           u8 in 1..20, 20 values present   17268/17268
    #   intl caps/goals        goals<=caps<=300                 17267/17268
    #   u21 caps/goals (u8)    goals<=caps                      17251/17268
    #   injury list 0xF8       heap 6797 / null 10471, no junk
    #   season root 0x108      heap pointer, never null         17268/17268
    #   career stats 0x110     heap 15262 / null 2006
    #   person fields          first/last name, nationality, DOB and
    #                          relationships 99..100% non-null; languages 48.7%
    #                          and previous club 50.0% (both genuinely optional)
    # Morale is the one field where the READ WIDTH decides the result: the u16
    # at 0x26C is inside 1..20 for only 24.18% of rows, while the consumer's u8
    # read is 17268/17268. Never "fix" it by widening.
    # A three-version diff (round 111) puts the whole block in context: FM27
    # shares all 34 of its player/person offsets with FM26 and NONE with FM24
    # (FM24 keeps positions/attributes at 0x208/0x217, height at 0x14E and
    # CA/PA at 0x200/0x202), so the row really is FM26's layout behind an
    # FM26-sized person prefix, and the round-25 hoist rule left no stray number
    # anywhere in the object. The only two deliberate divergences are fatigue
    # (unset) and attribute_display_bias (2 here, 0 in both other versions).
    # Archive cross-check (round 114). Everything above reads the LIVE process;
    # this block compares it against the game's own SAVE FILE, parsed by
    # rhiever/fmsave from the .fm container - a different artifact, a different
    # code path, and therefore an independent oracle rather than a second
    # opinion from the same source. Build 27.0.0+2486695, database 27.0.0+0,
    # game date 2036-02-10 (identical to the running session, so nothing moved
    # in between).
    #   join key    memory person uid == save `unique_id`. fmsave's own `uid` is
    #               a different handle for the same row (216444 vs 222724 for
    #               Joe Gelhardt) and name joins find nothing, because the save
    #               stores display names (Pedri, Vitinha, Mezenga). The join is
    #               verified, not assumed: live names match the save's on
    #               14365/14509 rows (99.01%) against 0.00% for a deliberately
    #               wrong partner. The 144 disagreements are all nicknames.
    #   height      14509/14509 exact, delta histogram is the single bucket
    #               0 -> 14509
    #   CA          14509/14509 exact
    #   PA          14509/14509 exact
    #   attributes  52 named ids x 14509 players = 754468 comparisons, 0
    #               mismatches under clamp((raw + 2) // 5)
    #   controls    person+0x36E height 3.69% exact (mean|d| 9.01) and
    #               person+0x3A4 CA 1.52% exact (mean|d| 23.98) - the round-25
    #               hoist addresses lose by a factor of 27, so this test can
    #               tell a right offset from a plausible neighbour.
    #   positions   the save lists 1..3 natural positions per player from a
    #               13-label set (no GK/SW). Naming each of the 15 memory slots
    #               by the label whose presence best predicts its rating
    #               recovers DL/DC/DR/DM/ML/MR/AML/AMC/AMR/STC/WBL/WBR on EXACTLY
    #               the shipped indices, with gaps of 10.6..13.6 rating points;
    #               GK (0) and SW (1) get no match because the save omits them.
    #               The set-level comparison is NOT usable: fmsave's list is an
    #               "accomplished" list (mean rating when present 15.3) not a
    #               threshold on the matrix, and 9998 of its rows have no list.
    # Names are read through TWO dereferences for 0x50/0x58/0x60 but ONE for
    # 0x40: person+0x50 -> entry -> string -> <u32 len><utf8>. A round-113 scan
    # used one dereference for all four, read the entry itself as if it were a
    # string header, and concluded that 0x50/0x58 held "inline binary" with zero
    # decodable names. That conclusion is RETRACTED; with the correct chain the
    # coverage is 17267/17268 first names, 17265/17268 last names and
    # 1862/17268 common names, which is what _build_person_search_index already
    # does at database_index.py:913-921.
    # Round 118 pushed the same join through the fields that range tests and the
    # CA model could not separate, and every one of them came back exact:
    #   positions     all 15 slots at 0x150 exact for 14509/14509, max |delta| 0,
    #                 including GK and SW. This supersedes the round-114 label
    #                 derivation, and it explains why the set comparison failed
    #                 there: that dump used the save's ACCOMPLISHED tuple, while
    #                 the save's `positions` object is the same 15 ratings the
    #                 matrix holds - same slots, same values, same scale.
    #   condition     0x258 = raw_match_sharpness 100.00%, 0x25C = raw_condition
    #                 100.00%, and 0x25A matches neither (0.00% exact, 0.00%
    #                 within +-1), so the unset fatigue field is now ruled out by
    #                 positive evidence rather than by a failed range test.
    #   reputation    home 0x25E, current 0x260, world 0x262: each is 100% against
    #                 its own save field and <= 18.22% against the others.
    #   feet          the two attribute ids the save cannot name are the feet:
    #                 id 0x27 == raw_left_foot and id 0x28 == raw_right_foot, both
    #                 100.00%, on the same 1..100 scale the display transform
    #                 expects (100 -> 20, 50 -> 10).
    #   personality   person+0x70..0x77 is an 8x8 perfect diagonal against the
    #                 save's adaptability, ambition, loyalty, pressure,
    #                 professionalism, sportsmanship, temperament and controversy,
    #                 which is exactly PERSON_HIDDEN_ATTRIBUTE_NAMES' order.
    #   traits        the u64 at person+0xC0 equals the save's trait_bits for
    #                 14509/14509 (the 59.4% that neighbours score is the share
    #                 of players whose bitfield is zero - a built-in control).
    #   asking price  the u32 at 0x234 equals the save's transfer_value for
    #                 14509/14509 while 0x22C/0x230/0x238/0x23C are all 0.00%,
    #                 which confirms the value the product hardcodes for every
    #                 non-FM24 build.
    # Limits of the oracle, stated up front: fmsave registers a single FM27
    # build and marks every FM27 field unconfirmed, so it can flag anomalies but
    # never proves a layout; 2773 of its 17631 rows carry unique_id 0 and cannot
    # be joined; and 14509/17268 (84%) of live players are covered, so the
    # untouched quarter is still only backed by the population audit above.
    # International counters (rounds 112/113) are the one place where an offset
    # can be NAMED by behaviour instead of by range, and the answer is split:
    #   * 0x3BC/0x3BE (u16 caps, u16 goals) are the senior caps/goals pair.
    #     Caps are 0 for 100% of 16-year-olds, 96% of 18-year-olds, 43% of
    #     34-year-olds and 11% of 38-year-olds; goals<=caps holds 17267/17268
    #     and caps==0 implies goals==0 in 17268/17268. That age gradient is not
    #     something a wrong offset reproduces by accident.
    #   * 0x3C0/0x3C1, which is where FM26 keeps its U21 pair, are NOT U21
    #     counters: 42% of players over 23 read non-zero there and the mean
    #     RISES with age (1.87 under 20 vs 5.26 at 24+), the exact opposite of a
    #     U21 counter. A scan of every u8 and u16 in player 0x3A0..0x3C7 found no
    #     candidate that is non-zero for 17..22-year-olds and zero afterwards,
    #     so FM27 does not keep the U21 pair in the person block at all.
    # Round 119 tried to settle the pair from the save instead, and that route
    # is closed for reasons worth recording so nobody retries it.
    # player_season_history DOES hold 17497 rows with kind INTERNATIONAL, but:
    #   * 15.9% of player-seasons repeat one identical (starts, subs, goals,
    #     minutes) line under more than one kind, so a kind is not a partition;
    #   * a mid-season transfer splits a season into two team blocks that repeat
    #     the same numbers (Donnarumma 2034: Manchester City and Aston Villa,
    #     both 10/0/0/930), and the "missing reference" sentinel rows repeat the
    #     neighbouring season's line verbatim;
    #   * the team reference on those rows resolves to the player's CLUB, so a
    #     row cannot be attributed to a national team at all;
    #   * summing over-counts the young (a 2011-born player shows 61 against 36
    #     live) and under-counts anyone whose caps predate the save's first
    #     season, so both signs of residual appear at once.
    # Joined uid -> unique_id -> memory uid over 2881 players, the sum equals
    # 0x3BC for 785 (27.2%, null model 2.7%) and memory >= the sum for 88.0% -
    # real signal, but a career counter must satisfy that for all of them and
    # 347 rows break the one-sided test. The age profile of the rows (mean 2.0
    # appearances at 16 rising to 7.1 at 30) says they ARE senior
    # internationals, so the disagreement belongs to the archive.
    # Conclusion: the save is an anomaly detector for this field, not an oracle,
    # and the quartet stays unset rather than being half-published on "looks
    # about right". The one route not yet tried is save.player_match_stats(),
    # which holds one row per match instead of per season and therefore cannot
    # double count: summing a player's international matches would give a
    # clean lower bound, and "live caps >= that bound" should then hold for
    # every player. It carries its own limit - fmsave documents that match
    # histories may be truncated - but truncation only ever lowers the bound,
    # which is the safe direction for a one-sided test. It needs a way to tell
    # an international competition from a club one, and the save stores no
    # competition names, so that is where the next attempt starts.
    # DECISION (round 120): the senior pair is now declared and the U21 pair is
    # declared absent, instead of withholding all four. The old reasoning - a
    # reader that needs all four nothing to show if two do not exist - was a
    # limitation of the reader, not a fact about the build: FM27 keeps no U21
    # counter in this object (the scan above covers every u8 and u16 in
    # 0x3A0..0x3C7), so demanding two offsets that cannot exist published
    # nothing at all. player_u21_international_counters_absent records the
    # structural claim and read_fm26_international_stats then reports the
    # senior counters with the U21 values as None, which the profile renders as
    # "-" rather than 0. The senior pair itself stays behaviour-named rather
    # than oracle-confirmed: see the round-119 note above, which is why this
    # declaration ships with a comment instead of a claim of verification.
    # Measured over 120 rows against the OLD (shifted) base, so these numbers
    # describe the neighbouring row and are kept only as history: morale 1..20,
    # sharpness 3500..10000, fitness 6100..10000, world reputation 1925..8676,
    # international caps 0..109 with goals 0..87 and u21 0..17; 0x13C..0x13F
    # zero in 120/120 rows. Full name 0x40 is null for 63/120 rows and the
    # injury list is null for 70/120, both nullable pointers rather than errors.
    # Human manager (probe rounds 26-28): the existing FM26 human-manager root
    # AOB matches FM27 on disk at exactly 0xEB7253 and resolves to global RVA
    # 0x56F3008. That global holds a singleton vector (1 element), so the root
    # is confirmed for 27.0. The element's vtable is 0x4CC086C: that is the
    # human_manager_vtable_rvas value for FM27, with
    # human_manager_vtable_on_person=False (the vtable sits on the manager
    # object, exactly as in FM26). The object references its team at +0x60
    # (vtable 0x4CE1108 = team_vtable_rva), a nation at +0x4A8 (vtable
    # 0x4CDF658) and its club at +0x548 (vtable 0x4CDA1F8 = club_vtable_rva),
    # which confirms the singleton really is the human manager.
    # manager_person_offset is 0x440; FM26's 0x450 does not transfer (+0x450
    # holds a scalar here, and name-based scanning finds no person at any
    # offset). It was resolved with a constraint that needs no name strings: the
    # consumer reads u32(manager + manager_person_offset + 0x0C) as the manager
    # entity UID, so the offset must simultaneously present a module vtable at
    # its start and a genuine person UID at +0x0C. All 72257 person-table UIDs
    # are distinct, which makes that membership test strong. Exactly one offset
    # of the object satisfies both: +0x440 has vtable 0x4CC08D8 (the same
    # multiple-inheritance sub-vtable family as the primary 0x4CC086C) and
    # u32(+0x44C) = 2002075149, a UID that is present in the person table. The
    # other in-module pointers (+0, +8, +0x468, +0x470) all fail the UID test.
    # Still unverified: savegame_root_rvas (staff is resolved above).
    # Value search fails on it: the consumer signature is only "root =
    # ptr(base+rva) with two non-zero u32 at +0xB8/+0xBC", which produced 1519
    # heap-root candidates and even matched the fixture-pool header itself, and
    # the FM26-relative offsets (fixture-0x1190 / fixture+0xD860) do not
    # transfer. A disk-side RTTI walk (name -> _TypeDescriptor ->
    # _RTTICompleteObjectLocator -> vtable[-1]) gives the save classes by name:
    # SAVE_SYSTEM 0x4CBBCB0/0x4CBBCC8, GAME_GAME_DATABASE_RECORD
    # 0x4C9E838/0x4C9E948, GAME_SAVE_PROVIDER_LOCAL 0x4B46B18. Requiring a
    # save-class vtable on the target collapses .sdata to exactly two slots:
    # 0x56F1BC8 (SAVE_SYSTEM) and 0x56F8358 (save provider). Both are rejected:
    #   - 0x56F1BC8: +0xB8 holds a heap POINTER (the u32 pair read there is the
    #     two halves of that pointer, high half 0x272), so it is not the scalar
    #     id/nonce field the FM26 contract reads.
    #   - 0x56F8358: +0xB8 does read as a scalar pair, but the same
    #     "non-pointer u32 pair between two heap pointers" shape recurs 12 times
    #     across the object at a regular stride (0x38/0x58/0xB8/0xD8/0x218/...)
    #     with near-identical values, so it is one element of a repeated record
    #     array rather than a unique id/nonce field.
    # Resolving it needs a semantic anchor (the save name, or a save id obtained
    # independently), not another value search.
    # Exhausted: value search, FM26-relative offsets, every save class (all
    # eight RTTI names, including the FM27-only SAVE_LOAD_PROVIDER@sicomps,
    # which has no resolvable vtable), every module data section, the
    # heap-neighbourhood anchor, and shared-identity pairing. FM27 has exactly
    # two module-data globals pointing at save-class objects and both are
    # rejected, so the id/nonce pair is not at +0xB8/+0xBC on any rooted save
    # object.
    # This is why the field cannot be settled as an RVA: SAVEGAME_ID_OFFSET
    # (0xB8) and SESSION_NONCE_OFFSET (0xBC) are module-level constants in
    # save_memory, NOT GameLayout fields, so savegame_root_rvas cannot express a
    # moved pair. Supporting FM27's save identity would require making those two
    # offsets per-layout, with values that are not yet known. It is not a
    # missing RVA.
    # Registration impact: save_memory.read_savegame_identity no longer falls
    # back to FM26's SAVEGAME_ROOT_RVAS for a layout that declares none, so an
    # empty tuple now fails closed and returns no identity. Callers already
    # handle an absent identity - preview_cup_odds leaves stable_scope as None
    # and passes it guarded - so FM27 does not need this field in order to be
    # registered. It stays empty rather than carrying a guess, because a wrong
    # root would be published as a save id and corrupt the scope keys built
    # from it.
    # player_fatigue 0x25A is the one predicted field whose range does not match
    # a clean 0..10000 scale (27/120 rows exceed 10000, one reads 65535), so it
    # stays a hypothesis. (Measured at person+0x39A, which round 105 showed is
    # this same 0x25A field of the next row.)
    #
    # Applied after the owner authorized live reads. Round 105 replaced the
    # "hoist" story: CA/PA, the person fields and positions/attributes/height
    # all use FM26's player-relative offsets, and the one thing that changed is
    # the base - the table hands out a person pointer, not a player pointer.
    # RESOLVED (round 105). This block used to be titled "OPEN, not resolved",
    # because the owner's screenshot for UID 2002085134 "Can Erdem" showed a
    # FORWARD, 178 cm, while the row read GK / 196 at +0x290 / +0x36E. The
    # answer is the row geometry, not the fields: +0x290 and +0x36E were the
    # player block of the row at person+0x3C8, so every "self-consistent"
    # observation below was about the neighbour.
    # Kept as the audit trail, with the reading it actually supports:
    #   - "height at +0x36E is within 150..210 in 4000/4000 rows with a realistic
    #     distribution" - true of any player block, including the neighbour's;
    #   - "the +0x290 block has a peak of exactly 20 in 4000/4000 rows" - same;
    #   - "178 appears nowhere in the 968-byte row" - correct, and the reason is
    #     that his height sits BEFORE the person pointer (person-0x5A), not that
    #     the field is missing;
    #   - "no duplicate of this person" - correct and still true; the row is his,
    #     only the fields read out of it belonged to the next row;
    #   - "no overlapping rows: consecutive objects are exactly 0x3C8 apart" -
    #     correct, and that is exactly why a fixed +0x140 shift always lands on a
    #     neighbouring player instead of on garbage;
    #   - "the attribute block is not garbage: RCA reproduces CA 96 within 8 at
    #     bias 3, self-consistent with a goalkeeper" - the strongest form of the
    #     trap: CA and the attribute block were BOTH the neighbour's, so the
    #     model agreed with itself;
    #   - "the same offsets read correct values for other rows (2002088995 = ST
    #     20/177, 2002095195 = MC 20/184, 2002085131 = ST 20/178)" - those rows
    #     are 0x3C8 apart, so each one's "+0x290 neighbour" was another real
    #     player with another plausible profile.
    # Object identity was always certain (u32(+0x0C) = the UID, names, nation
    # 799, DOB 2012-day168); it was the field base that was wrong.
    # FINAL identity proof (rounds 75-76): the object IS the person the UI shows.
    # UID 2002085134, name pair "Can"/"Erdem" (the UI's 詹·埃尔登), nation 799
    # (Turkey), DOB 2012-day168 (= 2012-06-16) and team 130343 巴沙克舒希 all
    # match the screenshots. And the attribute VALUES prove the record is a
    # coherent goalkeeper, not garbage: with real names, handling 64, aerial
    # reach 68, one-on-one 75, rushing out 67, command of area 59, reflexes 57
    # (mean 62.5) against finishing 5, crossing 5, flair 10, dribbling 15
    # (mean 20.9) - a forward cannot carry that combination. The reference
    # striker Erdem Cakir shows the mirror image (gk mean 11.9 vs outfield 60.9).
    # So both records are internally valid; the game simply renders this player
    # as a forward/178 while storing a goalkeeper record for him.
    # Population check (17267 rows with a decisive position peak): only 37 rows
    # (0.21%) are internally contradictory between position slot 0 and the
    # goalkeeping-vs-outfield attribute profile, and every one of those is a DC
    # with both means near 10 (differences of 1-3, i.e. noise). This player is
    # NOT among them: his slot 0 agrees with a genuine goalkeeper profile
    # (gk 62.5 vs outfield 20.9), so his stored row is entirely self-consistent.
    # Also ruled out: a per-UID "display copy" inside module data - the UID does
    # not appear in .sdata/.data1/.tls at all, and no surname containing "Erden"
    # exists in the save (the Chinese 埃尔登 transliteration is this person).
    # Final sweep (round 83): every one of the 24 usable database tables was
    # walked for rows named *Erdem*/*Erden*. Only 5 rows match and ALL of them
    # live in the person table (slot 0x68); no other table holds a "Can Erdem"
    # or "Can Erden" row, so no forward/178 record of this person exists
    # anywhere in the database.
    # The save itself is not clean either, which is worth knowing before trusting
    # any single sample: rows with height 0 / CA 0 (Nandin-Erdene) and CA 3084 /
    # PA 3852 (Tuguldur Bat-Erdene, beyond the 200 cap) are present.
    # The CA-residual statistics that once "confirmed" the hoist rule were
    # misleading: CA reproduces because the CA/PA offsets are genuinely correct,
    # which says nothing about where positions/attributes live.
    #
    # RESOLVED (round 105). Both earlier verdicts were right to distrust the
    # numbers and wrong about the cause. The three offsets were never wrong
    # *fields*; they were one row off. person+0x290 / +0x29F / +0x36E are
    # player+0x150 / +0x15F / +0x22E of the row at person+0x3C8. That single
    # mistake explains every symptom the two retractions documented:
    #   - "the values look plausible but belong to nobody": they belong to the
    #     neighbouring player, who is a real player with real attributes;
    #   - "the keeper-height gap of +8.68 cm validates itself": it measured the
    #     neighbour's keeper flag against the neighbour's height, so it was
    #     never circular in the statistical sense - it was simply about the
    #     wrong man;
    #   - "no byte in 184..186 exists anywhere in Osimhen's row": true, because
    #     his height lives before the person pointer, not after it;
    #   - "the values drift between sessions": the neighbour changes whenever
    #     the arena is rebuilt, so cross-session comparisons of these offsets
    #     were meaningless (Aasgaard 186->165, Erdem 196->179).
    # The owner's two screenshots agree with the corrected offsets, not with the
    # old ones: Goto's UI value is MC/179 and his own player block reads a
    # 179 cm MC-range field, while the +0x36E neighbour read 188.
    player_positions_offset=0x150,
    player_attributes_offset=0x15F,
    player_height_offset=0x22E,
    # Person-relative, so the person subobject keeps the FM26 values.
    #
    # flags 0x18 is a packed bitfield, NOT a pointer: the qwords read
    # 0x00000004104002xx / 0x00000004304002xx / 0x0000000510400200 across the
    # whole population. Bit 0x1000, which the consumer decodes as "female", is
    # set in 398/17268 = 2.3% of rows - a plausible women's-football share
    # rather than a coin flip, so the slot is real. (Round 109 first flagged it
    # as a heap pointer; that was its own bucketing letting a small integer into
    # the pointer range, not a finding.)
    person_flags_offset=0x18,
    # Hidden attributes 0x70 is likewise a packed bitfield, read as 8 raw bytes:
    # never null, never a pointer, values in the 0x...02xx range.
    person_hidden_attributes_offset=0x70,
    # FM26 values, restored with the base. Its companions (total_slot /
    # block_size) are deliberately NOT set: they were never measured, and
    # player_details_fm26 is hard-gated on key == "fm26", so this field is inert
    # for this layout.
    player_season_stats_root_offset=0x108,
    # Player-relative companions of the same base. They are NOT optional:
    # _player reads them with a bare `address + offset` in the
    # roster_summary=None path, so leaving any of them None raises
    # "unsupported operand type(s) for +: 'int' and 'NoneType'" (observed live on
    # /api/world-players/detail before these were set).
    #
    # 0x262 sits in a run of three adjacent u16s (0x25E, 0x260, 0x262) that are
    # ALL 0..10000 in 17268/17268 rows, so a range test cannot name any of them.
    # Round 114 named 0x262 from the FM24/FM26 slot order plus the CA-correlation
    # gradient (+0.989 / +0.980 / +0.838); round 118 then settled the whole run
    # with the save, which carries home/current/world separately: over 14509
    # matched players each slot is exact for 14509/14509 against its own field
    # and only 18.22% / 18.22% / 0.05% against the others. So 0x25E is home,
    # 0x260 is current and 0x262 is world, and the three are now declared.
    # Do not reuse the old shortcut "world should be the biggest": the means run
    # 3927 / 4107 / 2527, i.e. world is the SMALLEST of the three.
    player_home_reputation_offset=0x25E,
    player_current_reputation_offset=0x260,
    player_world_reputation_offset=0x262,
    player_sharpness_offset=0x258,
    player_fitness_offset=0x25C,
    player_morale_offset=0x26C,
    player_injury_list_offset=0xF8,
    # Round 120: the senior pair is published, the U21 pair is declared absent
    # (see the round-112/119 note above). Widths stay at the FM26 values.
    player_international_appearances_offset=0x3BC,
    player_international_goals_offset=0x3BE,
    player_u21_international_counters_absent=True,
    player_international_counter_width=2,
    # player_fatigue_offset is DELIBERATELY unset. The measurement that killed
    # it is still valid after the round-105 base fix, it just names the field
    # differently: the old read at person+0x39A is this row's 0x25A one row
    # along, and it is not a condition value - sampling 300 live players gives
    # only 192/300 inside 0..10000 (median 90, max 65530), while its neighbours
    # 0x258 (sharpness) and 0x25C (fitness) are 300/300 inside 0..10000. So
    # FM27's 0x25A is not fatigue either. Round 109 re-ran the same test over
    # all 17268 rows instead of 300: 14242/17268 (82.48%) inside 0..10000,
    # median 62, max 65535, against 17268/17268 for both neighbours, so the
    # verdict does not depend on the sample. It must not be guessed because
    # adjust_player_training_condition WRITES u16(address + player_fatigue_offset)
    # and clamps to 0..10000, so a wrong offset would corrupt unrelated bytes.
    # Round 118 added positive evidence instead of another range test: the save
    # carries raw_condition and raw_match_sharpness, and 0x25A matches NEITHER
    # of them for a single player (0.00% exact and 0.00% within +-1, over 14509
    # rows), while 0x258 = sharpness and 0x25C = condition are each 100.00%.
    # So 0x25A is not a condition field at all in FM27, and the write path stays
    # closed. Left None, it raises "当前游戏版本尚未完整映射球员身体状态" and the
    # read path reports None.
    player_fatigue_offset=None,
    # Resolved by the vtable + person-UID constraint (see the note above).
    # The person sub-object at +0x440 is confirmed twice over: u32(+0x44C) = the
    # manager UID and the nested name strings at +0x490/+0x498 read his name.
    #
    # managed_team: the FM26 contract route does not exist in FM27, and that is
    # expected rather than a missing offset - a human manager is not a club
    # employee with a player-style contract. Evidence (read-only):
    #   - the manager row's contract slots +0xA8/+0xB0/+0xC8 are all 0, while a
    #     player row has values at +0xA8 and +0xC8;
    #   - the consumer's acceptance test (ptr(c+0x08)==person and
    #     ptr(c+0x10)>0) yields 0 candidates across person+0x000..0x400,
    #     manager+0x000..0x800, a +/-0x4000 window around person, AND all 24
    #     database table slots;
    #   - no team in the 67033-row team table references the manager object or
    #     his person;
    #   - team+0x80 (TEAM_MANAGER) holds 0x2737EDEDC40, whose vtable_rva is
    #     0x4D8BE08 (adjacent to the db::ACTUAL_NON_PLAYER vtable 0x4D8BE78) and
    #     which carries no name, no person UID, and no pointer to the manager,
    #     so it is not a manager back-reference and the FM26 equality check
    #     `team+TEAM_MANAGER == manager` does not hold here.
    # What DOES work: manager+0x60 points at the managed team directly. Resolving
    # it through Reader.team() returns id=406, team_type=club, club
    # 0x27372CF9A00, competition 130931, stadium, morale 16 - the full payload
    # managed_team needs. It reads identically twice and no other person-table
    # row carries that value.
    # Not written yet: there is only ONE human manager in this save, so a single
    # sample cannot establish that +0x60 is the general layout offset rather than
    # a coincidence, and the goal forbids writing an offset without cross
    # validation. The field is therefore left out; the session keeps reporting
    # source "human_manager_without_team", which is the honest state.
    manager_person_offset=0x440,
    # manager+0x60 points at the managed team directly. Verified on the live
    # 27.0 session with two independent pointers agreeing on one club:
    #   manager+0x60  -> team 406 (北京国安足球俱乐部, 中超联赛, 工人体育场),
    #                    club_address 0x27372CF9A00
    #   manager+0x548 -> club 0x27372CF9A00 (the same object)
    # and the club object itself resolves to vtable 0x4CDA1F8 (== club_vtable_rva),
    # uid 406 (== the team id) and name "北京国安足球俱乐部". The pointer reads
    # identically twice and no other person-table row carries that value.
    # This is the only route in FM27: the manager has no player-style contract
    # (see the managed_team note above).
    human_manager_team_offset=0x60,
)


SUPPORTED_LAYOUTS = (
    FM27_LAYOUT, FM26_LAYOUT, FM24_LAYOUT, FM24_240_LAYOUT, FM24_241_LAYOUT,
    FM24_EPIC_LAYOUT,
)
LAYOUT_BY_HASH = {layout.executable_sha256: layout for layout in SUPPORTED_LAYOUTS}


@lru_cache(maxsize=8)
def _hash_executable(path: str, size: int, modified_ns: int) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def executable_hash(path: str) -> str:
    details = Path(path).stat()
    return _hash_executable(str(Path(path).resolve()), details.st_size, details.st_mtime_ns)


def layout_for_executable(path: str) -> GameLayout | None:
    if not path:
        return None
    try:
        return LAYOUT_BY_HASH.get(executable_hash(path))
    except OSError:
        return None


def _pe_sections(raw: mmap.mmap) -> list[tuple[int, int, int, int]]:
    pe_offset = struct.unpack_from("<I", raw, 0x3C)[0]
    if raw[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise ValueError("invalid PE image")
    count = struct.unpack_from("<H", raw, pe_offset + 6)[0]
    optional_size = struct.unpack_from("<H", raw, pe_offset + 20)[0]
    table = pe_offset + 24 + optional_size
    return [
        struct.unpack_from("<IIII", raw, table + index * 40 + 8)
        for index in range(count)
    ]


def _pe_image_base(raw: mmap.mmap) -> int:
    pe_offset = struct.unpack_from("<I", raw, 0x3C)[0]
    return struct.unpack_from("<Q", raw, pe_offset + 24 + 24)[0]


def _read_rva(
    raw: mmap.mmap, sections: list[tuple[int, int, int, int]], rva: int, size: int,
) -> bytes | None:
    for virtual_size, virtual_rva, raw_size, raw_offset in sections:
        relative = rva - virtual_rva
        if 0 <= relative and relative + size <= raw_size:
            return raw[raw_offset + relative:raw_offset + relative + size]
    return None


def _occurrences(
    raw: mmap.mmap, sections: list[tuple[int, int, int, int]], needle: bytes,
):
    for _virtual_size, virtual_rva, raw_size, raw_offset in sections:
        cursor, end = raw_offset, min(len(raw), raw_offset + raw_size)
        while True:
            cursor = raw.find(needle, cursor, end)
            if cursor < 0:
                break
            yield virtual_rva + cursor - raw_offset
            cursor += 1


def _pattern_occurrences(
    raw: mmap.mmap, sections: list[tuple[int, int, int, int]],
    pattern: tuple[int | None, ...],
) -> list[int]:
    runs: list[tuple[int, bytes]] = []
    start = 0
    while start < len(pattern):
        while start < len(pattern) and pattern[start] is None:
            start += 1
        end = start
        while end < len(pattern) and pattern[end] is not None:
            end += 1
        if end > start:
            runs.append((start, bytes(int(value) for value in pattern[start:end])))
        start = end
    if not runs:
        return []
    anchor, needle = max(runs, key=lambda row: len(row[1]))
    rows: list[int] = []
    for _virtual_size, virtual_rva, raw_size, raw_offset in sections:
        cursor, end = raw_offset, min(len(raw), raw_offset + raw_size)
        while True:
            cursor = raw.find(needle, cursor, end)
            if cursor < 0:
                break
            start = cursor - anchor
            if raw_offset <= start and start + len(pattern) <= end and all(
                value is None or raw[start + index] == value
                for index, value in enumerate(pattern)
            ):
                rows.append(virtual_rva + start - raw_offset)
            cursor += 1
    return rows


def _resolve_fm26_game_date_rva(
    raw: Any, sections: list[tuple[int, int, int, int]],
) -> int:
    """Resolve FM26's current-date global from its RIP-relative load."""
    hits = _pattern_occurrences(raw, sections, FM26_GAME_DATE_PATTERN)
    if len(hits) != 1:
        raise RuntimeError(
            f"FM26 game-date signature matched {len(hits)} locations"
        )
    instruction_rva = hits[0] + 14
    instruction = _read_rva(raw, sections, instruction_rva, 6)
    if not instruction or len(instruction) != 6:
        raise RuntimeError("FM26 game-date instruction is unreadable")
    return instruction_rva + 6 + struct.unpack_from("<i", instruction, 2)[0]


def _resolve_fm26_game_date_rva_from_path(image_path: str) -> int:
    with Path(image_path).open("rb") as stream, mmap.mmap(
        stream.fileno(), 0, access=mmap.ACCESS_READ,
    ) as raw:
        return _resolve_fm26_game_date_rva(raw, _pe_sections(raw))


@lru_cache(maxsize=8)
def _cached_fm26_game_date_rva(
    image_path: str, file_size: int, modified_ns: int, module_sha256: str,
) -> int:
    # File identity is part of the key. The absolute process address is never
    # cached here; callers add the current session's module base each time.
    del file_size, modified_ns, module_sha256
    return _resolve_fm26_game_date_rva_from_path(image_path)


def resolve_fm26_game_date_rva(
    module_path: str, module_sha256: str = "",
) -> int:
    path = Path(module_path).resolve()
    details = path.stat()
    return _cached_fm26_game_date_rva(
        str(path), details.st_size, details.st_mtime_ns,
        str(module_sha256 or "").upper(),
    )


def _validate_fm26_runtime_game_date(
    process: Any, module_base: int, module_size: int, game_date_rva: int,
) -> None:
    from fm_collector.win32 import read_process_memory

    if not 0 <= game_date_rva <= module_size - 4:
        raise RuntimeError("FM26 game-date target is outside game_plugin.dll")
    address = module_base + game_date_rva
    first = read_process_memory(process, address, 4)
    second = read_process_memory(process, address, 4)
    if not first or len(first) != 4 or not second or len(second) != 4:
        raise RuntimeError("FM26 current game date is unreadable")
    if first != second:
        raise RuntimeError("FM26 current game date changed during validation")
    date_code = struct.unpack("<I", second)[0]
    year, day = date_code >> 16, date_code & 0x1FF
    if (
        not 1900 <= year <= 2200
        or not 1 <= day <= 366
        or (year == 1900 and day == 1)
    ):
        raise RuntimeError("FM26 current game-date signature failed validation")


def resolve_fm26_steam_layout(
    pid: int, module_base: int, module_size: int, module_path: str,
) -> GameLayout:
    """Bind the verified Steam profile to its dynamically resolved date RVA."""
    from fm_collector.win32 import open_process

    try:
        module_hash = executable_hash(module_path)
        game_date_rva = resolve_fm26_game_date_rva(module_path, module_hash)
    except OSError as error:
        raise RuntimeError(
            "FM26 Steam game_plugin.dll cannot be read for identity resolution"
        ) from error
    if module_hash != FM26_STEAM_PLUGIN_SHA256:
        raise RuntimeError(
            "FM26 Steam game_plugin.dll build mismatch; dynamically resolved "
            f"game-date RVA 0x{game_date_rva:X}, but the remaining 26.3.2 "
            f"layout is not verified for plugin SHA256 {module_hash}"
        )
    expected_rva = int(FM26_LAYOUT.game_date_rva or 0)
    if game_date_rva != expected_rva:
        raise RuntimeError(
            "FM26 Steam verified plugin resolved an unexpected game-date RVA: "
            f"0x{game_date_rva:X} (expected 0x{expected_rva:X})"
        )
    with open_process(pid) as process:
        _validate_fm26_runtime_game_date(
            process, module_base, module_size, game_date_rva,
        )
    return replace(FM26_LAYOUT, game_date_rva=game_date_rva)


def _rtti_vtables(
    raw: mmap.mmap, sections: list[tuple[int, int, int, int]], name: str,
) -> list[int]:
    preferred_base = _pe_image_base(raw)
    vtables: set[int] = set()
    # FM's large .rodata section contains assets rather than MSVC RTTI. Avoid
    # rescanning hundreds of megabytes for every required class name.
    rtti_sections = [section for section in sections if section[2] <= 128 * 1024 * 1024]
    for name_rva in _occurrences(raw, rtti_sections, name.encode("ascii") + b"\0"):
        type_rva = name_rva - 16
        for reference_rva in _occurrences(raw, rtti_sections, struct.pack("<I", type_rva)):
            locator_rva = reference_rva - 12
            locator = _read_rva(raw, sections, locator_rva, 24)
            if not locator:
                continue
            signature, _offset, _cd, found_type, _class, self_rva = struct.unpack(
                "<IIIIII", locator,
            )
            if signature != 1 or found_type != type_rva or self_rva != locator_rva:
                continue
            pointer = struct.pack("<Q", preferred_base + locator_rva)
            for pointer_rva in _occurrences(raw, rtti_sections, pointer):
                vtables.add(pointer_rva + 8)
    return sorted(vtables)


def _validate_fm26_xgp_core_rtti(resolved: dict[str, list[int]]) -> None:
    required_single = (
        "fixture", "fixture_result", "season_result", "team", "national_team",
        "nation", "club", "competition",
    )
    if any(len(resolved[key]) != 1 for key in required_single):
        raise RuntimeError("XGP RTTI layout is incomplete or ambiguous")


def _alternate_plugin_path(process_path: str, module_path: str) -> Path | None:
    candidates = [
        Path(module_path),
        Path(process_path).parent / "fm_Data" / "Plugins" / "x86_64" / "game_plugin.dll",
    ]
    for candidate in candidates:
        try:
            with candidate.open("rb") as source:
                if source.read(2) == b"MZ":
                    return candidate
        except OSError:
            continue
    return None


def module_pe_identity(process: Any, module: Any) -> tuple[int, int] | None:
    from fm_collector.win32 import read_process_memory

    header = read_process_memory(process, module.base_address, 0x1000)
    if not header or header[:2] != b"MZ":
        return None
    pe_offset = struct.unpack_from("<I", header, 0x3C)[0]
    if header[pe_offset:pe_offset + 4] != b"PE\0\0":
        return None
    timestamp = struct.unpack_from("<I", header, pe_offset + 8)[0]
    image_size = struct.unpack_from("<I", header, pe_offset + 24 + 56)[0]
    return timestamp, image_size


def is_fm26_xgp_module(process: Any, module: Any) -> bool:
    return module_pe_identity(process, module) == (
        FM26_XGP_PE_TIMESTAMP, FM26_XGP_IMAGE_SIZE,
    )


def is_fm24_xgp_module(process: Any, module: Any) -> bool:
    return module_pe_identity(process, module) == (
        FM24_XGP_PE_TIMESTAMP, FM24_XGP_IMAGE_SIZE,
    )


def _scan_process_span_for_bytes(
    process: Any, start: int, size: int, needle: bytes, *, chunk_size: int = 4 * 1024 * 1024,
):
    from fm_collector.win32 import read_process_memory

    carry = b""
    for address in range(start, start + size, chunk_size):
        data = read_process_memory(process, address, min(chunk_size, start + size - address))
        if not data:
            carry = b""
            continue
        combined = carry + data
        combined_base = address - len(carry)
        cursor = 0
        while True:
            cursor = combined.find(needle, cursor)
            if cursor < 0:
                break
            yield combined_base + cursor
            cursor += 1
        carry = combined[-(len(needle) - 1):] if len(needle) > 1 else b""


def _resolve_match_session_pointer_rva(
    process: Any, module_base: int, module_size: int, vtable_rva: int,
) -> int | None:
    from fm_collector.win32 import (
        MEM_IMAGE, MEM_PRIVATE, PAGE_READWRITE, PAGE_WRITECOPY,
        PAGE_EXECUTE_READWRITE, PAGE_EXECUTE_WRITECOPY,
        iter_readable_regions, read_process_memory,
    )

    vtable = module_base + vtable_rva
    object_hits: list[int] = []
    needle = struct.pack("<Q", vtable)
    for region in iter_readable_regions(process):
        if region.type != MEM_PRIVATE:
            continue
        for address in _scan_process_span_for_bytes(process, region.base_address, region.size, needle):
            vector = read_process_memory(process, address + 0x08, 24)
            if not vector or len(vector) != 24:
                continue
            begin, end, capacity = struct.unpack("<QQQ", vector)
            if begin <= end <= capacity and (not begin or (end - begin) % 8 == 0):
                object_hits.append(address)
                if len(object_hits) >= 16:
                    break
        if len(object_hits) >= 16:
            break
    if not object_hits:
        return None

    writable = {
        PAGE_READWRITE, PAGE_WRITECOPY,
        PAGE_EXECUTE_READWRITE, PAGE_EXECUTE_WRITECOPY,
    }
    candidates: set[int] = set()
    module_end = module_base + module_size
    for object_address in object_hits:
        pointer = struct.pack("<Q", object_address)
        for region in iter_readable_regions(process):
            if region.type != MEM_IMAGE or region.protect not in writable:
                continue
            start = max(module_base, region.base_address)
            end = min(module_end, region.base_address + region.size)
            if start >= end:
                continue
            for address in _scan_process_span_for_bytes(process, start, end - start, pointer):
                candidates.add(address - module_base)
    return next(iter(candidates)) if len(candidates) == 1 else None


@lru_cache(maxsize=4)
def resolve_fm24_xgp_layout(pid: int, module_base: int, module_size: int) -> GameLayout:
    from fm_collector.win32 import open_process, read_process_memory

    with open_process(pid) as process:
        date_raw = read_process_memory(
            process, module_base + int(FM24_XGP_LAYOUT.game_date_rva or 0), 4,
        )
        if not date_raw or len(date_raw) != 4:
            raise RuntimeError("FM24 XGP current game date is unreadable")
        date_code = struct.unpack("<I", date_raw)[0]
        year, day = date_code >> 16, date_code & 0x1FF
        if not 1900 <= year <= 2200 or not 1 <= day <= 366:
            raise RuntimeError("FM24 XGP current game-date validation failed")
        for rva in (
            FM24_XGP_LAYOUT.fixture_vtable_rva,
            FM24_XGP_LAYOUT.team_vtable_rva,
            FM24_XGP_LAYOUT.club_vtable_rva,
            FM24_XGP_LAYOUT.competition_vtable_rva,
            FM24_XGP_LAYOUT.actual_player_vtable_rvas[0],
        ):
            method = read_process_memory(process, module_base + rva, 8)
            if not method or len(method) != 8:
                raise RuntimeError("FM24 XGP vtable validation failed")
            target = struct.unpack("<Q", method)[0]
            if not module_base <= target < module_base + module_size:
                raise RuntimeError("FM24 XGP vtable method is outside fm.exe")
        match_session_pointer_rva = _resolve_match_session_pointer_rva(
            process, module_base, module_size,
            int(FM24_XGP_LAYOUT.match_session_vtable_rva or 0),
        )
    return replace(
        FM24_XGP_LAYOUT,
        match_session_pointer_rva=match_session_pointer_rva,
    )


@lru_cache(maxsize=4)
def resolve_fm26_xgp_layout(
    pid: int, process_path: str, module_base: int, module_size: int, module_path: str,
) -> GameLayout:
    from fm_collector.win32 import open_process, read_process_memory

    image_path = _alternate_plugin_path(process_path, module_path)
    if image_path is None:
        raise RuntimeError("unable to read the XGP game_plugin.dll through its install path")
    names = {
        "fixture": ".?AVFIXTURE@sicomps@@",
        "fixture_result": ".?AVFIXTURE_RESULT@sicomps@@",
        "season_result": ".?AVBASIC_SCORELINE@SCORELINE@db@@",
        "team": ".?AVTEAM@db@@",
        "national_team": ".?AVNATIONAL_TEAM@db@@",
        "nation": ".?AVNATIONAL_TEAM_CONTAINER@db@@",
        "club": ".?AVCLUB@db@@",
        "competition": ".?AVCOMP@db@@",
        "player": ".?AVACTUAL_PLAYER@db@@",
        "player_non_player": ".?AVACTUAL_PLAYER_AND_NON_PLAYER@db@@",
        "staff": ".?AVACTUAL_NON_PLAYER@db@@",
        "future_transfer_manager": ".?AVTRANSFER_MANAGER@@",
        "future_transfer_full_offer": ".?AVFULL_TRANSFER_OFFER@@",
        "future_transfer_loan_offer": ".?AVLOAN_OFFER@@",
    }
    with image_path.open("rb") as stream, mmap.mmap(
        stream.fileno(), 0, access=mmap.ACCESS_READ,
    ) as raw:
        sections = _pe_sections(raw)
        with ThreadPoolExecutor(max_workers=4) as executor:
            resolved = dict(zip(
                names,
                executor.map(
                    lambda name: _rtti_vtables(raw, sections, name),
                    names.values(),
                ),
            ))
        _validate_fm26_xgp_core_rtti(resolved)
        if not resolved["player"] or not resolved["player_non_player"] or len(resolved["staff"]) < 2:
            raise RuntimeError("XGP player RTTI layout is incomplete")

        human_markers = list(_occurrences(
            raw, sections, b"_number_news_item_to_human\0",
        ))
        if len(human_markers) != 1:
            raise RuntimeError("XGP human-manager marker is ambiguous")
        human_manager_rva = human_markers[0] + 0x20

        game_date_rva = _resolve_fm26_game_date_rva(raw, sections)

        save_pattern = (
            0x48, 0x8B, 0x35, None, None, None, None, 0x48, 0x85, 0xF6,
            0x74, None, 0x48, 0x8D, 0x05, None, None, None, None,
            0x48, 0x89, 0x86, 0x00, 0x02, 0x00, 0x00,
        )
        save_hits = _pattern_occurrences(raw, sections, save_pattern)
        if len(save_hits) != 1:
            raise RuntimeError(f"XGP save-root signature matched {len(save_hits)} locations")
        save_raw = _read_rva(raw, sections, save_hits[0], 7)
        if not save_raw:
            raise RuntimeError("XGP save-root instruction is unreadable")
        save_root_rva = save_hits[0] + 7 + struct.unpack_from("<i", save_raw, 3)[0]

    layout = replace(
        FM26_XGP_TEMPLATE,
        fixture_vtable_rva=resolved["fixture"][0],
        fixture_result_vtable_rva=resolved["fixture_result"][0],
        season_result_vtable_rva=resolved["season_result"][0],
        team_vtable_rva=resolved["team"][0],
        national_team_vtable_rva=resolved["national_team"][0],
        nation_vtable_rva=resolved["nation"][0],
        club_vtable_rva=resolved["club"][0],
        competition_vtable_rva=resolved["competition"][0],
        actual_player_vtable_rvas=(resolved["player"][0],),
        player_and_non_player_vtable_rvas=(resolved["player_non_player"][0],),
        staff_person_vtable_rva=resolved["staff"][1],
        human_manager_vtable_rvas=(human_manager_rva,),
        future_transfer_manager_vtable_rva=(
            resolved["future_transfer_manager"][0]
            if len(resolved["future_transfer_manager"]) == 1 else None
        ),
        future_transfer_full_offer_vtable_rva=(
            resolved["future_transfer_full_offer"][0]
            if len(resolved["future_transfer_full_offer"]) == 1 else None
        ),
        future_transfer_loan_offer_vtable_rva=(
            resolved["future_transfer_loan_offer"][0]
            if len(resolved["future_transfer_loan_offer"]) == 1 else None
        ),
        game_date_rva=game_date_rva,
        savegame_root_rvas=(save_root_rva,),
    )
    with open_process(pid) as process:
        _validate_fm26_runtime_game_date(
            process, module_base, module_size, game_date_rva,
        )
        for rva in (
            layout.fixture_vtable_rva, layout.team_vtable_rva, layout.club_vtable_rva,
            layout.competition_vtable_rva, layout.actual_player_vtable_rvas[0],
        ):
            method = read_process_memory(process, module_base + rva, 8)
            if not method or len(method) != 8:
                raise RuntimeError("XGP vtable validation failed")
            target = struct.unpack("<Q", method)[0]
            if not module_base <= target < module_base + module_size:
                raise RuntimeError("XGP vtable method is outside game_plugin.dll")
    return layout
