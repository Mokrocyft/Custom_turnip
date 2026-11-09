#!/usr/bin/env python3
"""
OnePlus 15 (Snapdragon 8 Elite Gen 5 / SM8850 / Adreno 840) Hardware, Autotune & Power Tuning.

Supports two modes via env var `OP15_POWER_PROFILE`:
- "eco" (default for `a8xx-oneplus15-ultra`):
    * Maximizes FPS-per-Watt and lowers battery drain & heat.
    * Uses smart GMEM autotune (drawcall threshold 8, bandwidth multiplier 9/10).
    * Keeps standard KGSL dynamic frequency scaling active.
- "max" (for `a8xx-oneplus15-max` / WinNative-P style):
    * Aggressive GMEM retention (drawcall threshold 10, bandwidth multiplier 9/10).
    * Enables WinNative-P `KGSL_PROP_PWR_CONSTRAINT = PWR_MAX` at queue creation.
"""
import os
from pathlib import Path
import re

power_profile = os.environ.get("OP15_POWER_PROFILE", "eco")
print(f"a840_oneplus15_tune: configuring for OnePlus 15 profile = '{power_profile}'")

# ── 1. freedreno_devices.py ──────────────────────────────────────────────────
devices_py = Path("src/freedreno/common/freedreno_devices.py")
if devices_py.exists():
    txt = devices_py.read_text(encoding="utf-8")
    changed = False

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


# ── 5. Super-Optimization & Power-Efficiency Suite (Vauzi-17 / Turnip-Z / Droid-Deck / K11MCH1) ──
def _patch_opt(rel_path, old, new, label):
    p = Path(rel_path)
    if not p.exists():
        return
    txt = p.read_text(encoding="utf-8")
    if new in txt:
        print(f"  [ok] {label}")
        return
    if old in txt:
        p.write_text(txt.replace(old, new, 1), encoding="utf-8")
        print(f"  [applied] {label}")
    else:
        print(f"  [info] {label}: anchor not matched, skipping")

# K11MCH1: A8xx CCU flush on suspended render pass resume
_patch_opt(
    "src/freedreno/vulkan/tu_cmd_buffer.cc",
    "   if (cmd->state.suspended) {\n      tu_emit_event_write<CHIP>(cmd, &cmd->cs, FD_CACHE_CLEAN);\n   }",
    "   if (cmd->state.suspended) {\n"
    "      tu_emit_event_write<CHIP>(cmd, &cmd->cs, FD_CACHE_CLEAN);\n"
    "      if (CHIP >= A8XX) {\n"
    "         tu_emit_event_write<CHIP>(cmd, &cmd->cs, FD_CCU_FLUSH_COLOR);\n"
    "         tu_emit_event_write<CHIP>(cmd, &cmd->cs, FD_CCU_FLUSH_DEPTH);\n"
    "      }\n"
    "   }",
    "A8xx CCU flush on suspended render pass resume (K11MCH1)"
)

# LRZ: Dynamic color_write_enable & depth_write_enable + FS side-effects preservation
_patch_opt(
    "src/freedreno/vulkan/tu_lrz.cc",
    "   bool color_write = cmd->state.pipeline->color_write_enable;\n",
    "   bool color_write = cmd->state.pipeline->color_write_enable &&\n"
    "                      cmd->state.color_write_enable;\n",
    "LRZ dynamic color_write_enable check (Droid-Deck / Vauzi-17 #48)"
)
_patch_opt(
    "src/freedreno/vulkan/tu_lrz.cc",
    "   bool z_write = cmd->state.pipeline->depth_write_enable;\n",
    "   bool z_write = cmd->state.pipeline->depth_write_enable &&\n"
    "                  cmd->state.depth_write_enable;\n",
    "LRZ dynamic depth_write_enable check (Turnip-Z #30)"
)
_patch_opt(
    "src/freedreno/vulkan/tu_lrz.cc",
    "   if (fs->has_side_effects) {\n      lrz.valid = false;\n      return lrz;\n   }",
    "   if (fs->has_side_effects) {\n      lrz.write = false;\n   }",
    "Preserve LRZ validity across FS side-effect draws (Vauzi-17 #61)"
)
_patch_opt(
    "src/freedreno/vulkan/tu_lrz.cc",
    "   if (cmd->state.rp.sysmem_single_prim_mode)\n      lrz.valid = false;",
    "   if (cmd->state.rp.sysmem_single_prim_mode && CHIP < A8XX)\n      lrz.valid = false;",
    "Keep LRZ valid in A8xx sysmem single-prim mode (#02)"
)

