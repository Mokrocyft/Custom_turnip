#!/usr/bin/env python3
"""
WN-Turnip A8xx GPU support applicator for freedreno_devices.py.

Adjusts upstream Mesa 26.3 (`staging/26.3`) so A810, A825, A829, and A840
entries include all KGSL speedbin chip IDs and appropriate GPUProps without
emitting noisy anchor warnings.

Safe to run multiple times.
"""
import re
import sys

DEVICES_PY = "src/freedreno/common/freedreno_devices.py"

with open(DEVICES_PY, "r", encoding="utf-8") as f:
    content = f.read()

changes = []

# ── A810 ──────────────────────────────────────────────────────────────────────
A810_LOCATE = "GPUId(chip_id=0xffff44010000"
if A810_LOCATE in content:
    if "GPUId(chip_id=0x44010000" not in content:
        content, n = re.subn(
            r'(\s*GPUId\(chip_id=0xffff44010000,\s*name="Adreno \(TM\) 810"\),)',
            r'\n        GPUId(chip_id=0x44010000, name="Adreno (TM) 810"),\1',
            content,
            count=1,
        )
        if n:
            changes.append("added speedbin chip_id 0x44010000 to A810 entry")

    a810_idx = content.find(A810_LOCATE)
    a810_window = content[a810_idx:a810_idx + 1200]
    if "disable_gmem = True" not in a810_window:
        new_window, n = re.subn(
            r"(has_fs_tex_prefetch\s*=\s*False,)",
            r"\1\n            disable_gmem = True,",
            a810_window,
            count=1,
        )
        if n:
            content = content[:a810_idx] + new_window + content[a810_idx + 1200:]
            changes.append("injected disable_gmem=True into A810 GPUProps")

# ── A829 ──────────────────────────────────────────────────────────────────────
A829_LOCATE = "GPUId(chip_id=0x44030a20"
if A829_LOCATE in content:
    if "GPUId(chip_id=0x44030A00" not in content:
        content, n = re.subn(
            r'(\s*GPUId\(chip_id=0x44030a20,\s*name="Adreno \(TM\) 829"\),[^\n]*)',
            r'\n        GPUId(chip_id=0x44030A00, name="Adreno (TM) 829"), # KGSL'
            r'\n        GPUId(chip_id=0xffff44030A00, name="Adreno (TM) 829"), # KGSL fallback\1',
            content,
            count=1,
        )
        if n:
            changes.append("added KGSL chip_ids 0x44030A00 / 0xffff44030A00 to A829 entry")

    a829_idx = content.find(A829_LOCATE)
    a829_window = content[a829_idx:a829_idx + 1200]
    if "disable_gmem = True" not in a829_window:
        new_window, n = re.subn(
            r"(sysmem_vpc_bv_pos_buf_size\s*=\s*24576,)",
            r"\1\n            disable_gmem = True,",
            a829_window,
            count=1,
        )
        if n:
            content = content[:a829_idx] + new_window + content[a829_idx + 1200:]
            changes.append("injected disable_gmem=True into A829 GPUProps")

# ── A825 ──────────────────────────────────────────────────────────────────────
A825_BLOCK = """

add_gpus([
        GPUId(chip_id=0x44030000, name="Adreno (TM) 825"),
    ], A6xxGPUInfo(
        CHIP.A8XX,
        [a7xx_base, a7xx_gen3, a8xx_base, a8xx_gen1, GPUProps(
            sysmem_ccu_depth_cache_fraction = CCUColorCacheFraction.THREE_QUARTER.value,
            sysmem_per_ccu_depth_cache_size = 96 * 1024,
        )],
        num_ccu = 4,
        num_slices = 2,
        tile_align_w = 96,
        tile_align_h = 32,
        tile_max_w = 16416,
        tile_max_h = 16384,
        num_vsc_pipes = 32,
        cs_shared_mem_size = 32 * 1024,
        wave_granularity = 2,
        fibers_per_sp = 128 * 2 * 16,
        magic_regs = dict(),
        raw_magic_regs = a8xx_base_raw_magic_regs,
    ))
"""

if 'GPUId(chip_id=0x44030000' not in content and 'name="FD825"' not in content:
    a810_idx = content.find(A810_LOCATE)
    if a810_idx != -1:
        m = re.search(r"raw_magic_regs\s*=\s*a8xx_base_raw_magic_regs,\s*\n\s*\)\)\n", content[a810_idx:])
        if m:
            insert_at = a810_idx + m.end()
            content = content[:insert_at] + A825_BLOCK + content[insert_at:]
            changes.append("inserted A825 entry after A810")

# ── Write result ──────────────────────────────────────────────────────────────
if changes:
    try:
        compile(content, DEVICES_PY, "exec")
    except SyntaxError as e:
        print(f"  FATAL: syntax error after patching at line {e.lineno}: {e.msg}", file=sys.stderr)
        sys.exit(1)
    with open(DEVICES_PY, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  Applied {len(changes)} change(s) to {DEVICES_PY}:")
    for c in changes:
        print(f"    + {c}")
else:
    print(f"  All A8xx GPU entries in {DEVICES_PY} already up to date")

print("apply_a8xx_gpus.py: done")
