/* Observe original launchd main with a hardware breakpoint; never patch user code. */
#include <sys/types.h>
#include <sys/systm.h>
#include <sys/proc.h>
#include <sys/proc_internal.h>
#include <sys/syscall.h>
#include <arm/thread.h>
#include <arm64/proc_reg.h>
#include <kern/thread.h>
#include <kern/task.h>
#include <vm/vm_map.h>
#include <kern/thread_call.h>
#include <kern/clock.h>
#include <mach/mach_time.h>
#include <mach/arm/thread_status.h>
#include "RamRootProbePins.h"
#ifndef RAM_ROOT_PROGRESS_SECONDS
#define RAM_ROOT_PROGRESS_SECONDS 90
#endif
#ifndef RAM_ROOT_NATIVE_HASH_CACHE
#define RAM_ROOT_NATIVE_HASH_CACHE 0
#endif
#ifndef RAM_ROOT_FOLLOW_DENY_ATTACH
#define RAM_ROOT_FOLLOW_DENY_ATTACH 0
#endif
#ifndef RAM_ROOT_PROGRESS_SVC_LIMIT
#define RAM_ROOT_PROGRESS_SVC_LIMIT 131072U
#endif
/* bsd/sys/ptrace.h request number; only this one request is ever allowed. */
#define RAM_ROOT_PT_DENY_ATTACH 31
#if RAM_ROOT_SYSTEM_WATCHDOG
extern volatile uint64_t fuxi_ram_watchdog_state[8];
#endif
#if RAM_ROOT_NATIVE_HASH_CACHE
extern unsigned long cs_native4k_full_blocks, cs_native4k_page_checks, cs_native4k_ready_blobs;
#endif

extern volatile unsigned fuxi_ram_root_stage;
extern volatile unsigned fuxi_ram_system_user_entry;
/* Owned by RamRootProbe.c; set after a verified native root switch. */
extern volatile unsigned fuxi_ram_native_root_entry;
extern volatile unsigned fuxi_ram_cache_stage;
extern volatile unsigned fuxi_ram_cache_carrier;
extern volatile unsigned long long fuxi_ram_cache_dyld_flags;
/* This Mach-private declaration is hidden from the BSD compile-record headers. */
extern kern_return_t machine_thread_set_state(thread_t, thread_flavor_t, thread_state_t, mach_msg_type_number_t);
static const char ram_main_argv0[] = "/sbin/launchd";
static const char ram_main_system_argv0[] = "/mnt1/sbin/launchd";
void fuxi_ram_main_arm(thread_t target);
void fuxi_ram_main_svc(uint64_t, uint32_t, uint32_t, uint64_t);
void fuxi_ram_main_unix(unsigned);
void fuxi_ram_main_break(uint64_t, uint32_t, uint32_t);
uint64_t fuxi_ram_progress_unix(unsigned, uint64_t, uint64_t);
void fuxi_ram_progress_unix_result(uint64_t, unsigned, int, int);
static thread_t observed_thread;
static uint64_t launchd_header, launchd_main, checkpoint_pc;
static volatile unsigned svc_count;
static volatile int32_t recent_calls[8];
static volatile uint64_t last_pc;
static thread_call_t deadline_call;

/* RAM only. The panic renderer also displays this on unrelated kernel faults.
 * Never hold this best-effort lock across copyin, panic or a system call. */
