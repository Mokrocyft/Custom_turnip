#!/usr/bin/env python3
"""
OnePlus 15 (Snapdragon 8 Elite Gen 5 / SM8850 / Adreno 840) Hardware, Autotune & Power Tuning.

Supports two modes via env var `OP15_POWER_PROFILE`:
- "eco" (default for `a8xx-oneplus15-ultra`):
    * Maximizes FPS-per-Watt and lowers battery drain & heat.
    * Uses smart GMEM autotune (drawcall threshold 8, bandwidth multiplier 9/10) so tiny passes
      skip GMEM binning overhead while heavy deferred lighting passes stay inside the 18 MB HPM
      (avoiding costly external LPDDR5X traffic).
    * Keeps standard KGSL dynamic frequency scaling active (no artificial PWR_MAX lock).
- "max" (for `a8xx-oneplus15-max` / WinNative-P style):
    * Aggressive GMEM retention (drawcall threshold 10, bandwidth multiplier 9/10).
    * Enables WinNative-P `KGSL_PROP_PWR_CONSTRAINT = PWR_MAX` at queue creation to lock
      Adreno 840 at peak clocks for zero clock-drop stutters.
"""
import os
from pathlib import Path
import re
import sys

power_profile = os.environ.get("OP15_POWER_PROFILE", "eco")
print(f"a840_oneplus15_tune: configuring for OnePlus 15 profile = '{power_profile}'")

# ── 1. freedreno_devices.py ──────────────────────────────────────────────────
devices_py = Path("src/freedreno/common/freedreno_devices.py")
if devices_py.exists():
    txt = devices_py.read_text(encoding="utf-8")
    changed = False

    # Upgrade a8xx_gen2 depth & GMEM color cache for 18 MB HPM on Adreno 840
    a8xx_gen2_start = txt.find("a8xx_gen2 = GPUProps(")
    if a8xx_gen2_start >= 0:
        a8xx_gen2_end = txt.find(")", a8xx_gen2_start)
        if a8xx_gen2_end >= 0:
            block = txt[a8xx_gen2_start:a8xx_gen2_end]
            new_block = block.replace(
                "sysmem_ccu_depth_cache_fraction = CCUColorCacheFraction.THREE_QUARTER.value,",
                "sysmem_ccu_depth_cache_fraction = CCUColorCacheFraction.FULL.value,"
            ).replace(
                "sysmem_per_ccu_depth_cache_size = 192 * 1024,",
                "sysmem_per_ccu_depth_cache_size = 256 * 1024,"
            ).replace(
                "gmem_ccu_color_cache_fraction = CCUColorCacheFraction.EIGHTH.value,",
                "gmem_ccu_color_cache_fraction = CCUColorCacheFraction.QUARTER.value,"
            ).replace(
                "gmem_per_ccu_color_cache_size = 16 * 1024,",
                "gmem_per_ccu_color_cache_size = 64 * 1024,"
            )
            if new_block != block:
                txt = txt[:a8xx_gen2_start] + new_block + txt[a8xx_gen2_end:]
                changed = True
                print("a840_oneplus15_tune: upgraded a8xx_gen2 CCU cache allocations for 18MB HPM")

    # Ensure all Adreno 840 KGSL speedbin IDs are registered
    a840_ids = [
        ("0x44050A31", "840"),
        ("0x44050A30", "840"),
        ("0xffff44050A30", "840"),
        ("0x43050A32", "840"),
    ]
    m = re.search(r'^\s*GPUId\(chip_id=(?:0xffff44050A31|0x44050A31)\b[^\n]*$', txt, re.M | re.I)
    if m:
        stop = txt.find("], A6xxGPUInfo(", m.end())
        if stop != -1:
            additions = []
            for cid, cname in a840_ids:
                if not re.search(r"GPUId\(chip_id=" + re.escape(cid) + r"\b", txt, re.I):
                    additions.append(f'        GPUId(chip_id={cid}, name="Adreno (TM) {cname}"), # KGSL OnePlus 15\n')
            if additions:
                txt = txt[:stop] + "".join(additions) + txt[stop:]
                changed = True
                print("a840_oneplus15_tune: added OnePlus 15 Adreno 840 KGSL speedbin IDs")

    if changed:
        compile(txt, str(devices_py), "exec")
        devices_py.write_text(txt, encoding="utf-8")

