/* Native root-authentication preflight. Never pivots or starts userland. */
#include <sys/types.h>
#include <sys/systm.h>
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

extern volatile unsigned fuxi_ram_root_stage, fuxi_ram_system_stage, fuxi_ram_system_user_entry;
extern dev_t rootdev;
extern dev_t mdevlookup(int);
void fuxi_ram_system_root_preflight(unsigned) __attribute__((noreturn));

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

void fuxi_ram_system_root_preflight(unsigned profile)
{
    unsigned enabled = 0;
    if (profile != 1 || current_task() != kernel_task || proc_getpid(current_proc()) != 0 ||
        fuxi_ram_root_stage != 7 || fuxi_ram_system_stage != 6 || fuxi_ram_system_user_entry ||
        !PE_parse_boot_argn("ram-system-root-preflight", &enabled, sizeof(enabled)) || enabled != 1)
        panic("RAM_ROOT_PREFLIGHT refused: mode/stage/task");

    vnode_t old = NULLVP, roots[2] = { NULLVP, NULLVP };
    mount_t mounts[2];
    const char *paths[2] = { "/mnt1", "/mnt2" };
    uint64_t before[2], after[2];
    int auth[2], dirs[2][4];
    const char *suffix[4] = { "dev", "private/var", "System/Volumes/Restore", "System/Volumes/BaseSystem" };
    int error = vnode_lookup("/", 0, &old, vfs_context_kernel());
    if (error || old != rootvnode || rootdev != mdevlookup(0))
        panic("RAM_ROOT_PREFLIGHT refused: old root errno=%d", error);
    mount_t old_mount = vnode_mount(old);
    uint64_t old_flags = old_mount ? vfs_flags(old_mount) : 0;
    if (!old_mount || (old_flags & (MNT_ROOTFS | MNT_RDONLY)) != (MNT_ROOTFS | MNT_RDONLY) ||
        strcmp(vfs_statfs(old_mount)->f_fstypename, "apfs"))
        panic("RAM_ROOT_PREFLIGHT refused: old root identity");

    /* Validate both targets before issuing either native request. */
    for (unsigned i = 0; i < 2; i++) {
        error = vnode_lookup(paths[i], 0, &roots[i], vfs_context_kernel());
        if (error) panic("RAM_ROOT_PREFLIGHT refused: target=%u lookup=%d", i + 1, error);
        mounts[i] = vnode_mount(roots[i]);
        before[i] = mounts[i] ? vfs_flags(mounts[i]) : 0;
        if (!mounts[i] || mounts[i] == old_mount || (i && mounts[i] == mounts[0]) ||
            vnode_vtype(roots[i]) != VDIR || !vnode_isvroot(roots[i]) ||
            !(before[i] & MNT_RDONLY) || (before[i] & MNT_ROOTFS) ||
            strcmp(vfs_statfs(mounts[i])->f_fstypename, "apfs"))
            panic("RAM_ROOT_PREFLIGHT refused: target=%u identity", i + 1);
        for (unsigned j = 0; j < 4; j++) dirs[i][j] = directory(paths[i], suffix[j], mounts[i]);
    }
    for (unsigned i = 0; i < 2; i++) {
        /* Same request/data/fflag/context as the native iOS bsd_init root path.
         * This deliberately invokes authentication; it is not a passive query.
         * Preserve its real result and stop, including when authentication fails.
         */
        auth[i] = VNOP_IOCTL(roots[i], FSIOC_KERNEL_ROOTAUTH, NULL, 0, vfs_context_kernel());
        after[i] = vfs_flags(mounts[i]);
    }
    int unchanged = rootvnode == old && rootdev == mdevlookup(0) &&
        vnode_mount(old) == old_mount && vfs_flags(old_mount) == old_flags &&
        !fuxi_ram_system_user_entry && fuxi_ram_root_stage == 7;
    vnode_put(roots[1]); vnode_put(roots[0]); vnode_put(old);
    panic("RAM_ROOT_PREFLIGHT observed\nroot=md0 unchanged=%d userland=0\n"
        "OS auth=%d flags=%llx/%llx\ndirs dev=%d var=%d restore=%d base=%d\n"
        "SystemOS auth=%d flags=%llx/%llx\ndirs dev=%d var=%d restore=%d base=%d\n"
        "native rootauth only; NO root switch",
        unchanged, auth[0], (unsigned long long)before[0], (unsigned long long)after[0],
        dirs[0][0], dirs[0][1], dirs[0][2], dirs[0][3],
        auth[1], (unsigned long long)before[1], (unsigned long long)after[1],
        dirs[1][0], dirs[1][1], dirs[1][2], dirs[1][3]);
}
