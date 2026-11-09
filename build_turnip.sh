#!/usr/bin/env bash
set -euo pipefail

green='\033[0;32m'
red='\033[0;31m'
nocolor='\033[0m'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKDIR="$REPO_ROOT/turnip_workdir"
NDKVER="${NDKVER:-android-ndk-r30}"
NDK_FALLBACK="android-ndk-r29"
NDK="$WORKDIR/$NDKVER/toolchains/llvm/prebuilt/linux-x86_64/bin"
BUILD_VERSION="${BUILD_VERSION:-1.0}"
VARIANT="${VARIANT:-a8xx-oneplus15-ultra}"
MESA_BRANCH="${MESA_BRANCH:-staging/26.3}"
MESA_COMMIT="${MESA_COMMIT:-}"

log() { echo -e "${green}$*${nocolor}"; }
die() { echo -e "${red}$*${nocolor}" >&2; exit 1; }

check_deps() {
    local deps="git meson ninja patchelf unzip curl pip flex bison zip glslangValidator python3 patch"
    for dep in $deps; do
        command -v "$dep" >/dev/null 2>&1 || die "Missing dependency: $dep"
    done
    pip install mako pyyaml packaging --break-system-packages >/dev/null 2>&1 || true
}

prepare_workdir() {
    mkdir -p "$WORKDIR"
    if [ ! -d "$WORKDIR/$NDKVER" ]; then
        log "Downloading $NDKVER (with fallback to $NDK_FALLBACK)..."
        if ! curl -fsSL "https://dl.google.com/android/repository/${NDKVER}-linux.zip" -o "$WORKDIR/${NDKVER}-linux.zip"; then
            log "Warning: $NDKVER not reachable, falling back to $NDK_FALLBACK..."
            NDKVER="$NDK_FALLBACK"
            NDK="$WORKDIR/$NDKVER/toolchains/llvm/prebuilt/linux-x86_64/bin"
            curl -fsSL "https://dl.google.com/android/repository/${NDKVER}-linux.zip" -o "$WORKDIR/${NDKVER}-linux.zip"
        fi
        unzip -q -o "$WORKDIR/${NDKVER}-linux.zip" -d "$WORKDIR"
    fi

    rm -rf "$WORKDIR/mesa"
    mkdir -p "$WORKDIR/mesa"
    git -C "$WORKDIR/mesa" init -q
    git -C "$WORKDIR/mesa" remote add origin https://gitlab.freedesktop.org/mesa/mesa.git

    if [ -n "$MESA_COMMIT" ]; then
        log "Fetching Mesa pinned commit $MESA_COMMIT (from $MESA_BRANCH)..."
        git -C "$WORKDIR/mesa" fetch -q --depth=1 origin "$MESA_COMMIT"
    else
        log "Fetching Mesa branch $MESA_BRANCH..."
        if ! git -C "$WORKDIR/mesa" fetch -q --depth=1 origin "refs/heads/$MESA_BRANCH"; then
            log "Branch $MESA_BRANCH not found on remote, falling back to refs/heads/main..."
            git -C "$WORKDIR/mesa" fetch -q --depth=1 origin refs/heads/main
        fi
    fi
    git -C "$WORKDIR/mesa" checkout -q --detach FETCH_HEAD
}

apply_Gen8_a8xx() {
    log "A8xx Gen8: applying turnip/gen8 stack..."
    patch -p1 -N --fuzz=4 --no-backup-if-mismatch < "$REPO_ROOT/patches/patchsv2/a8xx_gen8.patch" || true
    find src -type f \( -name "*.rej" -o -name "*.orig" \) -delete
    python3 -m py_compile src/freedreno/common/freedreno_devices.py || die "freedreno_devices.py syntax error after a8xx_gen8.patch"
    python3 "$REPO_ROOT/patches/patchsv1/android/apply_a8xx_gpus.py"

    log "A8xx Gen8: shared memory 32 KiB -> 64 KiB..."
    python3 "$REPO_ROOT/patches/patchsv2/a8xx_shared_mem.py"

    log "A8xx Gen8: enabling modern Qualcomm gralloc, IMapper5 and UBWC..."
    python3 "$REPO_ROOT/patches/patchsv1/android/fix_gralloc_flushall.py"
    python3 "$REPO_ROOT/patches/patchsv1/android/add_aimapper_gralloc.py"
    python3 "$REPO_ROOT/patches/patchsv1/android/add_ubwc_swapchain_usage.py"
}