char fuxi_ram_progress_text[1024];
#if RAM_ROOT_PROGRESS
static volatile unsigned trace_lock, dropped_events, unix_count, spawn_total;
static uint64_t trace_sequence;
struct ram_event { unsigned code; int error, result, pid; uint64_t arg, token; thread_t thread; };
struct ram_spawn {
    unsigned code; int error, result, pid, child, pid_error;
    uint64_t token, pid_address; thread_t thread; char path[64];
};
static struct ram_event events[8];
/* Sticky cache evidence survives the later console writes and fatal exit. */
static struct ram_event cache_open, cache_map;
static unsigned cache_open_count, cache_map_count;
/* Last userspace brk fault: pure observation, never alters exception handling. */
static uint64_t fault_pc, fault_far;
static uint32_t fault_esr;
static int fault_pid = -1;
static unsigned fault_count;
static struct ram_spawn spawns[8];
static char last_path[96], last_stat[64], last_write[48];
static int last_pid, last_stat_pid, last_write_pid;
/* Follow mode after launchd main: the restore userland's own children run, so
 * the latest calls may belong to any process. Each line is bounded so the
 * 1024-byte snapshot still fits the portrait framebuffer. */
static void progress_snapshot(void)
{
    size_t used = (size_t)snprintf(fuxi_ram_progress_text, sizeof(fuxi_ram_progress_text),
        "RAM_PROGRESS stage=%u main=%s\n%s\n"
        "cache=%u carrier=%u dyld_flags=%llx\npid=%d svc=%u unix=%u exec=%u drop=%u\nlastpc=%llx x16=%d\n"
        "cache-open=%u err=%d fd=%d map=%u err=%d rv=%d\n",
        fuxi_ram_root_stage, fuxi_ram_root_stage == 10 ? "PASS" : "pending",
        fuxi_ram_native_root_entry ? "root=switched OS-volume apfs readonly rootfs=1" :
            "md0 APFS readonly; original hashes OK",
        fuxi_ram_cache_stage, fuxi_ram_cache_carrier, fuxi_ram_cache_dyld_flags,
        last_pid, svc_count, unix_count, spawn_total, dropped_events, last_pc, recent_calls[(svc_count - 1) & 7],
        cache_open_count, cache_open_count ? cache_open.error : -9999, cache_open_count ? cache_open.result : -9999,
        cache_map_count, cache_map_count ? cache_map.error : -9999, cache_map_count ? cache_map.result : -9999);
#if RAM_ROOT_NATIVE_HASH_CACHE
    if (used < sizeof(fuxi_ram_progress_text))
        used += (size_t)snprintf(fuxi_ram_progress_text + used, sizeof(fuxi_ram_progress_text) - used,
            "sigblobs=%lu blocks=%lu pagechecks=%lu\n", cs_native4k_ready_blobs,
            cs_native4k_full_blocks, cs_native4k_page_checks);
#endif
    if (used < sizeof(fuxi_ram_progress_text) && fault_count)
        used += (size_t)snprintf(fuxi_ram_progress_text + used, sizeof(fuxi_ram_progress_text) - used,
            "fault=%u pid=%d esr=%x pc=%llx far=%llx\n", fault_count, fault_pid, fault_esr, fault_pc, fault_far);
#if RAM_ROOT_SYSTEM_WATCHDOG
    if (used < sizeof(fuxi_ram_progress_text))
        used += (size_t)snprintf(fuxi_ram_progress_text + used, sizeof(fuxi_ram_progress_text) - used,
            "wdt=%llu clients=%llu polls=%llu beats=%llu enabled=%llu\n",
            fuxi_ram_watchdog_state[0], fuxi_ram_watchdog_state[1], fuxi_ram_watchdog_state[2],
            fuxi_ram_watchdog_state[3], fuxi_ram_watchdog_state[4]);
#endif
    unsigned first = unix_count > 3 ? unix_count - 3 : 0;
    for (unsigned i = first; i < unix_count && used < sizeof(fuxi_ram_progress_text); i++) {
        struct ram_event *event = &events[i & 7];
        used += (size_t)snprintf(fuxi_ram_progress_text + used, sizeof(fuxi_ram_progress_text) - used,
            "u%u pid=%d code=%u err=%d rval=%d a0=%llx\n", i + 1, event->pid, event->code, event->error, event->result, event->arg);
    }
    if (used < sizeof(fuxi_ram_progress_text) && last_path[0])
        used += (size_t)snprintf(fuxi_ram_progress_text + used, sizeof(fuxi_ram_progress_text) - used, "path=%.56s\n", last_path);
    if (used < sizeof(fuxi_ram_progress_text) && last_stat[0])
        used += (size_t)snprintf(fuxi_ram_progress_text + used, sizeof(fuxi_ram_progress_text) - used,
            "stat=%.44s pid=%d\n", last_stat, last_stat_pid);
    if (used < sizeof(fuxi_ram_progress_text) && last_write[0])
        used += (size_t)snprintf(fuxi_ram_progress_text + used, sizeof(fuxi_ram_progress_text) - used,
            "write=%.36s pid=%d\n", last_write, last_write_pid);
    if (spawn_total && used < sizeof(fuxi_ram_progress_text)) {
        used += (size_t)snprintf(fuxi_ram_progress_text + used, sizeof(fuxi_ram_progress_text) - used, "exec/spawn:\n");
        unsigned n = spawn_total < 2 ? spawn_total : 2;
        for (unsigned i = 0; i < n && used < sizeof(fuxi_ram_progress_text); i++) {
            struct ram_spawn *spawn = &spawns[(spawn_total - 1 - i) & 7];
            used += (size_t)snprintf(fuxi_ram_progress_text + used, sizeof(fuxi_ram_progress_text) - used,
                " %d c=%u e=%d rv=%d child=%d pe=%d %.32s\n", spawn->pid, spawn->code,
                spawn->error, spawn->result, spawn->child, spawn->pid_error, spawn->path);
        }
    }

}
static int progress_lock(void)
{
    if (__sync_lock_test_and_set(&trace_lock, 1)) {
        (void)__sync_fetch_and_add(&dropped_events, 1);
        return 0;
    }
    return 1;
}
/* The snapshot is drawn by a fixed ASCII font, so keep log payloads printable. */
static void progress_printable(char *s)
{
    for (; *s; s++) if (*s < 32 || *s > 126) *s = '.';
}
static void progress_unlock(void)
{
    progress_snapshot();
    __sync_lock_release(&trace_lock);
}
#endif

