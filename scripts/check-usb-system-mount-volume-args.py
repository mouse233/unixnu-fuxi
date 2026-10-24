#!/usr/bin/env python3
"""Check the 21H461 APFS mount ABI, real RAM argument plumbing and ARM64 copyin.

The original APFS dispatch is audited, not modified or executed as a filesystem.
Host C exercises extracted XNU code under ASan/UBSan; hardware is still required.
"""
import argparse
import hashlib
import json
import re
import runpy
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

spi = runpy.run_path(str(Path(__file__).with_name('check-usb-system-mount-spi.py')), run_name='spi')
braced = spi['braced']


def exercise(source, copy_source, constants, baseline_source):
    suffix = 's1' if 'const char *volume_device' in source else ''
    constants += '\n#define RAM_DEVICE1 "/dev/md1' + suffix + '"\n#define RAM_DEVICE2 "/dev/md2' + suffix + '"\n'
    types = spi['ram_types'](source)
    wrapper = braced(source, source.index('int\nfuxi_ram_mount_ro('))
    block_start = source.index('        if (internal_flags & KERNEL_MOUNT_RAM_SYSTEM) {\n            if (strcmp(fstypename')
    plumbing = braced(source, block_start)
    baseline_start = baseline_source.index('        if (internal_flags & KERNEL_MOUNT_RAM_SYSTEM) {\n            if (strcmp(fstypename')
    baseline_plumbing = braced(baseline_source, baseline_start)
    copy_functions = '\n'.join(braced(copy_source, copy_source.index(s)) for s in
        ['static inline bool\nis_kernel_to_kernel_copy()', 'static int\ncopy_validate(',
         'int\ncopyin_kern(', 'int\ncopyin('])
    c = constants + r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <limits.h>
