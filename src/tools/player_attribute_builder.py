"""Build a CA-consistent attribute draft for the world player editor.

The module is pure maths. It never opens the game process, never reads a
layout and never imports ``club_reader``: callers hand in the 54-byte
attribute block, the 15 position ratings and the attribute keys the write
path will actually accept. That keeps the solver testable without a live
save and keeps the FM offsets in the reader layer where they belong.

The CA target is expressed through the verified RCA formula in
``tools.player_rca``:

    A  = (sum(w_i * display_i) + foot_w * weaker_foot) / (sum(w_i) + foot_w)
    CA = 1 if A <= 6 else 200 if A > 16 else 19.929 * A - 119.38

Because the formula is linear in the weighted average, an integer CA target
is a narrow band of averages. One point on a weight-1 attribute moves the
average by ~0.0116, while a CA band is ~0.0502 wide, so a band is always
reachable with integer attributes. The solver therefore shapes attributes by
the position weights, calibrates the level with a single offset, and then
walks the average into the band using the cheapest (lowest weight)
attributes only.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from tools.player_rca import (
    FM24_RCA_MODEL,
    FM26_RCA_MODEL,
    FMRTE_POSITION_PRIORITY,
    FMRTE_RCA_WEIGHTS,
    fm24_best_position_key,
    fm24_recommended_ca,
    fm26_recommended_ca,
)

CA_SLOPE = 19.929
CA_INTERCEPT = -119.38
ATTRIBUTE_OFFSET = 0x0F
ATTRIBUTE_BLOCK_SIZE = 54
LEFT_FOOT_ID = 0x27
RIGHT_FOOT_ID = 0x28

# Position keys match FMRTE PlayerGeneralPosition; the slot names follow the
# 15-byte familiarity block in club_reader.POSITION_NAMES.
POSITION_KEY_NAMES = {
    0: "GK", 1: "DC", 2: "DL/DR", 3: "WBL/WBR", 4: "DM",
    5: "MC", 6: "ML/MR", 7: "AMC", 8: "AML/AMR", 9: "ST",
}
SLOT_NAMES = (
    "GK", "SW", "DL", "DC", "DR", "DM", "ML", "MC", "MR",
    "AML", "AMC", "AMR", "ST", "WBL", "WBR",
)
SLOT_TO_POSITION_KEY = {slot: key for slot, key in FMRTE_POSITION_PRIORITY}
GOALKEEPING_ATTRIBUTE_ID = 0x1A

# Each style is a small display-point bias table. Values stay within +/-3 so a
# style re-shapes a player without inventing a different level; the level is
# always re-solved against the CA target afterwards. An empty table means the
# style is not offered for that group.
ATTRIBUTE_STYLES: dict[str, dict[str, dict[int, int]]] = {
    "balanced": {
        "outfield": {0x21: 1, 0x44: 1, 0x2C: 1},
        "goalkeeper": {0x21: 1, 0x44: 1, 0x2C: 1},
    },
    "technical": {
        "outfield": {0x26: 3, 0x25: 3, 0x10: 2, 0x29: 2, 0x16: 1, 0x33: -1, 0x36: -1},
        "goalkeeper": {0x1A: 2, 0x25: 2, 0x16: 1, 0x26: 1},
    },
    "pace": {
        "outfield": {0x31: 3, 0x35: 3, 0x3D: 2, 0x10: 1, 0x33: -1, 0x12: -1},
        "goalkeeper": {0x24: 2, 0x3D: 2, 0x35: 1, 0x31: 1},
    },
    "physical": {
        "outfield": {0x33: 3, 0x36: 2, 0x39: 2, 0x41: 2, 0x34: 1, 0x3D: -1},
        "goalkeeper": {0x33: 2, 0x36: 2, 0x39: 1, 0x1B: 1},
    },
    "playmaker": {
        "outfield": {0x19: 3, 0x16: 2, 0x21: 2, 0x26: 1, 0x2B: 1, 0x29: 1, 0x18: -1, 0x36: -1},
        "goalkeeper": {0x16: 2, 0x19: 2, 0x1E: 2, 0x21: 1},
    },
    "defensive": {
        "outfield": {0x18: 3, 0x14: 3, 0x23: 2, 0x3C: 2, 0x33: 1, 0x3A: 1, 0x11: -2, 0x13: -2, 0x10: -1},
        "goalkeeper": {0x2F: 2, 0x1D: 2, 0x1C: 1, 0x22: 1},
    },
    "finishing": {
        "outfield": {0x11: 3, 0x15: 2, 0x43: 2, 0x25: 1, 0x12: 1, 0x13: 1, 0x18: -2, 0x14: -2},
        "goalkeeper": {},
    },
    "aerial": {
        "outfield": {0x36: 3, 0x12: 3, 0x33: 2, 0x3A: 1, 0x39: 1, 0x3D: -2, 0x10: -1, 0x0F: -1},
        "goalkeeper": {0x1B: 3, 0x30: 2, 0x36: 2, 0x33: 1},
    },
    "workhorse": {
        "outfield": {0x2C: 3, 0x34: 3, 0x3C: 2, 0x2B: 2, 0x3A: 1, 0x18: 1, 0x29: -2, 0x17: -1},
        "goalkeeper": {0x2C: 3, 0x34: 2, 0x3C: 1, 0x3A: 1},
    },
    "genius": {
        "outfield": {0x29: 3, 0x26: 2, 0x10: 2, 0x19: 1, 0x13: 1, 0x32: 1, 0x2C: -2, 0x2B: -1, 0x18: -1},
        "goalkeeper": {0x24: 2, 0x22: 2, 0x1A: 1, 0x2E: 1},
    },
}


def attribute_style_catalog() -> list[dict[str, Any]]:
    """Return the styles the editor may offer, keyed by their message suffix."""
    return [
        {
            "key": key,
            "outfield": bool(profile["outfield"]),
            "goalkeeper": bool(profile["goalkeeper"]),
        }
        for key, profile in ATTRIBUTE_STYLES.items()
    ]


def style_keys_for_group(goalkeeper: bool) -> list[str]:
    group = "goalkeeper" if goalkeeper else "outfield"
    return [key for key, profile in ATTRIBUTE_STYLES.items() if profile[group]]


def _clamp_display(value: int) -> int:
    return max(1, min(20, int(value)))


def _display(raw_value: int, display_bias: int) -> int:
    return max(1, min(20, (int(raw_value) + int(display_bias)) // 5))


def _ca_from_average(average: float) -> int:
    if average <= 6:
        return 1
    if average > 16:
        return 200
    return max(1, min(200, int(CA_SLOPE * average + CA_INTERCEPT)))


def _target_band(target: int) -> tuple[float, float, float]:
    """Return ``(low, high, ideal_average)`` for an integer CA target.

    ``low`` is inclusive and ``high`` exclusive, except for CA 200 where the
    formula switches to ``average > 16`` and the comparison is strict.
    """
    if target <= 1:
        return (1.0, 6.0, 4.0)
    if target >= 200:
        return (16.0, 20.0, 16.6)
    low = (target + 119.38) / CA_SLOPE
    # A target of 199 sits right under the "average > 16 becomes 200" branch,
    # so the band has to stop at 16.0 instead of running into that branch.
    high = min((target + 1 + 119.38) / CA_SLOPE, 16.0)
    return (low, high, (low + high) / 2.0)


def _band_direction(average: float, target: int) -> int:
    """Return +1 when the average has to rise to reach the target band.

    CA 200 needs a strictly positive margin over 16 and CA 1 needs to stay at
    or under 6, so those two ends are one-sided bands.
    """
    low, high, _ideal = _target_band(target)
    if target >= 200:
        return 1 if average <= high else -1
    if target <= 1:
        return -1
    return 1 if average < low else -1


def _weighted_average(
    block: bytes, position_key: int, display_bias: int,
) -> float:
    weights, foot_weight = FMRTE_RCA_WEIGHTS[position_key]
    total_value = 0.0
    total_weight = 0.0
    for attribute_id, weight in weights.items():
        total_value += float(weight) * _display(
            block[attribute_id - ATTRIBUTE_OFFSET], display_bias,
        )
        total_weight += float(weight)
    weaker_foot = min(
        _display(block[LEFT_FOOT_ID - ATTRIBUTE_OFFSET], display_bias),
        _display(block[RIGHT_FOOT_ID - ATTRIBUTE_OFFSET], display_bias),
    )
    total_value += foot_weight * weaker_foot
    total_weight += foot_weight
    return total_value / total_weight


def _recommended_ca_function(model: str) -> Any:
    label = str(model or "")
    if label.startswith("fm24"):
        return fm24_recommended_ca
    if label.startswith("fm26"):
        return fm26_recommended_ca
    raise ValueError("当前游戏版本没有已验证的 CA 属性公式")


def _build_block(
    raw: bytes, values: Mapping[int, int], display_bias: int,
) -> bytes:
    block = bytearray(raw)
    for attribute_id, display in values.items():
        block[int(attribute_id) - ATTRIBUTE_OFFSET] = int(display) * 5 - int(display_bias)
    return bytes(block)


def _evaluate(
    block: bytes,
    position_ratings: Sequence[int],
    display_bias: int,
    recommended_ca: Any,
    position_key: int,
) -> tuple[int | None, float, bool]:
    average = _weighted_average(block, position_key, display_bias)
    try:
        result = recommended_ca(block, position_ratings, display_bias)
    except ValueError:
        return None, average, False
    return int(result["recommended_ca"]), float(result["weighted_average"]), True


def _resolve_positions(
    position_ratings: Sequence[int],
) -> tuple[int, list[int], list[dict[str, Any]], bool, list[dict[str, Any]]]:
    """Return the authoritative key, the tied keys, the slots used and flags."""
    warnings: list[dict[str, Any]] = []
    supported = [(slot, key) for slot, key in FMRTE_POSITION_PRIORITY]
    peak = max(int(position_ratings[slot]) for slot, _key in supported)
    tied_slots = [
        slot for slot, _key in supported
        if peak > 0 and int(position_ratings[slot]) == peak
    ]
    if tied_slots:
        tied_keys = sorted({SLOT_TO_POSITION_KEY[slot] for slot in tied_slots})
    else:
        tied_slots = []
        tied_keys = [fm24_best_position_key(position_ratings)]
    swiper_rating = int(position_ratings[1]) if len(position_ratings) > 1 else 0
    if swiper_rating > 0 and swiper_rating >= peak:
        # FMRTE's best-position scan never reads the sweeper slot, so the
        # verified formula settles on a different position for this player.
        warnings.append({"code": "sweeper_fallback"})
    used = [
        {
            "slot": SLOT_NAMES[slot],
            "rating": int(position_ratings[slot]),
            "position_key": SLOT_TO_POSITION_KEY[slot],
        }
        for slot in tied_slots
    ]
    return tied_keys[0], tied_keys, used, len(tied_keys) > 1, warnings


def _shape_weights(tied_keys: Sequence[int]) -> dict[int, float]:
    attribute_ids: set[int] = set()
    for key in tied_keys:
        attribute_ids.update(FMRTE_RCA_WEIGHTS[key][0])
    return {
        attribute_id: sum(
            FMRTE_RCA_WEIGHTS[key][0].get(attribute_id, 0.0) for key in tied_keys
        ) / len(tied_keys)
        for attribute_id in attribute_ids
    }


def _pick_adjustment(
    values: Mapping[int, int],
    baseline: Mapping[int, int],
    formula_weights: Mapping[int, float],
    adjustable: Sequence[int],
    direction: int,
) -> int | None:
    """Pick the cheapest attribute to move one display point.

    Weight classes are tried from cheapest to most expensive and, inside a
    class, the attribute already closest to its shaped baseline wins. That
    keeps the fine tuning on the attributes the position formula ignores and
    spreads the drift instead of draining a single one.
    """
    weight_classes = sorted({float(formula_weights[attribute_id]) for attribute_id in adjustable})
    for weight in weight_classes:
        candidates: list[tuple[int, int]] = []
        for attribute_id in adjustable:
            if float(formula_weights[attribute_id]) != weight:
                continue
            target = int(values[attribute_id]) + int(direction)
            if not 1 <= target <= 20:
                continue
            candidates.append((abs(target - int(baseline[attribute_id])), attribute_id))
        if candidates:
            candidates.sort()
            return candidates[0][1]
    return None


def build_player_attribute_draft(
    raw_attributes: bytes,
    position_ratings: Sequence[int],
    target_ca: int,
    *,
    style: str = "balanced",
    display_bias: int = 0,
    writable: Mapping[int, str],
    model: str = FM26_RCA_MODEL,
    max_steps: int = 400,
) -> dict[str, Any]:
    """Return a draft attribute block whose verified CA equals ``target_ca``."""
    raw = bytes(raw_attributes or b"")
    if len(raw) != ATTRIBUTE_BLOCK_SIZE:
        raise ValueError("球员属性块无法安全读取")
    ratings = tuple(int(value) for value in position_ratings)
    if len(ratings) < len(SLOT_NAMES):
        raise ValueError("球员位置数据无法安全读取")
    if any(not 0 <= value <= 20 for value in ratings):
        raise ValueError("球员位置熟练度超出范围")
    target = int(target_ca)
    if not 1 <= target <= 200:
        raise ValueError("目标 CA 必须是 1 至 200 的整数")
    style_key = str(style or "balanced")
    if style_key not in ATTRIBUTE_STYLES:
        raise ValueError("未知的球员风格")
    bias = int(display_bias)
    if not 0 <= bias <= 4:
        raise ValueError("属性显示偏差无效")
    writable_ids = {int(key): str(value) for key, value in dict(writable).items()}
    if not writable_ids:
        raise ValueError("该球员没有可写入的属性")
    recommended_ca = _recommended_ca_function(model)

    is_goalkeeper = GOALKEEPING_ATTRIBUTE_ID in writable_ids
    group = "goalkeeper" if is_goalkeeper else "outfield"
    formula_key = fm24_best_position_key(ratings)
    formula_weights, _foot_weight = FMRTE_RCA_WEIGHTS[formula_key]
    adjustable = [
        attribute_id for attribute_id in formula_weights
        if attribute_id in writable_ids and float(formula_weights[attribute_id]) > 0
    ]
    if not adjustable:
        raise ValueError("该球员可写入的属性都不参与当前位置的 CA 公式")

    tied_key, tied_keys, positions_used, mixed, warnings = _resolve_positions(ratings)
    shape = _shape_weights(tied_keys)
    style_bias = ATTRIBUTE_STYLES[style_key][group]

    _low, _high, ideal = _target_band(target)
    span = 6.0 if ideal >= 11.0 else 5.0 if ideal >= 6.0 else 3.5
    damping = 1.0 if ideal >= 8.0 else 0.6
    mean_shape = sum(float(shape.get(attribute_id, 0.0)) for attribute_id in adjustable) / len(adjustable)
    top_shape = max(float(shape.get(attribute_id, 0.0)) for attribute_id in adjustable) or 1.0
    base = {
        attribute_id: ideal
        + span * (float(shape.get(attribute_id, 0.0)) - mean_shape) / top_shape
        + style_bias.get(attribute_id, 0) * damping
        for attribute_id in adjustable
    }
    baseline = {
        attribute_id: _clamp_display(round(base[attribute_id]))
        for attribute_id in adjustable
    }
    values = dict(baseline)
    for attribute_id in sorted(writable_ids):
        if attribute_id in values:
            continue
        # Attributes outside the current position formula never move the CA,
        # so they are free to carry the style.
        z = (float(shape.get(attribute_id, 0.0)) - mean_shape) / top_shape
        values[attribute_id] = _clamp_display(round(
            ideal * 0.92 + span * 0.8 * z + style_bias.get(attribute_id, 0) * 1.2
        ))

    def average_for(offset: float) -> float:
        trial = {
            attribute_id: _clamp_display(round(base[attribute_id] + offset))
            for attribute_id in adjustable
        }
        trial.update({
            attribute_id: values[attribute_id]
            for attribute_id in values
            if attribute_id not in baseline
        })
        return _weighted_average(_build_block(raw, trial, bias), formula_key, bias)

    best_offset = 0.0
    best_gap = abs(average_for(0.0) - ideal)
    low_offset, high_offset = -14.0, 14.0
    for _step in range(26):
        middle = (low_offset + high_offset) / 2.0
        gap = average_for(middle) - ideal
        if abs(gap) < best_gap:
            best_offset, best_gap = middle, abs(gap)
        if gap < 0:
            low_offset = middle
        else:
            high_offset = middle
    values.update({
        attribute_id: _clamp_display(round(base[attribute_id] + best_offset))
        for attribute_id in adjustable
    })

    def evaluate() -> tuple[int | None, float, bool]:
        return _evaluate(
            _build_block(raw, values, bias), ratings, bias,
            recommended_ca, formula_key,
        )

    formula_ca, average, readable = evaluate()
    best_values = dict(values)
    best_ca, best_average, best_readable = formula_ca, average, readable
    best_gap = abs(int(formula_ca) - target) if formula_ca is not None else 201
    iterations = 0
    for step in range(max(0, int(max_steps))):
        if formula_ca == target:
            break
        if not readable:
            break
        direction = _band_direction(average, target)
        picked = _pick_adjustment(values, baseline, formula_weights, adjustable, direction)
        if picked is None:
            break
        values[picked] = int(values[picked]) + direction
        formula_ca, average, readable = evaluate()
        iterations = step + 1
        if formula_ca is not None:
            gap = abs(int(formula_ca) - target)
            if gap < best_gap:
                best_gap = gap
                best_values = dict(values)
                best_ca, best_average, best_readable = formula_ca, average, readable
    if formula_ca != target and best_gap <= abs(int(formula_ca or 0) - target):
        values = best_values
        formula_ca, average, readable = best_ca, best_average, best_readable

    hit = readable and formula_ca == target
    if not readable:
        warnings.append({"code": "formula_unreadable"})
        formula_ca = _ca_from_average(average)
    elif not hit:
        warnings.append({
            "code": "unreachable",
            "params": {"target": target, "settled": int(formula_ca or 0)},
        })

    attributes: dict[str, dict[str, int]] = {}
    changed = 0
    for attribute_id, label in writable_ids.items():
        before = _display(raw[attribute_id - ATTRIBUTE_OFFSET], bias)
        after = int(values.get(attribute_id, before))
        attributes[label] = {"before": before, "after": after}
        if after != before:
            changed += 1

    feet_trusted = (
        raw[LEFT_FOOT_ID - ATTRIBUTE_OFFSET] <= 100
        and raw[RIGHT_FOOT_ID - ATTRIBUTE_OFFSET] <= 100
    )
    return {
        "target_ca": target,
        "formula_ca": int(formula_ca or 0),
        "hit": bool(hit),
        "ca_model": str(model),
        "style": style_key,
        "group": group,
        "position_key": formula_key,
        "position_key_name": POSITION_KEY_NAMES[formula_key],
        "shape_position_key": tied_key,
        "positions_used": positions_used,
        "mixed": bool(mixed),
        "weighted_average": round(float(average), 6),
        "required_average": round(float(ideal), 6),
        "feet": {
            "left": _display(raw[LEFT_FOOT_ID - ATTRIBUTE_OFFSET], bias),
            "right": _display(raw[RIGHT_FOOT_ID - ATTRIBUTE_OFFSET], bias),
            "used": min(
                _display(raw[LEFT_FOOT_ID - ATTRIBUTE_OFFSET], bias),
                _display(raw[RIGHT_FOOT_ID - ATTRIBUTE_OFFSET], bias),
            ),
            "trusted": bool(feet_trusted),
        },
        "attributes": attributes,
        "changed": changed,
        "iterations": iterations,
        "warnings": warnings,
    }
