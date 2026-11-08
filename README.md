# Turnip A8xx / Adreno 840 Custom Build — OnePlus 15 (Android 17)

Custom Android/Bionic Vulkan driver (`libvulkan_freedreno.so`) build system for **OnePlus 15 (Snapdragon 8 Elite Gen 5 / SM8850 / Adreno 840)** targeting **Android 17 (API 37)** and Mesa `staging/26.3`.

All legacy A7xx and Samsung OneUI patches have been removed.

## Build Variants

| Variant | Artifact | Description |
| --- | --- | --- |
| `a8xx-oneplus15-ultra` | `Turnip-A840-OnePlus15-Ultra` | **Recommended for Dying Light 1 & 2 (DXVK / vkd3d-proton)**. Retains 3-slice Concurrent Binning, applies 18 MB HPM GMEM autotune, 64 KiB CS shared memory, 0001–0005 shader/KGSL patches, LRZ/NIR/FDL fixes, UBWC 5/6 + IMapper5, and Oryon Gen 3 (`armv9.2-a`) + ThinLTO compiler optimizations. |
| `a8xx-gen8` | `Turnip-A8xx-Gen8-OP15` | Alternative Gen8 branch stack with OnePlus 15 Adreno 840 18 MB HPM, 100% vec4 register unlock, 64 KiB shared memory, and Dying Light profiles. |

## Key Optimizations for OnePlus 15 & Dying Light

- **Mesa `staging/26.3` Target**: Pulls directly from `refs/heads/staging/26.3` (with automatic fallback to `main`).
- **Qualcomm Oryon Gen 3 CPU + ThinLTO**: Compiled with `-march=armv9.2-a+sve2+fp16+dotprod+i8mm+bf16+crc+crypto -O3 -ffast-math -fno-rtti` and `-Db_lto=true -Db_lto_mode=thin`, reducing DXVK draw-call submission overhead and speeding up SPIR-V -> NIR -> ir3 shader compilation.
- **Adreno 840 18 MB HPM & GMEM Autotune (`a840_oneplus15_tune.py`)**:
  - Upgrades `a8xx_gen2` CCU depth cache to `FULL` (256 KiB) and GMEM color cache to `QUARTER` (64 KiB).
  - Tunes `tu_autotune.cc` (drawcall threshold `8`, bandwidth weight `9/10`) so deferred shading passes in *Dying Light* stay inside the 18 MB High Performance Memory instead of falling back to LPDDR5X sysmem.
  - Unlocks 100% vec4 register file allocation (`reg_efficiency = 100`) on Adreno 840 in the Gen8 profile.
- **Dying Light 1 & 2 Profiles (`game_ssbo_profiles.py`)**: Adds `DyingLightGame.exe` and `DyingLightGame_x64_rwdi.exe` rules (`tu_dont_care_as_load=false`, `tu_allow_oob_indirect_ubo_loads=true`).
- **Android 17 (API 37) & OxygenOS 16/17 Display Stack**: Includes Android NDK r30 (with fallback to r29), `-Dplatform-sdk-version=37`, `AIMapper` (IMapper5 SP-HAL), Qualcomm UBWC swapchain compression (`0x10000000`), and `KGSL_UBWC_5_0` / `KGSL_UBWC_6_0` support.
- **Fixed `patchsv3` Pipeline**: Fixes the broken `patches/Compatibility` path in the original script so LRZ reset after flush, 32-bit NIR offset wrap, 32-bit `NUM_ELEMENTS` buffer descriptors, and process-wide heap usage tracking are properly applied.

## Building

### GitHub Actions
Run **Build Turnip A8xx OnePlus 15 (Android 17)** from the **Actions** tab. All workflow actions track `@main`.

### Local Build
```bash
chmod +x build_turnip.sh
VARIANT=a8xx-oneplus15-ultra MESA_BRANCH=staging/26.3 ./build_turnip.sh
```
Outputs are placed in `turnip_workdir/`.
