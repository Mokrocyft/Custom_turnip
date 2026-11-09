#!/usr/bin/env python3
"""
OnePlus 15 (Snapdragon 8 Elite Gen 5 / SM8850 / Adreno 840) Next-Gen Custom Optimization
& Ultra-Low-Power Suite for Mesa 26.3 (`staging/26.3`).

Combines custom technologies and verified source-level patches from:
  - Custom Adreno 840 18MB HPM Energy-Aware Autotune (7/10 Eco, 6/10 MaxPerf)
  - Custom Adreno 840 UBO Range Coalescing (128B Eco / 256B MaxPerf gap merging into constant RAM)
  - Vauzi-17/710 (#53, #55, #60, #61, #62, #63) with flexible Mesa 26.3 regex anchors
  - Turnip-Z (#30 Mutable UBWC TILE6_3)
  - K11MCH1 / Droid-Deck (A8xx suspended render-pass CCU flush)
  - NOTE: KGSL_PROP_PWRCTRL=0 is intentionally NOT used because it disables Qualcomm DCVS
    frequency scaling on Snapdragon 8 Elite Gen 5 and locks Adreno 840 below peak 1.2 GHz!
"""
import os
from pathlib import Path
import re

power_profile = os.environ.get("OP15_POWER_PROFILE", "eco")
print(f"a840_oneplus15_tune: configuring OnePlus 15 Adreno 840 Next-Gen Suite (profile = '{power_profile}')")

# ── 1. freedreno_devices.py (Adreno 840 18MB HPM Cache + KGSL IDs) ───────────
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
                print("  [applied] a8xx_gen2 CCU cache upgraded for 18MB HPM (256KB depth / 64KB GMEM color)")

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
                print("  [applied] OnePlus 15 Adreno 840 KGSL speedbin IDs registered")

    if changed:
        compile(txt, str(devices_py), "exec")
        devices_py.write_text(txt, encoding="utf-8")

# ── 2. tu_knl_kgsl.cc (UBWC 5/6 without breaking DCVS boost!) ─────────────────
kgsl_cc = Path("src/freedreno/vulkan/tu_knl_kgsl.cc")
if kgsl_cc.exists():
    ktxt = kgsl_cc.read_text(encoding="utf-8")
    if "case KGSL_UBWC_4_0:" in ktxt and "case 5:" not in ktxt and "KGSL_UBWC_5_0" not in ktxt:
        ktxt = ktxt.replace(
            "case KGSL_UBWC_4_0:",
            "case KGSL_UBWC_4_0:\n   case 5:\n   case 6:",
            1
        )
        kgsl_cc.write_text(ktxt, encoding="utf-8")
        print("  [applied] UBWC_5 and UBWC_6 enabled in tu_knl_kgsl.cc")

# ── 3. ir3_compiler.c & UBO Range Coalescing for Adreno 840 ──────────────────
ir3_c = Path("src/freedreno/ir3/ir3_compiler.c")
if ir3_c.exists():
    itxt = ir3_c.read_text(encoding="utf-8")
    ichanged = False
    if "ir3_get_gpu_profile" in itxt:
        old_prof = """    case 0x43050A32: /* Adreno 840 */
        return (struct ir3_gpu_profile){70, 20, 16, true};"""
        new_prof = """    case 0x43050A32: /* Adreno 840 */
    case 0x44050A31:
    case 0x44050A30:
        return (struct ir3_gpu_profile){100, 24, 16, false};"""
        if old_prof in itxt:
            itxt = itxt.replace(old_prof, new_prof, 1)
            ichanged = True
            print("  [applied] Unlocked 100% vec4 register efficiency for Adreno 840 in ir3_compiler.c")

    itxt, n_coal = re.subn(
        r"(compiler->coalesce_ubo_push_ranges\s*=\s*)[^;]+;",
        r"\1(compiler->gen >= 8);",
        itxt,
        count=1,
    )
    if n_coal:
        ichanged = True
        print("  [applied] Enabled UBO push range coalescing for all Gen8 / Adreno 840 in ir3_compiler.c")

    if ichanged:
        ir3_c.write_text(itxt, encoding="utf-8")

