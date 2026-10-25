/* Native root switch: pivot md0 to the OS volume with vfs_switch_root.
 * Mount-only builds stop before PID1 after verification; user-entry builds
 * arm the bounded native-root init (/sbin/launchd on the switched root).
 * Never modifies authentication results. */
#include <sys/types.h>
#include <sys/systm.h>
#include <sys/uio.h>
#include <sys/errno.h>
#include <sys/fsctl.h>
#include <sys/mount.h>
#include <sys/vnode.h>
#include <sys/vnode_internal.h>
#include <sys/proc.h>
#include <sys/proc_internal.h>
#include <kern/task.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>
#include <libkern/crypto/sha2.h>
#include "RamSystemProbePins.h"
#ifndef RAM_SYSTEM_ALLOW_USER_ENTRY
#define RAM_SYSTEM_ALLOW_USER_ENTRY 0
#endif
#ifndef RAM_SYSTEM_CACHE
#define RAM_SYSTEM_CACHE 0
#endif
#if RAM_SYSTEM_CACHE
extern void fuxi_ram_system_cache_bind(mount_t);
#endif

extern volatile unsigned fuxi_ram_root_stage, fuxi_ram_system_stage, fuxi_ram_system_user_entry;
extern dev_t rootdev;
extern dev_t mdevlookup(int);
extern int vfs_switch_root(const char *, const char *, uint32_t);
#if RAM_SYSTEM_ALLOW_USER_ENTRY
/* Owned by RamRootProbe.c so non-system builds still link; read by its
 * exec/SVC checkpoints. */
extern volatile unsigned fuxi_ram_native_root_entry;
extern volatile int fuxi_ram_switch_auth;
extern volatile unsigned long long fuxi_ram_switch_flags;
void fuxi_ram_system_root_switch(unsigned);
#else
void fuxi_ram_system_root_switch(unsigned) __attribute__((noreturn));
#endif

static int directory(const char *prefix, const char *suffix, mount_t expected)
{
    char path[96];
    vnode_t vnode = NULLVP;
    snprintf(path, sizeof(path), "%s/%s", prefix, suffix);
    int error = vnode_lookup(path, 0, &vnode, vfs_context_kernel());
    if (error) return error;
    if (vnode_mount(vnode) != expected) error = EXDEV;
    else if (vnode_vtype(vnode) != VDIR) error = ENOTDIR;
    vnode_put(vnode);
    return error;
}

/* Hash a same-mount launchd at path and check it against the pinned size. */
static int launchd_digest(const char *path, mount_t expected, off_t *bytes_out, uint8_t digest[32])
{
    static uint8_t buffer[4096];
    vnode_t file = NULLVP;
    off_t bytes = 0;
    SHA256_CTX hash;
    vfs_context_t context = vfs_context_kernel();
    int error = vnode_lookup(path, 0, &file, context);
    if (error) return error;
    if (vnode_mount(file) != expected || vnode_vtype(file) != VREG) { vnode_put(file); return EXDEV; }
    error = vnode_size(file, &bytes, context);
    if (error || bytes != (off_t)RAM_SYSTEM_LAUNCHD_BYTES) { vnode_put(file); return error ? error : EFBIG; }
    SHA256_Init(&hash);
    for (off_t offset = 0; offset < bytes;) {
        int count = (int)((bytes - offset) < (off_t)sizeof(buffer) ? bytes - offset : (off_t)sizeof(buffer));
        int residual = count;
        error = vn_rdwr(UIO_READ, file, (caddr_t)buffer, count, offset,
            UIO_SYSSPACE, 0, vfs_context_ucred(context), &residual, vfs_context_proc(context));
        if (error || residual) { vnode_put(file); return error ? error : EIO; }
        SHA256_Update(&hash, buffer, (size_t)count);
        offset += count;
    }
    SHA256_Final(digest, &hash);
    vnode_put(file);
    *bytes_out = bytes;
    return 0;
}

