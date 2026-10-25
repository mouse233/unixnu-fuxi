#!/usr/bin/env python3
"""Exercise production RAM/root/main C, real hashes, and hardware-debug API boundaries."""
import ast
import hashlib
from pathlib import Path
import subprocess
import tempfile

import argparse
import json
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--ramdisk', type=Path, required=True)
parser.add_argument('--launchd', type=Path, required=True)
parser.add_argument('--syscall-header', type=Path, required=True, help='Generated sys/syscall.h of the pinned XNU source')
parser.add_argument('--progress', action='store_true', help='Exercise combined run, including continuation and RAM trace')
parser.add_argument('--restore-trust', action='store_true', help='Require successful original restore module load before md0')
parser.add_argument('--system-entry', action='store_true', help='Exercise the /mnt1 init source and argv, with md0 still the root')
parser.add_argument('--native-root', action='store_true', help='Exercise /sbin/launchd after the verified native root switch')
parser.add_argument('--output', type=Path, help='Write this run into its candidate directory')
args = parser.parse_args()
if args.native_root and not args.system_entry: parser.error('--native-root requires --system-entry')
if args.system_entry and args.progress and not args.native_root: parser.error('secondary system entry excludes progress')
if args.restore_trust and not args.progress: parser.error('--restore-trust requires --progress')
ROOT = Path(__file__).resolve().parents[1]
OUT = args.output or ROOT / ('out/ram-progress-guard-tests' if args.progress else 'out/ram-main-guard-tests')
OUT.mkdir(parents=True, exist_ok=True)
old_test = ast.parse((ROOT / 'scripts/check-ram-root-guards.py').read_text())
api = next(ast.literal_eval(node.value) for node in old_test.body if isinstance(node, ast.Assign)
           and any(isinstance(target, ast.Name) and target.id == 'api' for target in node.targets))
api = api.replace('static int pid=1,', 'static int target_pid=1;\nstatic int pid=1,').replace('if(!proc) abort();return pid;', 'if(!proc) abort();return proc==&target_pid?target_pid:pid;')
api += r'''
#define MNT_ROOTFS 0x4000
static vnode_t rootvnode = &root_node;
typedef void *task_t;
typedef void *vm_map_t;
typedef void *proc_t;
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
static arm_saved_state_t user_state, retiring_state;
static vm_map_t active_map=(void *)1;
static int target_map_reads=0, debug_armed_before_resume=0;
static int expected_debug_thread=3;
static thread_t get_threadtask(thread_t t) {return t;}
static vm_map_t get_task_map(task_t task) {return task;}
static void *get_bsdtask_info(task_t task) {if(task!=(void *)3) abort();return &target_pid;}
static vm_map_t vm_map_switch(vm_map_t map) {vm_map_t old=active_map;active_map=map;return old;}
static arm_debug_state64_t installed_debug;
static int set_state_error=0, allocate_error=0, debug_missing=0;
static void (*timeout_callback)(void *,void *);
static thread_t current_thread(void) {return host_thread;}
static arm_saved_state_t *get_user_regs(thread_t t) {return t==(void *)3?&user_state:&retiring_state;}
static int is_saved_state64(arm_saved_state_t *s) {return s!=NULL;}
static uint64_t get_saved_state_sp(arm_saved_state_t *s) {return s->sp;}
static uint32_t get_saved_state_cpsr(arm_saved_state_t *s) {return s->cpsr;}
kern_return_t machine_thread_set_state(thread_t t,thread_flavor_t flavor,thread_state_t state,mach_msg_type_number_t count) {
    if(t!=(void *)(uintptr_t)expected_debug_thread || flavor!=ARM_DEBUG_STATE64 || count!=ARM_DEBUG_STATE64_COUNT) abort();
    if(set_state_error) return set_state_error; if(host_thread==(void *)1) debug_armed_before_resume=1; memcpy(&installed_debug,state,sizeof(installed_debug));return 0;
}
static arm_debug_state64_t *find_debug_state64(thread_t t) {if(t!=(void *)3) abort();return debug_missing?NULL:&installed_debug;}
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
deadline = json.loads((OUT / 'pins.json').read_text()).get('progress_deadline_seconds', 90) if (OUT / 'pins.json').exists() else 90
svc_limit = json.loads((OUT / 'pins.json').read_text()).get('progress_svc_limit', 131072) if (OUT / 'pins.json').exists() else 131072
native_hash = json.loads((OUT / 'pins.json').read_text()).get('native_cs_hash_cache', False) if (OUT / 'pins.json').exists() else False
deny_attach = json.loads((OUT / 'pins.json').read_text()).get('follow_ptrace_deny_attach', False) if (OUT / 'pins.json').exists() else False
if args.progress:
    api = api.replace('seconds!=30', 'seconds!=' + str(deadline)).replace('30000000000ULL', str(deadline * 1000000000) + 'ULL')
    api += r'''