apply_Patched_android_scripts() {
    log "A8xx Patched: applying Android/Bionic A8xx production scripts (no A7xx/OneUI bloat)..."
    local scripts=(
        fix_gralloc_flushall.py
        fix_a8xx_dev_info.py
        apply_a8xx_gpus.py
        add_aimapper_gralloc.py
        add_ubwc_swapchain_usage.py
        apply_balance_variant.py
    )
    local script
    for script in "${scripts[@]}"; do
        log "Applying: $script"
        python3 "$REPO_ROOT/patches/patchsv1/android/$script"
    done
}

apply_Patched_extra_patches() {
    log "A8xx: applying extra shader & KGSL patchset 0001-0005..."
    local patch_file
    local count=0
    for patch_file in "$REPO_ROOT"/patches/patchsv1/000*.patch; do
        [ -f "$patch_file" ] || continue
        log "Checking $(basename "$patch_file")..."
        if git apply --check "$patch_file" 2>/dev/null; then
            git apply "$patch_file"
        elif patch -p1 -N --fuzz=3 --dry-run < "$patch_file" >/dev/null 2>&1; then
            log "Applying $(basename "$patch_file") with fuzz..."
            patch -p1 -N --fuzz=3 --no-backup-if-mismatch < "$patch_file"
        else
            log "Warning: $(basename "$patch_file") already applied or conflicted on $MESA_BRANCH, skipping cleanly"
        fi
        count=$((count + 1))
    done
    [ "$count" -eq 5 ] || die "Expected 5 patches in patchsv1, found $count"
}

apply_patchsv3_registers_only() {
    log "A8xx: applying patchsv3 register adjustments (RB_RESOLVE_CR_CNTL & LRZ color mask)..."
    local dd="$REPO_ROOT/patches/patchsv3/scripts"

    PYTHONPATH="$dd" python3 "$dd/a8xx_resolve_cr_cntl.py"
    PYTHONPATH="$dd" python3 "$dd/a8xx_lrz_color_mask.py"

    grep -Fq 'A6XXRegs.REG_A8XX_RB_RESOLVE_CR_CNTL' src/freedreno/common/freedreno_devices.py \
        || die "A8xx: RB_RESOLVE_CR_CNTL preamble missing"
    grep -Fq 'GRAS_LRZ_COLOR_COMP_MASK(CHIP, .dword = lrz_color_comp_mask)' src/freedreno/vulkan/tu_pipeline.cc \
        || die "A8xx: LRZ color mask missing"
}

apply_a8xx_subgroup_fix() {
    log "A8xx: retaining Mesa subgroup correctness (double_threadsize=False)..."
    python3 "$REPO_ROOT/patches/a8xx/a8xx_no_double_threadsize.py"
}

apply_a8xx_game_profiles() {
    log "A8xx: installing game-specific profiles (Dying Light 1/2/The Beast, Cyberpunk, Uncharted, MIO)..."
    python3 "$REPO_ROOT/patches/a8xx/game_ssbo_profiles.py"
}

apply_a840_oneplus15_tuning() {
    local profile="${1:-eco}"
    log "A840 OnePlus 15: applying Snapdragon 8 Elite Gen 5 (18MB HPM, UBWC5/6, Autotune, 64KB SharedMem, profile=$profile) tuning..."
    python3 "$REPO_ROOT/patches/patchsv2/a8xx_shared_mem.py"
    OP15_POWER_PROFILE="$profile" python3 "$REPO_ROOT/patches/a8xx/a840_oneplus15_tune.py"
}

