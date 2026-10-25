/* Use the authenticated RAM SystemCryptex after the native pivot. Only the
 * bounded tmpfs namespace is written; original APFS volumes stay read-only. */
#include <sys/types.h>
#include <sys/systm.h>
#include <sys/errno.h>
#include <sys/uio.h>
#include <sys/fsctl.h>
#include <sys/mount.h>
#include <sys/vnode.h>
#include <sys/vnode_internal.h>
#include <sys/proc.h>
#include <sys/sysproto.h>
#include <kern/thread.h>
#include <libkern/crypto/sha2.h>
#include <pexpert/pexpert.h>
#include "RamSystemProbePins.h"

extern volatile unsigned fuxi_ram_native_root_entry, fuxi_ram_root_stage, fuxi_ram_system_stage;
extern volatile unsigned fuxi_ram_cache_stage;
extern volatile unsigned fuxi_ram_cache_carrier;
extern volatile unsigned long long fuxi_ram_cache_dyld_flags;
extern uint64_t dyld_flags;
extern void commpage_update_dyld_flags(uint64_t);
extern int OSKextLoadKextWithIdentifier(const char *);
static mount_t cache_mount;
static const char cache_root[] = "/Developer/mnt2";
static const char cache_directory[] = "/Developer/mnt2/System/Library/Caches/com.apple.dyld";
static const char environment[] = "DYLD_SHARED_CACHE_DIR=/Developer/mnt2/System/Library/Caches/com.apple.dyld";
static const char preboot_path[] = "/private/preboot";
static const char carrier_path[] = "/private/preboot/Cryptexes";
void fuxi_ram_system_cache_bind(mount_t);
void fuxi_ram_system_cache_prepare(proc_t, user_addr_t, user_addr_t *);
#if RAM_SYSTEM_WATCHDOG
extern int fuxi_ram_watchdog_prepare(void);
#endif

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

/* The unmodified non-CSR shared-region policy follows this path and compares
 * actual mount identity. Make that identity reachable through a sealed RAM
 * namespace, instead of changing the policy or mutating an APFS volume. */
static void prepare_carrier(proc_t process, user_addr_t scratch)
{
    vnode_t node = checked_node(preboot_path, vnode_mount(rootvnode), VDIR);
    if (vnode_isvroot(node)) panic("RAM_CACHE preboot already mounted");
    vnode_put(node);
    int error = vnode_lookup(carrier_path, 0, &node, vfs_context_kernel());
    if (!error) { vnode_put(node); panic("RAM_CACHE carrier already exists"); }
    if (error != ENOENT) panic("RAM_CACHE carrier preflight errno=%d", error);

    int load = OSKextLoadKextWithIdentifier("com.apple.filesystems.tmpfs");
    if (load) panic("RAM_CACHE tmpfs load failed: status=%x", load);
    /* Original 21H461 tmpfs_mount copies exactly 24 bytes: page quota,
     * node quota, option byte/padding. 256 native 4K pages = 1 MiB. */
    struct { uint64_t pages, nodes; uint32_t options, reserved; } data = {256, 32, 0, 0};
    _Static_assert(sizeof(data) == 24, "original tmpfs mount ABI");
    struct mount_args request = { .type = scratch + 0x100, .path = scratch + 0x120,
        .flags = MNT_NOSUID | MNT_NODEV | MNT_NOEXEC | MNT_DONTBROWSE, .data = scratch + 0x180 };
    int32_t result = 0;
    if ((error = copyout("tmpfs", request.type, 6)) ||
        (error = copyout(preboot_path, request.path, sizeof(preboot_path))) ||
        (error = copyout(&data, request.data, sizeof(data))))
        panic("RAM_CACHE tmpfs args failed: errno=%d", error);
    error = mount(process, &request, &result);
    if (error) panic("RAM_CACHE tmpfs mount failed: errno=%d", error);
    error = vnode_lookup(preboot_path, 0, &node, vfs_context_kernel());
    if (error) panic("RAM_CACHE tmpfs lookup failed: errno=%d", error);
    mount_t space = vnode_mount(node);
    if (!space || space == cache_mount || space == vnode_mount(rootvnode) ||
        strcmp(vfs_statfs(space)->f_fstypename, "tmpfs") || !vnode_isvroot(node))
        panic("RAM_CACHE tmpfs mount identity failed");
    vnode_put(node);
    struct symlink_args link = { .path = scratch + 0x200, .link = scratch + 0x280 };
    if ((error = copyout(cache_root, link.path, sizeof(cache_root))) ||
        (error = copyout(carrier_path, link.link, sizeof(carrier_path))))
        panic("RAM_CACHE carrier args failed: errno=%d", error);
    error = symlink(process, &link, &result);
    if (error) panic("RAM_CACHE carrier symlink failed: errno=%d", error);
    request.flags |= MNT_UPDATE | MNT_RDONLY;
    request.data = USER_ADDR_NULL; /* tmpfs update does not consume initial arguments. */
    error = mount(process, &request, &result);
    if (error) panic("RAM_CACHE tmpfs seal failed: errno=%d", error);
    node = checked_node(preboot_path, space, VDIR);
    unsigned required = MNT_RDONLY | MNT_NOSUID | MNT_NODEV | MNT_NOEXEC;
    if ((vfs_flags(space) & required) != required) panic("RAM_CACHE tmpfs seal flags failed");
    vnode_put(node);
    node = checked_node(carrier_path, cache_mount, VDIR);
    if (!vnode_isvroot(node)) panic("RAM_CACHE carrier is not cache volume root");
    vnode_put(node);
    fuxi_ram_cache_carrier = 1;
    printf("RAM_CACHE carrier ready: %s -> %s; tmpfs readonly quota=1MiB\n", carrier_path, cache_root);
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
    prepare_carrier(process, scratch);
#if RAM_SYSTEM_WATCHDOG
    int watchdog_error = fuxi_ram_watchdog_prepare();
    if (watchdog_error) panic("RAM_WATCHDOG prepare failed: errno=%d", watchdog_error);
#endif
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
