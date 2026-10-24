/* Observe existing AMFI dependency calls; never initiate an ioctl or change a verdict. */
#include <sys/types.h>
#include <sys/systm.h>
#include <sys/proc.h>
#include <sys/proc_internal.h>
#include <sys/mount.h>
#include <sys/vnode.h>
#include <kern/thread.h>
#include <pexpert/pexpert.h>

extern volatile unsigned fuxi_ram_root_stage, fuxi_ram_system_user_entry;
int fuxi_ram_launch_begin(proc_t, vnode_t, const char *);
void fuxi_ram_launch_end(int, int, size_t);
void fuxi_ram_launch_flags(mount_t, uint64_t);
void fuxi_ram_launch_ioctl(vnode_t, u_long, int);
const char *fuxi_ram_launch_info(void);

static thread_t owner;
static vnode_t target_vnode;
static mount_t target_mount;
static volatile unsigned active, finished;
static unsigned flag_reads, ioctl_calls, flags_changed, ioctl_changed;
static uint64_t last_flags;
static int last_ioctl;
static char observation[256];

int fuxi_ram_launch_begin(proc_t process, vnode_t vnode, const char *policy)
{
    unsigned enabled = 0, profile = 0;
    if (active || finished || !process || !vnode || !policy || strcmp(policy, "AMFI") ||
        !fuxi_ram_system_user_entry || fuxi_ram_root_stage != 7 ||
        proc_getpid(process) != 1 || proc_getpid(current_proc()) != 1 ||
        !PE_parse_boot_argn("ram-launch-observe", &enabled, sizeof(enabled)) || enabled != 1 ||
        !PE_parse_boot_argn("ram-system-probe", &profile, sizeof(profile)) || profile != 2)
        return 0;
    target_mount = vnode_mount(vnode);
    if (!target_mount) return 0;
    owner = current_thread();
    target_vnode = vnode;
    active = 1;
    return 1;
}

void fuxi_ram_launch_flags(mount_t mount, uint64_t flags)
{
    if (!active || owner != current_thread() || mount != target_mount) return;
    if (flag_reads && flags != last_flags) flags_changed = 1;
    last_flags = flags;
    if (flag_reads != ~0U) flag_reads++;
}

void fuxi_ram_launch_ioctl(vnode_t vnode, u_long command, int error)
{
    if (!active || owner != current_thread() || vnode != target_vnode || command != 0x80084118UL) return;
    if (ioctl_calls && error != last_ioctl) ioctl_changed = 1;
    last_ioctl = error;
    if (ioctl_calls != ~0U) ioctl_calls++;
}

void fuxi_ram_launch_end(int token, int error, size_t fatal_length)
{
    if (token != 1 || !active || owner != current_thread()) return;
    active = 0; /* Formatting must not observe itself or add filesystem calls. */
    char flags[80], ioctl[80];
    if (flag_reads)
        snprintf(flags, sizeof(flags), "flags=%llx rootfs=%u reads=%u change=%u",
            (unsigned long long)last_flags, (unsigned)((last_flags & MNT_ROOTFS) != 0), flag_reads, flags_changed);
    else
        snprintf(flags, sizeof(flags), "flags=unseen reads=0");
    if (ioctl_calls)
        snprintf(ioctl, sizeof(ioctl), "auth=80084118 errno=%d calls=%u change=%u",
            last_ioctl, ioctl_calls, ioctl_changed);
    else
        snprintf(ioctl, sizeof(ioctl), "auth=80084118 unseen calls=0");
    snprintf(observation, sizeof(observation), "RAM_LC_OBS err=%d fatal=%u\n%s\n%s",
        error, (unsigned)(fatal_length != 0), flags, ioctl);
    finished = 1;
}

const char *fuxi_ram_launch_info(void)
{
    return finished ? observation : "";
}
