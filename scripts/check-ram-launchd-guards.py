#!/usr/bin/env python3
"""Exercise production RAM/root/main C, real hashes, and hardware-debug API boundaries."""
import ast
from pathlib import Path
import subprocess
import tempfile

import argparse
import json
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--ramdisk', type=Path, required=True)
parser.add_argument('--launchd', type=Path, required=True)
parser.add_argument('--syscall-header', type=Path, required=True, help='Generated sys/syscall.h of the pinned XNU source')
args = parser.parse_args()
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'out/ram-main-guard-tests'
OUT.mkdir(parents=True, exist_ok=True)
old_test = ast.parse((ROOT / 'scripts/check-ram-root-guards.py').read_text())
api = next(ast.literal_eval(node.value) for node in old_test.body if isinstance(node, ast.Assign)
           and any(isinstance(target, ast.Name) and target.id == 'api' for target in node.targets))
api += r'''
typedef void *thread_t;
typedef int kern_return_t;
typedef int thread_flavor_t;
typedef uint32_t *thread_state_t;
typedef unsigned mach_msg_type_number_t;
typedef struct {uint64_t sp;uint32_t cpsr;} arm_saved_state_t;
typedef struct {uint64_t bvr[16], bcr[16], wvr[16], wcr[16], mdscr_el1;} arm_debug_state64_t;
typedef void *thread_call_t;
typedef void *thread_call_param_t;
#define KERN_SUCCESS 0
#define ARM_DEBUG_STATE64 15
#define ARM_DEBUG_STATE64_COUNT ((unsigned)(sizeof(arm_debug_state64_t)/4))
#define ARM_DBG_CR_BYTE_ADDRESS_SELECT_MASK 0x1e0
#define ARM_DBG_CR_MODE_CONTROL_USER 4
#define ARM_DBG_CR_ENABLE_ENABLE 1
#define NSEC_PER_SEC 1000000000
static thread_t host_thread=(void *)1;
static arm_saved_state_t user_state;
static arm_debug_state64_t installed_debug;
static int set_state_error=0, allocate_error=0, debug_missing=0;
static void (*timeout_callback)(void *,void *);
static thread_t current_thread(void) {return host_thread;}
static arm_saved_state_t *get_user_regs(thread_t t) {if(t!=host_thread) abort();return &user_state;}
static int is_saved_state64(arm_saved_state_t *s) {return s!=NULL;}
static uint64_t get_saved_state_sp(arm_saved_state_t *s) {return s->sp;}
static uint32_t get_saved_state_cpsr(arm_saved_state_t *s) {return s->cpsr;}
kern_return_t machine_thread_set_state(thread_t t,thread_flavor_t flavor,thread_state_t state,mach_msg_type_number_t count) {
    if(t!=host_thread || flavor!=ARM_DEBUG_STATE64 || count!=ARM_DEBUG_STATE64_COUNT) abort();
    if(set_state_error) return set_state_error; memcpy(&installed_debug,state,sizeof(installed_debug));return 0;
}
static arm_debug_state64_t *find_debug_state64(thread_t t) {if(t!=host_thread) abort();return debug_missing?NULL:&installed_debug;}
static thread_call_t thread_call_allocate(void (*fn)(void *,void *),void *arg) {
    if(arg) abort();timeout_callback=fn;return allocate_error?NULL:(void *)1;
}
static void clock_interval_to_deadline(unsigned seconds,unsigned scale,uint64_t *deadline) {
    if(seconds!=30 || scale!=NSEC_PER_SEC) abort();*deadline=30000000000ULL;
}
static int thread_call_enter_delayed(thread_call_t call,uint64_t deadline) {
    if(!call || deadline!=30000000000ULL) abort();return 0;
}
'''
# Keep syscall numbers tied to the target kernel header, not the host OS.
header = args.syscall_header
api += '\n' + '\n'.join(line for line in header.read_text().splitlines() if line.startswith('#define\tSYS_'))
pins_data = json.loads((ROOT / 'diagnostics/ram-root/pins.json').read_text())
pins = '#define RAM_ROOT_IMAGE_BYTES UINT64_C(184549376)\n#define RAM_ROOT_FILE_BYTES 561856\n#define RAM_ROOT_ALLOW_USER_ENTRY 1\n#define RAM_ROOT_ALLOW_MAIN_ENTRY 1\n#define RAM_ROOT_MAIN_OFFSET UINT64_C(85896)\n'
for name, key in [('image', 'image_sha256'), ('file', 'file_sha256')]:
    pins += 'static const uint8_t ram_root_' + name + '_sha256[32] = {' + ','.join(hex(x) for x in bytes.fromhex(pins_data[key])) + '};\n'