static void deadline_expired(thread_call_param_t one, thread_call_param_t two)
{
    (void)one; (void)two;
#if RAM_ROOT_PROGRESS
    panic("RAM_PROGRESS bounded run ended (deadline or SVC limit)\nstage=%u exec=%u\npath=%.56s",
        fuxi_ram_root_stage, spawn_total, last_path);
#else
    panic("RAM_MAIN timeout before checkpoint\nstage=%u svc=%u lastpc=%llx\nrecent=%d,%d,%d,%d,%d,%d,%d,%d\nprocess creation blocked",
        fuxi_ram_root_stage, svc_count, last_pc, recent_calls[0], recent_calls[1], recent_calls[2], recent_calls[3],
        recent_calls[4], recent_calls[5], recent_calls[6], recent_calls[7]);
#endif
}

static void require_observed_thread(void)
{
    int pid = proc_getpid(current_proc());
    if (fuxi_ram_root_stage == 8 && pid == 1 &&
        (RAM_ROOT_PROGRESS || current_thread() == observed_thread))
        return;
#if RAM_ROOT_PROGRESS
    /* Follow mode: after main, launchd's own children are allowed to run so
     * their syscalls and spawn results can be observed. */
    if (fuxi_ram_root_stage == 10)
        return;
#endif
    panic("RAM_MAIN refused context: stage=%u pid=%d", fuxi_ram_root_stage, pid);
}

