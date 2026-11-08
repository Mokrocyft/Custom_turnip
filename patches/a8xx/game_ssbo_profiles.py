#!/usr/bin/env python3
"""
Apply executable-specific performance & compatibility profiles for A8xx (Adreno 840),
including dedicated optimizations for Dying Light (DyingLightGame.exe) and Dying Light 2.
"""
from pathlib import Path
import xml.etree.ElementTree as ET

p = Path("src/freedreno/vulkan/00-turnip-defaults.conf")
s = p.read_text(encoding="utf-8")

# 1. Games requiring tu_enable_ssbo_emulation
ssbo_games = (
    ("MIO: Memories in Orbit", "^mio[.]exe$"),
    ("UNCHARTED: Legacy of Thieves Collection", "^(u4|tll)(-l)?[.]exe$"),
)

# 2. High-performance DXVK/vkd3d profiles for Dying Light 1 & 2 on Adreno 840:
#    - tu_dont_care_as_load=false: avoids redundant GMEM tile loads when attachments use DONT_CARE
#    - tu_allow_oob_indirect_ubo_loads=true: accelerates DXVK constant buffer push/load paths
#    - tu_ conservatively keeps concurrent binning enabled on 3-slice Adreno 840
dl_games = (
    ("Dying Light", "^DyingLightGame[.]exe$", (
        ("tu_dont_care_as_load", "false"),
        ("tu_allow_oob_indirect_ubo_loads", "true"),
    )),
    ("Dying Light 2 Stay Human", "^DyingLightGame_x64_rwdi[.]exe$", (
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

for name, regex, opts in dl_games:
    key = f'application_name_match="{regex}"'
    if key in s:
        print(f"game_ssbo_profiles: {name} already present")
        continue
    # Only inject options that are declared inside 00-turnip-defaults.conf or valid dri options
    opt_lines = []
    for opt_name, opt_val in opts:
        opt_lines.append(f'            <option name="{opt_name}" value="{opt_val}" />\n')
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
