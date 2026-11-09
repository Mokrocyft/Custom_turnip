#!/usr/bin/env python3
"""
Apply executable-specific & engine-level performance, power-saving & compatibility profiles
for OnePlus 15 (Snapdragon 8 Elite Gen 5 / Adreno 840) in `00-turnip-defaults.conf`:

1. Overrides upstream Mesa's global `<engine engine_name_match="DXVK|vkd3d">` rule from
   `tu_autotune_algorithm="prefer_sysmem"` to `"bandwidth"` on Adreno 840 (which has 18 MB HPM),
   plus `tu_emulate_alpha_to_coverage=false`, `tu_dont_care_as_load=false`, and
   `tu_allow_oob_indirect_ubo_loads=true`, cutting external LPDDR5X DRAM traffic & shader bloat
   across ALL DXVK games (Muck, Dying Light, etc.) even when the emulator masks the .exe name!
2. Adds a dedicated `<engine engine_name_match="Unity.*">` rule with the same hardware A2C and
   18MB HPM bandwidth autotune.
3. Adds dedicated profiles for Muck, Dying Light 1/2/The Beast, Cyberpunk 2077, MIO, and UNCHARTED.
"""
from pathlib import Path
import xml.etree.ElementTree as ET

p = Path("src/freedreno/vulkan/00-turnip-defaults.conf")
s = p.read_text(encoding="utf-8")

# ── 1. Upgrade global DXVK|vkd3d & Unity engine defaults for Adreno 840 18MB HPM ──
dxvk_idx = s.find('engine_name_match="DXVK|vkd3d"')
if dxvk_idx != -1:
    dxvk_end = s.find("</engine>", dxvk_idx)
    if dxvk_end != -1:
        block = s[dxvk_idx:dxvk_end]
        new_block = block.replace(
            '<option name="tu_autotune_algorithm" value="prefer_sysmem" />',
            '<option name="tu_autotune_algorithm" value="bandwidth" />\n'
            '            <option name="tu_dont_care_as_load" value="false" />\n'
            '            <option name="tu_allow_oob_indirect_ubo_loads" value="true" />'
        ).replace(
            '<option name="tu_emulate_alpha_to_coverage" value="true" />',
            '<option name="tu_emulate_alpha_to_coverage" value="false" />'
        )
        if new_block != block:
            s = s[:dxvk_idx] + new_block + s[dxvk_end:]
            print("game_ssbo_profiles: upgraded DXVK|vkd3d engine rule -> bandwidth autotune + native A2C + no dont_care load")

anchor = "    </device>"
if anchor not in s:
    raise SystemExit("game_ssbo_profiles: Mesa driconf device anchor missing")

if 'engine_name_match="Unity' not in s:
    unity_engine = (
        '        <engine engine_name_match="Unity.*">\n'
        '            <option name="tu_autotune_algorithm" value="bandwidth" />\n'
        '            <option name="tu_emulate_alpha_to_coverage" value="false" />\n'
        '            <option name="tu_dont_care_as_load" value="false" />\n'
        '            <option name="tu_allow_oob_indirect_ubo_loads" value="true" />\n'
        '        </engine>\n'
    )
    idx = s.rfind(anchor)
    s = s[:idx] + unity_engine + s[idx:]
    print("game_ssbo_profiles: added Unity engine rule for low power & high FPS (native A2C + 18MB HPM bandwidth autotune)")

# ── 2. Per-Executable Profiles ────────────────────────────────────────────────
ssbo_games = (
    ("MIO: Memories in Orbit", "^mio[.]exe$"),
    ("UNCHARTED: Legacy of Thieves Collection", "^(u4|tll)(-l)?[.]exe$"),
)

perf_games = (
    ("Muck", "^(Muck|muck)([.]exe|[.]x86_64)?$", (
        ("tu_autotune_algorithm", "bandwidth"),
        ("tu_emulate_alpha_to_coverage", "false"),
        ("tu_dont_care_as_load", "false"),
        ("tu_allow_oob_indirect_ubo_loads", "true"),
        ("tu_enable_fast_border_color_for_undefined_formats", "true"),
        ("tu_use_tex_coord_round_nearest_even_mode", "true"),
    )),
    ("Dying Light", "^DyingLightGame[.]exe$", (
        ("tu_autotune_algorithm", "bandwidth"),
        ("tu_emulate_alpha_to_coverage", "false"),
        ("tu_dont_care_as_load", "false"),
        ("tu_allow_oob_indirect_ubo_loads", "true"),
        ("tu_enable_fast_border_color_for_undefined_formats", "true"),
    )),
    ("Dying Light 2 Stay Human", "^DyingLightGame_x64_rwdi[.]exe$", (
        ("tu_autotune_algorithm", "bandwidth"),
        ("tu_emulate_alpha_to_coverage", "false"),
        ("tu_dont_care_as_load", "false"),
        ("tu_allow_oob_indirect_ubo_loads", "true"),
    )),
    ("Dying Light: The Beast", "^DyingLight.*[.]exe$", (
        ("tu_autotune_algorithm", "bandwidth"),
        ("tu_emulate_alpha_to_coverage", "false"),
        ("tu_dont_care_as_load", "false"),
        ("tu_allow_oob_indirect_ubo_loads", "true"),
    )),
    ("Cyberpunk 2077", "^Cyberpunk2077[.]exe$", (
        ("tu_autotune_algorithm", "bandwidth"),
        ("tu_dont_care_as_load", "false"),
        ("tu_allow_oob_indirect_ubo_loads", "true"),
    )),
)

for name, regex in ssbo_games:
    key = f'application_name_match="{regex}"'
    if key in s:
        print(f"game_ssbo_profiles: {name} already present")
        continue
    entry = (
        f'        <application name="{name}" application_name_match="{regex}">\n'
        '            <option name="tu_enable_ssbo_emulation" value="true" />\n'
        '        </application>\n'
    )
    idx = s.rfind(anchor)
    s = s[:idx] + entry + s[idx:]
    print(f"game_ssbo_profiles: added {name}")

for name, regex, opts in perf_games:
    key = f'application_name_match="{regex}"'
    if key in s:
        print(f"game_ssbo_profiles: {name} already present")
        continue
    opt_lines = [f'            <option name="{k}" value="{v}" />\n' for k, v in opts]
    entry = (
        f'        <application name="{name}" application_name_match="{regex}">\n'
        + "".join(opt_lines) +
        '        </application>\n'
    )
    idx = s.rfind(anchor)
    s = s[:idx] + entry + s[idx:]
    print(f"game_ssbo_profiles: added {name} optimization profile")

ET.fromstring(s)
p.write_text(s, encoding="utf-8")