ubo_c = Path("src/freedreno/ir3/ir3_nir_analyze_ubo_ranges.c")
if ubo_c.exists():
    utxt = ubo_c.read_text(encoding="utf-8")
    ubo_gap = 256 if power_profile == "max" else 128
    old_gap = "if (r.start > plan_r->end || r.end < plan_r->start)\n         continue;"
    new_gap = (
        f"/* Custom Adreno 840 optimization: coalesce nearby UBO loads within {ubo_gap} bytes\n"
        "       * into the constant file to eliminate memory-backed LDC instructions in DXVK! */\n"
        "      uint32_t gap = (plan_r->end < r.start) ? (r.start - plan_r->end) :\n"
        "                     ((r.end < plan_r->start) ? (plan_r->start - r.end) : 0);\n"
        f"      if (gap > {ubo_gap})\n"
        "         continue;"
    )
    if old_gap in utxt and f"gap > {ubo_gap}" not in utxt:
        utxt = utxt.replace(old_gap, new_gap, 1)
        old_neigh = "if (a->start > b->end || a->end < b->start)\n         continue;"
        new_neigh = (
            "uint32_t gap = (a->end < b->start) ? (b->start - a->end) :\n"
            "                     ((b->end < a->start) ? (a->start - b->end) : 0);\n"
            f"      if (gap > {ubo_gap})\n"
            "         continue;"
        )
        if old_neigh in utxt:
            utxt = utxt.replace(old_neigh, new_neigh, 1)
        ubo_c.write_text(utxt, encoding="utf-8")
        print(f"  [applied] Custom {ubo_gap}-byte UBO range coalescing in ir3_nir_analyze_ubo_ranges.c")

# ── 4. tu_autotune.cc (Custom Energy-Aware 18MB HPM Autotuner for Muck & Dying Light) ──
autotune_cc = Path("src/freedreno/vulkan/tu_autotune.cc")
if autotune_cc.exists():
    atxt = autotune_cc.read_text(encoding="utf-8")
    achanged = False

    # Stronger GMEM preference: 7/10 in Ultra/Eco (keeps passes in 18MB HPM to save LPDDR5X battery),
    # 6/10 in MaxPerf (maximizes on-chip HPM bandwidth for peak FPS).
    bw_mult = 6 if power_profile == "max" else 7
    atxt, n_bw = re.subn(
        r"gmem_bandwidth\s*=\s*\(\s*gmem_bandwidth\s*\*\s*(?:8|9|10|11)\s*\+\s*total_draw_call_bandwidth\s*\)\s*/\s*10\s*;",
        f"gmem_bandwidth = (gmem_bandwidth * {bw_mult} + total_draw_call_bandwidth) / 10;",
        atxt,
        count=1,
    )
    if n_bw:
        achanged = True
        print(f"  [applied] Energy-Aware 18MB HPM GMEM bandwidth factor -> {bw_mult}/10 in tu_autotune.cc")

    atxt, n_dc = re.subn(r"(drawcall_count\s*[<>]=?\s*)(?:5|10)\b", r"\g<1>6", atxt, count=1)
    if n_dc:
        achanged = True
        print("  [applied] Tuned Mesa 26.3 drawcall_count bypass threshold -> 6 for 18MB HPM")

    hist_target = "1" if power_profile == "max" else "2"
    atxt, n_hist = re.subn(r"(history_size\s*=\s*)(?:4|5)\b", rf"\g<1>{hist_target}", atxt, count=1)
    if n_hist:
        achanged = True
        print(f"  [applied] Reduced autotune history window -> {hist_target} frame(s) for instant adaptation")

    if achanged:
        autotune_cc.write_text(atxt, encoding="utf-8")

# ── 5. Exact Mesa 26.3 Vauzi-17 / Turnip-Z / Droid-Deck Optimizations ────────

# 5a. Vauzi-17 #60: Relax subpass slice wait from WFI (Wait For Idle) to WAIT_FOR_ME in sysmem mode
cmd_cc = Path("src/freedreno/vulkan/tu_cmd_buffer.cc")
if cmd_cc.exists():
    ctxt = cmd_cc.read_text(encoding="utf-8")
    cchanged = False

    wfi_pat = re.compile(r'(if\s*\(\s*emit_subpass_slice_wait\s*\)\s*)\n(\s*)tu_cs_emit_wfi\s*\(\s*cs\s*\)\s*;')
    if wfi_pat.search(ctxt):
        ctxt = wfi_pat.sub(r'\1\n\2tu_cs_emit_pkt7(cs, CP_WAIT_FOR_ME, 0);', ctxt, count=1)
        cchanged = True
        print("  [applied] Relaxed subpass slice wait from WFI to CP_WAIT_FOR_ME (#60)")

    susp_pat = re.compile(r'(if\s*\(\s*cmd->state\.suspended\s*\)\s*\{\s*\n\s*tu_emit_event_write<CHIP>\s*\(\s*cmd\s*,\s*&cmd->cs\s*,\s*FD_CACHE_CLEAN\s*\)\s*;)')
    if susp_pat.search(ctxt) and "FD_CCU_FLUSH_COLOR" not in ctxt[ctxt.find("cmd->state.suspended"):ctxt.find("cmd->state.suspended")+350]:
        ctxt = susp_pat.sub(
            r'\1\n      if (CHIP >= A8XX) {\n         tu_emit_event_write<CHIP>(cmd, &cmd->cs, FD_CCU_FLUSH_COLOR);\n         tu_emit_event_write<CHIP>(cmd, &cmd->cs, FD_CCU_FLUSH_DEPTH);\n      }',
            ctxt,
            count=1
        )
        cchanged = True
        print("  [applied] A8xx CCU flush before suspended render pass chain resume (K11MCH1)")

    if cchanged:
        cmd_cc.write_text(ctxt, encoding="utf-8")

