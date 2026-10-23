/* Observe original launchd main with a hardware breakpoint; never patch user code. */
#include <sys/types.h>
#include <sys/systm.h>
#include <sys/proc.h>
#include <sys/proc_internal.h>
#include <sys/syscall.h>
#include <arm/thread.h>
#include <arm64/proc_reg.h>
#include <kern/thread.h>
#include <kern/thread_call.h>
#include <kern/clock.h>
#include <mach/mach_time.h>
#include <mach/arm/thread_status.h>
#include "RamRootProbePins.h"

extern volatile unsigned fuxi_ram_root_stage;
/* This Mach-private declaration is hidden from the BSD compile-record headers. */
extern kern_return_t machine_thread_set_state(thread_t, thread_flavor_t, thread_state_t, mach_msg_type_number_t);
void fuxi_ram_main_arm(void);
void fuxi_ram_main_svc(uint64_t, uint32_t, uint32_t, uint64_t);
void fuxi_ram_main_unix(unsigned);
void fuxi_ram_main_break(uint64_t, uint32_t, uint32_t) __attribute__((noreturn));
static thread_t observed_thread;
static uint64_t launchd_header, launchd_main, checkpoint_pc;
static volatile unsigned svc_count;
static volatile int32_t recent_calls[8];
static volatile uint64_t last_pc;
static thread_call_t deadline_call;

static void deadline_expired(thread_call_param_t one, thread_call_param_t two)
{
    (void)one; (void)two;
    panic("RAM_MAIN timeout before checkpoint\nstage=%u svc=%u lastpc=%llx\nrecent=%d,%d,%d,%d,%d,%d,%d,%d\nprocess creation blocked",
        fuxi_ram_root_stage, svc_count, last_pc, recent_calls[0], recent_calls[1], recent_calls[2], recent_calls[3],
        recent_calls[4], recent_calls[5], recent_calls[6], recent_calls[7]);
}

static void require_observed_thread(void)
{
    if (fuxi_ram_root_stage != 8 || proc_getpid(current_proc()) != 1 || current_thread() != observed_thread)
        panic("RAM_MAIN refused context: stage=%u pid=%d", fuxi_ram_root_stage, proc_getpid(current_proc()));
}

void fuxi_ram_main_arm(void)
{
    uint64_t startup[2];
    uint32_t header[8], entry[4];
    arm_debug_state64_t debug = {0};
    uint64_t deadline;
    observed_thread = current_thread();
    require_observed_thread();
    arm_saved_state_t *state = get_user_regs(observed_thread);
    if (!state || !is_saved_state64(state) || (get_saved_state_cpsr(state) & 0x1f))
        panic("RAM_MAIN invalid initial user state");
    if (copyin((user_addr_t)get_saved_state_sp(state), startup, sizeof(startup)) ||
        !startup[0] || startup[0] >> 63 || (startup[0] & 4095) || startup[1] != 1)
        panic("RAM_MAIN invalid original dyld startup stack");
    launchd_header = startup[0];
    launchd_main = launchd_header + RAM_ROOT_MAIN_OFFSET;
    checkpoint_pc = launchd_main + 4;
    if (copyin(launchd_header, header, sizeof(header)) ||
        header[0] != 0xfeedfacfU || header[1] != 0x100000cU || header[2] != 0 || header[3] != 2 ||
        copyin(launchd_main, entry, sizeof(entry)) || memcmp(entry, ram_root_main_bytes, sizeof(entry)))
        panic("RAM_MAIN original executable header/entry mismatch");
    debug.bvr[0] = checkpoint_pc;
    debug.bcr[0] = ARM_DBG_CR_BYTE_ADDRESS_SELECT_MASK | ARM_DBG_CR_MODE_CONTROL_USER | ARM_DBG_CR_ENABLE_ENABLE;
    kern_return_t result = machine_thread_set_state(observed_thread, ARM_DEBUG_STATE64,
        (thread_state_t)&debug, ARM_DEBUG_STATE64_COUNT);
    if (result != KERN_SUCCESS) panic("RAM_MAIN cannot arm EL0 hardware breakpoint: %d", result);
    deadline_call = thread_call_allocate(deadline_expired, NULL);
    if (!deadline_call) panic("RAM_MAIN cannot allocate diagnostic deadline");
    clock_interval_to_deadline(30, NSEC_PER_SEC, &deadline);
    thread_call_enter_delayed(deadline_call, deadline);
}

void fuxi_ram_main_svc(uint64_t pc, uint32_t cpsr, uint32_t esr, uint64_t syscall)
{
    require_observed_thread();
    if ((cpsr & 0x1f) || (esr >> 26) != 0x15 || pc < 4 || pc >> 63)
        panic("RAM_MAIN invalid SVC context: pc=%llx cpsr=%x esr=%x", pc, cpsr, esr);
    last_pc = pc;
    recent_calls[svc_count & 7] = (int32_t)syscall;
    if (++svc_count > 2048) deadline_expired(NULL, NULL);
    /* Keep the debug register armed. A user request cannot silently remove the checkpoint. */
    arm_debug_state64_t *debug = find_debug_state64(observed_thread);
    if (!debug || debug->bvr[0] != checkpoint_pc || !(debug->bcr[0] & ARM_DBG_CR_ENABLE_ENABLE))
        panic("RAM_MAIN hardware checkpoint state lost; stopping before dispatch");
}

void fuxi_ram_main_unix(unsigned code)
{
    require_observed_thread();
    switch (code) {
    case SYS_fork: case SYS_vfork: case SYS_execve: case SYS_posix_spawn: case SYS___mac_execve:
    case SYS_mount: case SYS_unmount: case SYS___mac_mount: case SYS_fmount:
    case SYS_reboot: case SYS_ptrace:
        panic("RAM_MAIN blocked syscall=%u before dispatch\nsvc=%u lastpc=%llx\nmain checkpoint not reached\nrecovery programs not started", code, svc_count, last_pc);
    default:
        return;
    }
}

void fuxi_ram_main_break(uint64_t pc, uint32_t cpsr, uint32_t esr)
{
    uint32_t entry[4];
    require_observed_thread();
    if ((cpsr & 0x1f) || (esr >> 26) != 0x30 || pc != checkpoint_pc ||
        copyin(launchd_main, entry, sizeof(entry)) || memcmp(entry, ram_root_main_bytes, sizeof(entry)))
        panic("RAM_MAIN unexpected hardware breakpoint: pc=%llx expected=%llx cpsr=%x esr=%x", pc, checkpoint_pc, cpsr, esr);
    fuxi_ram_root_stage = 10;
    panic("RAM_LAUNCHD_MAIN PASS\nroot=md0 apfs readonly=1\noriginal launchd LC_MAIN+4 reached\nfirst instruction and entry bytes intact\nheader=%llx main=%llx\npc=%llx cpsr=%x esr=%x\nsvc=%u; process creation blocked\nrecovery programs not started",
        launchd_header, launchd_main, pc, cpsr, esr, svc_count);
}
