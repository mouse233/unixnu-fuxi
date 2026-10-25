#!/usr/bin/env python3
"""Exercise real cache-prepare C and the pinned original dyld CacheFinder bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import tempfile
from ram_probe_policy import llvm_tool, sha

ROOT = Path(__file__).resolve().parents[1]
DYLD_SHA = 'f8df3c58fae6e409db740df22c98aa699aee110b73523541a63f1bd5e23cf705'
CACHE_DIR = '/Developer/mnt2/System/Library/Caches/com.apple.dyld'


def dyld_model(path):
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
    from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_PC
    raw = path.read_bytes()
    assert sha(path) == DYLD_SHA and len(raw) == 937056
    symbols = {}
    for line in subprocess.check_output([llvm_tool('llvm-nm'), str(path)], text=True).splitlines():
        fields = line.split()
        if len(fields) == 3:
            symbols[fields[2]] = int(fields[0], 16)
    ctor = '__ZN5dyld411CacheFinderC2ERKNS_13ProcessConfig7ProcessERKNS1_7LoggingERNS_15SyscallDelegateE'
    dtor = '__ZN5dyld411CacheFinderD2Ev'
    getenv = '__simple_getenv'
    opn = '__ZNK5dyld415SyscallDelegate4openEPKcii'
    close = '__ZNK5dyld415SyscallDelegate5closeEi'
    console = '__ZN5dyld47consoleEPKcz'
    base = 0x1000000
    results = []
    for flags, present, open_result in [(8, True, 0), (0, True, 0), (4, True, 0), (8, True, -1), (8, False, -1)]:
        uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.mem_map(base, 0x800000)
        offset = 32
        for _ in range(struct.unpack_from('<I', raw, 16)[0]):
            cmd, size = struct.unpack_from('<II', raw, offset)
            if cmd == 0x19:
                vm, _, fileoff, filesize = struct.unpack_from('<4Q', raw, offset + 24)
                if filesize:
                    uc.mem_write(base + vm, raw[fileoff:fileoff + filesize])
            offset += size
        obj, process, logging, delegate = [base + x for x in (0x400000, 0x401000, 0x402000, 0x403000)]
        path_at, stop = base + 0x404000, base + 0x700000
        uc.mem_write(path_at, CACHE_DIR.encode() + b'\0')
        uc.mem_write(process + 0x58, struct.pack('<Q', flags))
        uc.mem_write(process + 0x90, struct.pack('<I', 1))
        calls = dict(ignite=0, opens=[], closes=[])

        def cstring(at):
            return bytes(uc.mem_read(at, 256)).split(b'\0')[0].decode()

        def hook(machine, address, size, unused):
            if address == stop:
                machine.emu_stop()
                return
            result = None
            if address == base + symbols[getenv]:
                assert cstring(machine.reg_read(UC_ARM64_REG_X1)) == 'DYLD_SHARED_CACHE_DIR'
                result = path_at if present else 0
            elif address == base + symbols[opn]:
                calls['opens'].append(cstring(machine.reg_read(UC_ARM64_REG_X1)))
                result = open_result
            elif address == base + symbols[close]:
                calls['closes'].append(machine.reg_read(UC_ARM64_REG_X1))
                result = 0
            elif address == base + symbols['_ignite']:
                calls['ignite'] += 1
                result = 8  # Stubbed external ignition refusal, never a claim of full ignition emulation.
            elif address == base + symbols[console]:
                result = 0
            if result is not None:
                machine.reg_write(UC_ARM64_REG_X0, result & ((1 << 64) - 1))
                machine.reg_write(UC_ARM64_REG_PC, machine.reg_read(UC_ARM64_REG_X30))

        uc.hook_add(UC_HOOK_CODE, hook)
        for register, value in [(UC_ARM64_REG_X0, obj), (UC_ARM64_REG_X1, process),
                                (UC_ARM64_REG_X2, logging), (UC_ARM64_REG_X3, delegate),
                                (UC_ARM64_REG_SP, base + 0x600000), (UC_ARM64_REG_X30, stop)]:
            uc.reg_write(register, value)
        uc.emu_start(base + symbols[ctor], stop + 4, count=4000)
        assert uc.reg_read(UC_ARM64_REG_PC) == stop
        assert calls['ignite'] == int(not (flags & 8) and open_result != -1)
        assert not calls['closes'], 'override descriptor must survive constructor in the no-ignition path'
        assert calls['opens'] == [CACHE_DIR if present else '/System/Library/Caches/com.apple.dyld/']
        fd = struct.unpack('<i', uc.mem_read(obj, 4))[0]
        assert fd == open_result
        if flags == 8 and open_result == 0:
            uc.reg_write(UC_ARM64_REG_X0, obj)
            uc.reg_write(UC_ARM64_REG_X30, stop)
            uc.emu_start(base + symbols[dtor], stop + 4, count=1000)
            assert calls['closes'] == [0], 'only destructor closes the override fd after use'
        results.append(dict(flags=flags, environment_present=present, opened_fd=open_result, **calls))
    return results


API = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <setjmp.h>
#include <errno.h>
#include <assert.h>
#include <sys/types.h>
#include <CommonCrypto/CommonDigest.h>
#define SHA256_CTX CC_SHA256_CTX
#define SHA256_Init CC_SHA256_Init
#define SHA256_Final CC_SHA256_Final
#define SHA256_Update(c,b,n) CC_SHA256_Update(c,b,(CC_LONG)(n))
#define MNT_RDONLY 1
#define MNT_ROOTFS 0x4000
#define MNT_NOSUID 8
#define MNT_NODEV 16
#define MNT_NOEXEC 4
#define MNT_DONTBROWSE 0x100000
#define MNT_UPDATE 0x10000
#define FSIOC_KERNEL_ROOTAUTH 0x80046833U
#define UIO_READ 0
#define UIO_SYSSPACE 1
#define MIN(a,b) ((a)<(b)?(a):(b))
typedef uint64_t user_addr_t,user64_addr_t;
typedef void *proc_t;
typedef void *vfs_context_t;
enum vtype {VDIR=1,VREG=2};
struct mount {unsigned flags;struct {char f_fstypename[16];} stat;};
typedef struct mount *mount_t;
struct node {mount_t mount;enum vtype type;};
typedef struct node *vnode_t;
#define NULLVP ((vnode_t)0)
static struct mount root_mount={MNT_ROOTFS|MNT_RDONLY,{"apfs"}}, other_mount={MNT_RDONLY,{"apfs"}};
static struct node root_node={&root_mount,VDIR}, cache_node={&other_mount,VDIR}, directory_node={&other_mount,VDIR}, dyld_node={&root_mount,VREG};
static struct mount space_mount={0,{"tmpfs"}};
static struct node preboot_node={&root_mount,VDIR};
static int mounted,linked,load_error,mount_error,link_error,seal_error,bad_carrier,preexisting;
static int copyout_count,copyout_fail_call;
struct mount_args {user_addr_t type,path;int flags;user_addr_t data;};
struct symlink_args {user_addr_t path,link;};
static vnode_t rootvnode=&root_node;
volatile unsigned fuxi_ram_native_root_entry=1,fuxi_ram_root_stage=7,fuxi_ram_system_stage=6,fuxi_ram_cache_stage,fuxi_ram_cache_carrier;
volatile unsigned long long fuxi_ram_cache_dyld_flags;
uint64_t dyld_flags,comm_flags;
static int selector=1,auth_error,lookup_error,read_error,copyout_error,pid=1,bad_hash,bad_size,auth_calls;
static uint8_t *original_dyld;
static jmp_buf caught;
static char message[2048];
static void panic(const char *format,...) __attribute__((noreturn));
static void panic(const char *format,...) {va_list a;va_start(a,format);vsnprintf(message,sizeof(message),format,a);va_end(a);longjmp(caught,1);}
static mount_t vnode_mount(vnode_t n) {return n->mount;}
static unsigned vfs_flags(mount_t m) {return m->flags;}
#define vfs_statfs(m) (&(m)->stat)
static enum vtype vnode_vtype(vnode_t n) {return n->type;}
static int vnode_isvroot(vnode_t n) {return n==&cache_node || (n==&preboot_node && mounted);}
static void vnode_put(vnode_t n) {if(!n) abort();}
static void *vfs_context_kernel(void) {return (void*)1;}
static void *vfs_context_ucred(void *c) {return c;}
static void *vfs_context_proc(void *c) {return c;}
static int vnode_lookup(const char *p,int f,vnode_t *n,void *c) {
    if(f || !c) abort();if(lookup_error) return lookup_error;
    if(!strcmp(p,"/Developer/mnt2")) *n=&cache_node;
    else if(!strcmp(p,"/Developer/mnt2/System/Library/Caches/com.apple.dyld")) *n=&directory_node;
    else if(!strcmp(p,"/usr/lib/dyld")) *n=&dyld_node;
    else if(!strcmp(p,"/private/preboot")) *n=&preboot_node;
    else if(!strcmp(p,"/private/preboot/Cryptexes")) {
        if(!linked && !preexisting) return ENOENT;
        *n=bad_carrier==2?&preboot_node:bad_carrier?&root_node:&cache_node;
    } else abort();return 0;
}
static int VNOP_IOCTL(vnode_t n,unsigned long cmd,void *data,int f,void *c) {
    if(n!=&cache_node || cmd!=FSIOC_KERNEL_ROOTAUTH || data || f || !c) abort();auth_calls++;return auth_error;
}
static int PE_parse_boot_argn(const char *name,void *v,size_t n) {if(strcmp(name,"ram-system-cache") || n!=sizeof(unsigned)) abort();memcpy(v,&selector,n);return 1;}
static int proc_getpid(proc_t p) {if(!p) abort();return pid;}
static int vnode_size(vnode_t n,off_t *bytes,void *c) {if(n!=&dyld_node || !c) abort();*bytes=RAM_SYSTEM_DYLD_BYTES+bad_size;return 0;}
static int vn_rdwr(int d,vnode_t n,caddr_t b,int bytes,off_t at,int space,int flags,void *cred,int *resid,void *p) {
    if(d!=UIO_READ || n!=&dyld_node || space!=UIO_SYSSPACE || flags || !cred || !p || at+bytes>RAM_SYSTEM_DYLD_BYTES) abort();
    if(read_error) return read_error;memcpy(b,original_dyld+at,(size_t)bytes);if(bad_hash && !at) b[0]^=1;*resid=0;return 0;
}
static int copyout(const void *in,user_addr_t out,size_t n) {copyout_count++;if(copyout_error && (!copyout_fail_call || copyout_count==copyout_fail_call)) return copyout_error;memcpy((void*)(uintptr_t)out,in,n);return 0;}
void commpage_update_dyld_flags(uint64_t f) {comm_flags=f;}
int OSKextLoadKextWithIdentifier(const char *id) {if(strcmp(id,"com.apple.filesystems.tmpfs")) abort();return load_error;}
static int mount(proc_t p,struct mount_args *r,int32_t *result) {
    if(!p || !result || strcmp((void*)(uintptr_t)r->type,"tmpfs") || strcmp((void*)(uintptr_t)r->path,"/private/preboot")) abort();
    if(r->flags&MNT_UPDATE) {
        if(!mounted || !linked || r->data) abort();if(seal_error) return seal_error;
        space_mount.flags=r->flags&~MNT_UPDATE;return 0;
    }
    if(mount_error) return mount_error;
    uint64_t *data=(void*)(uintptr_t)r->data;
    if(mounted || data[0]!=256 || data[1]!=32 || data[2]) abort();
    mounted=1;space_mount.flags=r->flags;preboot_node.mount=&space_mount;return 0;
}
static int symlink(proc_t p,struct symlink_args *r,int32_t *result) {
    if(!p || !result || !mounted || (space_mount.flags&MNT_RDONLY) ||
       strcmp((void*)(uintptr_t)r->path,"/Developer/mnt2") || strcmp((void*)(uintptr_t)r->link,"/private/preboot/Cryptexes")) abort();
    if(link_error) return link_error;linked=1;return 0;
}
static void reset_layout(void) {
    mounted=linked=0;preboot_node.mount=&root_mount;space_mount.flags=0;
    copyout_count=0;fuxi_ram_cache_carrier=0;
}
'''

TESTS = r'''
#define REQUIRE(v) do {if(!(v)) {fprintf(stderr,"assertion line %d: %s\n",__LINE__,#v);exit(1);}} while(0)
#define REFUSE(text,...) do {message[0]=0;if(!setjmp(caught)) {__VA_ARGS__;REQUIRE(0);} REQUIRE(strstr(message,text));cases++;} while(0)
int main(int argc,char **argv) {
    REQUIRE(argc==2);FILE *f=fopen(argv[1],"rb");REQUIRE(f);original_dyld=malloc(RAM_SYSTEM_DYLD_BYTES);
    REQUIRE(fread(original_dyld,1,RAM_SYSTEM_DYLD_BYTES,f)==RAM_SYSTEM_DYLD_BYTES && fgetc(f)==EOF);fclose(f);
    int cases=0;uint8_t *scratch=calloc(1,4096);REQUIRE(scratch);user_addr_t envp=0;
    REFUSE("bind mode/stage",fuxi_ram_system_cache_bind(NULL));
    selector=0;REFUSE("bind mode/stage",fuxi_ram_system_cache_bind(&other_mount));selector=1;
    fuxi_ram_native_root_entry=0;REFUSE("bind mode/stage",fuxi_ram_system_cache_bind(&other_mount));fuxi_ram_native_root_entry=1;
    REFUSE("mount identity",fuxi_ram_system_cache_bind(&root_mount));
    other_mount.flags=0;REFUSE("mount identity",fuxi_ram_system_cache_bind(&other_mount));other_mount.flags=MNT_RDONLY;
    other_mount.flags|=MNT_ROOTFS;REFUSE("mount identity",fuxi_ram_system_cache_bind(&other_mount));other_mount.flags=MNT_RDONLY;
    strcpy(other_mount.stat.f_fstypename,"hfs");REFUSE("mount identity",fuxi_ram_system_cache_bind(&other_mount));strcpy(other_mount.stat.f_fstypename,"apfs");
    lookup_error=2;REFUSE("lookup failed",fuxi_ram_system_cache_bind(&other_mount));lookup_error=0;
    cache_node.mount=&root_mount;REFUSE("node identity",fuxi_ram_system_cache_bind(&other_mount));cache_node.mount=&other_mount;
    auth_error=80;REFUSE("rootauth failed",fuxi_ram_system_cache_bind(&other_mount));auth_error=0;
    fuxi_ram_system_cache_bind(&other_mount);REQUIRE(fuxi_ram_cache_stage==1 && auth_calls==2);cases++;
    REFUSE("bind mode/stage",fuxi_ram_system_cache_bind(&other_mount));
    REFUSE("init context",fuxi_ram_system_cache_prepare((void*)1,(uintptr_t)scratch,&envp));fuxi_ram_system_stage=7;
    pid=2;REFUSE("init context",fuxi_ram_system_cache_prepare((void*)1,(uintptr_t)scratch,&envp));pid=1;
    envp=4;REFUSE("init context",fuxi_ram_system_cache_prepare((void*)1,(uintptr_t)scratch,&envp));envp=0;
    other_mount.flags=0;REFUSE("mount flags changed",fuxi_ram_system_cache_prepare((void*)1,(uintptr_t)scratch,&envp));other_mount.flags=MNT_RDONLY;
    bad_size=1;REFUSE("dyld size",fuxi_ram_system_cache_prepare((void*)1,(uintptr_t)scratch,&envp));bad_size=0;
    read_error=5;REFUSE("dyld read",fuxi_ram_system_cache_prepare((void*)1,(uintptr_t)scratch,&envp));read_error=0;
    bad_hash=1;REFUSE("SHA256 mismatch",fuxi_ram_system_cache_prepare((void*)1,(uintptr_t)scratch,&envp));bad_hash=0;
    load_error=7;REFUSE("tmpfs load failed",fuxi_ram_system_cache_prepare((void*)1,(uintptr_t)scratch,&envp));load_error=0;
    mount_error=19;REFUSE("tmpfs mount failed",fuxi_ram_system_cache_prepare((void*)1,(uintptr_t)scratch,&envp));mount_error=0;
    link_error=30;REFUSE("symlink failed",fuxi_ram_system_cache_prepare((void*)1,(uintptr_t)scratch,&envp));link_error=0;reset_layout();
    seal_error=22;REFUSE("tmpfs seal failed",fuxi_ram_system_cache_prepare((void*)1,(uintptr_t)scratch,&envp));seal_error=0;reset_layout();
    preexisting=1;REFUSE("carrier already exists",fuxi_ram_system_cache_prepare((void*)1,(uintptr_t)scratch,&envp));preexisting=0;
    bad_carrier=1;REFUSE("node identity",fuxi_ram_system_cache_prepare((void*)1,(uintptr_t)scratch,&envp));bad_carrier=0;reset_layout();
    for(int i=1;i<=7;i++) {
        reset_layout();copyout_error=14;copyout_fail_call=i;
        REFUSE("errno=14",fuxi_ram_system_cache_prepare((void*)1,(uintptr_t)scratch,&envp));
        REQUIRE(!envp && !dyld_flags && !comm_flags);cases++;
        copyout_error=copyout_fail_call=0;
    }
    reset_layout();
    REQUIRE(!envp && !dyld_flags && !comm_flags);cases++;
    dyld_flags=0x100;fuxi_ram_system_cache_prepare((void*)1,(uintptr_t)scratch,&envp);
    REQUIRE(envp==(uintptr_t)scratch+0x500 && fuxi_ram_cache_stage==2);
    user64_addr_t *v=(void*)(scratch+0x500);REQUIRE(v[0]==(uintptr_t)scratch+0x400 && v[1]==0);
    REQUIRE(!strcmp((void*)(uintptr_t)v[0],"DYLD_SHARED_CACHE_DIR=/Developer/mnt2/System/Library/Caches/com.apple.dyld"));
    REQUIRE(dyld_flags==0x108 && comm_flags==0x108 && fuxi_ram_cache_dyld_flags==0x108);
    for(unsigned i=0;i<0x100;i++) REQUIRE(!scratch[i]);cases++;
    REQUIRE(fuxi_ram_cache_carrier==1 && (space_mount.flags&MNT_RDONLY));cases++;
    REFUSE("init context",fuxi_ram_system_cache_prepare((void*)1,(uintptr_t)scratch,&envp));
    printf("PASS: %d real-C cache bind/env/hash/startup refusal cases\n",cases);
    free(scratch);free(original_dyld);return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--dyld', type=Path, required=True)
    p.add_argument('--model-only', action='store_true')
    a = p.parse_args()
    out = a.output.resolve()
    raw = a.dyld.read_bytes()
    assert len(raw) == 937056 and sha(a.dyld) == DYLD_SHA
    source = ROOT / 'package/RamSystemCache.c'
    body = '\n'.join(line for line in source.read_text().splitlines() if not line.startswith('#include'))
    pins = '#define RAM_SYSTEM_DYLD_BYTES 937056\nstatic const uint8_t ram_system_dyld_sha256[32] = {' + ','.join(hex(x) for x in hashlib.sha256(raw).digest()) + '};\n'
    with tempfile.TemporaryDirectory(prefix='ram-system-cache-') as work:
        work = Path(work)
        test = work / 'test.c'
        command = ['clang', '-Wall', '-Wextra', '-Werror', '-Wno-deprecated-declarations',
                   '-fsanitize=address,undefined', str(test), '-o', str(work / 'test')]
        def run(code):
            test.write_text('#include <stdint.h>\n' + pins + API + code + TESTS)
            compiled = subprocess.run(command, capture_output=True, text=True)
            if compiled.returncode:
                raise RuntimeError(compiled.stdout + compiled.stderr)
            return subprocess.run([str(work/'test'), str(a.dyld.resolve())], capture_output=True, text=True)
        positive = run(body)
        assert positive.returncode == 0, positive.stdout + positive.stderr
        mutations = [body.replace('if (auth) panic', 'if (0) panic'),
                     body.replace('dyld_flags |= UINT64_C(8);', 'dyld_flags |= UINT64_C(0);'),
                     body.replace('memcmp(digest, ram_system_dyld_sha256, sizeof(digest))', '0 * memcmp(digest, ram_system_dyld_sha256, sizeof(digest))'),
                     body.replace('if (error) panic("RAM_CACHE tmpfs seal failed:', 'if (0) panic("RAM_CACHE tmpfs seal failed:')]
        for mutant in mutations:
            assert mutant != body
            assert run(mutant).returncode != 0, 'cache protection mutation survived'
    models = dyld_model(a.dyld)
    evidence = dict(real_c=positive.stdout.strip(), source_sha256=sha(source), original_dyld_sha256=DYLD_SHA,
                    mutation_checks=len(mutations), sanitizers=['address', 'undefined'], original_cachefinder_cases=models,
                    cache_directory=CACHE_DIR, hardware_result='pending')
    if not a.model_only:
        build = out / 'work/tree/build/ram-root-probe'
        record = json.loads((build/'build-report.json').read_text())
        candidate_pins = json.loads((out/'pins.json').read_text())
        assert record['system_cache'] is True and record['cache_source_sha256'] == sha(source) == sha(out/'work/tree/kits/sheng/RamSystemCache.c')
        assert record['archive_sha256'] == sha(build/'libRamRootProbe.a') and record['pins'] == candidate_pins
        assert candidate_pins['system_dyld_sha256'] == DYLD_SHA and candidate_pins['system_dyld_bytes'] == len(raw)
        assert '#define RAM_SYSTEM_CACHE 1' in (build/'RamSystemProbePins.h').read_text()
        assert candidate_pins['cache_carrier_namespace'] is True and candidate_pins['native_cs_hash_cache'] is True
        assert candidate_pins['progress_deadline_seconds'] in (300, 900)
        assert candidate_pins['progress_svc_limit'] in (131072, 1048576)
        header = (build/'RamRootProbePins.h').read_text()
        assert '#define RAM_ROOT_PROGRESS_SECONDS %d' % candidate_pins['progress_deadline_seconds'] in header
        assert '#define RAM_ROOT_PROGRESS_SVC_LIMIT %dU' % candidate_pins['progress_svc_limit'] in header
        assert '#define RAM_ROOT_NATIVE_HASH_CACHE 1' in header
        for obj, names in [(build/'RamSystemCache.o', ['_fuxi_ram_system_cache_prepare', '_commpage_update_dyld_flags', '_VNOP_IOCTL', '_mount', '_symlink', '_OSKextLoadKextWithIdentifier']),
                           (build/'RamSystemRootSwitch.o', ['_fuxi_ram_system_cache_bind']),
                           (out/'work/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/kern_exec.o', ['_fuxi_ram_system_cache_prepare'])]:
            symbols = subprocess.check_output([llvm_tool('llvm-nm'), str(obj)], text=True)
            assert all(name in symbols for name in names), obj
        image = out/'work/kernel/kernel.macho'
        symbols = subprocess.check_output([llvm_tool('llvm-nm'), str(image)], text=True)
        for name in ['_fuxi_ram_system_cache_bind', '_fuxi_ram_system_cache_prepare']:
            assert any(line.split()[-1:] == [name] and ' U ' not in line for line in symbols.splitlines())
        patched = (out/'work/tree/.cache/xnu-work/bsd/kern/kern_exec.c').read_text()
        init = patched[patched.index('load_init_program(proc_t p)'):]
        assert init.index('fuxi_ram_system_cache_prepare(p,') < init.index('fuxi_ram_system_user_init_path(&system_init)')
        syscalls = (out/'work/tree/.cache/xnu-work/bsd/dev/arm/systemcalls.c').read_text()
        assert 'uint64_t ram_trace_token = fuxi_ram_progress_unix(' in syscalls
        assert 'fuxi_ram_progress_unix_result(ram_trace_token, code, error, uthread->uu_rval[0])' in syscalls
        evidence.update(boot_sha256=sha(out/'Mu-fuxi-usb-ram.img'), kernel_sha256=sha(image), archive_sha256=record['archive_sha256'])
    (out/'system-cache-tests.json').write_text(json.dumps(evidence, indent=2)+'\n')
    print(positive.stdout.strip())
    print('PASS: 5 original CacheFinder executions; bit 3 skips ignition and preserves fd 0 until destructor')


if __name__ == '__main__':
    main()
