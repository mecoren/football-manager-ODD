"""Re-check the FM27 CA model against the live save (read-only).

FM27 reuses FM26's position weight table and CA curve on top of the FM27
display bias (2) plus a fitted CA intercept (``FM27_RCA_CA_OFFSET``). This
script measures that shipped model on the save that is currently loaded,
through the exact read path the attribute draft uses:
``GET /api/world-players/detail`` publishes ``editor.expected_raw.attributes``
(108 hex characters), ``editor.positions`` and
``editor.attribute_display_bias``.

Only GET requests are made. Nothing is written to the game process, no file is
produced, and no player id or name is printed - the report is aggregate only.

Start the development server with an FM27 save loaded, then run:

    python scripts\\verify_fm27_rca.py

The verdict line follows the gate agreed for keeping the FM27 draft open:

    PASS  mean|err| <= 4.5, |signed| <= 1.5, |err|<=5 >= 85%, and the
          display_bias=0 control is at least twice as bad
    STOP  mean|err| > 5.0, or |signed| > 2.0, or |err|<=5 < 80%
    GRAY  anything in between - report the numbers before trusting the model
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tools.club_reader import VISIBLE_ATTRIBUTE_IDS  # noqa: E402
from tools.player_attribute_builder import (  # noqa: E402
    SLOT_NAMES, build_player_attribute_draft,
)
from tools.player_rca import (  # noqa: E402
    FM27_RCA_MODEL, fm24_best_position_key, fm26_recommended_ca,
    fm27_recommended_ca,
)

DEFAULT_API = "http://127.0.0.1:7857"
BANDS = ((1, 6), (7, 40), (41, 80), (81, 120), (121, 160), (161, 200))
PER_BAND = 12
PER_POSITION_LIMIT = 2
ROUND_TRIP_PLAYERS = 12
FORMAL_BIAS = 2
CONTROL_BIAS = 0


def _get(api: str, path: str, params: dict[str, object]) -> dict:
    query = urllib.parse.urlencode(
        {key: value for key, value in params.items() if value not in ("", None)}
    )
    url = api + path + ("?" + query if query else "")
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as error:
        raise SystemExit(
            "cannot reach " + api + " (" + str(error.reason) + ").\n"
            "Start the development server with an FM27 save loaded first:\n"
            "  python -u src/fm_odds_web.py --port 7857 --no-browser --keep-alive"
        )


def _ratings(row: dict) -> tuple[int, ...]:
    published = row.get("position_ratings") or {}
    return tuple(int(published.get(name) or 0) for name in SLOT_NAMES)


def _sample(api: str) -> list[tuple[int, int, int]]:
    picks: list[tuple[int, int, int]] = []
    for index, (low, high) in enumerate(BANDS):
        page = _get(api, "/api/world-players", {
            "page_size": 50, "sort_by": "ca", "sort_order": "desc",
            "min_ca": low, "max_ca": high,
        })
        rows = [row for row in (page.get("players") or []) if int(row.get("ca") or 0) > 0]
        chosen: set[int] = set()
        per_key: dict[int, int] = {}
        for row in rows:
            if len(chosen) >= PER_BAND:
                break
            key = fm24_best_position_key(_ratings(row))
            if per_key.get(key, 0) >= PER_POSITION_LIMIT:
                continue
            per_key[key] = per_key.get(key, 0) + 1
            chosen.add(int(row["id"]))
            picks.append((index, int(row["ca"]), int(row["id"])))
        # A band with few position keys would under-fill; top up with any row.
        for row in rows:
            if len(chosen) >= PER_BAND:
                break
            if int(row["id"]) in chosen:
                continue
            chosen.add(int(row["id"]))
            picks.append((index, int(row["ca"]), int(row["id"])))
    return picks


def _detail(api: str, player_id: int) -> dict:
    return _get(api, "/api/world-players/detail", {"player_id": player_id}).get("player") or {}


def _parse(player: dict, band: int) -> tuple[dict | None, str]:
    editor = player.get("editor") or {}
    raw = str((editor.get("expected_raw") or {}).get("attributes") or "")
    if len(raw) != 108:
        return None, "no-attribute-block"
    try:
        block = bytes.fromhex(raw)
    except ValueError:
        return None, "bad-hex"
    if any(value > 100 for value in block):
        return None, "byte-out-of-range"
    ca = int(player.get("ca") or 0)
    if not 1 <= ca <= 200:
        return None, "ca-out-of-range"
    published = editor.get("positions") or {}
    ratings = tuple(int(published.get(name) or 0) for name in SLOT_NAMES)
    if len(published) < len(SLOT_NAMES) or any(not 0 <= value <= 20 for value in ratings):
        return None, "position-out-of-range"
    return {
        "band": band,
        "ca": ca,
        "block": block,
        "ratings": ratings,
        "bias": int(editor.get("attribute_display_bias") or 0),
        "attributes": player.get("attributes") or {},
        "position_key": fm24_best_position_key(ratings),
    }, ""


def _summary(errors: list[int]) -> dict[str, float]:
    if not errors:
        return {"n": 0, "mae": 0.0, "signed": 0.0, "within": 0.0, "max": 0}
    count = len(errors)
    return {
        "n": count,
        "mae": sum(abs(value) for value in errors) / count,
        "signed": sum(errors) / count,
        "within": sum(1 for value in errors if abs(value) <= 5) / count,
        "max": max(abs(value) for value in errors),
    }


def _line(label: str, stats: dict[str, float]) -> str:
    return (
        "  {:<22} n={:<3} mean|err|={:<6.3f} signed={:<+7.3f} "
        "|err|<=5={:>3}/{:<3} ({:>5.1f}%) max|err|={}"
    ).format(
        label, int(stats["n"]), stats["mae"], stats["signed"],
        round(stats["within"] * int(stats["n"])), int(stats["n"]),
        stats["within"] * 100, int(stats["max"]),
    )


def _writable(attributes: dict) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for group, values in attributes.items():
        for name in values or {}:
            label = str(group) + ":" + str(name)
            attribute_id = VISIBLE_ATTRIBUTE_IDS.get(label)
            if attribute_id is not None:
                mapping[int(attribute_id)] = label
    return mapping


def _round_trip(samples: list[dict]) -> tuple[int, int, int]:
    hits = 0
    reproduced = 0
    attempted = 0
    for sample in samples[:ROUND_TRIP_PLAYERS]:
        writable = _writable(sample["attributes"])
        if not writable:
            continue
        try:
            draft = build_player_attribute_draft(
                sample["block"], sample["ratings"], sample["ca"],
                display_bias=FORMAL_BIAS, writable=writable, model=FM27_RCA_MODEL,
            )
        except ValueError:
            continue
        attempted += 1
        if draft.get("hit"):
            hits += 1
        rebuilt = bytearray(sample["block"])
        for label, values in (draft.get("attributes") or {}).items():
            attribute_id = VISIBLE_ATTRIBUTE_IDS.get(label)
            if attribute_id is None:
                continue
            rebuilt[int(attribute_id) - 0x0F] = int(values["after"]) * 5 - FORMAL_BIAS
        settled = fm27_recommended_ca(bytes(rebuilt), sample["ratings"])
        if int(settled["recommended_ca"]) == int(sample["ca"]):
            reproduced += 1
    return attempted, hits, reproduced


def _verdict(formal: dict[str, float], control: dict[str, float]) -> str:
    if formal["mae"] > 5.0 or abs(formal["signed"]) > 2.0 or formal["within"] < 0.80:
        return "STOP"
    if (
        formal["mae"] <= 4.5
        and abs(formal["signed"]) <= 1.5
        and formal["within"] >= 0.85
        and control["mae"] >= 2 * formal["mae"]
    ):
        return "PASS"
    return "GRAY"


def main(api: str = DEFAULT_API) -> int:
    picks = _sample(api)
    samples: list[dict] = []
    skipped: dict[str, int] = {}
    biases: dict[int, int] = {}
    for band, _ca, player_id in picks:
        parsed, reason = _parse(_detail(api, player_id), band)
        if parsed is None:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        biases[parsed["bias"]] = biases.get(parsed["bias"], 0) + 1
        samples.append(parsed)

    if not samples:
        print("no usable sample was read; nothing to report")
        return 2

    formal_errors: list[int] = []
    control_errors: list[int] = []
    plain_errors: list[int] = []
    by_band: dict[int, list[int]] = {index: [] for index in range(len(BANDS))}
    by_key: dict[int, list[int]] = {}
    for sample in samples:
        formal = fm27_recommended_ca(sample["block"], sample["ratings"])
        plain = fm26_recommended_ca(sample["block"], sample["ratings"], FORMAL_BIAS)
        control = fm26_recommended_ca(sample["block"], sample["ratings"], CONTROL_BIAS)
        error = int(formal["recommended_ca"]) - int(sample["ca"])
        formal_errors.append(error)
        plain_errors.append(int(plain["recommended_ca"]) - int(sample["ca"]))
        control_errors.append(int(control["recommended_ca"]) - int(sample["ca"]))
        by_band[sample["band"]].append(error)
        by_key.setdefault(int(formal["position_key"]), []).append(error)

    formal_stats = _summary(formal_errors)
    control_stats = _summary(control_errors)

    print("FM27 CA model re-check (read-only)")
    print("  api={}  sampled={}  usable={}  skipped={}".format(
        api, len(picks), len(samples),
        ", ".join(sorted(key + "=" + str(value) for key, value in skipped.items())) or "0",
    ))
    print("  observed display_bias={}".format(
        ", ".join(str(key) + " x" + str(value) for key, value in sorted(biases.items()))
    ))
    print("")
    print("FORMAL  shipped model ({})".format(FM27_RCA_MODEL))
    print(_line("overall", formal_stats))
    for index, errors in by_band.items():
        if errors:
            print(_line("CA {}-{}".format(*BANDS[index]), _summary(errors)))
    print("")
    print("REFERENCE  same table, bias={}, no intercept".format(FORMAL_BIAS))
    print(_line("overall", _summary(plain_errors)))
    print("")
    print("CONTROL bias={} (wrong transform)".format(CONTROL_BIAS))
    print(_line("overall", control_stats))
    print("")
    print("BY POSITION KEY (formal)")
    for key in sorted(by_key):
        print(_line("key {}".format(key), _summary(by_key[key])))
    print("")
    attempted, hits, reproduced = _round_trip(samples)
    print("SOLVER ROUND-TRIP   attempted={} hit={} rebuilt-block-reproduces-stored-ca={}".format(
        attempted, hits, reproduced,
    ))
    print("")
    print("verdict: " + _verdict(formal_stats, control_stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_API))