#define NULLVP NULL
#define __improbable(x) (x)
#define os_add_overflow(a,b,p) __builtin_add_overflow(a,b,p)
#define VM_KERNEL_STRIP_UPTR(p) (p)
#define VM_MIN_KERNEL_ADDRESS 1
#define VM_MAX_KERNEL_ADDRESS UINTPTR_MAX
#define ARM_TBI_USER_MASK 0xff00000000000000ULL
#define COPYIO_IN 1
#define COPYIO_OUT 2
#define COPYIO_ALLOW_KERNEL_TO_KERNEL 4
#define COPYIO_VALIDATE_USER_ONLY 8
#define COPYIO_ATOMIC 16
#define USER_ACCESS_READ 0
#define KASAN 0
#define panic(...) abort()
#define fuxi_ram_mount_witness() ""
#define bcopy(s,d,n) memcpy(d,s,n)
#define fuxi_ram_mount_reset() ((void)0)
typedef size_t vm_size_t;
typedef unsigned copyio_flags_t;
typedef bool boolean_t;
typedef void *vnode_t;
typedef void *vfs_context_t;
struct fake_map { uintptr_t min,max; void *pmap; };
struct fake_thread { struct fake_map *map; };
typedef struct fake_thread *thread_t;
static struct fake_map mapping={1,UINTPTR_MAX,(void *)1};
static struct fake_thread thread={&mapping};
static struct fake_thread *current_thread(void) { return &thread; }
static uintptr_t vm_map_min(struct fake_map *m) { return m->min; }
static uintptr_t vm_map_max(struct fake_map *m) { return m->max; }
static void *kernel_pmap=(void *)1;
static void *kernel_task=(void *)1, *task=(void *)1;
static void *current_task(void) { return task; }
static uintptr_t physmap_base=1,physmap_end=UINTPTR_MAX;
static int copysize_limit_panic=64*1024*1024;
static unsigned slow_copy_calls;
static void zone_element_bounds_check(uintptr_t p,size_t n) { (void)p;(void)n; }
static void user_access_enable(int direction) { (void)direction; }
static void user_access_disable(int direction) { (void)direction; }
static int _bcopyin(const char *s,char *d,vm_size_t n) { slow_copy_calls++;memcpy(d,s,n);return 0; }
static vfs_context_t vfs_context_kernel(void) { return (void *)1; }
static struct { const char *stage; uint32_t dev,spec,covered; int opens,apfs; } fuxi_ram_mount_diag __attribute__((unused));
''' + copy_functions + '\n' + types + r'''
static unsigned calls;
static int kernel_mount(const char *fstypename,vnode_t p,vnode_t v,const char *path,void *data,size_t n,int flags,uint32_t internal_flags,vfs_context_t ctx) {
    assert(!p && !v && ctx==vfs_context_kernel());
    assert(n==sizeof(struct fuxi_ram_mount_request));
    assert(flags==(MNT_RDONLY|MNT_DONTBROWSE));
    user_addr_t fsmountargs=(uintptr_t)data,devpath=0;
    boolean_t do_copyin_devpath=true;
    int error=0;
''' + plumbing + r'''
    assert(!do_copyin_devpath && devpath);
    assert(!strcmp((const char *)devpath,!strcmp(path,"/mnt1")?RAM_DEVICE1:RAM_DEVICE2));
    assert(fsmountargs==(uintptr_t)data+sizeof(char *));
    {
        user_addr_t fsmountargs=(uintptr_t)data,devpath=0;
        boolean_t do_copyin_devpath=true;
''' + baseline_plumbing + r'''
        assert(!do_copyin_devpath && devpath && fsmountargs==USER_ADDR_NULL);
    }
    uint8_t bytes[0x144];memset(bytes,0x5a,sizeof(bytes));
    assert(copyin(fsmountargs,bytes,sizeof(bytes))==0);
    struct fuxi_ram_apfs_args args;memcpy(&args,bytes,sizeof(args));
    assert(args.mount_flags==MNT_RDONLY && args.options==0 && args.mount_mode==1);
    assert(args.uid==99 && args.gid==99);
    for(unsigned i=0;i<sizeof(args.reserved);i++)assert(!args.reserved[i]);
    assert(slow_copy_calls==0);calls++;return 0;
out1: return error;
}
''' + wrapper + r'''
int main(void) {
    assert(fuxi_ram_mount_ro("/dev/md1","/mnt1")==0);
    assert(fuxi_ram_mount_ro("/dev/md2","/mnt2")==0);
    assert(calls==2);
    task=(void *)2;
    assert(fuxi_ram_mount_ro("/dev/md1","/mnt1")==EPERM && calls==2);
    task=kernel_task;
    assert(fuxi_ram_mount_ro("/dev/md1","/mnt2")==EINVAL && calls==2);
    uint8_t source[324]={0},dest[324];source[8]=1;
    mapping.pmap=(void *)2;mapping.max=0x1000;
    assert(copyin((uintptr_t)source,dest,sizeof(dest))==EFAULT && !slow_copy_calls);
    mapping.max=UINTPTR_MAX;mapping.pmap=kernel_pmap;
    assert(copyin((uintptr_t)source,dest,sizeof(dest))==0 && !slow_copy_calls);
    assert(!memcmp(source,dest,sizeof(dest)));
    puts("actual wrapper/plumbing: md1/md2 carry 324-byte read-only volume-mode args");
    puts("parent plumbing negative control: the real v27 block discards APFS args");
    puts("actual ARM64 copyin: kernel-map EXDEV path copies; user-map bounds reject kernel input");
    puts("guards: non-kernel task and crossed mount paths refused before mount call");
}
'''
    with tempfile.TemporaryDirectory(prefix='ram-volume-args-') as directory:
        p=Path(directory);(p/'test.c').write_text(c)
        subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-Wno-sign-compare','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
        return subprocess.check_output([str(p/'test')]).decode().splitlines()


def audit_apfs(out, base):
    sys.path.insert(0,str(base/'kits/NativePrelink'))
    import native_prelink as np
    data=(out/'work/kernel/kernel.macho').read_bytes()
    plan=json.loads((out/'work/kernel/kernel.json').read_text())
    assert plan['original_sha256']=='f18b4df865405f2a287b84977d7aca745e056d656d9fc9df1662970ce2969de7'
    digest=plan['original_executable_sha256']['com.apple.filesystems.apfs']
    assert digest=='3192441dae74abc0d82e9069447194934598c1c63d2dacba0fb8599eef9996d7'
    image=np.macho(data)
    def read(va,n):
        at=np.backed_offset(image['segments'],va,n,len(data));return data[at:at+n]
    extent=next(e for e in plan['extents'] if e['module']=='com.apple.filesystems.apfs' and e['segment']=='__TEXT_EXEC')
    assert hashlib.sha256(read(extent['va'],extent['size'])).hexdigest()==digest
    # Addresses belong only to this hash-pinned, slide=-16 MiB APFS binary.
    assert plan['slide']==-0x1000000
    instructions={0xfffffff0056c6080:0xb4000397, # cbz x23 -> zero/default mount args
                  0xfffffff0056c608c:0x52802882, # mov w2,#324 before copyin
                  0xfffffff0056c6370:0x79401268} # ldrh w8,[x19,#8] mount mode
    for va,word in instructions.items():assert struct.unpack('<I',read(va,4))[0]==word,hex(va)
    table=struct.unpack('<9i',read(0xfffffff0056c81f0,36))
    targets=[0xfffffff0056c6388+x for x in table]
    assert targets[0]==0xfffffff0056c67dc and targets[1]==0xfffffff0056c66f0
    return dict(original_apfs_text_sha256=digest,apfs_text_unchanged=True,copyin_bytes=0x144,
                mode_offset=8,null_arguments_mode=0,requested_volume_mode=1,
                root_dispatch=hex(targets[0]),volume_dispatch=hex(targets[1]),
                limitation='Static original-code dispatch audit, not an executed APFS mount or an internal EBUSY breakpoint')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True,type=Path);p.add_argument('--base',required=True,type=Path)
    p.add_argument('--baseline',type=Path,help='v27 candidate output with NULL APFS args')
    a=p.parse_args();out,base=a.output.resolve(),a.base.resolve();root=out/'work/tree/.cache/xnu-work'
    source=(root/'bsd/vfs/vfs_syscalls.c').read_text();copy_source=(root/'osfmk/arm64/copyio.c').read_text()
    public=(root/'bsd/sys/mount.h').read_text();internal=(root/'bsd/sys/mount_internal.h').read_text()
    constants='\n'.join(re.findall(r'^#define (?:MNT_RDONLY|MNT_DONTBROWSE)\s+[^\n]+',public,re.M)+re.findall(r'^#define KERNEL_MOUNT_RAM_SYSTEM\s+[^\n]+',internal,re.M))
    patch=base/'patches/xnu/zz-ram-system-mount-volume-args.patch'
    spi['check_mount_patch'](root,base,patch)
    parent=a.baseline.resolve() if a.baseline else out.parent/'usb-system-mount-v27'
    baseline_source=(parent/'work/tree/.cache/xnu-work/bsd/vfs/vfs_syscalls.c').read_text()
    results=exercise(source,copy_source,constants,baseline_source);audit=audit_apfs(out,base)
    payload=json.loads((out/'payload-verification.json').read_text())
    assert hashlib.sha256((out/'work/kernel/kernel.macho').read_bytes()).hexdigest()==payload['kernel_sha256']
    report=dict(passed=results,apfs_audit=audit,source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                baseline_source_sha256=hashlib.sha256(baseline_source.encode()).hexdigest(),
                copyio_source_sha256=hashlib.sha256(copy_source.encode()).hexdigest(),patch_sha256=hashlib.sha256(patch.read_bytes()).hexdigest(),
                boot_sha256=payload['boot_sha256'],kernel_sha256=payload['kernel_sha256'],sanitizers=['address','undefined'],hardware_result='pending')
    (out/'mount-volume-args-tests.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
