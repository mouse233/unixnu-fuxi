/* Bounded RAM root diagnostics: mount-only, or stop before the first EL0 SVC. */
#include <sys/types.h>
#include <sys/systm.h>
#include <sys/uio.h>
#include <sys/vnode.h>
#include <sys/vnode_internal.h>
#include <sys/mount.h>
#include <sys/proc.h>
#include <kern/thread.h>
#include <mach/vm_param.h>
#include <pexpert/pexpert.h>
#include <libkern/crypto/sha2.h>
#include "RamRootProbePins.h"
#ifndef RAM_ROOT_RESTORE_TRUST
#define RAM_ROOT_RESTORE_TRUST 0
#endif
#if RAM_ROOT_RESTORE_TRUST
extern unsigned fuxi_ram_restore_trust_loaded;
#endif
#ifndef RAM_ROOT_PROGRESS
#define RAM_ROOT_PROGRESS 0
#endif

extern dev_t mdevlookup(int);
extern int mdevgetrange(int, uint64_t *, uint64_t *);
void fuxi_ram_root_probe_before_mount(dev_t);
void fuxi_ram_root_probe_mount_failed(int) __attribute__((noreturn));
#if RAM_ROOT_ALLOW_USER_ENTRY
void fuxi_ram_root_probe_after_mount(dev_t, vnode_t);
void fuxi_ram_user_exec_result(int, const char *, int);
void fuxi_ram_user_exec_begin(const char *, int);
void fuxi_ram_user_exec_ready(thread_t, int);
void fuxi_ram_user_svc(uint64_t, uint32_t, uint32_t, uint64_t) __attribute__((noreturn));
#if RAM_ROOT_ALLOW_MAIN_ENTRY
extern void fuxi_ram_main_arm(thread_t);
#endif
#else
void fuxi_ram_root_probe_after_mount(dev_t, vnode_t) __attribute__((noreturn));
#endif
volatile unsigned fuxi_ram_root_stage;
/* Set by RamSystemProbe's user-entry profile; RamSystemProbe.o is only linked
 * in system-object builds, so this owns the definition. */
volatile unsigned fuxi_ram_system_user_entry;
/* Set by RamSystemRootSwitch after a verified native pivot; the switch object
 * is only linked in system builds, so this file owns these definitions too. */
volatile unsigned fuxi_ram_native_root_entry;
volatile int fuxi_ram_switch_auth = -1;
volatile unsigned long long fuxi_ram_switch_flags = 0;
#if RAM_ROOT_ALLOW_USER_ENTRY
static unsigned exec_path_verified;
#endif
static uint8_t file_buffer[4096];

void fuxi_ram_root_probe_before_mount(dev_t device)
{
    unsigned enabled = 0;
    uint64_t base = 0, bytes = 0;
    uint8_t digest[32];
    SHA256_CTX hash;
    fuxi_ram_root_stage = 1;
    if (!PE_parse_boot_argn("ram-root-probe", &enabled, sizeof(enabled)) ||
        enabled != (RAM_ROOT_PROGRESS ? 4U : RAM_ROOT_ALLOW_MAIN_ENTRY ? 3U : RAM_ROOT_ALLOW_USER_ENTRY ? 2U : 1U))
        panic("RAM_ROOT refused: diagnostic argument missing");
    if (device == NODEV || device != mdevlookup(0) || mdevgetrange(0, &base, &bytes) ||
        base < VM_MIN_KERNEL_ADDRESS || bytes != RAM_ROOT_IMAGE_BYTES || base > UINT64_MAX - bytes)
        panic("RAM_ROOT refused: not expected md0 range; dev=%x bytes=%llu", device, bytes);
#if RAM_ROOT_RESTORE_TRUST
    if (fuxi_ram_restore_trust_loaded != 267) panic("RAM_ROOT restore trust module was not loaded");
#endif
    fuxi_ram_root_stage = 2;
    SHA256_Init(&hash);
    SHA256_Update(&hash, (const void *)(uintptr_t)base, (size_t)bytes);
    SHA256_Final(digest, &hash);
    if (memcmp(digest, ram_root_image_sha256, sizeof(digest)))
        panic("RAM_ROOT refused: media SHA256 mismatch");
    fuxi_ram_root_stage = 3;
}

