from types import SimpleNamespace

from tools.club_reader import (
    PLAYER_HIDDEN_ATTRIBUTE_IDS,
    VISIBLE_ATTRIBUTE_IDS,
    _display_attribute,
    _visible_player_attributes,
    plan_attribute_reallocation,
)
from tools.game_layout import FM24_LAYOUT, FM26_LAYOUT, FM27_LAYOUT


def test_supported_layouts_discard_partial_attribute_points():
    assert FM24_LAYOUT.attribute_display_bias == 0
    assert FM26_LAYOUT.attribute_display_bias == 0


def test_fm27_display_reproduces_the_save_file():
    """Raw -> display pairs read off the round-114 oracle tables.

    The save records the displayed value and live memory records the byte, so
    each pair below is a direct observation (crossing for the two-step
    boundaries, pace for the 21..23 run) rather than a reconstruction. FM27
    reproduces all of them with bias 2 and misses 47 -> 9 with the old bias 3,
    which is exactly the raw % 5 == 2 boundary the two constants disagree on.
    """
    pairs = {
        1: 1, 7: 1, 8: 2, 12: 2, 13: 3, 21: 4, 22: 4, 23: 5, 47: 9, 76: 15, 100: 20,
    }
    bias = FM27_LAYOUT.attribute_display_bias
    assert {raw: _display_attribute(raw, bias) for raw in pairs} == pairs
    assert _display_attribute(47, 3) == 10
    assert _display_attribute(47, 0) == 9


def test_the_seven_unpublished_attribute_ids_are_foot_and_hidden_fields():
    """The save names 52 of the 54 bytes; the product publishes 47 of them.

    The gap is exactly the seven ids the shipped tables leave out. Round 114
    named five of them from data (0x38 dirtiness, 0x3B consistency, 0x3E
    important matches, 0x3F injury proneness, 0x40 versatility); 0x27 and 0x28
    have no counterpart in the save at all and both read 1..100 with 100 as the
    modal value, consistent with the two foot ratings.
    """
    published = set(VISIBLE_ATTRIBUTE_IDS.values())
    assert published <= set(range(0x0F, 0x45))
    assert set(range(0x0F, 0x45)) - published == {
        0x27, 0x28, 0x38, 0x3B, 0x3E, 0x3F, 0x40,
    }

    # Round 118 named all seven from the save: the two the save has no field for
    # are the feet (id 0x27 == raw_left_foot, id 0x28 == raw_right_foot, both
    # 14509/14509 exact on the same 1..100 scale), and the other five are its
    # dirtiness / consistency / important matches / injury proneness /
    # versatility. The shipped table already used those ids.
    assert PLAYER_HIDDEN_ATTRIBUTE_IDS == {
        "左脚": 0x27, "右脚": 0x28, "肮脏动作": 0x38,
        "稳定性": 0x3B, "大赛发挥": 0x3E,
        "受伤倾向": 0x3F, "多面性": 0x40,
    }


def test_attribute_display_discards_raw_remainder():
    expected = {
        1: 1,
        4: 1,
        5: 1,
        9: 1,
        10: 2,
        99: 19,
        100: 20,
    }

    assert {raw: _display_attribute(raw) for raw in expected} == expected


def test_visible_attributes_use_the_same_discard_rule():
    raw = bytearray([50] * 54)
    raw[0x16 - 0x0F] = 54  # Passing: 10 after discarding the remainder.
    reader = SimpleNamespace(
        layout=SimpleNamespace(
            player_attributes_offset=0x200,
            attribute_display_bias=0,
        ),
    )

    attributes = _visible_player_attributes(
        reader, 0x1000, bytes(raw), goalkeeper=False,
    )

    assert attributes["技术"]["传球"] == 10


def test_attribute_reallocation_uses_discarded_display_value_by_default():
    raw = bytearray([50] * 54)
    raw[0x16 - 0x0F] = 54
    raw[0x0F - 0x0F] = 54

    updated, audit = plan_attribute_reallocation(
        bytes(raw),
        {"技术:传球": -1, "技术:传中": 1},
        {"技术:传球": 10, "技术:传中": 10},
    )

    assert updated[0x16 - 0x0F] == 49
    assert updated[0x0F - 0x0F] == 59
    assert audit == {
        "before": {"技术:传球": 10, "技术:传中": 10},
        "after": {"技术:传球": 9, "技术:传中": 11},
    }


def test_attribute_reallocation_ignores_unselected_sentinel_fields():
    raw = bytearray([50] * 54)
    raw[-1] = 255

    updated, _audit = plan_attribute_reallocation(
        bytes(raw),
        {"技术:传球": -1, "技术:传中": 1},
        {"技术:传球": 10, "技术:传中": 10},
    )

    assert updated[-1] == 255
