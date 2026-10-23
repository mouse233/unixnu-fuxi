#!/usr/bin/env python3
"""Exercise the actual diagnostic C with real image/file SHA256 and host API stubs."""
from pathlib import Path
import subprocess
import tempfile

import argparse
import json
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--ramdisk', type=Path, required=True)
parser.add_argument('--launchd', type=Path, required=True, help='Original /sbin/launchd extracted read-only from the pinned RAM disk')
args = parser.parse_args()
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'out/ram-guard-tests'
OUT.mkdir(parents=True, exist_ok=True)
production = ROOT / 'package/RamRootProbe.c'
pins_data = json.loads((ROOT / 'diagnostics/ram-root/pins.json').read_text())
header = '#define RAM_ROOT_IMAGE_BYTES UINT64_C(184549376)\n#define RAM_ROOT_FILE_BYTES 561856\n#define RAM_ROOT_ALLOW_USER_ENTRY 1\n#define RAM_ROOT_ALLOW_MAIN_ENTRY 0\n'
for name, key in [('image', 'image_sha256'), ('file', 'file_sha256')]:
    header += 'static const uint8_t ram_root_' + name + '_sha256[32] = {' + ','.join(hex(x) for x in bytes.fromhex(pins_data[key])) + '};\n'
body = '\n'.join(line for line in production.read_text().splitlines() if not line.startswith('#include'))
api = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <setjmp.h>
#include <sys/types.h>
#include <CommonCrypto/CommonDigest.h>
#define SHA256_CTX CC_SHA256_CTX
#define SHA256_Init CC_SHA256_Init
#define SHA256_Final CC_SHA256_Final
#define SHA256_Update(ctx, data, bytes) CC_SHA256_Update(ctx, data, (CC_LONG)(bytes))
#define VM_MIN_KERNEL_ADDRESS 4096ULL
#define NODEV ((dev_t)-1)
#define UIO_READ 0
#define UIO_SYSSPACE 1
#define MNT_RDONLY 1
typedef uint64_t user_addr_t;
typedef void *thread_t;
typedef void *vfs_context_t;
struct fake_statfs {char f_fstypename[16];};
struct fake_mount {struct fake_statfs stat; int flags;};
typedef struct fake_mount *mount_t;
struct fake_vnode {int unused;};
typedef struct fake_vnode *vnode_t;
#define NULLVP ((vnode_t)0)
static struct fake_mount root_mount = {{"apfs"}, MNT_RDONLY};
static struct fake_vnode root_node, file_node;
static unsigned selector=2;
static uint8_t *media, *launchd;
static uint64_t media_size;
static int pid=1, lookup_error=0, read_error=0, copy_error=0;
static int read_calls=0;
static char message[2048];
static jmp_buf caught;
static void panic(const char *format, ...) __attribute__((noreturn));
static void panic(const char *format, ...) {
    va_list args; va_start(args,format); vsnprintf(message,sizeof(message),format,args); va_end(args);
    longjmp(caught,1);
}
static int PE_parse_boot_argn(const char *name, void *value, size_t bytes) {
    if (strcmp(name,"ram-root-probe") || bytes!=sizeof(selector)) abort();
    memcpy(value,&selector,bytes); return 1;
}
dev_t mdevlookup(int index) {if(index) abort();return (dev_t)0x6000000;}
int mdevgetrange(int index,uint64_t *base,uint64_t *bytes) {
    if(index) abort(); *base=(uintptr_t)media; *bytes=media_size; return 0;
}
static vfs_context_t vfs_context_kernel(void) {return (void *)1;}
static mount_t vnode_mount(vnode_t node) {if(node!=&root_node) abort();return &root_mount;}
static struct fake_statfs *vfs_statfs(mount_t mount) {return &mount->stat;}
static int vfs_flags(mount_t mount) {return mount->flags;}
static int vnode_lookup(const char *path,int flags,vnode_t *file,vfs_context_t context) {
    if(strcmp(path,"/sbin/launchd") || flags || !context) abort(); *file=&file_node; return lookup_error;
}
static int vnode_size(vnode_t node,off_t *bytes,vfs_context_t context) {
    if(node!=&file_node || !context) abort(); *bytes=561856;return 0;
}
static int vnode_put(vnode_t node) {if(node!=&file_node) abort();return 0;}
static void *vfs_context_ucred(vfs_context_t context) {return context;}
static void *vfs_context_proc(vfs_context_t context) {return context;}
static int vn_rdwr(int direction,vnode_t node,caddr_t buffer,int bytes,off_t offset,
    int space,int flags,void *credential,int *residual,void *process) {
    if(direction!=UIO_READ || node!=&file_node || space!=UIO_SYSSPACE || flags ||
       !credential || !process || offset<0 || offset+bytes>561856) abort();
    read_calls++; if(read_error) return read_error;
    memcpy(buffer,launchd+offset,(size_t)bytes); *residual=0;return 0;
}
static void *current_proc(void) {return (void *)1;}
static int proc_getpid(void *proc) {if(!proc) abort();return pid;}
static int copyin(user_addr_t address,void *buffer,size_t bytes) {
    if(copy_error) return copy_error;memcpy(buffer,(void *)(uintptr_t)address,bytes);return 0;
}
'''
tests = r'''
#define REQUIRE(value) do {if(!(value)) {fprintf(stderr,"assertion line %d: %s\n",__LINE__,#value);exit(1);}} while(0)
static int cases=0;
#define REFUSE(text, ...) do {message[0]=0;if(!setjmp(caught)) {__VA_ARGS__;REQUIRE(0);} \
    REQUIRE(strstr(message,text)!=NULL);cases++;} while(0)
