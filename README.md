# Turnip A8xx / Adreno 840 SuperOpt Build — OnePlus 15 (Android 17)

Custom Android/Bionic Vulkan driver (`libvulkan_freedreno.so`) build system for **OnePlus 15 (Snapdragon 8 Elite Gen 5 / SM8850 / Adreno 840)** targeting **Android 17 (API 37)** and Mesa `staging/26.3`.

Combines patches and optimizations from **WinNative (`WN-Turnip -b / -p`)**, **Vauzi-17 (`-win` / `-sw` series)**, **Turnip-Z (`30`–`32`)**, **Droid-Deck (`DD-Turnip`)**, and **Banners-Turnip (`A8xx`)**. All legacy A7xx and Samsung OneUI patches have been removed.

## Build Variants (3-Pack)

| Variant | Artifact | Power & Performance Profile |
| --- | --- | --- |
| `a8xx-oneplus15-ultra` | `Turnip-A840-OnePlus15-Ultra` | **Recommended for Dying Light 1/2/The Beast (High FPS + Low Power/Heat)**. Standard KGSL dynamic clocks + 18 MB HPM smart autotune (threshold `8`), Turnip-Z Mutable UBWC compression, Vauzi-17 LRZ preservation across FS side-effect draws, IR3 live-register pressure scheduler optimization, dynamic draw-state deduplication, 0001–0005 DX12U patches, and Oryon Gen 3 (`armv9.2-a`) optimizations. |
| `a8xx-oneplus15-max` | `Turnip-A840-OnePlus15-MaxPerf` | **WinNative-P (`PWR_MAX`) Turbo Mode**. Locks Adreno 840 to peak clocks via `KGSL_PROP_PWR_CONSTRAINT = PWR_MAX` at queue creation with aggressive 18 MB HPM GMEM retention (threshold `10`). Maximum FPS for short benchmarks or heavy scenes with active cooling. |
| `a8xx-gen8` | `Turnip-A8xx-Gen8-OP15` | **Gen8 IMR / No-Concurrent-Binning Stack**. Uses `a8xx_gen8.patch` (`TU_DEBUG=nocb` default + `TU_DEBUG=deck_emu` Steam Deck spoofing support) with fixed 32-bit `ir3_get_gpu_profile`, 100% vec4 register unlock on Adreno 840, 0001–0005 patches, and the SuperOpt suite. |
