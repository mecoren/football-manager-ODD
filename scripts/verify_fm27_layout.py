"""Verify the statically resolved FM27 probe values against a live game.

Read-only validation: opens the running FM27 process with read access,
checks the fm.exe / game_plugin.dll identity hashes, reads the game-date
value at the statically resolved RVA and confirms every statically
resolved vtable RVA points at a method inside the loaded module.

No memory is written and no hook is installed. Launch Football Manager 27
first, keep it running, then run:

    python scripts\\verify_fm27_layout.py
"""
from __future__ import annotations

import hashlib
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fm_collector.win32 import (  # noqa: E402
    find_module, find_processes, open_process, read_process_memory,
)
from tools.game_layout import (  # noqa: E402
    FM26_LAYOUT, FM27_EXE_SHA256, FM27_LAYOUT, FM27_STEAM_PLUGIN_SHA256,
    _pe_sections, _pattern_occurrences, _read_rva,
)

GAME_PLUGIN = "game_plugin.dll"

# Static candidates recorded in FM27_LAYOUT plus the staff candidates that
# still need a verified single choice before entering the layout.
PROBE_VTABLES = {
    "fixture": (FM27_LAYOUT.fixture_vtable_rva,),
    "fixture_result": (FM27_LAYOUT.fixture_result_vtable_rva,),
    "season_result": (FM27_LAYOUT.season_result_vtable_rva,),
    "team": (FM27_LAYOUT.team_vtable_rva,),
    "national_team": (FM27_LAYOUT.national_team_vtable_rva,),
    "nation": (FM27_LAYOUT.nation_vtable_rva,),
    "club": (FM27_LAYOUT.club_vtable_rva,),
    "competition": (FM27_LAYOUT.competition_vtable_rva,),
    "player": FM27_LAYOUT.actual_player_vtable_rvas,
    "player_non_player": FM27_LAYOUT.player_and_non_player_vtable_rvas,
    "staff_candidates": (0x4D8BE28, 0x4D8BE78),
}


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def find_fm27_process() -> tuple[int, str]:
    for item in find_processes("fm"):
        if item.name.casefold() != "fm.exe" or int(item.thread_count) <= 0:
            continue
        path = item.path or ""
        if not path:
            continue
        try:
            if sha256_of(path) == FM27_EXE_SHA256:
                return int(item.pid), path
        except OSError:
            continue
    raise SystemExit(
        "no FM27 process found (no running fm.exe matches FM27_EXE_SHA256)"
    )


def verify_root_global(
    label: str, module_path: str, pattern: tuple[int | None, ...],
    probe_rva: int, rel32_offset: int, instruction_size: int,
    read_pointer,
) -> int | None:
    """Statically re-resolve a root AOB from disk and check its live pointer."""
    with open(module_path, "rb") as stream:
        import mmap
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as raw:
            hits = _pattern_occurrences(raw, _pe_sections(raw), pattern)
            if hits != [probe_rva]:
                print(
                    f"{label}: FAIL (disk matches {len(hits)} at "
                    f"{[hex(h) for h in hits]}, expected [0x{probe_rva:X}])"
                )
                return None
            tail = _read_rva(raw, _pe_sections(raw), probe_rva + rel32_offset, 4)
            target = probe_rva + instruction_size + struct.unpack("<i", tail)[0]
    head = read_pointer(target, 8)
    if not head or len(head) != 8 or not struct.unpack("<Q", head)[0]:
        print(f"{label}: FAIL (global at 0x{target:X} is null or unreadable)")
        return None
    pointer = struct.unpack("<Q", head)[0]
    print(f"{label}: PASS (probe 0x{probe_rva:X} -> 0x{target:X}, holds 0x{pointer:X})")
    return target


