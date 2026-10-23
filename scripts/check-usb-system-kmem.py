#!/usr/bin/env python3
"""Run the pinned XNU compressor sizing function and bound the fuxi KMEM VA.

The C function is extracted from the candidate's patched XNU source. Host stubs
provide boot arguments and platform globals; no compressor math is reimplemented.
The VA calculation covers the full 1 GiB physmap slide and PMAP rounding. This
is a host budget check, not proof of successful hardware initialization.
"""
import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path


def compressor_function(source):
    start = source.index('static void\nvm_compressor_set_size(void)')
    end = source.index('\nSTARTUP(KMEM, STARTUP_RANK_FIRST, vm_compressor_set_size);', start)
    return source[start:end]


PREAMBLE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CONFIG_JETSAM 1
#define CONFIG_FREEZE 1
#define XNU_TARGET_OS_IOS 1
#define XNU_TARGET_OS_OSX 0
#define XNU_TARGET_OS_WATCH 0
#define RECORD_THE_COMPRESSED_DATA 0
#define PAGE_SIZE 4096ULL
#define C_SEG_BUFSIZE_DEFAULT (256 * 1024)
#define C_SEG_BUFSIZE_ARM_SWAP (64 * 1024)
#define C_SEG_MAX_LIMIT UINT32_MAX
#define VM_COMPRESSOR_MAX_POOL_SIZE (192ULL << 30)
#define FALSE false
#define ptoa_64(x) ((uint64_t)(x) * PAGE_SIZE)
#define C_SEG_BYTES_TO_OFFSET(x) ((x) / 4)
#define VM_MAP_PAGE_MASK(x) (PAGE_SIZE - 1)
#define vm_map_round_page(x, m) (((x) + (m)) & ~(m))
#define assertf(ok, ...) assert(ok)
typedef uint64_t vm_size_t;
struct c_slot_mapping { unsigned s_cseg : 22; };
union c_segu { void *segment; uintptr_t number; };
static uint64_t max_mem = 12ULL << 30;
static uint64_t compressor_pool_size, compressor_pool_max_size;
static uint64_t c_buffers_size, compressor_size;
static uint32_t vm_compression_limit, c_seg_bufsize, c_seg_max_pages;
static uint32_t c_seg_off_limit, c_seg_allocsize, c_seg_slot_var_array_min_len;
static uint32_t c_segments_limit, c_segments_incore_limit, c_segments_nearing_limit;
static uint32_t c_segment_pages_compressed_limit, c_segment_pages_compressed_nearing_limit;
static uint32_t compressor_pool_multiplier;
static bool memorystatus_swap_all_apps, freezer_incore_cseg_acct = true;
static int disable_freezer;
static int PE_parse_boot_argn(const char *name, void *value, size_t size) {
    (void)value; (void)size;
    return !strcmp(name, "-disable_freezer_cseg_acct") && disable_freezer;
}
static void vm_compressor_swap_init_swap_file_limit(void) {}
static uint64_t vm_swap_get_max_configured_space(void) { return 5ULL * 128 * 1024 * 1024; }
'''
MAIN = r'''
int main(int argc, char **argv) {
    assert(argc == 3);
    vm_compression_limit = (uint32_t)strtoul(argv[1], NULL, 0);
    disable_freezer = atoi(argv[2]);
    vm_compressor_set_size();
    uint64_t first = compressor_size;
    vm_compressor_set_size();
    assert(first == compressor_size);
    printf("%llu\n", (unsigned long long)compressor_size);
}
'''


def run_sizing(source, pages, disabled):
    with tempfile.TemporaryDirectory(prefix='fuxi-kmem-') as directory:
        path = Path(directory)
        (path / 'sizing.c').write_text(PREAMBLE + compressor_function(source) + MAIN)
        subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-variable', '-fsanitize=address,undefined',
                        str(path / 'sizing.c'), '-o', str(path / 'sizing')], check=True)
        return int(subprocess.check_output([str(path / 'sizing'), str(pages), str(int(disabled))]))


def check_budget(kernel_base, window, retained, compressor):
    gib = 1 << 30
    # arm_vm_init: physmap starts at phystokv(topOfKernelData), then adds a
    # <1 GiB slide, mem_size and eight 2 MiB PTOV slots. pmap_virtual_region
    # rounds the reservation up to 8 MiB on TEST_PAGE_SIZE_4K.
    dynamic_end = kernel_base + retained + (gib - 4096) + window + 8 * (2 << 20)
    dynamic_end = (dynamic_end + (8 << 20) - 1) & ~((8 << 20) - 1)
    heap = (0xfffffffbffffffff & ~4095) - dynamic_end
    # Stock iOS zone VA = 24 GiB. A conservative 512 MiB covers its metadata,
    # bitmap/tag arrays (physical zone limit <= half of the remaining RAM).
    # Another 64 MiB covers IPC/io maps and the <=2 MiB KMEM fuzz allocation.
    other = (512 + 64) << 20
    remaining = heap - (24 << 30) - other - compressor
    if remaining < (512 << 20):
        raise ValueError(f'KMEM VA budget exhausted: remaining={remaining:#x}')
    return dict(worst_heap_bytes=heap, zone_va_bytes=24 << 30,
                other_claims_bound=other, compressor_claim_bytes=compressor,
                remaining_va_lower_bound=remaining)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--base', type=Path, required=True)
    a = p.parse_args()
    out, base = a.output.resolve(), a.base.resolve()
    tree = out / 'work/tree'
    source_path = tree / '.cache/xnu-work/osfmk/vm/vm_compressor.c'
    source = source_path.read_text()
    source_root = source_path.parents[2]
    revision = subprocess.check_output(['git', '-C', str(source_root), 'rev-parse', 'HEAD']).decode().strip()
    assert revision == 'd8b80295118ef25ac3a784134bcf95cd8e88109f', 'unqualified XNU revision'
    record_path = tree / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/vm_compressor.o.json'
    record = json.loads(record_path.read_text().rstrip().removesuffix(','))
    flags = record['arguments']
    assert str(source_path) in flags, 'compiler record belongs to another candidate'
    assert record_path.with_suffix('').is_file(), 'compiled compressor object missing'
    assert 'ARM64_BOARD_CONFIG_BCM2837' in flags and 'PAGE_SIZE_FIXED' in flags
    assert 'XNU_TARGET_OS_IOS' in flags and 'CONFIG_FREEZE' in flags
    assert 'ARM_LARGE_MEMORY' in flags and flags[flags.index('ARM_LARGE_MEMORY') - 1] == '-U'
    pristine = subprocess.check_output(['git', '-C', str(source_path.parents[2]),
                                       'show', 'HEAD:osfmk/vm/vm_compressor.c']).decode()
    patch = base / 'patches/xnu/native-compressor-early-freezer-mode.patch'
    subprocess.run(['git', '-C', str(source_path.parents[2]), 'apply', '--reverse',
                    '--check', str(patch)], check=True)
    args = json.loads((out / 'work/payload/payload.json').read_text())['kernel_args'].split()
    assert args.count('vm_compression_limit=131072') == 1
    assert args.count('-disable_freezer_cseg_acct') == 1
    # Regression and negative controls run the real old/new function bodies.
    old = run_sizing(pristine, 0, False)
    limited_but_late = run_sizing(pristine, 131072, True)
    fixed = run_sizing(source, 131072, True)
    default = run_sizing(source, 0, False)
    assert old == default, 'unflagged compressor behavior changed'
    assert limited_but_late > fixed + (600 << 20), 'late freezer reserve was not reproduced'
    assert (500 << 20) < fixed < (513 << 20), 'unexpected bounded compressor size'
    require_early = run_sizing(source, 0, True)
    assert old > require_early + (600 << 20)
    payload = json.loads((out / 'payload-verification.json').read_text())
    objdump = '/opt/homebrew/opt/llvm/bin/llvm-objdump'
    assembly = subprocess.check_output([objdump, '--disassemble-symbols=_vm_compressor_set_size',
                                       str(out / 'work/kernel/kernel.macho')]).decode()
    # The first parse reads segment size; the second reads the freezer flag.
    # Both must precede the swap/size calculation in the delivered prelink.
    before_swap = assembly.split('<_vm_compressor_swap_init_swap_file_limit>')[0]
    assert before_swap.count('<_PE_parse_boot_argn>') == 2, 'early freezer parse absent from kernel'
    (out / 'compressor-disassembly.txt').write_text(assembly)
    header = (base / 'package/UniXNUPkg/Include/Protocol/UniXNURamObjects.h').resolve()
    # Evaluate the production layout macros rather than repeating object lengths.
    with tempfile.TemporaryDirectory(prefix='fuxi-layout-') as directory:
        path = Path(directory)
        (path / 'layout.c').write_text('#include <stdio.h>\n#include "' + str(header) +
            '"\nint main(void) { printf("%llu %llu\\n", UR_WINDOW, UR_RETAIN_END); }\n')
        subprocess.run(['clang', str(path / 'layout.c'), '-o', str(path / 'layout')], check=True)
        window, retained = map(int, subprocess.check_output([str(path / 'layout')]).split())
    budget = check_budget(int(payload['kernel_base'], 16), window, retained, fixed)
    try:
        check_budget(int(payload['kernel_base'], 16), window, retained, old)
    except ValueError:
        pass
    else:
        raise AssertionError('old configuration unexpectedly passed the VA budget')
    report = dict(cases=7, xnu_revision=revision,
                  kernel_sha256=payload['kernel_sha256'], boot_sha256=payload['boot_sha256'],
                  compile_record_sha256=hashlib.sha256(record_path.read_bytes()).hexdigest(),
                  early_parse_verified_in_prelink=True,
                  source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                  patch_sha256=hashlib.sha256(patch.read_bytes()).hexdigest(),
                  old_claim_bytes=old, limited_but_late_claim_bytes=limited_but_late,
                  no_freezer_reserve_claim_bytes=require_early, **budget,
                  boot_args=args, sanitizers=['address', 'undefined'], hardware_result='pending')
    (out / 'kmem-tests.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