static uint8_t *read_file(const char *path, size_t expected) {
    FILE *file=fopen(path,"rb");REQUIRE(file);uint8_t *data=malloc(expected);REQUIRE(data);
    REQUIRE(fread(data,1,expected,file)==expected);REQUIRE(fgetc(file)==EOF);fclose(file);return data;
}
int main(int argc,char **argv) {
    REQUIRE(argc==3);media_size=184549376;media=read_file(argv[1],media_size);launchd=read_file(argv[2],561856);
    selector=1;REFUSE("diagnostic argument missing",fuxi_ram_root_probe_before_mount(mdevlookup(0)));selector=2;
    REFUSE("not expected md0",fuxi_ram_root_probe_before_mount(NODEV));
    media_size--;REFUSE("not expected md0",fuxi_ram_root_probe_before_mount(mdevlookup(0)));media_size++;
    media[0]^=1;REFUSE("media SHA256 mismatch",fuxi_ram_root_probe_before_mount(mdevlookup(0)));media[0]^=1;
    fuxi_ram_root_probe_before_mount(mdevlookup(0));REQUIRE(fuxi_ram_root_stage==3);
    root_mount.flags=0;REFUSE("without read-only flag",fuxi_ram_root_probe_after_mount(mdevlookup(0),&root_node));root_mount.flags=MNT_RDONLY;
    strcpy(root_mount.stat.f_fstypename,"hfs");REFUSE("unexpected device or filesystem",fuxi_ram_root_probe_after_mount(mdevlookup(0),&root_node));strcpy(root_mount.stat.f_fstypename,"apfs");
    lookup_error=2;REFUSE("lookup errno=2",fuxi_ram_root_probe_after_mount(mdevlookup(0),&root_node));lookup_error=0;
    read_error=5;REFUSE("read errno=5",fuxi_ram_root_probe_after_mount(mdevlookup(0),&root_node));read_error=0;
    launchd[0]^=1;REFUSE("launchd SHA256 mismatch",fuxi_ram_root_probe_after_mount(mdevlookup(0),&root_node));launchd[0]^=1;
    fuxi_ram_root_probe_after_mount(mdevlookup(0),&root_node);REQUIRE(fuxi_ram_root_stage==7 && read_calls>100);
    REFUSE("refused exec",fuxi_ram_user_exec_result(0,"/usr/local/bin/restored_external",1));
    REFUSE("refused exec",fuxi_ram_user_exec_result(0,"/sbin/launchd",2));
    REFUSE("exec failed: errno=13",fuxi_ram_user_exec_result(13,"/sbin/launchd",1));
    REFUSE("without replacement-thread checkpoint",fuxi_ram_user_exec_result(0,"/sbin/launchd",1));
    REFUSE("refused ready",fuxi_ram_user_exec_ready(NULL,1));
    REFUSE("refused ready",fuxi_ram_user_exec_ready((void *)3,2));
    fuxi_ram_user_exec_ready((void *)3,1);
    fuxi_ram_user_exec_result(0,"/sbin/launchd",1);REQUIRE(fuxi_ram_root_stage==8);
    REFUSE("refused ready",fuxi_ram_user_exec_ready((void *)3,1));
    uint32_t instruction=0xd4001001;uint64_t pc=(uintptr_t)&instruction+4;
    pid=2;REFUSE("refused SVC",fuxi_ram_user_svc(pc,0,0x56000080,20));pid=1;
    REFUSE("refused SVC",fuxi_ram_user_svc(pc,5,0x56000080,20));
    REFUSE("refused SVC",fuxi_ram_user_svc(pc,0,0x96000000,20));
    REFUSE("refused SVC",fuxi_ram_user_svc(UINT64_MAX,0,0x56000080,20));
    fuxi_ram_root_stage=7;REFUSE("refused SVC",fuxi_ram_user_svc(pc,0,0x56000080,20));fuxi_ram_root_stage=8;
    copy_error=14;REFUSE("invalid SVC instruction",fuxi_ram_user_svc(pc,0,0x56000080,20));copy_error=0;
    instruction=0xd503201f;REFUSE("invalid SVC instruction",fuxi_ram_user_svc(pc,0,0x56000080,20));instruction=0xd4001001;
    REFUSE("RAM_USER_ENTRY PASS",fuxi_ram_user_svc(pc,0,0x56000080,(uint64_t)-31));
    REQUIRE(fuxi_ram_root_stage==9 && strstr(message,"stopped BEFORE first syscall dispatch"));
    printf("PASS: actual diagnostic C, real image/file SHA256, %d stop/refusal cases; first SVC never dispatched\n",cases);
    free(media);free(launchd);return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='fuxi-user-guards-') as temporary:
    work = Path(temporary)
    (work / 'test.c').write_text(api + '\n' + header + '\n' + body + '\n' + tests)
    subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-Wno-deprecated-declarations',
                    str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    result = subprocess.run([str(work / 'test'),
        str(args.ramdisk.resolve()),
        str(args.launchd.resolve())], check=True, capture_output=True, text=True)
    (OUT / 'guard-tests.log').write_text(result.stdout + result.stderr)
    print(result.stdout, end='')