# ── 2. tu_knl_kgsl.cc (UBWC 5/6 + optional WinNative-P PWR_MAX turbo lock) ────
kgsl_cc = Path("src/freedreno/vulkan/tu_knl_kgsl.cc")
if kgsl_cc.exists():
    ktxt = kgsl_cc.read_text(encoding="utf-8")
    kchanged = False
    if "case KGSL_UBWC_4_0:" in ktxt and "case 5:" not in ktxt and "KGSL_UBWC_5_0" not in ktxt:
        ktxt = ktxt.replace(
            "case KGSL_UBWC_4_0:",
            "case KGSL_UBWC_4_0:\n   case 5:\n   case 6:",
            1
        )
        kchanged = True
        print("a840_oneplus15_tune: enabled UBWC_5 and UBWC_6 in tu_knl_kgsl.cc")

    if power_profile == "max" and "wnturnip_set_pwr_max" not in ktxt:
        helper_code = """
#ifndef KGSL_PROP_PWR_CONSTRAINT
#define KGSL_PROP_PWR_CONSTRAINT 0x12
#define KGSL_CONSTRAINT_PWRLEVEL 1
#define KGSL_CONSTRAINT_PWR_MAX  1
struct kgsl_device_constraint_pwrlevel {
   unsigned int sub_type;
};
struct kgsl_device_constraint {
   unsigned int type;
   unsigned int context_id;
   void *data;
   size_t size;
};
#endif

static void
wnturnip_set_pwr_max(int fd, uint32_t context_id)
{
   struct kgsl_device_constraint_pwrlevel pwr = {
      .sub_type = KGSL_CONSTRAINT_PWR_MAX,
   };
   struct kgsl_device_constraint constraint = {
      .type = KGSL_CONSTRAINT_PWRLEVEL,
      .context_id = context_id,
      .data = &pwr,
      .size = sizeof(pwr),
   };
   struct kgsl_device_getproperty prop = {
      .type = KGSL_PROP_PWR_CONSTRAINT,
      .value = &constraint,
      .sizebytes = sizeof(constraint),
   };
   safe_ioctl(fd, IOCTL_KGSL_SETPROPERTY, &prop);
}
"""
        safe_anchor = "safe_ioctl("
        if safe_anchor in ktxt:
            idx = ktxt.find("\n}\n", ktxt.find(safe_anchor))
            if idx != -1:
                ktxt = ktxt[:idx+3] + helper_code + ktxt[idx+3:]
                if "queue->msm_queue_id = req.drawctxt_id;" in ktxt:
                    ktxt = ktxt.replace(
                        "queue->msm_queue_id = req.drawctxt_id;",
                        "queue->msm_queue_id = req.drawctxt_id;\n   wnturnip_set_pwr_max(device->fd, req.drawctxt_id);",
                        1
                    )
                kchanged = True
                print("a840_oneplus15_tune: injected WinNative-P PWR_MAX clock constraint for Max profile")

    if kchanged:
        kgsl_cc.write_text(ktxt, encoding="utf-8")

# ── 3. ir3_compiler.c (Fix 32-bit switch & unlock 100% vec4 registers) ───────
ir3_c = Path("src/freedreno/ir3/ir3_compiler.c")
if ir3_c.exists():
    itxt = ir3_c.read_text(encoding="utf-8")
    if "ir3_get_gpu_profile" in itxt:
        old_prof = """    case 0x43050A32: /* Adreno 840 */
        return (struct ir3_gpu_profile){70, 20, 16, true};"""
        new_prof = """    case 0x43050A32: /* Adreno 840 */
    case 0x44050A31:
    case 0x44050A30:
        return (struct ir3_gpu_profile){100, 24, 16, false};"""
        if old_prof in itxt:
            itxt = itxt.replace(old_prof, new_prof, 1)
            ir3_c.write_text(itxt, encoding="utf-8")
            print("a840_oneplus15_tune: unlocked 100% vec4 register efficiency for Adreno 840 in ir3_compiler.c")

# ── 4. tu_autotune.cc (18 MB HPM GMEM retention for Dying Light) ─────────────
autotune_cc = Path("src/freedreno/vulkan/tu_autotune.cc")
if autotune_cc.exists():
    atxt = autotune_cc.read_text(encoding="utf-8")
    achanged = False
    target_dc = 10 if power_profile == "max" else 8
    for old_dc in (
        "if (cmd_buffer->state.rp.drawcall_count > 5)",
        "if (cmd_buffer->state.rp.drawcall_count > 7)",
        "if (cmd_buffer->state.rp.drawcall_count > 8)",
    ):
        if old_dc in atxt:
            atxt = atxt.replace(old_dc, f"if (cmd_buffer->state.rp.drawcall_count > {target_dc})", 1)
            achanged = True
            print(f"a840_oneplus15_tune: tuned autotune drawcall threshold -> {target_dc}")
            break
    for old_bw in (
        "gmem_bandwidth = (gmem_bandwidth * 11 + total_draw_call_bandwidth) / 10;",
        "gmem_bandwidth = (gmem_bandwidth * 10 + total_draw_call_bandwidth) / 10;",
    ):
        if old_bw in atxt:
            atxt = atxt.replace(old_bw, "gmem_bandwidth = (gmem_bandwidth * 9 + total_draw_call_bandwidth) / 10;", 1)
            achanged = True
            print("a840_oneplus15_tune: tuned GMEM bandwidth factor -> 9/10 for 18MB HPM")
            break
    if achanged:
        autotune_cc.write_text(atxt, encoding="utf-8")

print("a840_oneplus15_tune.py: done")
