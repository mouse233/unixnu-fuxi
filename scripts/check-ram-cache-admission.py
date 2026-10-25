#!/usr/bin/env python3
"""Exercise original carrier policy and actual 16K-to-4K signature preparation."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
from ram_probe_policy import llvm_tool, sha

ROOT = Path(__file__).resolve().parents[1]


def tmpfs_model(base, out):
    """Run unchanged tmpfs mount admission/update instructions, with KPI stubs."""
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_UNMAPPED
    from unicorn.arm64_const import UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2, UC_ARM64_REG_X3, UC_ARM64_REG_X30, UC_ARM64_REG_SP, UC_ARM64_REG_PC
    sys.path.insert(0, str(base.resolve()/'kits/NativePrelink'))
    from native_prelink import macho, backed_offset, unserialize_xml, exports
    raw = (out/'cache/ipsw/21H461/kernelcache.decompressed').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == 'f18b4df865405f2a287b84977d7aca745e056d656d9fc9df1662970ce2969de7'
    parent = macho(raw)
    info = next(s for s in parent['segments'] if s['name'] == '__PRELINK_INFO')
    at = backed_offset(parent['segments'], info['va'], info['filesize'], len(raw))
    entries = unserialize_xml(raw[at:at+info['filesize']])['_PrelinkInfoDictionary']
    entry = next(e for e in entries if e['CFBundleIdentifier'] == 'com.apple.filesystems.tmpfs')
    module = macho(raw, backed_offset(parent['segments'], entry['_PrelinkExecutableLoadAddr'], 32, len(raw)))
    # 21H461 original kmod start registers the vfsops at this original data VA.
    # Pin it to this exact kernelcache, and pin every callee through actual GOT.
    vfsops = 0xfffffff007c29ae0
    at = backed_offset(parent['segments'], vfsops, 8, len(raw))
    mount = struct.unpack_from('<Q', raw, at)[0]
    assert mount == 0xfffffff006c3d108
    copyin_instruction = struct.unpack_from('<I', raw, backed_offset(parent['segments'], mount+0xdc, 4, len(raw)))[0]
    assert copyin_instruction & 0xffe0001f == 0x52800002 and (copyin_instruction >> 5) & 0xffff == 24
    names = exports(raw, parent)
    text = next(s for s in module['segments'] if s['name'] == '__TEXT_EXEC')
    stubs = next(s for s in text['sections'] if s['name'] == '__stubs')
    callees = {}
    for stub in range(stubs['va'], stubs['va']+stubs['size'], 12):
        w, v = struct.unpack_from('<II', raw, backed_offset(parent['segments'], stub, 8, len(raw)))
        immediate = ((w >> 5) & 0x7ffff) << 2 | ((w >> 29) & 3)
        if immediate & (1 << 20): immediate -= 1 << 21
        got = (stub & ~4095) + (immediate << 12) + ((v >> 10) & 0xfff)*8
        target = struct.unpack_from('<Q', raw, backed_offset(parent['segments'], got, 8, len(raw)))[0]
        callees[stub] = names.get(target, [hex(target)])[0]
    results = []
    for mode in ('new-copyin', 'new-bad-copyin', 'readonly-update', 'reload-refused', 'nonroot-refused'):
        uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc.mem_map(0x2000000, 0x100000)
        data, state, stop = 0x2010000, 0x2020000, 0x20f0000
        uc.mem_write(data, struct.pack('<QQII', 256, 32, 0, 0))
        mapped = set()
        observation = dict(mode=mode, copyin_bytes=None, stopped_before_allocation=False)

        def demand(machine, access, address, size, value, context):
            for page in range(address & ~4095, (address+size+4095) & ~4095, 4096):
                if page in mapped: continue
                segment = next((s for s in parent['segments'] if s['va'] <= page < s['va']+s['size']), None)
                if segment is None: return False
                machine.mem_map(page, 4096)
                offset = page - segment['va']
                n = max(0, min(4096, segment['filesize']-offset))
                if n: machine.mem_write(page, raw[segment['fileoff']+offset:segment['fileoff']+offset+n])
                mapped.add(page)
            return True

        def hook(machine, address, size, context):
            if address == stop:
                observation['returncode'] = machine.reg_read(UC_ARM64_REG_X0)
                machine.emu_stop(); return
            if address not in callees: return
            name = callees[address]
            if name == '_vfs_context_ucred': result = 1
            elif name == '_kauth_cred_issuser': result = int(mode != 'nonroot-refused')
            elif name == '_vfs_isupdate': result = int(mode in ('readonly-update', 'reload-refused'))
            elif name == '_vfs_fsprivate': result = state
            elif name == '_vfs_isreload': result = int(mode == 'reload-refused')
            elif name == '_vfs_iswriteupgrade': result = 0
            elif name == '_vfs_isrdonly': result = int(mode == 'readonly-update')
            elif name == '_copyin':
                assert machine.reg_read(UC_ARM64_REG_X0) == data
                assert machine.reg_read(UC_ARM64_REG_X2) == 24
                observation['copyin_bytes'] = 24
                if mode == 'new-copyin':
                    observation['stopped_before_allocation'] = True
                    machine.emu_stop(); return
                assert mode == 'new-bad-copyin'
                result = 14
            elif name in ('__os_log_error_impl', '__os_log_internal'): result = 0
            else: raise AssertionError('unexpected original tmpfs callee: ' + name)
            machine.reg_write(UC_ARM64_REG_X0, result)
            machine.reg_write(UC_ARM64_REG_PC, machine.reg_read(UC_ARM64_REG_X30))

        uc.hook_add(UC_HOOK_MEM_UNMAPPED, demand)
        uc.hook_add(UC_HOOK_CODE, hook)
        for register, value in [(UC_ARM64_REG_X0, 1), (UC_ARM64_REG_X1, 0), (UC_ARM64_REG_X2, data),
                                (UC_ARM64_REG_X3, 2), (UC_ARM64_REG_SP, 0x2080000), (UC_ARM64_REG_X30, stop)]:
            uc.reg_write(register, value)
        uc.emu_start(mount, 0, count=2000)
        if mode == 'new-copyin': assert observation['stopped_before_allocation']
        else:
            assert observation['returncode'] == {'new-bad-copyin':5, 'readonly-update':0, 'reload-refused':22, 'nonroot-refused':1}[mode]
            if mode == 'readonly-update': assert struct.unpack('<I', uc.mem_read(state+0x78, 4))[0] == 1
        results.append(observation)
    return results


def function(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


HASH_API = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <arpa/inet.h>
#include <sys/types.h>
#include <sys/mman.h>
#include <CommonCrypto/CommonDigest.h>
#define PAGE_SHIFT 12
#define PAGE_SIZE 4096
#define PAGE_MASK 4095
#define CS_HASH_MAX_SIZE 64
#define CS_SUPPORTSSCATTER 0x20100
#define CS_SUPPORTSCODELIMIT64 0x20300
#define UIO_READ 0
#define UIO_SYSSPACE 1
#define KMA_KOBJECT 1
#define KMA_DATA 2
#define KMA_ZERO 4
#define VM_KERN_MEMORY_SECURITY 7
#define KERN_SUCCESS 0
#define VM_PROT_READ PROT_READ
#define TRUE 1
#define Z_WAITOK 1
#define MIN(a,b) ((a)<(b)?(a):(b))
#define os_atomic_inc(p,o) ((void)++*(p))
#define os_mul_overflow __builtin_mul_overflow
#define os_add_overflow __builtin_add_overflow
#undef ntohll
#define ntohll __builtin_bswap64
typedef uintptr_t vm_offset_t;
typedef size_t vm_size_t;
typedef int kern_return_t;
typedef void *vnode_t;
union cs_hash_union {CC_SHA256_CTX context;};
struct cs_hash {
    unsigned cs_size,cs_digest_size;
    void (*cs_init)(union cs_hash_union *);
    void (*cs_update)(union cs_hash_union *,const void *,size_t);
    void (*cs_final)(uint8_t *,union cs_hash_union *);
};
typedef struct {uint32_t version,scatterOffset,codeLimit,nCodeSlots;uint64_t codeLimit64;} CS_CodeDirectory;
struct cs_blob {
    const CS_CodeDirectory *csb_cd;const struct cs_hash *csb_hashtype;
    unsigned csb_hash_pageshift,csb_hash_firstlevel_pageshift;
    off_t csb_base_offset;const char *csb_mem_kaddr;size_t csb_mem_size;
    const uint8_t *csb_native4k_hashes;size_t csb_native4k_hashes_size,csb_native4k_hash_count;
};
static uint8_t media[32768],expected_hash[64];
static unsigned cs_native4k_full_blocks,cs_native4k_ready_blobs,cs_validate_page_bad_hash;
static int kernel_map,regions,alloc_error,protect_error,read_error,residual_error,protect_calls;
static void hash_init(union cs_hash_union *c) {CC_SHA256_Init(&c->context);}
static void hash_update(union cs_hash_union *c,const void *p,size_t n) {CC_SHA256_Update(&c->context,p,(CC_LONG)n);}
static void hash_final(uint8_t *out,union cs_hash_union *c) {CC_SHA256_Final(out,&c->context);}
static const struct cs_hash algorithm={32,32,hash_init,hash_update,hash_final};
static int round_page_overflow(size_t n,size_t *out) {if(__builtin_add_overflow(n,(size_t)4095,out)) return 1;*out&=~(size_t)4095;return 0;}
static int kmem_alloc(int map,vm_offset_t *at,size_t bytes,unsigned flags,int tag) {
    if(map || flags!=7 || tag!=7) abort();if(alloc_error) return alloc_error;
    void *p=mmap(NULL,bytes,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
    if(p==MAP_FAILED) abort();*at=(uintptr_t)p;regions++;return 0;
}
static void kmem_free(int map,uintptr_t at,size_t bytes) {if(map || munmap((void*)at,bytes)) abort();regions--;}
static void *kalloc_data(size_t bytes,int flags) {if(flags!=1) abort();return malloc(bytes);}
static void kfree_data(void *p,size_t bytes) {if(bytes!=16384) abort();free(p);}
static int vm_map_protect(int map,uintptr_t start,uintptr_t end,int prot,int maximum) {
    if(map || prot!=PROT_READ || !maximum) abort();protect_calls++;
    if(protect_error) return protect_error;return mprotect((void*)start,end-start,PROT_READ);
}
static void *vfs_context_current(void) {return (void*)1;}
static void *vfs_context_ucred(void *c) {return c;}
static void *current_proc(void) {return (void*)1;}
static int vn_rdwr(int rw,vnode_t node,caddr_t out,int n,off_t at,int seg,int flags,void *cred,int *resid,void *p) {
    if(rw || !node || at<0 || at+n>32768 || seg!=1 || flags || !cred || !p) abort();
    if(read_error) return read_error;memcpy(out,media+at,(size_t)n);*resid=residual_error;return 0;
}
static const uint8_t *hashes(const CS_CodeDirectory *cd,uint32_t slot,unsigned bytes,const char *lower,const char *upper) {
    (void)cd;if(slot>=2 || bytes!=32 || upper-lower!=64) return NULL;return (const uint8_t*)lower+slot*32;
}
'''

HASH_TEST = r'''
#define REQUIRE(x) do {if(!(x)) {fprintf(stderr,"assert %d: %s\n",__LINE__,#x);return 1;}} while(0)
int main(void) {
    unsigned cases=0;for(unsigned i=0;i<sizeof(media);i++) media[i]=(uint8_t)(i%251);
    for(unsigned i=0;i<2;i++) CC_SHA256(media+i*16384,16384,expected_hash+i*32);
    CS_CodeDirectory cd={.version=htonl(0x20100),.codeLimit=htonl(32768),.nCodeSlots=htonl(2)};
    struct cs_blob blob={.csb_cd=&cd,.csb_hashtype=&algorithm,.csb_hash_pageshift=14,
        .csb_mem_kaddr=(const char*)expected_hash,.csb_mem_size=sizeof(expected_hash)};
    REQUIRE(cs_native4k_prepare((void*)1,&blob)==0 && regions==1 && protect_calls==1);
    REQUIRE(blob.csb_native4k_hash_count==8 && cs_native4k_full_blocks==2 && cs_native4k_ready_blobs==1);
    for(unsigned i=0;i<8;i++) {uint8_t digest[32];CC_SHA256(media+i*4096,4096,digest);REQUIRE(!memcmp(digest,blob.csb_native4k_hashes+i*32,32));cases++;}
    kmem_free(0,(uintptr_t)blob.csb_native4k_hashes,blob.csb_native4k_hashes_size);
    blob.csb_native4k_hashes=NULL;blob.csb_native4k_hashes_size=blob.csb_native4k_hash_count=0;
    for(unsigned i=0;i<8;i++) {
        media[i*4096]^=1;REQUIRE(cs_native4k_prepare((void*)1,&blob)==EBADEXEC);
        REQUIRE(!blob.csb_native4k_hashes && !regions);media[i*4096]^=1;cases++;
    }
    read_error=5;REQUIRE(cs_native4k_prepare((void*)1,&blob)==5 && !regions);read_error=0;cases++;
    residual_error=1;REQUIRE(cs_native4k_prepare((void*)1,&blob)==EIO && !regions);residual_error=0;cases++;
    alloc_error=5;REQUIRE(cs_native4k_prepare((void*)1,&blob)==ENOMEM && !regions);alloc_error=0;cases++;
    protect_error=5;REQUIRE(cs_native4k_prepare((void*)1,&blob)==EPERM && !blob.csb_native4k_hashes && !regions);protect_error=0;cases++;
    blob.csb_hash_pageshift=13;REQUIRE(cs_native4k_prepare((void*)1,&blob)==ENOTSUP);blob.csb_hash_pageshift=14;cases++;
    cd.scatterOffset=htonl(1);REQUIRE(cs_native4k_prepare((void*)1,&blob)==ENOTSUP);cd.scatterOffset=0;cases++;
    cd.nCodeSlots=htonl(3);REQUIRE(cs_native4k_prepare((void*)1,&blob)==ENOTSUP);cd.nCodeSlots=htonl(2);cases++;
    blob.csb_base_offset=1;REQUIRE(cs_native4k_prepare((void*)1,&blob)==ENOTSUP);blob.csb_base_offset=0;cases++;
    REQUIRE(cs_validate_page_bad_hash==8);
    printf("PASS: %u actual signature-preparation cases; complete original blocks, derived 4K hashes, RO publication and failures\n",cases);
    return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--model-only', action='store_true')
    a = p.parse_args()
    out = a.output.resolve()
    tree = out/'work/tree'
    source = tree/'.cache/xnu-work/bsd/kern/ubc_subr.c'
    ubc = source.read_text()
    body = 'static int\n' + function(ubc, 'cs_native4k_prepare(vnode_t vp, struct cs_blob *blob)\n')
    gate = re.search(r'\tif \(\(cd->pageSize != PAGE_SHIFT_4K\).*?\n\t\}', ubc, re.S)[0]
    results = []
    with tempfile.TemporaryDirectory(prefix='ram-cache-admission-') as tmp:
        tmp = Path(tmp)
        program, binary = tmp/'test.c', tmp/'test'
        def run(text, options=(), arguments=()):
            program.write_text(text)
            compiled = subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                '-Wno-deprecated-declarations', '-fsanitize=address,undefined', *options,
                str(program), '-o', str(binary)], capture_output=True, text=True)
            if compiled.returncode:
                raise RuntimeError(compiled.stdout+compiled.stderr)
            return subprocess.run([str(binary), *arguments], capture_output=True, text=True)
        positive = run(HASH_API+body+HASH_TEST)
        assert positive.returncode == 0, positive.stdout+positive.stderr
        results.append(positive.stdout.strip())
        mutations = [body.replace('if (bcmp(expected, digest, md->cs_size))', 'if (0 * bcmp(expected, digest, md->cs_size))'),
                     body.replace('kr = vm_map_protect(kernel_map, storage, storage + allocation, VM_PROT_READ, TRUE);',
                                  'kr = 0 * vm_map_protect(kernel_map, storage, storage + allocation, VM_PROT_READ, TRUE);')]
        for changed in mutations:
            assert changed != body
            assert run(HASH_API+changed+HASH_TEST).returncode != 0
        program_text = '#include <stdio.h>\n#include <stdint.h>\n#include <stdlib.h>\n#include <errno.h>\n#define PAGE_SHIFT_4K 12\n#define PAGE_SHIFT TEST_SHIFT\nstruct cd {uint8_t pageSize;};\nint main(int argc,char **argv) {if(argc!=2)return 2;struct cd value={(uint8_t)atoi(argv[1])};const struct cd *cd=&value;\n'+gate+'\nreturn 0;}\n'
        granules = []
        for cache in (0, 1):
            for page in (12, 14):
                for signing in (12, 14):
                    result = run(program_text, [f'-DXNU_NATIVE_CS_HASH_CACHE={cache}', f'-DTEST_SHIFT={page}'], [str(signing)])
                    expected = signing == 12 or page == 14 or cache == 1
                    assert (result.returncode == 0) == expected
                    granules.append(dict(cache=cache, page_shift=page, signature_shift=signing, accepted=expected))
        # Compile the unchanged actual non-CSR carrier branch, with VFS lookup
        # standing for normal path traversal (including following a symlink).
        vm_source = tree/'.cache/xnu-work/bsd/vm/vm_unix.c'
        vm = vm_source.read_text()
        start = vm.index('assert(rdir_vp != NULL);')
        branch = vm[start:vm.index('#endif /* CONFIG_CSR */', start)]
        carrier_api = r'''
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#define XNU_OWNER_CACHE_CARRIER 0
#define XNU_TARGET_OS_OSX 0
#define SHARED_REGION_TRACE_ERROR(...) ((void)0)
struct node {void *v_mount;};typedef struct node *vnode_t;
static struct node root={(void*)1},cache={(void*)2},wrong={(void*)3};
static int lookup_error=ENOENT,wrong_target;
static int vnode_lookup(const char *path,int flags,vnode_t *node,void *context) {
    assert(!flags && context && path[0]=='/');*node=wrong_target?&wrong:&cache;return lookup_error;
}
static void *vfs_context_current(void) {return (void*)1;}
static void vnode_put(vnode_t n) {assert(n);}
static int policy(vnode_t file) {vnode_t rdir_vp=&root;struct {vnode_t vp;} mapping={file};
    const typeof(mapping) *srfmp=&mapping;int error=0;
'''
        carrier_test = r'''
done: return error;}
int main(void) {
    assert(policy(&root)==0);assert(policy(&cache)==EPERM);
    lookup_error=0;assert(policy(&cache)==0);wrong_target=1;assert(policy(&cache)==EPERM);
    lookup_error=EIO;assert(policy(&cache)==EPERM);
    puts("PASS: 5 original carrier-policy cases; missing/wrong mounts rejected, real same mount accepted");return 0;
}
'''
        carrier = run(carrier_api+branch+carrier_test, ['-std=gnu11'])
        assert carrier.returncode == 0, carrier.stdout+carrier.stderr
        results.append(carrier.stdout.strip())
    tmpfs_cases = tmpfs_model(a.base, out)
    report = dict(passed=results, granule_gate_cases=granules, integrity_mutations_rejected=2,
                  original_tmpfs_cases=tmpfs_cases,
                  ubc_source_sha256=sha(source), carrier_source_sha256=sha(vm_source),
                  actual_carrier_branch_sha256=hashlib.sha256(branch.encode()).hexdigest(),
                  hardware_result='pending', original_cache_signature_or_trust_modified=False)
    if not a.model_only:
        objdir = tree/'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT'
        obj = objdir/'ubc_subr.o'
        arguments = json.loads(obj.with_suffix('.o.json').read_text().strip().rstrip(','))['arguments']
        assert arguments.count('XNU_NATIVE_CS_HASH_CACHE=1') + arguments.count('-DXNU_NATIVE_CS_HASH_CACHE=1') == 1
        symbols = subprocess.check_output([llvm_tool('llvm-nm'), str(obj)], text=True)
        assert '_cs_native4k_prepare' in symbols and '_cs_native4k_hash_cache_version' in symbols
        assert (objdir/'config_csr.h').read_text().strip() == '#define NCONFIG_CSR 0'
        vmargs = json.loads((objdir/'vm_unix.o.json').read_text().strip().rstrip(','))['arguments']
        assert 'XNU_OWNER_CACHE_CARRIER=0' in vmargs or '-DXNU_OWNER_CACHE_CARRIER=0' in vmargs
        parent = json.loads((out/'parent.json').read_text())
        assert sha(vm_source) == sha(Path(parent['path'])/'work/tree/.cache/xnu-work/bsd/vm/vm_unix.c')
        plan = json.loads((out/'work/kernel/kernel.json').read_text())
        assert 'com.apple.filesystems.tmpfs' in plan['components']
        assert 'com.apple.filesystems.tmpfs' in plan['original_executable_sha256']
        for name in ('com.apple.filesystems.apfs','com.apple.driver.AppleMobileFileIntegrity'):
            assert plan['original_executable_sha256'][name] == json.loads((Path(parent['path'])/'work/kernel/kernel.json').read_text())['original_executable_sha256'][name]
        report.update(boot_sha256=sha(out/'Mu-fuxi-usb-ram.img'), ubc_object_sha256=sha(obj),
                      original_tmpfs_executable_sha256=plan['original_executable_sha256']['com.apple.filesystems.tmpfs'])
    (out/'cache-admission-tests.json').write_text(json.dumps(report,indent=2)+'\n')
    for result in results: print(result)
    print('PASS: 8 original granule gates, two integrity mutation rejections, compiled config and unchanged carrier policy')


if __name__ == '__main__':
    main()
