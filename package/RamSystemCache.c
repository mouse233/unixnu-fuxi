/* Use the already authenticated RAM SystemCryptex after the native pivot.
 * No filesystem writes, second mount, trust changes or user-code patches. */
#include <sys/types.h>
#include <sys/systm.h>
#include <sys/errno.h>
#include <sys/uio.h>
#include <sys/fsctl.h>
#include <sys/mount.h>
#include <sys/vnode.h>
#include <sys/vnode_internal.h>
#include <sys/proc.h>
#include <kern/thread.h>
#include <libkern/crypto/sha2.h>
#include <pexpert/pexpert.h>
#include "RamSystemProbePins.h"

extern volatile unsigned fuxi_ram_native_root_entry, fuxi_ram_root_stage, fuxi_ram_system_stage;
extern volatile unsigned fuxi_ram_cache_stage;
extern volatile unsigned long long fuxi_ram_cache_dyld_flags;
extern uint64_t dyld_flags;
extern void commpage_update_dyld_flags(uint64_t);
static mount_t cache_mount;
static const char cache_root[] = "/Developer/mnt2";
static const char cache_directory[] = "/Developer/mnt2/System/Library/Caches/com.apple.dyld";
static const char environment[] = "DYLD_SHARED_CACHE_DIR=/Developer/mnt2/System/Library/Caches/com.apple.dyld";
void fuxi_ram_system_cache_bind(mount_t);
void fuxi_ram_system_cache_prepare(proc_t, user_addr_t, user_addr_t *);

static vnode_t checked_node(const char *path, mount_t expected, enum vtype type)
{
    vnode_t node = NULLVP;
    int error = vnode_lookup(path, 0, &node, vfs_context_kernel());
    if (error) panic("RAM_CACHE lookup failed: %s errno=%d", path, error);
    if (vnode_mount(node) != expected || vnode_vtype(node) != type)
        panic("RAM_CACHE refused node identity: %s", path);
    return node;
}

/* This is called with the original /mnt2 mount identity captured before pivot. */
void fuxi_ram_system_cache_bind(mount_t expected)
{
    unsigned enabled = 0;
    if (!expected || cache_mount || fuxi_ram_root_stage != 7 || fuxi_ram_system_stage != 6 ||
        !fuxi_ram_native_root_entry ||
        !PE_parse_boot_argn("ram-system-cache", &enabled, sizeof(enabled)) || enabled != 1)
        panic("RAM_CACHE refused bind mode/stage");
    if (!(vfs_flags(expected) & MNT_RDONLY) || (vfs_flags(expected) & MNT_ROOTFS) ||
        expected == vnode_mount(rootvnode) || strcmp(vfs_statfs(expected)->f_fstypename, "apfs"))
        panic("RAM_CACHE refused mount identity");
    vnode_t node = checked_node(cache_root, expected, VDIR);
    if (!vnode_isvroot(node)) panic("RAM_CACHE refused non-root vnode");
    int auth = VNOP_IOCTL(node, FSIOC_KERNEL_ROOTAUTH, NULL, 0, vfs_context_kernel());
    vnode_put(node);
    if (auth) panic("RAM_CACHE native rootauth failed: errno=%d", auth);
    node = checked_node(cache_directory, expected, VDIR);
    vnode_put(node);
    cache_mount = expected;
    fuxi_ram_cache_stage = 1;
}

static void verify_dyld(void)
{
    vnode_t node = checked_node("/usr/lib/dyld", vnode_mount(rootvnode), VREG);
    off_t bytes = 0;
    vfs_context_t context = vfs_context_kernel();
    int error = vnode_size(node, &bytes, context);
    if (error || bytes != RAM_SYSTEM_DYLD_BYTES) panic("RAM_CACHE dyld size: errno=%d", error);
    SHA256_CTX hash;
    uint8_t buffer[4096], digest[32];
    SHA256_Init(&hash);
    for (off_t offset = 0; offset < bytes;) {
        int count = (int)MIN((off_t)sizeof(buffer), bytes - offset), residual = count;
        error = vn_rdwr(UIO_READ, node, (caddr_t)buffer, count, offset, UIO_SYSSPACE, 0,
            vfs_context_ucred(context), &residual, vfs_context_proc(context));
        if (error || residual) panic("RAM_CACHE dyld read: errno=%d residual=%d", error, residual);
        SHA256_Update(&hash, buffer, (size_t)count);
        offset += count;
    }
    SHA256_Final(digest, &hash);
    vnode_put(node);
    if (memcmp(digest, ram_system_dyld_sha256, sizeof(digest))) panic("RAM_CACHE dyld SHA256 mismatch");
}

/* Called by load_init_program with its private one-page scratch mapping, before
 * exec builds the replacement thread. Low bytes remain reserved for argv. */
void fuxi_ram_system_cache_prepare(proc_t process, user_addr_t scratch, user_addr_t *envp)
{
    if (!process || proc_getpid(process) != 1 || !scratch || !envp || *envp ||
        !cache_mount || fuxi_ram_cache_stage != 1 || !fuxi_ram_native_root_entry ||
        fuxi_ram_root_stage != 7 || fuxi_ram_system_stage != 7)
        panic("RAM_CACHE refused init context");
    vnode_t node = checked_node(cache_root, cache_mount, VDIR);
    if (!(vfs_flags(cache_mount) & MNT_RDONLY) || (vfs_flags(cache_mount) & MNT_ROOTFS))
        panic("RAM_CACHE mount flags changed");
    vnode_put(node);
    verify_dyld();
    user64_addr_t vector[2] = {scratch + 0x400, USER_ADDR_NULL};
    int error = copyout(environment, vector[0], sizeof(environment));
    if (!error) error = copyout(vector, scratch + 0x500, sizeof(vector));
    if (error) panic("RAM_CACHE env copyout failed: errno=%d", error);
    /* Original CacheFinder checks bit 3 before ignite, even when the override
     * directory opened successfully. Without it ignition may close fd 0. */
    dyld_flags |= UINT64_C(8);
    commpage_update_dyld_flags(dyld_flags);
    fuxi_ram_cache_dyld_flags = dyld_flags;
    *envp = scratch + 0x500;
    fuxi_ram_cache_stage = 2;
    printf("RAM_CACHE ready: readonly auth=0 dir=%s dyld_flags=%llx envp=ready\n",
        cache_directory, (unsigned long long)dyld_flags);
}