# 5b. Vauzi-17 #61 & #55 & #53: LRZ preservation in src/freedreno/vulkan/tu_lrz.cc
lrz_cc = Path("src/freedreno/vulkan/tu_lrz.cc")
if lrz_cc.exists():
    ltxt = lrz_cc.read_text(encoding="utf-8")
    lchanged = False

    old_fs_lrz = 'if (cmd->state.lrz.prev_direction != TU_LRZ_UNKNOWN || !cmd->state.lrz.gpu_dir_tracking) {'
    new_fs_lrz = 'if (true || cmd->state.lrz.prev_direction != TU_LRZ_UNKNOWN || !cmd->state.lrz.gpu_dir_tracking) {'
    if old_fs_lrz in ltxt:
        ltxt = ltxt.replace(old_fs_lrz, new_fs_lrz, 1)
        lchanged = True
        print("  [applied] Preserve LRZ state across FS side-effect draws (Vauzi-17 #61)")

    dir_pat = re.compile(
        r'if\s*\(\s*z_write_enable\s*\)\s*\{\s*\n\s*tu_lrz_invalidate\s*\(\s*cmd\s*,\s*"Depth write \+ compare-op direction change"\s*\)\s*;\s*\n\s*\}\s*else\s*\{'
    )
    if dir_pat.search(ltxt):
        ltxt = dir_pat.sub(
            'if (cmd->state.lrz.gpu_dir_tracking) {\n'
            '         temporary_disable_lrz = true;\n'
            '         if (z_write_enable)\n'
            '            cmd->state.lrz.prev_direction = lrz_direction;\n'
            '      } else if (z_write_enable) {\n'
            '         tu_lrz_invalidate(cmd, "Depth write + compare-op direction change");\n'
            '      } else {',
            ltxt,
            count=1
        )
        lchanged = True
        print("  [applied] Preserve LRZ across depth compare-op direction changes (Vauzi-17 #55)")

    old_bidir = "const struct tu_reg_value reg = GRAS_SC_BIN_CNTL(CHIP, .force_lrz_dis = true);"
    new_bidir = "const struct tu_reg_value reg = GRAS_SC_BIN_CNTL(CHIP, .force_lrz_dis = true, .force_bi_dir_lrz_disable = CHIP >= A8XX);"
    if old_bidir in ltxt:
        ltxt = ltxt.replace(old_bidir, new_bidir, 1)
        lchanged = True
        print("  [applied] A8xx FORCE_BI_DIR_LRZ_DISABLE in GRAS_SC_BIN_CNTL (#53)")

    if lchanged:
        lrz_cc.write_text(ltxt, encoding="utf-8")

# 5c. Vauzi-17 #63: IR3 Instruction Scheduler Live Register Pressure Reduction
sched_c = Path("src/freedreno/ir3/ir3_sched.c")
if sched_c.exists():
    stxt = sched_c.read_text(encoding="utf-8")
    old_choose = """      unsigned distance = nearest_use(n->instr);

      if (!chosen || rank > chosen_rank ||
          (rank == chosen_rank && distance < chosen_distance)) {
         chosen = n;
         chosen_distance = distance;
         chosen_rank = rank;
      }"""
    new_choose = """      unsigned distance = nearest_use(n->instr);
      int live = live_effect(n->instr);

      bool better = !chosen || rank > chosen_rank;
      if (!better && rank == chosen_rank) {
         if (live < chosen_live)
            better = true;
         else if (live == chosen_live && distance < chosen_distance)
            better = true;
      }

      if (better) {
         chosen = n;
         chosen_distance = distance;
         chosen_rank = rank;
         chosen_live = live;
      }"""
    if old_choose in stxt and "int chosen_live = 0;" not in stxt:
        stxt = re.sub(r"(\b(?:unsigned|int)\s+chosen_distance\b[^\n]*\n)", r"\1   int chosen_live = 0;\n", stxt, count=1)
        stxt = stxt.replace(old_choose, new_choose, 1)
        sched_c.write_text(stxt, encoding="utf-8")
        print("  [applied] IR3 scheduler live register pressure reduction in choose_instr_inc (Vauzi-17 #63)")