void fuxi_ram_system_root_switch(unsigned profile)
{
    unsigned enabled = 0;
    if (profile != 1 || current_task() != kernel_task || proc_getpid(current_proc()) != 0 ||
        fuxi_ram_root_stage != 7 || fuxi_ram_system_stage != 6 || fuxi_ram_system_user_entry ||
        !PE_parse_boot_argn("ram-system-root-switch", &enabled, sizeof(enabled)) || enabled != 1)
        panic("RAM_ROOT_SWITCH refused: mode/stage/task");

    /* Validate everything before the destructive pivot; nothing may change after. */
    vnode_t old = NULLVP, roots[2] = { NULLVP, NULLVP };
    mount_t mounts[2];
    const char *paths[2] = { "/mnt1", "/mnt2" };
    uint8_t before[32], after[32];
    off_t before_bytes = 0, after_bytes = 0;
    int error = vnode_lookup("/", 0, &old, vfs_context_kernel());
    if (error || old != rootvnode || rootdev != mdevlookup(0))
        panic("RAM_ROOT_SWITCH refused: old root errno=%d", error);
    mount_t old_mount = vnode_mount(old);
    uint64_t old_flags = old_mount ? vfs_flags(old_mount) : 0;
    if (!old_mount || (old_flags & (MNT_ROOTFS | MNT_RDONLY)) != (MNT_ROOTFS | MNT_RDONLY) ||
        strcmp(vfs_statfs(old_mount)->f_fstypename, "apfs"))
        panic("RAM_ROOT_SWITCH refused: old root identity");

    for (unsigned i = 0; i < 2; i++) {
        error = vnode_lookup(paths[i], 0, &roots[i], vfs_context_kernel());
        if (error) panic("RAM_ROOT_SWITCH refused: target=%u lookup=%d", i + 1, error);
        mounts[i] = vnode_mount(roots[i]);
        uint64_t flags = mounts[i] ? vfs_flags(mounts[i]) : 0;
        if (!mounts[i] || mounts[i] == old_mount || (i && mounts[i] == mounts[0]) ||
            vnode_vtype(roots[i]) != VDIR || !vnode_isvroot(roots[i]) ||
            !(flags & MNT_RDONLY) || (flags & MNT_ROOTFS) ||
            strcmp(vfs_statfs(mounts[i])->f_fstypename, "apfs"))
            panic("RAM_ROOT_SWITCH refused: target=%u identity", i + 1);
    }
    /* Outgoing placement for md0 and the preserved devfs directory, both on the
     * incoming volume: vfs_switch_root requires them to exist beforehand. */
    if (directory("/mnt1", "Developer", mounts[0]))
        panic("RAM_ROOT_SWITCH refused: outgoing placement");
    if (directory("/mnt1", "dev", mounts[0]))
        panic("RAM_ROOT_SWITCH refused: preserved dev directory");
    error = launchd_digest("/mnt1/sbin/launchd", mounts[0], &before_bytes, before);
    if (error) panic("RAM_ROOT_SWITCH refused: incoming launchd errno=%d", error);
    if (memcmp(before, ram_system_launchd_sha256, sizeof(before)))
        panic("RAM_ROOT_SWITCH refused: incoming launchd SHA256 mismatch");

    /* Release every held vnode: the native pivot owns the mount moves itself. */
    vnode_put(roots[1]); vnode_put(roots[0]); vnode_put(old);
    roots[0] = roots[1] = old = NULLVP;

    int pivot = vfs_switch_root("/mnt1", "Developer", 0);
    if (pivot) panic("RAM_ROOT_SWITCH pivot failed: errno=%d; no userland", pivot);

    /* Verify the switched root against the mount captured before the pivot. */
    mount_t switched = vnode_mount(rootvnode);
    uint64_t flags = switched ? vfs_flags(switched) : 0;
    if (!switched || switched != mounts[0] || rootvnode == NULLVP ||
        (flags & (MNT_ROOTFS | MNT_RDONLY)) != (MNT_ROOTFS | MNT_RDONLY) ||
        strcmp(vfs_statfs(switched)->f_fstypename, "apfs"))
        panic("RAM_ROOT_SWITCH refused: switched root identity");
    if (vfs_flags(old_mount) & MNT_ROOTFS)
        panic("RAM_ROOT_SWITCH refused: outgoing root kept MNT_ROOTFS");
    vnode_t placed = NULLVP;
    int place = vnode_lookup("/Developer", 0, &placed, vfs_context_kernel());
    if (place || vnode_mount(placed) != old_mount || vnode_vtype(placed) != VDIR) {
        if (!place) vnode_put(placed);
        panic("RAM_ROOT_SWITCH refused: outgoing placement errno=%d", place);
    }
    vnode_put(placed);
    vnode_t dev = NULLVP;
    int dev_error = vnode_lookup("/dev", 0, &dev, vfs_context_kernel());
    int devfs = !dev_error && vnode_mount(dev) && !strcmp(vfs_statfs(vnode_mount(dev))->f_fstypename, "devfs");
    if (dev_error) dev = NULLVP;
    if (dev) vnode_put(dev);
    if (!devfs) panic("RAM_ROOT_SWITCH refused: devfs not preserved errno=%d", dev_error);
    error = launchd_digest("/sbin/launchd", switched, &after_bytes, after);
    if (error) panic("RAM_ROOT_SWITCH refused: switched launchd errno=%d", error);
    if (after_bytes != before_bytes || memcmp(after, before, sizeof(after)))
        panic("RAM_ROOT_SWITCH refused: switched launchd changed");

    /* Informational native authentication on the switched root; recorded, never gating. */
    int autherr = VNOP_IOCTL(rootvnode, FSIOC_KERNEL_ROOTAUTH, NULL, 0, vfs_context_kernel());
    uint64_t auth_flags = vfs_flags(switched);
    int unchanged = rootdev == mdevlookup(0) && !fuxi_ram_system_user_entry &&
        fuxi_ram_root_stage == 7 && fuxi_ram_system_stage == 6;
#if RAM_SYSTEM_ALLOW_USER_ENTRY
    /* Arm the bounded native-root entry: PID1 execs /sbin/launchd, which now
     * resolves on the switched OS-volume root; the SVC checkpoint stops before
     * any syscall dispatch. The init override stays off, so the kernel default
     * init path is used and its source is verified by the exec hooks. */
    fuxi_ram_switch_auth = autherr;
    fuxi_ram_switch_flags = auth_flags;
    fuxi_ram_native_root_entry = 1;
#if RAM_SYSTEM_CACHE
    fuxi_ram_system_cache_bind(mounts[1]);
#endif
    fuxi_ram_system_stage = 7;
    printf("RAM_SYSTEM native root entry armed: root=/sbin/launchd on switched OS volume "
        "rootfs=1 auth=%d flags=%llx unchanged=%d\n", autherr, auth_flags, unchanged);
    return;
#else
    panic("RAM_ROOT_SWITCH observed\n"
        "pivot=OK old=md0 newroot=apfs\n"
        "rootfs=1 flags=%llx (old %llx, rootfs cleared)\n"
        "outgoing=/Developer dev=devfs\n"
        "launchd=/sbin/launchd %lld bytes SHA256=OK\n"
        "auth=%d flags_after=%llx\n"
        "rootdev=md0 unchanged=%d userland=0",
        flags, old_flags, after_bytes, autherr, auth_flags, unchanged);
#endif
}