void fuxi_ram_root_probe_mount_failed(int error)
{
    panic("RAM_ROOT mount failed: errno=%d stage=%u; userland not started", error, fuxi_ram_root_stage);
}

void fuxi_ram_root_probe_after_mount(dev_t device, vnode_t root)
{
    vfs_context_t context = vfs_context_kernel();
    mount_t mount = vnode_mount(root);
    vnode_t file = NULLVP;
    off_t bytes = 0;
    uint8_t digest[32];
    SHA256_CTX hash;
    int error;
    fuxi_ram_root_stage = 4;
    if (device != mdevlookup(0) || !mount || strcmp(vfs_statfs(mount)->f_fstypename, "apfs"))
        panic("RAM_ROOT mounted unexpected device or filesystem");
    if (!(vfs_flags(mount) & MNT_RDONLY))
        panic("RAM_ROOT mounted md0 without read-only flag; stopping before userland");
    error = vnode_lookup("/sbin/launchd", 0, &file, context);
    if (error) panic("RAM_ROOT mounted md0/APFS; launchd lookup errno=%d", error);
    error = vnode_size(file, &bytes, context);
    if (error || bytes != RAM_ROOT_FILE_BYTES) {
        vnode_put(file);
        panic("RAM_ROOT launchd size mismatch: errno=%d bytes=%lld", error, bytes);
    }
    fuxi_ram_root_stage = 5;
    SHA256_Init(&hash);
    for (off_t offset = 0; offset < bytes;) {
        int count = (int)((bytes - offset) < (off_t)sizeof(file_buffer) ? bytes - offset : (off_t)sizeof(file_buffer));
        int residual = count;
        error = vn_rdwr(UIO_READ, file, (caddr_t)file_buffer, count, offset,
            UIO_SYSSPACE, 0, vfs_context_ucred(context), &residual, vfs_context_proc(context));
        if (error || residual) {
            vnode_put(file);
            panic("RAM_ROOT launchd read errno=%d residual=%d at=%lld", error, residual, offset);
        }
        SHA256_Update(&hash, file_buffer, (size_t)count);
        offset += count;
    }
    SHA256_Final(digest, &hash);
    vnode_put(file);
    if (memcmp(digest, ram_root_file_sha256, sizeof(digest))) panic("RAM_ROOT launchd SHA256 mismatch");
#if RAM_ROOT_ALLOW_USER_ENTRY
    fuxi_ram_root_stage = 7;
    return;
#else
    fuxi_ram_root_stage = 6;
    panic("RAM_ROOT PASS\nroot=md0 filesystem=apfs readonly=1\nmedia=176MiB SHA256=OK\nlaunchd=%lld bytes SHA256=OK\nuserland not started", bytes);
#endif
}

#if RAM_ROOT_ALLOW_USER_ENTRY
/* Check the kernel-selected executable before exec releases its new thread.
 * The retiring exec caller may never reach exec_result before the EL0 trap. */
void fuxi_ram_user_exec_begin(const char *path, int pid)
{
    const char *expected = fuxi_ram_system_user_entry ? "/mnt1/sbin/launchd" : "/sbin/launchd";
    if (fuxi_ram_root_stage != 7 || pid != 1 || !path || strcmp(path, expected))
        panic("RAM_USER refused init source: stage=%u pid=%d path=%s", fuxi_ram_root_stage, pid, path ? path : "<null>");
    if (fuxi_ram_native_root_entry) {
        /* The pivot armed this entry; re-assert the root identity at exec time
         * so an identical ramdisk /sbin/launchd cannot pass for the switched
         * OS-volume source (the two binaries are byte-identical). */
        mount_t root_mount = rootvnode ? vnode_mount(rootvnode) : NULL;
        if (!root_mount || !(vfs_flags(root_mount) & MNT_ROOTFS) ||
            strcmp(vfs_statfs(root_mount)->f_fstypename, "apfs"))
            panic("RAM_USER refused native root identity at exec: stage=%u", fuxi_ram_root_stage);
    }
    exec_path_verified = 1;
}

