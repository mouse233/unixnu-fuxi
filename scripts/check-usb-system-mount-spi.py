#!/usr/bin/env python3
"""Execute the RAM mount wrapper, admission gate and path-segment selection.

The C snippets come from the candidate's real patched vfs_syscalls.c. This
isolates VFS glue regressions; it does not emulate or claim an APFS mount.
"""
import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path


def braced(source, start):
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def snippets(source):
    wrapper = braced(source, source.index('int\nfuxi_ram_mount_ro('))
    gate = braced(source, source.index('if (kernelmount && (vfsp->vfc_vfsflags & VFC_VFSLOCALARGS)'))
    marker = 'if ((internal_flags & KERNEL_MOUNT_RAM_SYSTEM) &&'
    guard = braced(source, source.index(marker)) if marker in source else ''
    start = source.index('enum uio_seg seg = UIO_USERSPACE;')
    end = source.index('NDINIT(&nd, LOOKUP, OP_MOUNT, FOLLOW, seg, devpath, ctx);', start)
    return wrapper, guard, gate, source[start:end]


def exercise(source, constants, fixed):
    wrapper, guard, gate, segment = snippets(source)
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <stdlib.h>
#define fuxi_ram_mount_reset() ((void)0)
#define fuxi_ram_mount_mark(flags, stage) ((void)0)
#define panic(...) abort()
static struct {
    const char *stage;
    uint32_t dev, spec, covered;
    int opens, apfs;
} fuxi_ram_mount_diag __attribute__((unused));
#define CONFIG_BASESYSTEMROOT 1
#define NULLVP NULL
typedef void *vnode_t;
typedef void *vfs_context_t;
typedef bool boolean_t;
enum uio_seg { UIO_USERSPACE, UIO_SYSSPACE };
struct vfstable { unsigned vfc_vfsflags; };
static int calls;
static const char *seen_device, *seen_path;
static vfs_context_t vfs_context_kernel(void) { return (void *)1; }
''' + constants + '\n'
    harness += '''
static int admit(const char *fstypename, int flags, uint32_t internal_flags, unsigned local) {
    boolean_t kernelmount = !!(internal_flags & KERNEL_MOUNT_KMOUNT);
    struct vfstable table = { local }, *vfsp = &table;
    int error = 0;
''' + guard + '\n' + gate + '''
out1: return error;
}
static int segment_for(uint32_t internal_flags) {
''' + segment + '''
    return seg;
}
static int kernel_mount(const char *type, vnode_t pvp, vnode_t vp, const char *path,
    void *data, size_t datalen, int flags, uint32_t internal_flags, vfs_context_t ctx) {
    calls++; seen_device = *(const char **)data; seen_path = path;
    assert(!pvp && !vp && datalen == sizeof(char *) && ctx == vfs_context_kernel());
    assert(!strcmp(type,"apfs") && flags == (MNT_RDONLY | MNT_DONTBROWSE));
    assert(internal_flags == KERNEL_MOUNT_RAM_SYSTEM);
    return admit(type, flags, internal_flags | KERNEL_MOUNT_KMOUNT, VFC_VFSLOCALARGS);
}
''' + wrapper + '\n'
    harness += '#define FIXED ' + str(int(fixed)) + '\n' + r'''