void fuxi_ram_main_arm(thread_t target)
{
    uint64_t startup[5] = {0};
    const char *expected_argv0 = fuxi_ram_system_user_entry ? ram_main_system_argv0 : ram_main_argv0;
    unsigned argv0_bytes = fuxi_ram_system_user_entry ? (unsigned)sizeof(ram_main_system_argv0)
                                                      : (unsigned)sizeof(ram_main_argv0);
    char argv0[sizeof(ram_main_system_argv0)] = {0}, argv1[sizeof("-s")];
    uint32_t header[8], entry[4];
    arm_debug_state64_t debug = {0};
    uint64_t deadline;
    observed_thread = target;
    if (fuxi_ram_root_stage != 8 || !target ||
        proc_getpid((proc_t)get_bsdtask_info(get_threadtask(target))) != 1)
        panic("RAM_MAIN invalid replacement thread");
    arm_saved_state_t *state = get_user_regs(observed_thread);
    if (!state || !is_saved_state64(state) || (get_saved_state_cpsr(state) & 0x1f))
        panic("RAM_MAIN invalid initial user state");
    uint64_t sp = get_saved_state_sp(state);
    /* The exec caller still owns the old task/map. Read the replacement map
     * explicitly, as exec_copyout_strings does, then restore the caller's map. */
    vm_map_t old_map = vm_map_switch(get_task_map(get_threadtask(target)));
    int error = copyin((user_addr_t)sp, startup, sizeof(startup));
    if (error || !sp || (sp & 7) || sp >> 63 || !startup[0] ||
        startup[0] >> 63 || (startup[0] & 4095) ||
        (startup[1] != 1 && startup[1] != 2) || startup[2 + startup[1]]) {
        vm_map_switch(old_map);
        panic("RAM_MAIN invalid original dyld startup stack\nsp=%llx errno=%d header=%llx argc=%llu", sp, error, startup[0], startup[1]);
    }
    error = copyin(startup[2], argv0, argv0_bytes);
    if (error || memcmp(argv0, expected_argv0, argv0_bytes) ||
        (startup[1] == 2 && (copyin(startup[3], argv1, sizeof(argv1)) || memcmp(argv1, "-s", sizeof(argv1))))) {
        vm_map_switch(old_map);
        panic("RAM_MAIN unexpected launchd argv\nargc=%llu errno=%d\nexpected=%s actual=%.17s", startup[1], error, expected_argv0, argv0);
    }
    launchd_header = startup[0];
    launchd_main = launchd_header + RAM_ROOT_MAIN_OFFSET;
    checkpoint_pc = launchd_main + 4;
    if (copyin(launchd_header, header, sizeof(header)) ||
        header[0] != 0xfeedfacfU || header[1] != 0x100000cU || header[2] != 0 || header[3] != 2 ||
        copyin(launchd_main, entry, sizeof(entry)) || memcmp(entry, ram_root_main_bytes, sizeof(entry))) {
        vm_map_switch(old_map);
        panic("RAM_MAIN original executable header/entry mismatch");
    }
    vm_map_switch(old_map);
    debug.bvr[0] = checkpoint_pc;
    debug.bcr[0] = ARM_DBG_CR_BYTE_ADDRESS_SELECT_MASK | ARM_DBG_CR_MODE_CONTROL_USER | ARM_DBG_CR_ENABLE_ENABLE;
    kern_return_t result = machine_thread_set_state(observed_thread, ARM_DEBUG_STATE64,
        (thread_state_t)&debug, ARM_DEBUG_STATE64_COUNT);
    if (result != KERN_SUCCESS) panic("RAM_MAIN cannot arm EL0 hardware breakpoint: %d", result);
    deadline_call = thread_call_allocate(deadline_expired, NULL);
    if (!deadline_call) panic("RAM_MAIN cannot allocate diagnostic deadline");
    clock_interval_to_deadline(RAM_ROOT_PROGRESS ? RAM_ROOT_PROGRESS_SECONDS : 30, NSEC_PER_SEC, &deadline);
    thread_call_enter_delayed(deadline_call, deadline);
#if RAM_ROOT_PROGRESS
    if (progress_lock()) progress_unlock();
#endif
}