/* XNU exec creates a replacement thread. Arm it while FINAL_WAIT still
 * holds it, before it can execute dyld or race the retiring exec caller. */
void fuxi_ram_user_exec_ready(thread_t target, int pid)
{
    if (fuxi_ram_root_stage != 7 || !target || pid != 1)
        panic("RAM_USER refused ready: stage=%u pid=%d", fuxi_ram_root_stage, pid);
    if (!exec_path_verified)
        panic("RAM_USER init source was not verified before exec");
    fuxi_ram_root_stage = 8;
#if RAM_ROOT_ALLOW_MAIN_ENTRY
    fuxi_ram_main_arm(target);
#endif
}

void fuxi_ram_user_exec_result(int error, const char *path, int pid)
{
    int system_entry = fuxi_ram_system_user_entry != 0;
    const char *expected = system_entry ? "/mnt1/sbin/launchd" : "/sbin/launchd";
    const char *tag = fuxi_ram_native_root_entry ? "RAM_NATIVE_ROOT_USER" :
        system_entry ? "RAM_SYSTEM_USER" : "RAM_USER original";
    if (pid != 1 || !path || strcmp(path, expected))
        panic("RAM_USER refused exec: stage=%u pid=%d path=%s", fuxi_ram_root_stage, pid, path);
    if (error) panic("%s launchd exec failed: errno=%d; no user syscall dispatched",
        tag, error);
    if (fuxi_ram_root_stage != 8 && fuxi_ram_root_stage != 9 && fuxi_ram_root_stage != 10)
        panic("RAM_USER exec returned without replacement-thread checkpoint: stage=%u", fuxi_ram_root_stage);
}

void fuxi_ram_user_svc(uint64_t pc, uint32_t cpsr, uint32_t esr, uint64_t syscall)
{
    uint32_t instruction = 0;
    int pid = proc_getpid(current_proc());
    if (fuxi_ram_root_stage != 8 || pid != 1 || (cpsr & 0x1f) ||
        (esr >> 26) != 0x15 || pc < 4 || (pc >> 63))
        panic("RAM_USER refused SVC: stage=%u pid=%d pc=%llx cpsr=%x esr=%x",
            fuxi_ram_root_stage, pid, pc, cpsr, esr);
    if (copyin((user_addr_t)(pc - 4), &instruction, sizeof(instruction)) ||
        (instruction & 0xffe0001fU) != 0xd4000001U)
        panic("RAM_USER invalid SVC instruction: pc=%llx instruction=%x", pc, instruction);
    fuxi_ram_root_stage = 9;
    if (fuxi_ram_native_root_entry) {
        if (fuxi_ram_system_user_entry)
            panic("RAM_USER refused: both entry modes armed");
        panic("RAM_NATIVE_ROOT_USER PASS\nroot=switched OS-volume apfs readonly=1 rootfs=1\nexec=/sbin/launchd (after native vfs_switch_root)\nPID1 executed at EL0; SVC trapped\npc=%llx cpsr=%x esr=%x\nx16=%lld instruction=%x\nswitch auth=%d flags=%llx\nstopped BEFORE first syscall dispatch\nrecovery programs not started",
            pc, cpsr, esr, (int64_t)syscall, instruction,
            fuxi_ram_switch_auth, fuxi_ram_switch_flags);
    }
    if (fuxi_ram_system_user_entry)
        panic("RAM_SYSTEM_USER PASS\nroot=md0 apfs readonly=1\nexec=/mnt1/sbin/launchd\nPID1 executed at EL0; SVC trapped\npc=%llx cpsr=%x esr=%x\nx16=%lld instruction=%x\nstopped BEFORE first syscall dispatch\nrecovery programs not started",
            pc, cpsr, esr, (int64_t)syscall, instruction);
    panic("RAM_USER_ENTRY PASS\nroot=md0 apfs readonly=1\noriginal launchd exec=OK\nPID1 executed at EL0; SVC trapped\npc=%llx cpsr=%x esr=%x\nx16=%lld instruction=%x\nstopped BEFORE first syscall dispatch\nrecovery programs not started",
        pc, cpsr, esr, (int64_t)syscall, instruction);
}
#endif