int main(void) {
    unsigned ram = KERNEL_MOUNT_RAM_SYSTEM | KERNEL_MOUNT_KMOUNT;
    assert(admit("apfs", MNT_RDONLY, KERNEL_MOUNT_KMOUNT, VFC_VFSLOCALARGS) == EINVAL);
    assert(admit("apfs", MNT_RDONLY, 0, VFC_VFSLOCALARGS) == 0);
    assert(admit("apfs", MNT_RDONLY, KERNEL_MOUNT_KMOUNT | KERNEL_MOUNT_BASESYSTEMROOT, VFC_VFSLOCALARGS) == 0);
    assert(admit("apfs", MNT_RDONLY, KERNEL_MOUNT_KMOUNT | KERNEL_MOUNT_DATAVOL, VFC_VFSLOCALARGS) == 0);
    assert(segment_for(0) == UIO_USERSPACE);
    assert(segment_for(KERNEL_MOUNT_BASESYSTEMROOT) == UIO_SYSSPACE);
    assert(admit("apfs", MNT_RDONLY, ram, VFC_VFSLOCALARGS) == (FIXED ? 0 : EINVAL));
    assert(segment_for(ram) == (FIXED ? UIO_SYSSPACE : UIO_USERSPACE));
    assert(fuxi_ram_mount_ro("/dev/md1", "/mnt1") == (FIXED ? 0 : EINVAL));
    assert(!strcmp(seen_device,"/dev/md1") && !strcmp(seen_path,"/mnt1"));
    assert(fuxi_ram_mount_ro("/dev/md2", "/mnt2") == (FIXED ? 0 : EINVAL));
    assert(!strcmp(seen_device,"/dev/md2") && !strcmp(seen_path,"/mnt2"));
    int before = calls;
    assert(fuxi_ram_mount_ro(NULL,"/mnt1") == EINVAL);
    assert(fuxi_ram_mount_ro("/dev/md1",NULL) == EINVAL);
    assert(fuxi_ram_mount_ro("/dev/md1","/") == EINVAL);
    if (FIXED) {
        assert(admit("hfs", MNT_RDONLY, ram, VFC_VFSLOCALARGS) == EINVAL);
        assert(admit("apfs", 0, ram, VFC_VFSLOCALARGS) == EINVAL);
        assert(admit("apfs", MNT_RDONLY | MNT_UPDATE, ram, VFC_VFSLOCALARGS) == EINVAL);
        assert(admit("apfs", MNT_RDONLY, KERNEL_MOUNT_RAM_SYSTEM, VFC_VFSLOCALARGS) == EINVAL);
        assert(fuxi_ram_mount_ro("/dev/disk0","/mnt1") == EINVAL);
        assert(fuxi_ram_mount_ro("/dev/md1","/mnt2") == EINVAL);
        assert(fuxi_ram_mount_ro("/dev/md2","/mnt1") == EINVAL);
    }
    assert(calls == before);
    puts(FIXED ? "fixed: wrapper/admission/path assertions passed" : "baseline: EINVAL and userspace-segment defects reproduced");
}
'''
    with tempfile.TemporaryDirectory(prefix='ram-mount-spi-') as directory:
        path = Path(directory)
        (path / 'test.c').write_text(harness)
        subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-Wno-unused-parameter', '-fsanitize=address,undefined',
                        str(path / 'test.c'), '-o', str(path / 'test')], check=True)
        return subprocess.check_output([str(path / 'test')]).decode().strip()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--baseline', required=True, type=Path, help='previous candidate source, not its image')
    p.add_argument('--base', required=True, type=Path)
    a = p.parse_args()
    out, base = a.output.resolve(), a.base.resolve()
    root = out / 'work/tree/.cache/xnu-work'
    path = root / 'bsd/vfs/vfs_syscalls.c'
    source = path.read_text()
    internal = (root / 'bsd/sys/mount_internal.h').read_text()
    public = (root / 'bsd/sys/mount.h').read_text()
    defines = [line for line in internal.splitlines() if line.startswith('#define KERNEL_MOUNT_') or
               line.startswith('#define VFC_VFSLOCALARGS')]
    for name in ('MNT_RDONLY', 'MNT_DONTBROWSE', 'MNT_UPDATE'):
        defines.append(re.search(r'^#define '+name+r'\s+[^\n]+', public, re.M).group())
    constants = '\n'.join(defines)
    patch = base / 'patches/xnu/zz-ram-system-mount-spi.patch'
    # A later trace patch changes the wrapper's context. Remove it only in a
    # disposable copy, then verify the SPI patch; never edit a built candidate.
    with tempfile.TemporaryDirectory(prefix='ram-mount-patches-') as directory:
        scratch = Path(directory)
        target = scratch / 'bsd/vfs/vfs_syscalls.c'
        target.parent.mkdir(parents=True)
        target.write_text(source)
        trace = base / 'patches/xnu/zz-ram-system-mount-trace.patch'
        if 'fuxi_ram_mount_reset' in source:
            subprocess.run(['git', '-C', str(scratch), 'apply', '--reverse', str(trace)], check=True)
        subprocess.run(['git', '-C', str(scratch), 'apply', '--reverse', '--check', str(patch)], check=True)
    baseline = a.baseline.resolve().read_text()
    results = [exercise(baseline, constants, False), exercise(source, constants, True)]
    record_path = out / 'work/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/vfs_syscalls.o.json'
    record = json.loads(record_path.read_text().rstrip().removesuffix(','))
    assert str(path) in record['arguments'], 'VFS object compiled from another candidate'
    payload = json.loads((out / 'payload-verification.json').read_text())
    report = dict(passed=results, source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                  patch_sha256=hashlib.sha256(patch.read_bytes()).hexdigest(),
                  baseline_source_sha256=hashlib.sha256(baseline.encode()).hexdigest(),
                  boot_sha256=payload['boot_sha256'], kernel_sha256=payload['kernel_sha256'],
                  sanitizers=['address', 'undefined'], hardware_result='pending')
    (out / 'mount-spi-tests.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