void fuxi_ram_main_svc(uint64_t pc, uint32_t cpsr, uint32_t esr, uint64_t syscall)
{
    require_observed_thread();
    if ((cpsr & 0x1f) || (esr >> 26) != 0x15 || pc < 4 || pc >> 63)
        panic("RAM_MAIN invalid SVC context: pc=%llx cpsr=%x esr=%x", pc, cpsr, esr);
    last_pc = pc;
    unsigned count = __sync_add_and_fetch(&svc_count, 1);
    recent_calls[(count - 1) & 7] = (int32_t)syscall;
#if RAM_ROOT_PROGRESS
    if (progress_lock()) progress_unlock();
#endif
    if (count > (RAM_ROOT_PROGRESS ? RAM_ROOT_PROGRESS_SVC_LIMIT : 2048U)) deadline_expired(NULL, NULL);
    /* Keep the debug register armed. A user request cannot silently remove the checkpoint. */
    if (fuxi_ram_root_stage == 8 && current_thread() == observed_thread) {
        arm_debug_state64_t *debug = find_debug_state64(observed_thread);
        if (!debug || debug->bvr[0] != checkpoint_pc || !(debug->bcr[0] & ARM_DBG_CR_ENABLE_ENABLE))
            panic("RAM_MAIN hardware checkpoint state lost; stopping before dispatch");
    }
}

uint64_t fuxi_ram_progress_unix(unsigned code, uint64_t arg0, uint64_t arg1)
{
    require_observed_thread();
    uint64_t token = 0;
#if RAM_ROOT_PROGRESS
    char path[sizeof(last_path)] = {0};
    size_t copied = 0;
    uint64_t path_address = (code == SYS_posix_spawn || code == SYS_mount || code == SYS___mac_mount ||
                            code == SYS_openat || code == SYS_openat_nocancel) ? arg1 :
        (code == SYS_execve || code == SYS___mac_execve || code == SYS_open || code == SYS_open_nocancel ||
         code == SYS_unmount) ? arg0 : 0;
    uint64_t stat_address = (code == SYS_stat64 || code == SYS_lstat64 || code == SYS_stat || code == SYS_lstat) ? arg0 : 0;
    char stat[sizeof(last_stat)] = {0};
    char text[sizeof(last_write)] = {0};
    int writing = code == SYS_write || code == SYS_write_nocancel;
    if (path_address && copyinstr(path_address, path, sizeof(path), &copied)) {
        path[sizeof(path) - 1] = 0;
        if (!path[0]) (void)snprintf(path, sizeof(path), "<unreadable:%llx>", path_address);
    }
    if (stat_address && copyinstr(stat_address, stat, sizeof(stat), &copied)) {
        stat[sizeof(stat) - 1] = 0;
        if (!stat[0]) (void)snprintf(stat, sizeof(stat), "<unreadable:%llx>", stat_address);
    }
    if (writing && copyinstr(arg1, text, sizeof(text), &copied)) text[sizeof(text) - 1] = 0;
    progress_printable(text);
    int pid = proc_getpid(current_proc());
    if (progress_lock()) {
        token = ++trace_sequence;
        if (!token) panic("RAM_PROGRESS trace token exhausted");
        struct ram_event *event = &events[unix_count++ & 7];
        *event = (struct ram_event){ .code = code, .error = -9999, .result = -9999,
            .arg = arg0, .token = token, .thread = current_thread(), .pid = pid };
        if ((code == SYS_open || code == SYS_open_nocancel || code == SYS_openat || code == SYS_openat_nocancel) &&
            (strnstr(path, "dyld_shared_cache", sizeof(path)) || strnstr(path, "com.apple.dyld", sizeof(path)))) {
            cache_open = *event;
            cache_open_count++;
        }
        if (code == SYS_shared_region_map_and_slide_2_np) {
            cache_map = *event;
            cache_map_count++;
        }
        if (path_address) {
            memcpy(last_path, path, sizeof(last_path));
            last_pid = pid;
        }
        if (stat_address) {
            memcpy(last_stat, stat, sizeof(last_stat));
            last_stat_pid = pid;
        }
        if (writing && text[0]) {
            memcpy(last_write, text, sizeof(last_write));
            last_write_pid = pid;
        }
        if (code == SYS_posix_spawn || code == SYS_execve || code == SYS___mac_execve) {
            struct ram_spawn *spawn = &spawns[spawn_total++ & 7];
            *spawn = (struct ram_spawn){ .code = code, .error = -9999, .result = -9999,
                .pid = pid, .child = -1, .pid_error = -1, .token = token,
                .pid_address = code == SYS_posix_spawn ? arg0 : 0, .thread = current_thread() };
            memcpy(spawn->path, path, sizeof(spawn->path));
        }
        progress_unlock();
    }
    /* tmpfs is memory-backed, so the restore userland may mount it. Physical
     * filesystems stay blocked and none of their drivers is linked. */
    if (code == SYS_mount) {
        char type[8] = {0};
        if (!copyinstr(arg0, type, sizeof(type), &copied) && !strcmp(type, "tmpfs")) return token;
    }
#if RAM_ROOT_FOLLOW_DENY_ATTACH
    /* PT_DENY_ATTACH is the followed daemons' own anti-debug self-protect;
     * allow exactly that request. Tracing or attaching to any process stays
     * blocked with the rest of ptrace. */
    if (code == SYS_ptrace && arg0 == RAM_ROOT_PT_DENY_ATTACH) return token;
#endif
#else
    (void)arg0; (void)arg1;
#endif
    /* code has already resolved the indirect syscall(0, ...) form in XNU. */
    fuxi_ram_main_unix(code);
    return token;
}

