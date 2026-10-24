#!/usr/bin/env python3
"""Exercise the actual bounded mount-log snapshot and thread-scoped open record."""
import argparse
import hashlib
import json
import runpy
import subprocess
import tempfile
from pathlib import Path

spi=runpy.run_path(str(Path(__file__).with_name('check-usb-system-mount-spi.py')),run_name='spi')


def exercise(source,header):
    start=source.index('/* RAM mount witness:')
    witness=source[start:source.index('int\nvfs_mount_at_path(',start)]
    c=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <sys/types.h>
#define XNU_KERNEL_PRIVATE 1
#define KERNEL_MOUNT_RAM_SYSTEM 0x10000000
#define os_atomic_load(p,order) __atomic_load_n(p,__ATOMIC_ACQUIRE)
#define os_atomic_store(p,v,order) __atomic_store_n(p,v,__ATOMIC_RELEASE)
typedef void *thread_t;
static thread_t thread=(void *)1;
static thread_t current_thread(void) { return thread; }
static struct { int apfs; } fuxi_ram_mount_diag;
static int locked;
void bsd_log_lock_safe(void) { assert(!locked);locked=1; }
void bsd_log_unlock(void) { assert(locked);locked=0; }
''' + header + r'''
struct msgbuf *msgbufp;
static char ring[2048],replacement[2048];
static struct msgbuf message={.msg_magic=MSG_MAGIC,.msg_size=sizeof(ring),.msg_bufc=ring};
static void fresh(unsigned size,unsigned position) {
    memset(ring,0,sizeof(ring));message.msg_magic=MSG_MAGIC;message.msg_size=size;
    message.msg_bufx=position;message.msg_bufc=ring;msgbufp=&message;thread=(void *)1;
}
static void log_bytes(const char *s,size_t n) {
    bsd_log_lock_safe();
    for(size_t i=0;i<n;i++){message.msg_bufc[message.msg_bufx]=s[i];message.msg_bufx=(message.msg_bufx+1)%message.msg_size;}
    bsd_log_unlock();
}
''' + witness + r'''
int main(void) {
    fresh(2048,0);log_bytes("OLD",3);
    fuxi_ram_mount_witness_begin(KERNEL_MOUNT_RAM_SYSTEM);
    fuxi_ram_mount_open_result("/dev/md1",0);
    thread=(void *)2;fuxi_ram_mount_open_result("/unrelated",99);thread=(void *)1;
    fuxi_ram_mount_open_result("/missing",2);fuxi_ram_mount_open_result("/later-success",0);
    log_bytes("APFS inner ENOENT\n",18);
    fuxi_ram_mount_witness_end(KERNEL_MOUNT_RAM_SYSTEM);fuxi_ram_mount_diag.apfs=2;
    const char *result=fuxi_ram_mount_witness();
    assert(strstr(result,"open=2 count=3 /missing"));assert(strstr(result,"APFS inner ENOENT"));
    assert(!strstr(result,"OLD") && !strstr(result,"unrelated"));
    fuxi_ram_mount_open_result("/after",3);assert(fuxi_ram_mount_evidence.open_error==2);
    puts("callback scope: prior logs/other-thread opens excluded; failed path survives later success");

    fresh(64,60);fuxi_ram_mount_witness_begin(KERNEL_MOUNT_RAM_SYSTEM);
    log_bytes("WRAP\nOK",7);fuxi_ram_mount_witness_end(KERNEL_MOUNT_RAM_SYSTEM);
    assert(!strcmp(fuxi_ram_mount_evidence.tail,"WRAP\nOK"));assert(!locked);
    puts("ring wrap: tail copied across write-index rollover without out-of-bounds reads");

    fresh(2048,0);fuxi_ram_mount_witness_begin(KERNEL_MOUNT_RAM_SYSTEM);
    char long_log[1000];memset(long_log,'A',sizeof(long_log));log_bytes(long_log,sizeof(long_log));
    log_bytes("LAST%p\1\n",8);fuxi_ram_mount_witness_end(KERNEL_MOUNT_RAM_SYSTEM);
    assert(strlen(fuxi_ram_mount_evidence.tail)==640);
    assert(!strcmp(fuxi_ram_mount_evidence.tail+632,"LAST%p.\n"));
    assert(strlen(fuxi_ram_mount_witness())<sizeof(fuxi_ram_mount_evidence.text));
    puts("bounded output: 640-byte tail, termination, control-byte sanitization and literal format text");

    fresh(2048,0);fuxi_ram_mount_witness_begin(KERNEL_MOUNT_RAM_SYSTEM);
    message.msg_bufc=replacement;fuxi_ram_mount_witness_end(KERNEL_MOUNT_RAM_SYSTEM);
    assert(strstr(fuxi_ram_mount_witness(),"none/unavailable"));
    fresh(2048,0);message.msg_magic=0;fuxi_ram_mount_witness_begin(KERNEL_MOUNT_RAM_SYSTEM);
    fuxi_ram_mount_witness_end(KERNEL_MOUNT_RAM_SYSTEM);assert(fuxi_ram_mount_evidence.start==-1);
    msgbufp=NULL;fuxi_ram_mount_witness_begin(KERNEL_MOUNT_RAM_SYSTEM);fuxi_ram_mount_witness_end(KERNEL_MOUNT_RAM_SYSTEM);
    assert(strstr(fuxi_ram_mount_witness(),"none/unavailable"));assert(!locked);
    puts("unavailable/changed log buffers refused without stale pointer access");

    fresh(2048,0);fuxi_ram_mount_witness_begin(0);fuxi_ram_mount_open_result("/ordinary",2);fuxi_ram_mount_witness_end(0);
    assert(!fuxi_ram_mount_owner && !fuxi_ram_mount_evidence.opens);
    fuxi_ram_mount_diag.apfs=-1;assert(!strcmp(fuxi_ram_mount_witness(),"APFS not called"));
    puts("ordinary mounts and pre-APFS failures: no capture and no stale diagnostic display");
}
'''
    with tempfile.TemporaryDirectory(prefix='ram-witness-') as directory:
        p=Path(directory);(p/'test.c').write_text(c)
        subprocess.run(['clang','-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(p/'test.c'),'-o',str(p/'test')],check=True)
        return subprocess.check_output([str(p/'test')]).decode().splitlines()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--base',type=Path,required=True);p.add_argument('--parent',type=Path,required=True)
    a=p.parse_args();out,base,parent=a.output.resolve(),a.base.resolve(),a.parent.resolve();root=out/'work/tree/.cache/xnu-work'
    source=(root/'bsd/vfs/vfs_syscalls.c').read_text();subr=(root/'bsd/vfs/vfs_subr.c').read_text()
    old=(parent/'work/tree/.cache/xnu-work/bsd/vfs/vfs_subr.c').read_text()
    def vnode_open(text):return spi['braced'](text,text.index('errno_t\nvnode_open('))
    assert vnode_open(subr).replace('\tfuxi_ram_mount_open_result(path, error);\n','')==vnode_open(old)
    callback=source.index('\t\terror = VFS_MOUNT(mp, device_vnode, fsmountargs, ctx);')
    assert source[:callback].rstrip().endswith('fuxi_ram_mount_witness_begin(internal_flags);')
    assert source[callback:].splitlines()[1].strip()=='fuxi_ram_mount_witness_end(internal_flags);'
    patch=base/'patches/xnu/zz-ram-system-mount-witness.patch';spi['check_mount_patch'](root,base,patch)
    results=exercise(source,(root/'bsd/sys/msgbuf.h').read_text())
    for name in ['vfs_syscalls','vfs_subr']:
        record=json.loads((out/f'work/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/{name}.o.json').read_text().rstrip().removesuffix(','))
        assert str(root/f'bsd/vfs/{name}.c') in record['arguments']
    payload=json.loads((out/'payload-verification.json').read_text());kernel=(out/'work/kernel/kernel.macho').read_bytes()
    assert hashlib.sha256(kernel).hexdigest()==payload['kernel_sha256']
    assert b'LOG tail (bounded):\n' in kernel and b'open=%d count=%u %s' in kernel
    report=dict(passed=results,open_behavior_preserved=True,callback_scope_verified=True,
        source_sha256=hashlib.sha256(source.encode()).hexdigest(),subr_sha256=hashlib.sha256(subr.encode()).hexdigest(),
        patch_sha256=hashlib.sha256(patch.read_bytes()).hexdigest(),boot_sha256=payload['boot_sha256'],kernel_sha256=payload['kernel_sha256'],
        sanitizers=['address','undefined'],hardware_result='pending',
        limits='Shared log tail may contain other threads, drop earlier lines, or be unavailable; exact full-ring laps are not distinguished from zero output')
    (out/'mount-witness-tests.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
