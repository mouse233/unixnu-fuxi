#!/usr/bin/env python3
"""Check the RAM first-volume hard links, private mount selection and bindings.

Execute the actual old/new fuxi_mdev_add_ro in C with failing devfs/allocation
dependencies, then check the original APFS parser report and delivered strings.
This never opens the phone and does not claim to execute an APFS mount.
"""
import argparse
import hashlib
import json
import runpy
import struct
import subprocess
import tempfile
from pathlib import Path

spi = runpy.run_path(str(Path(__file__).with_name('check-usb-system-mount-spi.py')), run_name='spi')
braced = spi['braced']
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def exercise(source, fixed):
    function = braced(source, source.index('dev_t\nfuxi_mdev_add_ro('))
    code = r'''
#include <assert.h>
#include <stdint.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
typedef int dev_t;
#define mdRO 2
struct { uint32_t mdBias; uint64_t mdByteSize; int mdFlags; void *mdbdevb; } mdev[3];
static int defined[3], add_fail, link_fail, adds, links;
static char seen[32];
static uint64_t last_base; static unsigned last_pages;
static dev_t mdevlookup(int id) { return defined[id] ? id : -1; }
static dev_t mdevadd(int id,uint64_t base,unsigned pages,int phys) {
    assert(id==1||id==2);assert(!phys);adds++;
    if(add_fail)return -1;
    last_base=base;last_pages=pages;defined[id]=1;
    mdev[id].mdbdevb=&mdev[id];return id;
}
static int __attribute__((unused)) devfs_make_link(void *handle,char *fmt,...) {
    links++;va_list args;va_start(args,fmt);int id=va_arg(args,int);va_end(args);
    assert(id==1||id==2);assert(handle==mdev[id].mdbdevb);
    assert(mdev[id].mdFlags&mdRO);assert(mdev[id].mdByteSize);
    snprintf(seen,sizeof(seen),fmt,id);return link_fail ? -1 : 0;
}
''' + function + '\n#define FIXED ' + str(int(fixed)) + r'''
static void reset(void) {
    memset(mdev,0,sizeof(mdev));memset(defined,0,sizeof(defined));
    add_fail=link_fail=adds=links=0;seen[0]=0;
}
int main(void) {
    for(int id=1;id<=2;id++) {
        reset();assert(fuxi_mdev_add_ro(id,0x10000400,8192)==id);
        assert(adds==1 && links==FIXED);
        assert(last_base==0x10000 && last_pages==3);
        assert(mdev[id].mdBias==1024 && mdev[id].mdByteSize==8192 && (mdev[id].mdFlags&mdRO));
        if(FIXED) assert(!strcmp(seen,id==1?"md1s1":"md2s1"));
        int before=adds;assert(fuxi_mdev_add_ro(id,0x10000400,8192)==-1);assert(adds==before);
    }
    reset();assert(fuxi_mdev_add_ro(0,0x10000000,8192)==-1);
    assert(fuxi_mdev_add_ro(3,0x10000000,8192)==-1);
    assert(fuxi_mdev_add_ro(1,0x10000001,8192)==-1);
    assert(fuxi_mdev_add_ro(1,0x10000000,8193)==-1);
    assert(fuxi_mdev_add_ro(1,0x10000000,0)==-1);
    assert(fuxi_mdev_add_ro(1,UINT64_MAX-511,1024)==-1);
    assert(fuxi_mdev_add_ro(1,0x10000000,((uint64_t)UINT_MAX<<12))==-1);
    assert(!adds && !links);
    reset();add_fail=1;assert(fuxi_mdev_add_ro(1,0x10000000,8192)==-1);assert(!links);
    reset();link_fail=1;
    assert(fuxi_mdev_add_ro(1,0x10000000,8192)==(FIXED?-1:1));
    assert(mdev[1].mdFlags&mdRO);
    puts(FIXED?"fixed: two first-volume links; readonly-before-publish, bounds, duplicate and failures passed":
               "parent: readonly containers retain bounds but publish no volume links");
}
'''
    with tempfile.TemporaryDirectory(prefix='ram-volume-nodes-') as tmp:
        path = Path(tmp)
        (path / 'test.c').write_text(code)
        subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', str(path / 'test.c'), '-o', str(path / 'test')], check=True)
        return subprocess.check_output([str(path / 'test')], text=True).strip()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'parent', 'base', 'image', 'parser-report'):
        p.add_argument('--' + name, required=True, type=Path)
    a = p.parse_args()
    out, parent, base = a.output.resolve(), a.parent.resolve(), a.base.resolve()
    root = out / 'work/tree/.cache/xnu-work'
    source = (root / 'bsd/dev/memdev.c').read_text()
    vfs = (root / 'bsd/vfs/vfs_syscalls.c').read_text()
    old = (parent / 'work/tree/.cache/xnu-work/bsd/dev/memdev.c').read_text()
    results = [exercise(old, False), exercise(source, True)]
    patch = base / 'patches/xnu/zz-ram-system-volume-nodes.patch'
    spi['check_mount_patch'](root, base, patch)
    tree = (root / 'bsd/miscfs/devfs/devfs_tree.c').read_text()
    link = braced(tree, tree.index('int\ndevfs_make_link('))
    assert 'dev_add_name(buf, dirnode, NULL, orig->de_dnp, &new_dev)' in link
    assert 'DEV_LNK' not in link  # real hard link, not a symlink resolving back to mdN
    assert 'strlcpy(mp->mnt_vfsstat.f_mntfromname, nd.ni_cnd.cn_pnbuf, MAXPATHLEN);' in vfs
    assert 'const char *volume_device = !strcmp(device, "/dev/md1") ? "/dev/md1s1" : "/dev/md2s1";' in vfs
    args = (out / 'work/payload/payload.json').read_text()
    assert 'ram-system-probe=1' in args and 'native-ufs-root=0' in args
    kernel = out / 'work/kernel/kernel.macho'
    data = kernel.read_bytes()
    payload = json.loads((out / 'payload-verification.json').read_text())
    assert sha(kernel) == payload['kernel_sha256']
    for literal in (b'/dev/md1s1\0', b'/dev/md2s1\0', b'md%ds1\0'):
        assert literal in data
    parsed = json.loads(a.parser_report.read_text())
    assert parsed['kernel_sha256'] == payload['kernel_sha256']
    cases = {c['input']: c for c in parsed['parser_cases']}
    for name in ('md1', 'md2'):
        assert cases['/dev/' + name]['volume_index'] == -1
        assert cases['/dev/' + name + 's1']['volume_index'] == 0
        assert cases['/dev/' + name + 's1']['container_path'] == '/dev/' + name
    for name in ('memdev', 'vfs_syscalls'):
        record = out / f'work/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/{name}.o.json'
        entry = json.loads(record.read_text().rstrip().removesuffix(','))
        relative = 'bsd/dev/memdev.c' if name == 'memdev' else 'bsd/vfs/vfs_syscalls.c'
        assert str(root / relative) in entry['arguments']
    headers = []
    with a.image.open('rb') as f:
        for name, at in [('OS',1048576+17408),('SystemOS',6079643648)]:
            f.seek(at); block = f.read(4096)
            assert block[32:36] == b'NXSB'
            volumes = [(i, v) for i, v in enumerate(struct.unpack_from('<100Q', block, 0xb8)) if v]
            assert len(volumes) == 1 and volumes[0][0] == 0
            headers.append(dict(name=name,first_volume_oid=volumes[0][1],block_sha256=hashlib.sha256(block).hexdigest()))
    report = dict(passed=results, read_only_links_verified=True, names=['/dev/md1s1','/dev/md2s1'],
                  boot_sha256=payload['boot_sha256'],kernel_sha256=payload['kernel_sha256'],
                  source_sha256={'memdev.c':sha(root/'bsd/dev/memdev.c'),'vfs_syscalls.c':sha(root/'bsd/vfs/vfs_syscalls.c')},
                  patch_sha256=sha(patch),parser_report_sha256=sha(a.parser_report),container_headers=headers,
                  sanitizers=['address','undefined'],hardware_result='pending',
                  limits='Original parser and real C registration/guard checks; no executed APFS mount')
    (out / 'volume-nodes-tests.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