# Command Buffer: Keep TEX draw state across clears & skip identical dynamic state
_patch_opt(
    "src/freedreno/vulkan/tu_cmd_buffer.cc",
    "      tu_cs_emit_pkt7(cs, CP_SET_DRAW_STATE, 3);\n"
    "      tu_cs_emit(cs, CP_SET_DRAW_STATE_0_GROUP_ID(TU_DRAW_STATE_TEX) |\n"
    "                     CP_SET_DRAW_STATE_0_DISABLE_ALL_GROUPS);\n"
    "      tu_cs_emit_qw(cs, 0);",
    "      /* Turnip-Z #32: keep draw state groups active instead of issuing DISABLE_ALL_GROUPS */",
    "Keep draw state groups active across clears (Turnip-Z #32)"
)
_patch_opt(
    "src/freedreno/vulkan/tu_cmd_buffer.cc",
    "   case VK_DYNAMIC_STATE_DEPTH_BIAS_ENABLE:\n      cmd->state.depth_bias_enable = value;\n      break;",
    "   case VK_DYNAMIC_STATE_DEPTH_BIAS_ENABLE:\n"
    "      if (cmd->state.depth_bias_enable != value) {\n"
    "         cmd->state.depth_bias_enable = value;\n"
    "         cmd->state.dirty |= TU_CMD_DIRTY_LRZ;\n"
    "      }\n"
    "      break;",
    "Dirty LRZ only when dynamic depth_bias_enable changes (#30/#62)"
)
_patch_opt(
    "src/freedreno/vulkan/tu_cmd_buffer.cc",
    "   case VK_DYNAMIC_STATE_DEPTH_WRITE_ENABLE:\n      cmd->state.depth_write_enable = value;\n      cmd->state.dirty |= TU_CMD_DIRTY_LRZ;\n      break;",
    "   case VK_DYNAMIC_STATE_DEPTH_WRITE_ENABLE:\n"
    "      if (cmd->state.depth_write_enable != value) {\n"
    "         cmd->state.depth_write_enable = value;\n"
    "         cmd->state.dirty |= TU_CMD_DIRTY_LRZ;\n"
    "      }\n"
    "      break;",
    "Skip redundant dirtying on identical VK_DYNAMIC_STATE_DEPTH_WRITE_ENABLE (Vauzi-17 #62)"
)

# Turnip-Z #30: Keep MUTABLE_FORMAT images tiled (TILE6_3) & UBWC-compressed
_patch_opt(
    "src/freedreno/vulkan/tu_image.cc",
    "   if (modifier == DRM_FORMAT_MOD_QCOM_COMPRESSED) {\n      ubwc_enabled = true;\n   }",
    "   if (modifier == DRM_FORMAT_MOD_QCOM_COMPRESSED) {\n"
    "      force_linear_tile = false;\n"
    "      tile_mode = TILE6_3;\n"
    "      ubwc_enabled = true;\n"
    "   }",
    "Keep QCOM_COMPRESSED mutable images tiled and UBWC-enabled (Turnip-Z #30)"
)

# IR3 Register Pressure & Instruction Scheduler Optimization (Vauzi-17 #63)
sched_file = Path("src/freedreno/ir3/ir3_sched.c")
if sched_file.exists():
    stxt = sched_file.read_text(encoding="utf-8")
    old_sched = "   /* Prefer instructions that free up registers: */\n   int live_effect_a = live_effect(n1->instr);\n   int live_effect_b = live_effect(n2->instr);"
    new_sched = "   /* Vauzi-17 #63: prioritize instructions with lower live register pressure growth */\n   int live_effect_a = live_effect(n1->instr);\n   int live_effect_b = live_effect(n2->instr);\n   if (live_effect_a != live_effect_b)\n      return live_effect_a - live_effect_b;"
    if new_sched not in stxt and old_sched in stxt:
        sched_file.write_text(stxt.replace(old_sched, new_sched, 1), encoding="utf-8")
        print("  [applied] IR3 scheduler live register pressure prioritization (#63)")

# Remove any forced TU_DEBUG_FLUSHALL on A8xx in tu_device.cc
dev_file = Path("src/freedreno/vulkan/tu_device.cc")
if dev_file.exists():
    dtxt = dev_file.read_text(encoding="utf-8")
    if "tu_env.debug |= TU_DEBUG_FLUSHALL;" in dtxt:
        dtxt = dtxt.replace("tu_env.debug |= TU_DEBUG_FLUSHALL;", "/* Removed TU_DEBUG_FLUSHALL on A8xx for performance */")
        dev_file.write_text(dtxt, encoding="utf-8")
        print("  [applied] Removed forced TU_DEBUG_FLUSHALL on A8xx")

print("a840_oneplus15_tune.py: done")
