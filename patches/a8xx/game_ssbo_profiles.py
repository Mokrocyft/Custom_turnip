#!/usr/bin/env python3
"""
Apply executable-specific performance, power-saving & compatibility profiles for A8xx (Adreno 840):
- Dying Light (DyingLightGame.exe)
- Dying Light 2 Stay Human (DyingLightGame_x64_rwdi.exe)
- Dying Light: The Beast (DyingLightTheBeast.exe)
- Cyberpunk 2077 (Cyberpunk2077.exe)
- MIO: Memories in Orbit & UNCHARTED: Legacy of Thieves Collection
"""
from pathlib import Path
import xml.etree.ElementTree as ET

p = Path("src/freedreno/vulkan/00-turnip-defaults.conf")
s = p.read_text(encoding="utf-8")

ssbo_games = (
    ("MIO: Memories in Orbit", "^mio[.]exe$"),
    ("UNCHARTED: Legacy of Thieves Collection", "^(u4|tll)(-l)?[.]exe$"),
)

# High-efficiency & performance profiles for Dying Light series & Cyberpunk on Adreno 840:
# - tu_dont_care_as_load=false: avoids redundant GMEM tile loads when attachments use DONT_CARE (saves bandwidth & power)
# - tu_allow_oob_indirect_ubo_loads=true: accelerates DXVK/vkd3d constant buffer loads
perf_games = (
    ("Dying Light", "^DyingLightGame[.]exe$", (
        ("tu_dont_care_as_load", "false"),
        ("tu_allow_oob_indirect_ubo_loads", "true"),
    )),
    ("Dying Light 2 Stay Human", "^DyingLightGame_x64_rwdi[.]exe$", (
        ("tu_dont_care_as_load", "false"),
        ("tu_allow_oob_indirect_ubo_loads", "true"),
    )),
    ("Dying Light: The Beast", "^DyingLight.*[.]exe$", (
        ("tu_dont_care_as_load", "false"),
        ("tu_allow_oob_indirect_ubo_loads", "true"),
    )),
    ("Cyberpunk 2077", "^Cyberpunk2077[.]exe$", (
        ("tu_dont_care_as_load", "false"),
        ("tu_allow_oob_indirect_ubo_loads", "true"),
    )),
)

anchor = "    </device>"
if anchor not in s:
    raise SystemExit("game_ssbo_profiles: Mesa driconf device anchor missing")

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