# 5d. Vauzi-17 #62: Skip redundant dynamic draw state packet emission in tu_cmd_buffer.h & tu_pipeline.cc
cmd_h = Path("src/freedreno/vulkan/tu_cmd_buffer.h")
pipe_cc = Path("src/freedreno/vulkan/tu_pipeline.cc")
if cmd_h.exists() and pipe_cc.exists():
    htxt = cmd_h.read_text(encoding="utf-8")
    ptxt = pipe_cc.read_text(encoding="utf-8")
    old_h = "struct tu_draw_state dynamic_state[TU_DYNAMIC_STATE_COUNT];"
    new_h = "struct tu_draw_state dynamic_state[TU_DYNAMIC_STATE_COUNT];\n   const uint32_t *dynamic_state_map[TU_DYNAMIC_STATE_COUNT];"
    p_pat = re.compile(
        r"tu_cs_begin_sub_stream\(&cmd->sub_cs,\s*size,\s*&cs\);\s*\\\n"
        r"\s*tu6_emit_##name<CHIP>\(&cs,\s*__VA_ARGS__\);\s*\\\n"
        r"\s*cmd->state\.dynamic_state\[id\]\s*=\s*\\\n"
        r"\s*tu_cs_end_draw_state\(&cmd->sub_cs,\s*&cs\);\s*\\\n"
        r"\s*dirty_draw_states\s*\|=\s*\(1u\s*<<\s*id\);"
    )
    new_p = (
        "tu_cs_begin_sub_stream(&cmd->sub_cs, size, &cs); \\\n"
        "         tu6_emit_##name<CHIP>(&cs, __VA_ARGS__); \\\n"
        "         uint32_t emit_size = tu_cs_get_size(&cs); \\\n"
        "         if (cmd->state.dynamic_state_map[id] != NULL && \\\n"
        "             cmd->state.dynamic_state[id].size == emit_size && \\\n"
        "             memcmp(cmd->state.dynamic_state_map[id], cs.start, emit_size * sizeof(uint32_t)) == 0) { \\\n"
        "            cmd->sub_cs.cur = cs.start; \\\n"
        "            cmd->sub_cs.start = cs.start; \\\n"
        "         } else { \\\n"
        "            cmd->state.dynamic_state_map[id] = cs.start; \\\n"
        "            cmd->state.dynamic_state[id] = tu_cs_end_draw_state(&cmd->sub_cs, &cs); \\\n"
        "            dirty_draw_states |= (1u << id); \\\n"
        "         }"
    )
    if old_h in htxt and "dynamic_state_map" not in htxt and p_pat.search(ptxt):
        cmd_h.write_text(htxt.replace(old_h, new_h, 1), encoding="utf-8")
        pipe_cc.write_text(p_pat.sub(new_p, ptxt), encoding="utf-8")
        print("  [applied] Skip identical dynamic draw state packets (Vauzi-17 #62)")

# 5e. Turnip-Z #30: Keep MUTABLE_FORMAT images UBWC-compressed in tu_image.cc
img_cc = Path("src/freedreno/vulkan/tu_image.cc")
if img_cc.exists():
    imtxt = img_cc.read_text(encoding="utf-8")
    mod_pat = re.compile(r'(if\s*\(\s*modifier\s*==\s*DRM_FORMAT_MOD_QCOM_COMPRESSED\s*\)\s*\{[^\n]*\n\s*ubwc_enabled\s*=\s*true\s*;)')
    if mod_pat.search(imtxt) and "force_linear_tile = false;" not in imtxt:
        imtxt = mod_pat.sub(r'\1\n      force_linear_tile = false;\n      tile_mode = TILE6_3;', imtxt, count=1)
        img_cc.write_text(imtxt, encoding="utf-8")
        print("  [applied] Keep QCOM_COMPRESSED mutable images tiled (TILE6_3) and UBWC-enabled (Turnip-Z #30)")

# 5f. Remove any forced TU_DEBUG_FLUSHALL on A8xx in tu_device.cc
dev_file = Path("src/freedreno/vulkan/tu_device.cc")
if dev_file.exists():
    dtxt = dev_file.read_text(encoding="utf-8")
    if "tu_env.debug |= TU_DEBUG_FLUSHALL;" in dtxt:
        dtxt = dtxt.replace("tu_env.debug |= TU_DEBUG_FLUSHALL;", "/* Removed TU_DEBUG_FLUSHALL on A8xx for performance */")
        dev_file.write_text(dtxt, encoding="utf-8")
        print("  [applied] Removed forced TU_DEBUG_FLUSHALL on A8xx")

print("a840_oneplus15_tune.py: done")