def verify_database_tables(
    process: object, base: int, size: int, root_table_rva: int,
    failures: list[str],
) -> None:
    """Walk the root pointer table slots and sample row-object vtables.

    Requires a loaded save: on the start screen the table slots are null
    and the check is skipped with a notice instead of failing.
    """
    # FM27 slot layout matches FM26's native database container table.
    # 0x4F65538 is a non-CLUB row vtable observed inside the live club
    # table (derived class; RTTI name not statically resolvable yet) and
    # stays allow-listed until it is identified.
    slots = {
        "club": (0x10, {int(FM27_LAYOUT.club_vtable_rva), 0x4F65538}),
        "competition": (0x18, {int(FM27_LAYOUT.competition_vtable_rva)}),
        "national_team_container": (0x60, {int(FM27_LAYOUT.nation_vtable_rva)}),
        "team": (0x98, {
            int(FM27_LAYOUT.team_vtable_rva),
            int(FM27_LAYOUT.national_team_vtable_rva),
        }),
    }

    def read(address: int, length: int):
        return read_process_memory(process, address, length)

    def ptr(address: int) -> int:
        raw = read(address, 8)
        return struct.unpack("<Q", raw)[0] if raw and len(raw) == 8 else 0

    for name, (slot, expected) in slots.items():
        table_object = ptr(base + root_table_rva + slot)
        if not table_object:
            print(
                f"{name} table: SKIP (slot empty; load a save first)"
            )
            continue
        vector = ptr(table_object + 0x80)
        header = read(vector, 0x18) if vector else None
        if not header or len(header) != 0x18:
            failures.append(f"{name} table header unreadable")
            continue
        begin, end, capacity = struct.unpack("<QQQ", header)
        count = (end - begin) // 8
        if (
            not begin or end < begin or capacity < end
            or (end - begin) % 8 or (capacity - begin) % 8
        ):
            failures.append(f"{name} table header invalid")
            continue
        bad = []
        for index in {0, count // 2, max(count - 1, 0)}:
            obj = ptr(begin + index * 8)
            if not obj:
                continue
            vtable = ptr(obj)
            rva = vtable - base if base <= vtable < base + size else -1
            if rva not in expected:
                bad.append(f"row {index} vtable 0x{rva:X}")
        if bad:
            failures.append(f"{name} table row identity mismatch: {'; '.join(bad)}")
        else:
            print(f"{name} table: PASS (rows={count}, row vtables verified)")


def main() -> None:
    pid, process_path = find_fm27_process()
    failures: list[str] = []
    print(f"FM27 process pid={pid} exe={process_path}")
    with open_process(pid) as process:
        module = find_module(process, GAME_PLUGIN)
        if not module:
            raise SystemExit("game_plugin.dll is not loaded yet")
        base, size = int(module.base_address), int(module.size)
        plugin_hash = sha256_of(str(module.path))
        if plugin_hash != FM27_STEAM_PLUGIN_SHA256:
            failures.append(
                f"game_plugin.dll SHA256 mismatch: {plugin_hash} "
                f"(expected {FM27_STEAM_PLUGIN_SHA256})"
            )
        else:
            print("plugin identity: PASS")

        date_address = base + int(FM27_LAYOUT.game_date_rva)
        first = read_process_memory(process, date_address, 4)
        second = read_process_memory(process, date_address, 4)
        if not first or len(first) != 4 or first != second:
            failures.append("game-date readback is unreadable or unstable")
        else:
            code = struct.unpack("<I", first)[0]
            year, day = code >> 16, code & 0x1FF
            if 1900 <= year <= 2200 and 1 <= day <= 366 and (year, day) != (1900, 1):
                print(f"game date: PASS (0x{code:08X} -> year {year}, day {day})")
            else:
                failures.append(f"game-date value failed validation: 0x{code:08X}")

        for name, rvas in PROBE_VTABLES.items():
            for rva in rvas:
                method = read_process_memory(process, base + int(rva), 8)
                if not method or len(method) != 8:
                    failures.append(f"{name} vtable 0x{rva:X} unreadable")
                    continue
                target = struct.unpack("<Q", method)[0]
                if base <= target < base + size:
                    print(
                        f"{name} vtable 0x{rva:X}: PASS "
                        f"(first method rva 0x{target - base:X})"
                    )
                else:
                    failures.append(
                        f"{name} vtable 0x{rva:X} points outside the module"
                    )

        def read_pointer(address: int, length: int):
            return read_process_memory(process, base + address, length)

        resolved_roots: dict[str, int] = {}
        for label, pattern, probe, rel_off, inst_size in (
            (
                "database_root",
                tuple(FM26_LAYOUT.database_root_pattern),
                int(FM27_LAYOUT.database_root_probe_rva),
                int(FM27_LAYOUT.database_root_rel32_offset),
                int(FM27_LAYOUT.database_root_instruction_size),
            ),
            (
                "human_manager_root",
                tuple(FM26_LAYOUT.human_manager_root_pattern),
                int(FM27_LAYOUT.human_manager_root_probe_rva),
                int(FM27_LAYOUT.human_manager_root_rel32_offset),
                int(FM27_LAYOUT.human_manager_root_instruction_size),
            ),
        ):
            resolved = verify_root_global(
                label, str(module.path), pattern, probe, rel_off, inst_size,
                read_pointer,
            )
            if resolved is None:
                failures.append(f"{label} root validation failed")
            else:
                resolved_roots[label] = resolved

        root_table_rva = resolved_roots.get("database_root")
        if root_table_rva:
            verify_database_tables(process, base, size, root_table_rva, failures)
        else:
            failures.append("database tables skipped; database root unresolved")

    if failures:
        print("FAILURES:")
        for failure in failures:
            print(" -", failure)
        raise SystemExit(1)
    print("all FM27 runtime probes passed")


if __name__ == "__main__":
    main()