static int copyinstr(uint64_t address, char *out, size_t bytes, size_t *copied) {
    const char *in=(const char *)(uintptr_t)address;
    if(address==1) return 14;
    size_t n=strnlen(in,bytes);memcpy(out,in,n);
    if(n==bytes) return 63;out[n]=0;*copied=n+1;return 0;
}
'''
api = api.replace('static int copyin(', 'static int common_copyin(')
api += r'''
static int copyin(user_addr_t address,void *buffer,size_t bytes) {
    if(active_map!=(void *)3) return 14;
    target_map_reads++;return common_copyin(address,buffer,bytes);
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
if args.restore_trust:
    api += '\nunsigned fuxi_ram_restore_trust_loaded=267;\n'
    pins += '#define RAM_ROOT_RESTORE_TRUST 1\n'
pins += '#define RAM_ROOT_PROGRESS ' + str(int(args.progress)) + '\n'
pins += '#define RAM_ROOT_PROGRESS_SECONDS %d\n#define RAM_ROOT_NATIVE_HASH_CACHE %d\n' % (deadline, int(native_hash))
pins += '#define RAM_ROOT_PROGRESS_SVC_LIMIT %dU\n' % svc_limit
pins += '#define RAM_ROOT_FOLLOW_DENY_ATTACH %d\n' % int(deny_attach)
if native_hash: api += '\nunsigned long cs_native4k_full_blocks,cs_native4k_page_checks,cs_native4k_ready_blobs;\n'
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
    uint64_t startup[5]={(uintptr_t)image,1,(uintptr_t)"/sbin/launchd",0,0};user_state.sp=(uintptr_t)startup;user_state.cpsr=0;
    REFUSE("refused exec",fuxi_ram_user_exec_result(0,"/usr/local/bin/restored_external",1));
    REFUSE("exec failed",fuxi_ram_user_exec_result(13,"/sbin/launchd",1));
        REFUSE("without replacement-thread checkpoint",fuxi_ram_user_exec_result(0,"/sbin/launchd",1));
    REFUSE("init source was not verified",fuxi_ram_user_exec_ready((void *)3,1));
    fuxi_ram_user_exec_begin("/sbin/launchd",1);
    target_pid=2;REFUSE("invalid replacement thread",fuxi_ram_user_exec_ready((void *)3,1));target_pid=1;
    fuxi_ram_root_stage=7;
    startup[1]=3;REFUSE("invalid original dyld startup stack",fuxi_ram_user_exec_ready((void *)3,1));startup[1]=1;
    fuxi_ram_root_stage=8;image[0]^=1;REFUSE("header/entry mismatch",fuxi_ram_main_arm((void *)3));image[0]^=1;
    image[85896]^=1;REFUSE("header/entry mismatch",fuxi_ram_main_arm((void *)3));image[85896]^=1;
    startup[2]=(uintptr_t)"/wrong/program";REFUSE("unexpected launchd argv",fuxi_ram_main_arm((void *)3));startup[2]=(uintptr_t)"/sbin/launchd";
    startup[3]=1;REFUSE("invalid original dyld startup stack",fuxi_ram_main_arm((void *)3));startup[3]=0;
    REQUIRE(active_map==(void *)1);cases++;
    startup[1]=2;startup[3]=(uintptr_t)"-x";
    REFUSE("unexpected launchd argv",fuxi_ram_main_arm((void *)3));
    startup[3]=(uintptr_t)"-s";fuxi_ram_main_arm((void *)3);REQUIRE(active_map==(void *)1);cases++;
    startup[1]=1;startup[3]=0;
    copy_error=14;REFUSE("invalid original dyld startup stack",fuxi_ram_main_arm((void *)3));copy_error=0;
    REQUIRE(strstr(message,"errno=14") && active_map==(void *)1);cases++;
    set_state_error=5;REFUSE("cannot arm",fuxi_ram_main_arm((void *)3));set_state_error=0;
    allocate_error=1;REFUSE("cannot allocate diagnostic deadline",fuxi_ram_main_arm((void *)3));allocate_error=0;
    fuxi_ram_root_stage=7;fuxi_ram_user_exec_ready((void *)3,1);
    REQUIRE(host_thread==(void *)1 && retiring_state.sp==0 && active_map==(void *)1);
    REQUIRE(target_map_reads>0 && debug_armed_before_resume);cases++;
    fuxi_ram_user_exec_result(0,"/sbin/launchd",1);
    host_thread=(void *)3;active_map=(void *)3;
    REQUIRE(fuxi_ram_root_stage==8 && installed_debug.bvr[0]==(uintptr_t)image+85896+4 && installed_debug.bcr[0]==0x1e5);
    REQUIRE(!memcmp(image,launchd,561856));
    for(unsigned i=1;i<16;i++) REQUIRE(!installed_debug.bvr[i] && !installed_debug.bcr[i]);
    fuxi_ram_main_svc(0x100f60fb4,0,0x56000080,74);REQUIRE(svc_count==1);
    fuxi_ram_main_unix(SYS_mprotect);fuxi_ram_main_unix(20);
    unsigned blocked[]={SYS_fork,SYS_vfork,SYS_execve,SYS_posix_spawn,SYS___mac_execve,SYS_mount,SYS_unmount,SYS___mac_mount,SYS_fmount,SYS_reboot,SYS_ptrace};
    for(unsigned i=0;i<sizeof(blocked)/sizeof(blocked[0]);i++) REFUSE("blocked syscall",fuxi_ram_main_unix(blocked[i]));
    pid=2;REFUSE("refused context",fuxi_ram_main_svc(0x1000,0,0x56000080,74));pid=1;
    host_thread=(void *)2;REFUSE("refused context",fuxi_ram_main_svc(0x1000,0,0x56000080,74));host_thread=(void *)3;
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

if args.system_entry and not args.native_root:
    tests = tests.replace('REQUIRE(fuxi_ram_root_stage==7);',
                          'REQUIRE(fuxi_ram_root_stage==7);fuxi_ram_system_user_entry=1;')
    tests = tests.replace('"/sbin/launchd"', '"/mnt1/sbin/launchd"')
    tests = tests.replace('    fuxi_ram_user_exec_begin("/mnt1/sbin/launchd",1);',
        '    REFUSE("refused init source",fuxi_ram_user_exec_begin("/sbin/launchd",1));\n'
        '    fuxi_ram_user_exec_begin("/mnt1/sbin/launchd",1);')
    tests = tests.replace('    fuxi_ram_root_stage=8;image[0]^=1;',
        '    fuxi_ram_root_stage=8;startup[2]=(uintptr_t)"/sbin/launchd";\n'
        '    REFUSE("unexpected launchd argv",fuxi_ram_main_arm((void *)3));\n'
        '    startup[2]=(uintptr_t)"/mnt1/sbin/launchd";image[0]^=1;')
    tests = tests.replace('"RAM_LAUNCHD_MAIN PASS"', '"RAM_SYSTEM_MAIN PASS"')
    tests = tests.replace('    REQUIRE(fuxi_ram_root_stage==10 && !memcmp(image,launchd,561856));',
        '    REQUIRE(fuxi_ram_root_stage==10 && !memcmp(image,launchd,561856));\n'
        '    REQUIRE(strstr(message,"root=md0") && strstr(message,"exec=/mnt1/sbin/launchd"));')
if args.native_root:
    tests = tests.replace('    fuxi_ram_user_exec_begin("/sbin/launchd",1);',
        '    fuxi_ram_native_root_entry=1;\n'
        '    REFUSE("native root identity",fuxi_ram_user_exec_begin("/sbin/launchd",1));\n'
        '    root_mount.flags|=MNT_ROOTFS;fuxi_ram_user_exec_begin("/sbin/launchd",1);')
if args.restore_trust:
    tests = tests.replace('    fuxi_ram_root_probe_before_mount(mdevlookup(0));REQUIRE(fuxi_ram_root_stage==3);', '    fuxi_ram_restore_trust_loaded=0;REFUSE("restore trust module was not loaded",fuxi_ram_root_probe_before_mount(mdevlookup(0)));fuxi_ram_restore_trust_loaded=267;\n    fuxi_ram_root_probe_before_mount(mdevlookup(0));REQUIRE(fuxi_ram_root_stage==3);')
if args.progress:
    tests = tests.replace('selector=3;', 'selector=4;').replace('svc_count=2048;', 'svc_count=%d;' % svc_limit).replace('timeout before checkpoint', 'bounded run ended')
    tests = tests.replace('unsigned blocked[]={SYS_fork,SYS_vfork,SYS_execve,SYS_posix_spawn,SYS___mac_execve,SYS_mount,SYS_unmount,SYS___mac_mount,SYS_fmount,SYS_reboot,SYS_ptrace};',
                          'unsigned blocked_protected[]={SYS_mount,SYS_unmount,SYS___mac_mount,SYS_fmount,SYS_reboot,SYS_ptrace};')
    tests = tests.replace('for(unsigned i=0;i<sizeof(blocked)/sizeof(blocked[0]);i++) REFUSE("blocked syscall",fuxi_ram_main_unix(blocked[i]));',
                          'for(unsigned i=0;i<sizeof(blocked_protected)/sizeof(blocked_protected[0]);i++) REFUSE("blocked syscall",fuxi_ram_main_unix(blocked_protected[i]));')
    tests = tests.replace('host_thread=(void *)2;REFUSE("refused context",fuxi_ram_main_svc(0x1000,0,0x56000080,74));host_thread=(void *)3;',
                          'host_thread=(void *)2;fuxi_ram_main_svc(0x1000,0,0x56000080,74);cases++;host_thread=(void *)3;')
    start = tests.index('    REFUSE("RAM_LAUNCHD_MAIN PASS"')
    end = tests.index('    printf("PASS:', start)
    tests = tests[:start] + r'''
    uint64_t token=fuxi_ram_progress_unix(SYS_mprotect,0x1234,0);
    fuxi_ram_progress_unix_result(token,SYS_mprotect,0,7);
    REQUIRE(strstr(fuxi_ram_progress_text,"code=74 err=0 rval=7 a0=1234"));cases++;
    set_state_error=5;REFUSE("cannot clear",fuxi_ram_main_break(checkpoint_pc,0,0xc2000022));
    set_state_error=0;fuxi_ram_root_stage=8;
    fuxi_ram_main_break(checkpoint_pc,0,0xc2000022);
    REQUIRE(fuxi_ram_root_stage==10 && !memcmp(image,launchd,561856));
    REQUIRE(!installed_debug.bvr[0] && !installed_debug.bcr[0]);cases++;
    REQUIRE(strstr(fuxi_ram_progress_text,"main=PASS"));cases++;
    fuxi_ram_main_svc(0x1000,0,0x56000080,20);cases++;
    host_thread=(void *)2;
    fuxi_ram_main_svc(0x2000,0,0x56000080,20);
    fuxi_ram_main_unix(20);
    uint64_t token2=fuxi_ram_progress_unix(SYS_mprotect,0x2222,0);
    host_thread=(void *)3;
    uint64_t token3=fuxi_ram_progress_unix(SYS_mprotect,0x3333,0);
    host_thread=(void *)2;
    fuxi_ram_progress_unix_result(token2,SYS_mprotect,13,2);
    REQUIRE(events[1].arg==0x2222 && events[1].error==13 && events[2].error==-9999);cases++;
    host_thread=(void *)3;
    fuxi_ram_progress_unix_result(token3,SYS_mprotect,0,3);REQUIRE(events[2].result==3);cases++;
    const char *path="/usr/local/bin/restored_external";
    int child_pid=42;
    token=fuxi_ram_progress_unix(SYS_posix_spawn,(uintptr_t)&child_pid,(uintptr_t)path);
    REQUIRE(spawn_total==1 && strstr(fuxi_ram_progress_text,path));cases++;
    fuxi_ram_progress_unix_result(token,SYS_posix_spawn,0,0);
    REQUIRE(spawns[0].error==0 && spawns[0].result==0 && spawns[0].child==42 && spawns[0].pid_error==0);cases++;
    /* Same-PID threads finish in entry order: PID-only matching swapped A/B. */
    host_thread=(void *)2;
    token2=fuxi_ram_progress_unix(SYS_posix_spawn,0,(uintptr_t)"/sbin/A");
    host_thread=(void *)3;
    token3=fuxi_ram_progress_unix(SYS_posix_spawn,(uintptr_t)&child_pid,(uintptr_t)"/sbin/B");
    host_thread=(void *)2;fuxi_ram_progress_unix_result(token2,SYS_posix_spawn,13,13);
    REQUIRE(spawns[1].error==13 && spawns[2].error==-9999);cases++;
    host_thread=(void *)3;fuxi_ram_progress_unix_result(token3,SYS_posix_spawn,0,0);
    REQUIRE(spawns[2].error==0 && spawns[2].child==42 && spawns[1].child==-1);cases++;
    /* A dropped entry must not finish an older in-flight call on this thread. */
    token=fuxi_ram_progress_unix(SYS_posix_spawn,0,(uintptr_t)"/pending");
    trace_lock=1;uint64_t lost=fuxi_ram_progress_unix(SYS_posix_spawn,0,(uintptr_t)"/dropped");trace_lock=0;
    REQUIRE(lost==0);fuxi_ram_progress_unix_result(lost,SYS_posix_spawn,0,0);
    REQUIRE(spawns[3].error==-9999);cases++;
    fuxi_ram_progress_unix_result(token,SYS_posix_spawn,2,2);REQUIRE(spawns[3].error==2);cases++;
    /* Ring wrap including slots 4..7; stale results cannot complete a new entry. */
    for(unsigned i=0;i<12;i++) {
        token=fuxi_ram_progress_unix(SYS_posix_spawn,(uintptr_t)&child_pid,(uintptr_t)path);
        fuxi_ram_progress_unix_result(token,SYS_posix_spawn,0,0);
        REQUIRE(spawns[(spawn_total-1)&7].child==42);cases++;
    }
    token=fuxi_ram_progress_unix(SYS_posix_spawn,0,(uintptr_t)"/evicted");
    for(unsigned i=0;i<8;i++) (void)fuxi_ram_progress_unix(SYS_posix_spawn,0,(uintptr_t)"/new");
    fuxi_ram_progress_unix_result(token,SYS_posix_spawn,0,0);
    REQUIRE(spawns[(spawn_total-1)&7].error==-9999);cases++;
    token=fuxi_ram_progress_unix(SYS_posix_spawn,1,(uintptr_t)path);copy_error=14;
    fuxi_ram_progress_unix_result(token,SYS_posix_spawn,0,0);copy_error=0;
    REQUIRE(spawns[(spawn_total-1)&7].child==-1 && spawns[(spawn_total-1)&7].pid_error==14);cases++;
    token=fuxi_ram_progress_unix(SYS_posix_spawn,0,(uintptr_t)path);
    fuxi_ram_progress_unix_result(token,SYS_posix_spawn,0,0);
    REQUIRE(spawns[(spawn_total-1)&7].child==-1 && spawns[(spawn_total-1)&7].pid_error==-1);cases++;
    if (fuxi_ram_native_root_entry) {REQUIRE(strstr(fuxi_ram_progress_text,"root=switched OS-volume"));cases++;}

    const char *missing="/private/var/db/missing";
    fuxi_ram_progress_unix(SYS_stat64,(uintptr_t)missing,0);
    REQUIRE(strstr(fuxi_ram_progress_text,"stat=/private/var/db/missing"));cases++;
    fuxi_ram_progress_unix(SYS_write_nocancel,1,(uintptr_t)"waiting for service\n");
    REQUIRE(strstr(fuxi_ram_progress_text,"write=waiting for service."));cases++;
    fuxi_ram_progress_unix(SYS_mount,(uintptr_t)"tmpfs",(uintptr_t)"/private/var/tmp");
    REQUIRE(strstr(fuxi_ram_progress_text,"path=/private/var/tmp"));cases++;
    REFUSE("blocked syscall",fuxi_ram_progress_unix(SYS_mount,(uintptr_t)"apfs",(uintptr_t)"/dev/disk0s1"));
    REFUSE("blocked syscall",fuxi_ram_progress_unix(SYS___mac_mount,(uintptr_t)"tmpfs",(uintptr_t)"/private/var/tmp"));
    if (RAM_ROOT_FOLLOW_DENY_ATTACH) {
        token=fuxi_ram_progress_unix(SYS_ptrace,RAM_ROOT_PT_DENY_ATTACH,0);REQUIRE(token);cases++;
        REFUSE("blocked syscall",fuxi_ram_progress_unix(SYS_ptrace,0,0));
        REFUSE("blocked syscall",fuxi_ram_progress_unix(SYS_ptrace,7,0));
    } else {
        REFUSE("blocked syscall",fuxi_ram_progress_unix(SYS_ptrace,RAM_ROOT_PT_DENY_ATTACH,0));
    }
    REQUIRE(!strstr(fuxi_ram_progress_text,"fault="));cases++;
    fuxi_ram_progress_fault(0x1a2b3000,0,0xf2000000+0x1234,0x2a2b0000);
    REQUIRE(strstr(fuxi_ram_progress_text,"fault=1 pid=1 esr=f2001234 pc=1a2b3000 far=2a2b0000"));cases++;
    trace_lock=1;fuxi_ram_progress_fault(0xdead0000,0,0xf2005678,0);trace_lock=0;
    fuxi_ram_progress_fault(0x5a6b7000,0,0xf2005678,0x6a6b0000);
    REQUIRE(strstr(fuxi_ram_progress_text,"fault=2 pid=1 esr=f2005678 pc=5a6b7000 far=6a6b0000"));cases++;
    puts("TRACE_BEGIN");puts(fuxi_ram_progress_text);puts("TRACE_END");
    fuxi_ram_progress_unix(SYS_execve,1,0);
    REQUIRE(strstr(fuxi_ram_progress_text,"unreadable:1"));cases++;
    char longpath[200];memset(longpath,'a',sizeof(longpath));longpath[199]=0;
    fuxi_ram_progress_unix(SYS_execve,(uintptr_t)longpath,0);
    REQUIRE(last_path[95]==0 && strlen(last_path)==95);cases++;
    for(unsigned i=0;i<20;i++) {token=fuxi_ram_progress_unix(20,i,0);fuxi_ram_progress_unix_result(token,20,0,i);}
    REQUIRE(strlen(fuxi_ram_progress_text)<sizeof(fuxi_ram_progress_text));
    REQUIRE(!strstr(fuxi_ram_progress_text,"u1 code="));cases++;
    token=fuxi_ram_progress_unix(SYS_shared_region_map_and_slide_2_np,53,0);
    fuxi_ram_progress_unix_result(token,SYS_shared_region_map_and_slide_2_np,1,0);
    REQUIRE(cache_map_count==1 && cache_map.error==1);cases++;
    token=fuxi_ram_progress_unix(SYS_openat,0,(uintptr_t)"dyld_shared_cache_arm64.01");
    fuxi_ram_progress_unix_result(token,SYS_openat,0,7);
    for(unsigned i=0;i<12;i++) {token=fuxi_ram_progress_unix(20,0,0);fuxi_ram_progress_unix_result(token,20,0,0);}
    REQUIRE(cache_open_count==1 && cache_open.error==0 && cache_open.result==7 && cache_map.error==1);cases++;
    REQUIRE(strstr(fuxi_ram_progress_text,"map=1 err=1 rv=0"));cases++;
    unsigned saved=unix_count;dropped_events=0;trace_lock=1;fuxi_ram_progress_unix(20,0,0);
    REQUIRE(unix_count==saved && dropped_events==1);trace_lock=0;cases++;
    pid=2;fuxi_ram_main_unix(20);fuxi_ram_main_svc(0x2000,0,0x56000080,20);pid=1;cases++;
    for(unsigned i=0;i<sizeof(blocked_protected)/sizeof(blocked_protected[0]);i++) REFUSE("blocked syscall",fuxi_ram_main_unix(blocked_protected[i]));
    REFUSE("unexpected hardware breakpoint",fuxi_ram_main_break(checkpoint_pc,0,0xc2000022));
''' + tests[end:]

with tempfile.TemporaryDirectory(prefix='fuxi-main-guards-') as temporary:
    work = Path(temporary)
    (work / 'test.c').write_text(api + '\n' + pins + '\n' + '\n'.join(bodies) + '\n' + tests)
    subprocess.run(['clang', '-fsanitize=address,undefined', '-Wall', '-Wextra', '-Werror', '-Wno-deprecated-declarations',
                    str(work / 'test.c'), '-o', str(work / 'test')], check=True)
    result = subprocess.run([str(work / 'test'),
        str(args.ramdisk.resolve()),
        str(args.launchd.resolve())], check=False, capture_output=True, text=True)
    (OUT / 'guard-tests.log').write_text(result.stdout + result.stderr)
    if result.returncode:
        raise SystemExit(result.stdout + result.stderr)
    report = dict(system_entry=args.system_entry, native_root=args.native_root, progress=args.progress,
                  source_sha256={str(ROOT / 'package' / name): hashlib.sha256((ROOT / 'package' / name).read_bytes()).hexdigest()
                                 for name in ['RamRootProbe.c', 'RamLaunchdProbe.c']},
                  passed=result.stdout.strip(), hardware_result='pending')
    if (OUT / 'Mu-fuxi-usb-ram.img').is_file():
        report['boot_sha256'] = hashlib.sha256((OUT / 'Mu-fuxi-usb-ram.img').read_bytes()).hexdigest()
    (OUT / 'probe-guard-tests.json').write_text(json.dumps(report, indent=2) + '\n')
    if args.progress:
        trace = result.stdout.split('TRACE_BEGIN\n',1)[1].split('TRACE_END',1)[0].rstrip('\n') + '\n'
        (OUT / 'trace-fixture.txt').write_text(trace)
        print(result.stdout.split('TRACE_END\n',1)[1], end='')
    else:
        print(result.stdout, end='')