apply_patchsv3_a8xx() {
    log "A8xx: applying patchsv3 fixes (GPU IDs, Cat4 repeat, Process Heap Usage, Suspended RP Flush, LRZ, NIR 32-bit wrap, FDL num_elements)..."
    local dd="$REPO_ROOT/patches/patchsv3"

    PYTHONPATH="$dd/scripts" python3 "$dd/scripts/a8xx_gpu_ids.py"
    PYTHONPATH="$dd/scripts" python3 "$dd/scripts/apply_a8xx_cat4_rpt.py"
    PYTHONPATH="$dd/scripts" python3 "$dd/scripts/process_heap_usage.py"

    local patch_file target marker
    for patch_file in \
        "$dd/lrz/0001-tu-a8xx-lrz-reset-cntl-after-flush.patch" \
        "$dd/nir/0001-nir-lower-explicit-io-keep-32bit-offset-wrap.patch" \
        "$dd/fdl/0001-fdl-a8xx-buffer-num-elements.patch"; do
        case "$patch_file" in
            */lrz/*)
                target="src/freedreno/vulkan/tu_lrz.cc"
                marker='tu6_write_lrz_cntl<CHIP>(cmd, cs, {});'
                ;;
            */nir/*)
                target="src/compiler/nir/nir_lower_explicit_io.c"
                marker='offset = nir_iand_imm(b, offset, UINT32_MAX >> shift);'
                ;;
            */fdl/*)
                target="src/freedreno/fdl/fd6_view.cc"
                marker='A8XX_TEX_MEMOBJ_2_NUM_ELEMENTS(elements)'
                ;;
        esac

        if git apply --check "$patch_file" 2>/dev/null; then
            log "patchsv3: applying $(basename "$patch_file")"
            git apply "$patch_file"
        elif grep -Fq "$marker" "$target"; then
            log "patchsv3: $(basename "$patch_file") already present in Mesa"
        elif patch -p1 -N --fuzz=3 --dry-run < "$patch_file" >/dev/null 2>&1; then
            log "patchsv3: applying $(basename "$patch_file") with fuzz"
            patch -p1 -N --fuzz=3 --no-backup-if-mismatch < "$patch_file"
        else
            die "patchsv3: patch did not apply and upstream fix is absent: $patch_file"
        fi
    done

    grep -Fq 'u_gralloc_aimapper.c' src/util/u_gralloc/meson.build \
        || die "A8xx: IMapper5 backend not built"
    grep -Fq 'U_GRALLOC_TYPE_AIMAPPER' src/util/u_gralloc/u_gralloc.h \
        || die "A8xx: IMapper5 backend enum missing"
    grep -Fq 'ahb_vendor_usage_compressed = 0x10000000ull' src/freedreno/vulkan/tu_device.cc \
        || die "A8xx: vendor UBWC allocation usage missing"
    grep -Fq 'UBWC flag detection - bypass legacy gmsm magic check for A8XX/A840.' src/util/u_gralloc/u_gralloc_fallback.c \
        || die "A8xx: bounds-checked Qualcomm gralloc UBWC detection missing"
    grep -Fq 'tu_process_heap_used' src/freedreno/vulkan/tu_device.cc \
        || die "A8xx: process-wide memory budget fix missing"
    grep -Fq '0x44041430' src/freedreno/common/freedreno_devices.py \
        || die "A8xx: A845 ID missing"
    grep -Fq '0x44051431' src/freedreno/common/freedreno_devices.py \
        || die "A8xx: A850 ID missing"
}

verify_mesa_mr_44838() {
    local kgsl="src/freedreno/vulkan/tu_knl_kgsl.cc"
    log "Common: verifying upstream Mesa MR !44838 KGSL fixes..."

    [ -f "$kgsl" ] || die "Missing $kgsl"
    grep -Fq 'kgsl_syncobj_merge(struct tu_device *device' "$kgsl" \
        || die "Mesa is missing MR !44838 mixed timestamp/sync-file merge fix (1da50a1b)"
    grep -Fq 'return kgsl_poll_timestamp(device, context_id, timestamp);' "$kgsl" \
        || die "Mesa is missing MR !44838 zero-timeout nonblocking poll fix (e984ef29)"
    log "Mesa MR !44838: both upstream KGSL fixes verified"
}

apply_common_turnip_patches() {
    log "Common: applying sample-shading interpolation gate..."
    local sample_patch="$REPO_ROOT/patches/common/tu-sample-shading-gate.patch"
    local shader_file="src/freedreno/vulkan/tu_shader.cc"
    if git apply --check "$sample_patch" 2>/dev/null; then
        git apply "$sample_patch"
    elif grep -Fq 'nir->info.fs.uses_sample_shading' "$shader_file"; then
        log "Common: sample-shading gate already present in $shader_file"
    else
        patch -p1 -N --fuzz=3 --no-backup-if-mismatch < "$sample_patch"
    fi
}

apply_android_ndk_fixes() {
    log "Applying Android 17 / NDK r29-r30 compatibility fixes..."
    sed -i 's/typedef const native_handle_t\* buffer_handle_t;/typedef void* buffer_handle_t;/g' include/android_stub/cutils/native_handle.h || true
    sed -i 's/, hnd->handle/, (void *)hnd->handle/g' src/util/u_gralloc/u_gralloc_fallback.c || true
    sed -i -E 's/([a-z_]+)->handle->/((const native_handle_t *)\1->handle)->/g' src/vulkan/runtime/vk_android.c || true
    sed -i 's/anb->handle->/((const native_handle_t *)anb->handle)->/g' src/vulkan/runtime/vk_android.c || true
    sed -i "/-Werror=gnu-empty-initializer/d" meson.build || true
}

configure_variant() {
    case "$VARIANT" in
        a8xx-oneplus15-ultra)
            apply_Patched_android_scripts
            apply_Patched_extra_patches
            apply_patchsv3_registers_only
            apply_a840_oneplus15_tuning "eco"
            ;;
        a8xx-oneplus15-max)
            apply_Patched_android_scripts
            apply_Patched_extra_patches
            apply_patchsv3_registers_only
            apply_a840_oneplus15_tuning "max"
            ;;
        a8xx-gen8)
            apply_Gen8_a8xx
            apply_Patched_extra_patches
            apply_patchsv3_registers_only
            apply_a840_oneplus15_tuning "eco"
            ;;
        *)
            die "Invalid VARIANT (only A8xx variants are supported): $VARIANT"
            ;;
    esac
}

build_android() {
    local cver=""
    for candidate in 37 36 35 34; do
        if [ -f "$NDK/aarch64-linux-android${candidate}-clang" ]; then
            cver="$candidate"
            break
        fi
    done
    [ -n "$cver" ] || die "Clang Android aarch64 not found in $NDK"
    log "Using Android NDK API level: $cver (Target Platform SDK: 37 / Android 17)"

    mkdir -p "$WORKDIR/bin"
    ln -sf "$NDK/clang" "$WORKDIR/bin/cc"
    ln -sf "$NDK/clang++" "$WORKDIR/bin/c++"

    export PATH="$WORKDIR/bin:$NDK:$PATH"
    export CC=clang
    export CXX=clang++
    export AR=llvm-ar
    export RANLIB=llvm-ranlib
    export STRIP=llvm-strip
    export OBJDUMP=llvm-objdump
    export OBJCOPY=llvm-objcopy

    # Aggressive CPU tuning for Qualcomm Oryon Gen 3 (Snapdragon 8 Elite Gen 5 / SM8850)
    # Using IEEE-safe math flags (-fno-math-errno -fno-signed-zeros -fno-trapping-math) without -ffinite-math-only
    # so ir3's isnan()/INFINITY shader constant folding works without UB!
    local oryon_arch="-march=armv9.2-a+sve2+fp16+dotprod+i8mm+bf16+crc+crypto"
    if ! "$NDK/aarch64-linux-android${cver}-clang" $oryon_arch -x c -c /dev/null -o /dev/null 2>/dev/null; then
        oryon_arch="-march=armv8.6-a+fp16+dotprod+i8mm+crc+crypto"
    fi
    log "Compiler CPU target flags for Oryon Gen 3: $oryon_arch"

    local opt_flags="$oryon_arch -O3 -fno-math-errno -fno-signed-zeros -fno-trapping-math -fomit-frame-pointer -fdata-sections -ffunction-sections"
    local warn_flags="-D__ANDROID__ -Wno-error -Wno-error=gnu-empty-initializer -Wno-gnu-empty-initializer -Wno-deprecated-declarations -Wno-incompatible-pointer-types-discards-qualifiers -Wno-incompatible-pointer-types"

    export CFLAGS="$opt_flags $warn_flags"
    export CXXFLAGS="$opt_flags $warn_flags"
    export LDFLAGS="-fuse-ld=lld -Wl,--gc-sections -Wl,-O3 -Wl,--icf=safe"

    cat > android-aarch64.txt <<EOF
[binaries]
ar = '$NDK/llvm-ar'
c = ['$NDK/aarch64-linux-android${cver}-clang', '$oryon_arch', '-O3', '-fno-math-errno', '-fno-signed-zeros', '-fno-trapping-math', '-fomit-frame-pointer']
cpp = ['$NDK/aarch64-linux-android${cver}-clang++', '$oryon_arch', '-O3', '-fno-math-errno', '-fno-signed-zeros', '-fno-trapping-math', '-fomit-frame-pointer', '-fno-exceptions', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '-fno-rtti', '--start-no-unused-arguments', '-static-libstdc++', '--end-no-unused-arguments']
c_ld = '$NDK/ld.lld'
cpp_ld = '$NDK/ld.lld'
strip = '$NDK/llvm-strip'
pkg-config = ['env', 'PKG_CONFIG_LIBDIR=$NDK/pkg-config', '/usr/bin/pkg-config']

[host_machine]
system = 'android'
cpu_family = 'aarch64'
cpu = 'armv9-a'
endian = 'little'
EOF

    cat > native.txt <<EOF
[build_machine]
c = ['clang']
cpp = ['clang++']
ar = 'llvm-ar'
strip = 'llvm-strip'
c_ld = 'ld.lld'
cpp_ld = 'ld.lld'
system = 'linux'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
EOF

    local build_dir="build-android-aarch64"
    local output_dir="/tmp/turnip-$VARIANT"
    rm -rf "$build_dir" "$output_dir"

    meson setup "$build_dir" \
        --cross-file android-aarch64.txt \
        --native-file native.txt \
        --prefix "$output_dir" \
        -Dbuildtype=release \
        -Db_ndebug=true \
        -Dstrip=true \
        -Dplatforms=android \
        -Dvideo-codecs= \
        -Dplatform-sdk-version=37 \
        -Dandroid-stub=true \
        -Dgallium-drivers= \
        -Dvulkan-drivers=freedreno \
        -Dvulkan-beta=true \
        -Dfreedreno-kmds=kgsl \
        -Degl=disabled \
        -Dgbm=disabled \
        -Dglx=disabled \
        -Dopengl=false \
        -Dgles1=disabled \
        -Dgles2=disabled \
        -Dllvm=disabled \
        -Dandroid-libbacktrace=disabled

    ninja -C "$build_dir" install

    [ -f "$output_dir/lib/libvulkan_freedreno.so" ] || die "libvulkan_freedreno.so was not generated"
}

package_variant() {
    local mesa_short
    mesa_short="$(git rev-parse --short=12 HEAD)"

    local pretty_name desc suffix
    case "$VARIANT" in
        a8xx-oneplus15-ultra)
            pretty_name="Turnip A840 OnePlus 15 Ultra-Eco (Android 17)"
            desc="OnePlus 15 (SD 8 Elite Gen 5 / Adreno 840) Ultra-Eco — Low Power + High FPS, 18MB HPM Autotune, Turnip-Z Mutable UBWC, LRZ Preserve, Dying Light Profile"
            suffix="A840-OnePlus15-Ultra"
            ;;
        a8xx-oneplus15-max)
            pretty_name="Turnip A840 OnePlus 15 Ultra-Max PWR_MAX (Android 17)"
            desc="OnePlus 15 (SD 8 Elite Gen 5 / Adreno 840) WinNative-P PWR_MAX Turbo — Peak Clocks, Aggressive 18MB HPM GMEM, Turnip-Z + Vauzi-17 Suite"
            suffix="A840-OnePlus15-MaxPerf"
            ;;
        a8xx-gen8)
            pretty_name="Turnip A8xx Gen8 + SuperOpt (OnePlus 15 / Android 17)"
            desc="A8xx Gen8 Stack (TU_DEBUG=nocb/deck_emu) + 0001-0005 + Turnip-Z / Vauzi-17 Super-Optimizations for OnePlus 15 Adreno 840"
            suffix="A8xx-Gen8-OP15"
            ;;
    esac

    local output_dir="/tmp/turnip-$VARIANT"
    cd "$output_dir/lib"

    cat > meta.json <<EOF
{
  "schemaVersion": 1,
  "name": "$pretty_name",
  "description": "$desc — Mesa $mesa_short",
  "author": "Custom OnePlus 15 A840 SuperOpt",
  "packageVersion": "$BUILD_VERSION",
  "vendor": "Mesa",
  "driverVersion": "Mesa-26.3-$mesa_short",
  "minApi": 28,
  "libraryName": "libvulkan_freedreno.so"
}
EOF

    local zip_name="Turnip_${suffix}_V${BUILD_VERSION}_${mesa_short}.zip"
    rm -f "$WORKDIR/$zip_name"
    zip -9 -q "$WORKDIR/$zip_name" libvulkan_freedreno.so meta.json
    log "Generated: $WORKDIR/$zip_name"
}

main() {
    check_deps
    prepare_workdir
    cd "$WORKDIR/mesa"

    log "Mesa commit: $(git rev-parse HEAD) (Branch: $MESA_BRANCH)"
    log "Variant: $VARIANT"

    verify_mesa_mr_44838
    configure_variant
    apply_a8xx_subgroup_fix
    apply_common_turnip_patches
    apply_patchsv3_a8xx
    apply_a8xx_game_profiles
    apply_android_ndk_fixes
    build_android
    package_variant
}

main "$@"