void fuxi_ram_progress_unix_result(uint64_t token, unsigned code, int error, int result)
{
    require_observed_thread();
#if RAM_ROOT_PROGRESS
    if (!token) return; /* Entry was dropped: never complete somebody else's call. */
    uint64_t pid_address = 0;
    int child = -1, pid_error = -1;
    if (code == SYS_posix_spawn && !error && !result) {
        if (!progress_lock()) return;
        for (unsigned n = 0; n < 8 && n < spawn_total; n++) {
            struct ram_spawn *spawn = &spawns[(spawn_total - 1 - n) & 7];
            if (spawn->token == token && spawn->thread == current_thread() &&
                spawn->code == code && spawn->error == -9999) {
                pid_address = spawn->pid_address;
                break;
            }
        }
        /* copyin can fault: never hold the best-effort trace lock across it. */
        __sync_lock_release(&trace_lock);
        if (pid_address) pid_error = copyin(pid_address, &child, sizeof(child));
        if (pid_error || child <= 0) child = -1;
    }
    if (progress_lock()) {
        /* The token also rejects a dropped/reused entry and ring eviction. */
        struct ram_event *sticky[] = { &cache_open, &cache_map };
        for (unsigned i = 0; i < 2; i++) {
            if (sticky[i]->token == token && sticky[i]->thread == current_thread() &&
                sticky[i]->code == code && sticky[i]->error == -9999) {
                sticky[i]->error = error;
                sticky[i]->result = result;
            }
        }
        for (unsigned n = 0; n < 8 && n < unix_count; n++) {
            struct ram_event *event = &events[(unix_count - 1 - n) & 7];
            if (event->token == token && event->thread == current_thread() && event->code == code && event->error == -9999) {
                event->error = error;
                event->result = result;
                break;
            }
        }
        for (unsigned n = 0; n < 8 && n < spawn_total; n++) {
            struct ram_spawn *spawn = &spawns[(spawn_total - 1 - n) & 7];
            if (spawn->token == token && spawn->thread == current_thread() && spawn->code == code && spawn->error == -9999) {
                spawn->error = error;
                spawn->result = result;
                spawn->child = child;
                spawn->pid_error = pid_error;
                break;
            }
        }
        progress_unlock();
    }
#else
    (void)token; (void)code; (void)error; (void)result;
#endif
}