pins += 'static const uint8_t ram_root_main_bytes[16] = {' + ','.join(hex(x) for x in bytes.fromhex('ff8305d1fc6f12a9f65713a9f44f14a9')) + '};\n'
assert '#define RAM_ROOT_ALLOW_MAIN_ENTRY 1' in pins
bodies = []
for name in ['RamRootProbe.c', 'RamLaunchdProbe.c']:
    bodies.append('\n'.join(line for line in (ROOT / 'package' / name).read_text().splitlines()
                            if not line.startswith('#include')))
tests = r'''
#define REQUIRE(value) do {if(!(value)) {fprintf(stderr,"assertion line %d: %s\n",__LINE__,#value);exit(1);}} while(0)
static int cases=0;
#define REFUSE(text, ...) do {message[0]=0;if(!setjmp(caught)) {__VA_ARGS__;REQUIRE(0);} \
    REQUIRE(strstr(message,text)!=NULL);cases++;} while(0)
static uint8_t *read_file(const char *path,size_t bytes) {
    FILE *f=fopen(path,"rb");REQUIRE(f);uint8_t *b=malloc(bytes);REQUIRE(b);
    REQUIRE(fread(b,1,bytes,f)==bytes && fgetc(f)==EOF);fclose(f);return b;
}
int main(int argc,char **argv) {
    REQUIRE(argc==3);media_size=184549376;media=read_file(argv[1],media_size);launchd=read_file(argv[2],561856);
    selector=2;REFUSE("diagnostic argument missing",fuxi_ram_root_probe_before_mount(mdevlookup(0)));selector=3;
    REFUSE("not expected md0",fuxi_ram_root_probe_before_mount(NODEV));
    media[0]^=1;REFUSE("media SHA256 mismatch",fuxi_ram_root_probe_before_mount(mdevlookup(0)));media[0]^=1;
    fuxi_ram_root_probe_before_mount(mdevlookup(0));REQUIRE(fuxi_ram_root_stage==3);
    root_mount.flags=0;REFUSE("without read-only flag",fuxi_ram_root_probe_after_mount(mdevlookup(0),&root_node));root_mount.flags=1;
    launchd[0]^=1;REFUSE("launchd SHA256 mismatch",fuxi_ram_root_probe_after_mount(mdevlookup(0),&root_node));launchd[0]^=1;
    fuxi_ram_root_probe_after_mount(mdevlookup(0),&root_node);REQUIRE(fuxi_ram_root_stage==7);
    uint8_t *image=aligned_alloc(4096,565248);REQUIRE(image);memcpy(image,launchd,561856);
    uint64_t startup[2]={(uintptr_t)image,1};user_state.sp=(uintptr_t)startup;user_state.cpsr=0;
    REFUSE("refused exec",fuxi_ram_user_exec_result(0,"/usr/local/bin/restored_external",1));
    REFUSE("exec failed",fuxi_ram_user_exec_result(13,"/sbin/launchd",1));
    startup[1]=2;REFUSE("invalid original dyld startup stack",fuxi_ram_user_exec_result(0,"/sbin/launchd",1));startup[1]=1;
    fuxi_ram_root_stage=8;image[0]^=1;REFUSE("header/entry mismatch",fuxi_ram_main_arm());image[0]^=1;
    image[85896]^=1;REFUSE("header/entry mismatch",fuxi_ram_main_arm());image[85896]^=1;
    set_state_error=5;REFUSE("cannot arm",fuxi_ram_main_arm());set_state_error=0;
    allocate_error=1;REFUSE("cannot allocate diagnostic deadline",fuxi_ram_main_arm());allocate_error=0;
    fuxi_ram_root_stage=7;fuxi_ram_user_exec_result(0,"/sbin/launchd",1);
    REQUIRE(fuxi_ram_root_stage==8 && installed_debug.bvr[0]==(uintptr_t)image+85896+4 && installed_debug.bcr[0]==0x1e5);
    REQUIRE(!memcmp(image,launchd,561856));
    for(unsigned i=1;i<16;i++) REQUIRE(!installed_debug.bvr[i] && !installed_debug.bcr[i]);
    fuxi_ram_main_svc(0x100f60fb4,0,0x56000080,74);REQUIRE(svc_count==1);
    fuxi_ram_main_unix(SYS_mprotect);fuxi_ram_main_unix(20);
    unsigned blocked[]={SYS_fork,SYS_vfork,SYS_execve,SYS_posix_spawn,SYS___mac_execve,SYS_mount,SYS_unmount,SYS___mac_mount,SYS_fmount,SYS_reboot,SYS_ptrace};
    for(unsigned i=0;i<sizeof(blocked)/sizeof(blocked[0]);i++) REFUSE("blocked syscall",fuxi_ram_main_unix(blocked[i]));
    pid=2;REFUSE("refused context",fuxi_ram_main_svc(0x1000,0,0x56000080,74));pid=1;
    host_thread=(void *)2;REFUSE("refused context",fuxi_ram_main_svc(0x1000,0,0x56000080,74));host_thread=(void *)1;
    REFUSE("invalid SVC context",fuxi_ram_main_svc(0x1000,5,0x56000080,74));
    debug_missing=1;REFUSE("checkpoint state lost",fuxi_ram_main_svc(0x1000,0,0x56000080,74));debug_missing=0;
    installed_debug.bcr[0]=0;REFUSE("checkpoint state lost",fuxi_ram_main_svc(0x1000,0,0x56000080,74));installed_debug.bcr[0]=0x1e5;
    REFUSE("unexpected hardware breakpoint",fuxi_ram_main_break(checkpoint_pc+4,0,0xc2000022));
    REFUSE("unexpected hardware breakpoint",fuxi_ram_main_break(checkpoint_pc,5,0xc2000022));
    REFUSE("unexpected hardware breakpoint",fuxi_ram_main_break(checkpoint_pc,0,0x56000080));
    image[85896]^=1;REFUSE("unexpected hardware breakpoint",fuxi_ram_main_break(checkpoint_pc,0,0xc2000022));image[85896]^=1;
    svc_count=2048;REFUSE("timeout before checkpoint",fuxi_ram_main_svc(0x1000,0,0x56000080,74));svc_count=10;
    REQUIRE(timeout_callback);REFUSE("timeout before checkpoint",timeout_callback(NULL,NULL));
    REFUSE("RAM_LAUNCHD_MAIN PASS",fuxi_ram_main_break(checkpoint_pc,0,0xc2000022));
    REQUIRE(fuxi_ram_root_stage==10 && !memcmp(image,launchd,561856));
    printf("PASS: actual production C, real APFS/launchd hashes, %d stop/refusal cases; hardware checkpoint and unchanged user bytes\n",cases);
    free(image);free(launchd);free(media);return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='fuxi-main-guards-') as temporary:
    work = Path(temporary)
    (work / 'test.c').write_text(api + '\n' + pins + '\n' + '\n'.join(bodies) + '\n' + tests)
    subprocess.run(['clang', '-Wall', '-Wextra', '-Werror', '-Wno-deprecated-declarations',
                    str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    result = subprocess.run([str(work / 'test'),
        str(args.ramdisk.resolve()),
        str(args.launchd.resolve())], check=True, capture_output=True, text=True)
    (OUT / 'guard-tests.log').write_text(result.stdout + result.stderr)
    print(result.stdout, end='')
