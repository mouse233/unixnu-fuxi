#!/usr/bin/env python3
"""Fault-inject extracted XNU mount blocks; verify EBUSY stage survives cleanup.

Runs the real covered-vnode preparation, device preflight, APFS call site and
commit checks against host stubs. It cannot exercise APFS or replace a photo.
"""
import argparse
import hashlib
import json
import re
import runpy
import subprocess
import tempfile
from pathlib import Path

spi = runpy.run_path(str(Path(__file__).with_name('check-usb-system-mount-spi.py')), run_name='mount_spi')
braced = spi['braced']


def exercise(source, constants):
    diagnostic = source[source.index('/* RAM-only, single-caller'):source.index('__private_extern__\nint\nkernel_mount(')]
    wrapper = braced(source, source.index('int\nfuxi_ram_mount_ro('))
    covered = braced(source, source.index('int\nprepare_coveredvp('))
    preflight = braced(source, source.index('if (devpath && ((flags & MNT_UPDATE) == 0)) {'))
    callback_start = source.index('fuxi_ram_mount_mark(internal_flags, "apfs-mount");')
    callback_end = source.index('\n\t}', callback_start)
    callback = source[callback_start:callback_end]
    commit_start = source.index('fuxi_ram_mount_mark(internal_flags, "mount-list-add");')
    commit_if = source.index('if (mount_list_add(mp) != 0) {', commit_start)
    commit = source[commit_start:commit_if] + braced(source, commit_if)
    harness = constants + '\n' + r'''
#include <assert.h>
#include <errno.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <setjmp.h>
#define CONFIG_MACF 1
#define NULLVP NULL
#define NODEV (-1)
#define current_task() ((void *)1)
#define kernel_task ((void *)1)
#define fuxi_ram_mount_witness() ""
#define fuxi_ram_mount_witness_begin(flags) ((void)0)
#define fuxi_ram_mount_witness_end(flags) ((void)0)
#define MNT_WAIT 1
#define BUF_WRITE_DATA 1
#define VDIR 1
#define PVFS 1
#define FREAD 1
#define FWRITE 2
#define ISSET(v,b) ((v)&(b))
#define SET(v,b) ((v)|=(b))
#define CLR(v,b) ((v)&=~(b))
#define VATTR_INIT(v) ((v)->va_uid=0)
#define VATTR_WANTED(v,k) ((void)(v))
typedef bool boolean_t;
typedef void *vfs_context_t;
struct vnode { uint32_t v_flag, v_rdev, v_specflags; int v_type, locked; void *v_mountedhere; int v_lock; };
typedef struct vnode *vnode_t;
struct mount { int mnt_flag; vnode_t mnt_devvp; };
struct vnode_attr { int va_uid; };
struct componentname { int unused; };
static const char *injected;
static int opens, count_calls, apfs_calls, cleaned;
static char panic_text[512];
static jmp_buf panic_target;
static int fault(const char *stage) { return injected && !strcmp(injected,stage) ? EBUSY : 0; }
static void vnode_lock_spin(vnode_t v) { assert(!v->locked); v->locked=1; }
static void vnode_unlock(vnode_t v) { assert(v->locked); v->locked=0; }
static void vnode_lock_convert(vnode_t v) { assert(v->locked); }
static void msleep(void *p, void *l, int pri, const char *w, void *t) { (void)p;(void)l;(void)pri;(void)w;(void)t; }
static void wakeup(void *p) { (void)p; }
static vfs_context_t vfs_context_kernel(void) { return (void *)1; }
static int vfs_context_ucred(vfs_context_t c) { (void)c;return 0; }
static int kauth_cred_getuid(int c) { return c; }
static int vfs_context_issuser(vfs_context_t c) { (void)c;return 1; }
static int vnode_getattr(vnode_t v, struct vnode_attr *a, vfs_context_t c) { (void)v;(void)a;(void)c;return 0; }
static int VNOP_FSYNC(vnode_t v,int wait,vfs_context_t c) { (void)wait;(void)c;return fault(v->v_type==VDIR?"covered-fsync":"device-fsync"); }
static int buf_invalidateblks(vnode_t v,int f,int a,int b) { (void)f;(void)a;(void)b;return fault(v->v_type==VDIR?"covered-invalidate":"device-invalidate"); }
static int mac_mount_check_mount(vfs_context_t c,vnode_t v,struct componentname *n,const char *f) { (void)c;(void)v;(void)n;(void)f;return fault("covered-mac"); }
static int vnode_ref(vnode_t v) { (void)v;return fault("device-ref"); }
static void vnode_rele(vnode_t v) { (void)v; }
static int vfs_setmounting(vnode_t v) { if(fault("device-setmounting")){v->v_specflags=1;return EBUSY;} v->v_specflags=4;return 0; }
static int vcount(vnode_t v) { (void)v;count_calls++;return opens; }
static int vfs_flags(struct mount *m) { return m->mnt_flag; }
static int mac_vnode_check_open(vfs_context_t c,vnode_t v,int f) { (void)c;(void)v;assert(f==FREAD);return fault("device-mac-open"); }
static int VNOP_OPEN(vnode_t v,int f,vfs_context_t c) { (void)v;(void)c;assert(f==FREAD);return fault("device-open"); }
static int VFS_MOUNT(struct mount *m,vnode_t v,void *args,vfs_context_t c) { (void)v;(void)c;assert(m->mnt_flag&MNT_RDONLY);assert(!args);apfs_calls++;return fault("apfs-mount"); }
static int mount_list_add(struct mount *m) { (void)m;return fault("mount-list-add"); }
static void panic(const char *fmt,...) __attribute__((noreturn,format(printf,1,2)));
static void panic(const char *fmt,...) { va_list a;va_start(a,fmt);vsnprintf(panic_text,sizeof(panic_text),fmt,a);va_end(a);longjmp(panic_target,1); }
''' + diagnostic + '\n' + covered + r'''
static int run_blocks(uint32_t internal_flags) {
    int error=0, flags=MNT_RDONLY, ronly=0;
    uintptr_t devpath=1;
    void *fsmountargs=NULL;
    vfs_context_t ctx=vfs_context_kernel();
    struct mount storage={.mnt_flag=MNT_RDONLY}, *mp=&storage;
    vnode_t vp=calloc(1,sizeof(*vp)), devvp=calloc(1,sizeof(*devvp)), device_vnode=NULL;
    assert(vp && devvp);
    vp->v_type=VDIR;devvp->v_type=2;devvp->v_rdev=0x12000001;
    if (fault("covered-busy")) {vp->v_flag=VMOUNT;vp->v_mountedhere=(void *)1;}
    error=prepare_coveredvp(vp,ctx,NULL,"apfs",internal_flags);
    if(error)goto out1;
''' + preflight + '\n' + callback + r'''
    if(error)goto out3;
''' + commit + r'''
    goto out1;
out4:
out3:
    devvp->v_specflags=0; /* Deliberately erase state before releasing the vnode. */
out2:
out1:
    assert(!vp->locked && !devvp->locked);
    free(vp);free(devvp);cleaned=1;
    return error;
}
static int kernel_mount(const char *type,vnode_t pvp,vnode_t vp,const char *path,void *data,size_t n,int flags,uint32_t k,vfs_context_t c) {
    (void)pvp;(void)vp;(void)path;(void)data;(void)n;(void)c;
    assert(!strcmp(type,"apfs") && flags==(MNT_RDONLY|MNT_DONTBROWSE));
    return run_blocks(k|KERNEL_MOUNT_KMOUNT);
}
''' + spi['ram_types'](source) + wrapper + r'''
int main(void) {
    const char *failures[]={"covered-fsync","covered-invalidate","covered-busy","covered-mac","device-ref","device-setmounting","device-vcount","device-fsync","device-invalidate","device-mac-open","device-open","apfs-mount","mount-list-add"};
    for(unsigned i=0;i<sizeof(failures)/sizeof(*failures);i++) {
        injected=failures[i];opens=!strcmp(injected,"device-vcount")?2:0;
        count_calls=apfs_calls=cleaned=0;
        if(!setjmp(panic_target)) { fuxi_ram_mount_ro("/dev/md1","/mnt1");assert(!"expected panic"); }
        assert(cleaned && !strcmp(fuxi_ram_mount_diag.stage,injected));
        assert(strstr(panic_text,injected) && strstr(panic_text,"path=/mnt1"));
        assert(strstr(panic_text,!strcmp(injected,"device-fsync")?"errno=15":"errno=16"));
        assert(count_calls<=1);
        if(!strcmp(injected,"device-setmounting")) {assert(fuxi_ram_mount_diag.spec==1);assert(fuxi_ram_mount_diag.opens==-1);}
        if(!strcmp(injected,"device-vcount")) {assert(fuxi_ram_mount_diag.opens==2);assert(fuxi_ram_mount_diag.spec==4);}
        if(!strcmp(injected,"apfs-mount")) {assert(apfs_calls==1 && fuxi_ram_mount_diag.apfs==EBUSY);}
        else if(!strcmp(injected,"mount-list-add")) {assert(apfs_calls==1 && fuxi_ram_mount_diag.apfs==0);}
        else {assert(apfs_calls==0 && fuxi_ram_mount_diag.apfs==-1);}
        if(!strcmp(injected,"covered-busy"))assert(fuxi_ram_mount_diag.covered==VMOUNT);
        printf("%s: correct failure stage after vnode cleanup\n",injected);
    }
    injected=NULL;opens=0;count_calls=0;
    assert(fuxi_ram_mount_ro("/dev/md2","/mnt2")==0);
    assert(fuxi_ram_mount_diag.apfs==0 && count_calls==1);
    fuxi_ram_mount_reset();fuxi_ram_mount_diag.stage="untouched";
    assert(run_blocks(KERNEL_MOUNT_KMOUNT)==0);
    assert(!strcmp(fuxi_ram_mount_diag.stage,"untouched"));
    assert(fuxi_ram_mount_diag.dev==(uint32_t)NODEV && fuxi_ram_mount_diag.opens==-1 && fuxi_ram_mount_diag.apfs==-1);
    puts("success and ordinary mount: return/diagnostic isolation passed");
}
'''
    with tempfile.TemporaryDirectory(prefix='ram-mount-trace-') as directory:
        path = Path(directory)
        (path / 'test.c').write_text(harness)
        subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                        str(path / 'test.c'), '-o', str(path / 'test')], check=True)
        return subprocess.check_output([str(path / 'test')]).decode().splitlines()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--parent', type=Path, required=True)
    a = p.parse_args()
    out, base, parent = a.output.resolve(), a.base.resolve(), a.parent.resolve()
    root = out / 'work/tree/.cache/xnu-work'
    source_path = root / 'bsd/vfs/vfs_syscalls.c'
    source = source_path.read_text()
    parent_source = (parent / 'work/tree/.cache/xnu-work/bsd/vfs/vfs_syscalls.c').read_text()
    for begin, end in [('exit:\n', '\n/*\n * Flush in-core data')]:
        old = parent_source[parent_source.index(begin):parent_source.index(end)]
        new = source[source.index(begin):source.index(end)]
        assert old == new, 'mount cleanup changed'
    patch = base / 'patches/xnu/zz-ram-system-mount-trace.patch'
    spi['check_mount_patch'](root, base, patch)
    constants = []
    for header, pattern in [('bsd/sys/mount_internal.h', r'KERNEL_MOUNT_\w+'),
                            ('bsd/sys/mount.h', r'MNT_(?:RDONLY|DONTBROWSE|UPDATE|ROOTFS)'),
                            ('bsd/sys/vnode_internal.h', r'VMOUNT')]:
        constants += re.findall(r'^#define\s+(?:'+pattern+r')\s+[^\n]+', (root / header).read_text(), re.M)
    results = exercise(source, '\n'.join(constants))
    record_path = out / 'work/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/vfs_syscalls.o.json'
    record = json.loads(record_path.read_text().rstrip().removesuffix(','))
    assert str(source_path) in record['arguments'], 'VFS object compiled from another candidate'
    payload = json.loads((out / 'payload-verification.json').read_text())
    kernel = (out / 'work/kernel/kernel.macho').read_bytes()
    assert hashlib.sha256(kernel).hexdigest() == payload['kernel_sha256']
    # XNU's panic macro appends the source location to the format string.
    suffix = '\n%s' if '/* RAM mount witness:' in source else ''
    for text in ['RAM_SYSTEM mount failed errno=%d\nat=%s path=%s\ndev=%x opens=%d spec=%x\napfs=%d covered=%x' + suffix + ' @%s:%d',
                 'covered-busy', 'device-setmounting', 'device-vcount', 'apfs-mount', 'mount-list-add']:
        assert text.encode() + b'\0' in kernel, 'trace missing from delivered prelink: ' + text
    report = dict(passed=results, cleanup_identical_to_parent=True,
                  prelink_trace_strings_verified=True,
                  source_sha256=hashlib.sha256(source.encode()).hexdigest(),
                  patch_sha256=hashlib.sha256(patch.read_bytes()).hexdigest(),
                  boot_sha256=payload['boot_sha256'], kernel_sha256=payload['kernel_sha256'],
                  sanitizers=['address', 'undefined'], hardware_result='pending')
    (out / 'mount-trace-tests.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