/* Called from sleh.c before handle_breakpoint for an EL0 brk fault. Pure
 * observation: record and return so the original exception semantics, and
 * whatever the process does with the delivered signal, stay untouched. */
void fuxi_ram_progress_fault(uint64_t pc, uint32_t cpsr, uint32_t esr, uint64_t far)
{
#if RAM_ROOT_PROGRESS
    (void)cpsr;
    if (!progress_lock()) return;
    fault_pc = pc;
    fault_esr = esr;
    fault_far = far;
    fault_pid = proc_getpid(current_proc());
    fault_count++;
    progress_unlock();
#else
    (void)pc; (void)cpsr; (void)esr; (void)far;
#endif
}

void fuxi_ram_main_unix(unsigned code)
{
    require_observed_thread();
    switch (code) {
#if !RAM_ROOT_PROGRESS
    case SYS_fork: case SYS_vfork: case SYS_execve: case SYS_posix_spawn: case SYS___mac_execve:
#endif
    case SYS_mount: case SYS_unmount: case SYS___mac_mount: case SYS_fmount:
    case SYS_reboot: case SYS_ptrace:
#if RAM_ROOT_PROGRESS
        panic("RAM_FOLLOW blocked syscall=%u before dispatch\nsvc=%u lastpc=%llx stage=%u\npath=%.64s",
            code, svc_count, last_pc, fuxi_ram_root_stage, last_path);
#else
        panic("RAM_MAIN blocked syscall=%u before dispatch\nsvc=%u lastpc=%llx stage=%u\nrecovery programs not started", code, svc_count, last_pc, fuxi_ram_root_stage);
#endif
    default:
        return;
    }
}

void fuxi_ram_main_break(uint64_t pc, uint32_t cpsr, uint32_t esr)
{
    uint32_t entry[4];
    require_observed_thread();
    if (fuxi_ram_root_stage != 8 || current_thread() != observed_thread ||
        (cpsr & 0x1f) || (esr >> 26) != 0x30 || pc != checkpoint_pc ||
        copyin(launchd_main, entry, sizeof(entry)) || memcmp(entry, ram_root_main_bytes, sizeof(entry)))
        panic("RAM_MAIN unexpected hardware breakpoint: pc=%llx expected=%llx cpsr=%x esr=%x", pc, checkpoint_pc, cpsr, esr);
    fuxi_ram_root_stage = 10;
#if RAM_ROOT_PROGRESS
    arm_debug_state64_t debug = {0};
    kern_return_t result = machine_thread_set_state(observed_thread, ARM_DEBUG_STATE64,
        (thread_state_t)&debug, ARM_DEBUG_STATE64_COUNT);
    if (result != KERN_SUCCESS) panic("RAM_PROGRESS cannot clear hardware breakpoint: %d", result);
    if (progress_lock()) progress_unlock();
    return;
#else
    if (fuxi_ram_system_user_entry)
        panic("RAM_SYSTEM_MAIN PASS\nroot=md0 apfs readonly=1\nexec=/mnt1/sbin/launchd\nlaunchd LC_MAIN+4 reached; bytes intact\nheader=%llx main=%llx\npc=%llx cpsr=%x esr=%x\nsvc=%u; process creation blocked\nrecovery programs not started",
            launchd_header, launchd_main, pc, cpsr, esr, svc_count);
    panic("RAM_LAUNCHD_MAIN PASS\nroot=md0 apfs readonly=1\noriginal launchd LC_MAIN+4 reached\nfirst instruction and entry bytes intact\nheader=%llx main=%llx\npc=%llx cpsr=%x esr=%x\nsvc=%u; process creation blocked\nrecovery programs not started",
        launchd_header, launchd_main, pc, cpsr, esr, svc_count);
#endif
}
