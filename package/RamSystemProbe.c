// Read-only OS/SystemOS mount checkpoint; profile 2 continues to a bounded
// user entry but never dispatches a user syscall.
#include <sys/types.h>
#include <sys/systm.h>
#include <sys/vnode.h>
#include <sys/vnode_internal.h>
#include <sys/mount.h>
#include <sys/proc.h>
#include <mach/vm_param.h>
#include <machine/machine_routines.h>
#include <pexpert/pexpert.h>
#include <pexpert/device_tree.h>
#include <libkern/crypto/sha2.h>
#include <Protocol/UniXNURamObjects.h>
#include "RamSystemProbePins.h"
#if RAM_SYSTEM_ROOT_PREFLIGHT
extern void fuxi_ram_system_root_preflight(unsigned) __attribute__((noreturn));
#endif
#if RAM_SYSTEM_ROOT_SWITCH
#if RAM_SYSTEM_ALLOW_USER_ENTRY
extern void fuxi_ram_system_root_switch(unsigned);
#else
extern void fuxi_ram_system_root_switch(unsigned) __attribute__((noreturn));
#endif
#endif

extern unsigned long gPhysBase, gPhysSize;
extern dev_t fuxi_mdev_add_ro(int, uint64_t, uint64_t);
extern int mdevgetrange(int, uint64_t *, uint64_t *);
extern int fuxi_ram_mount_ro(const char *, const char *);
extern int fuxi_ram_system_publish(unsigned, uint64_t, uint64_t) __attribute__((weak_import));
#if RAM_SYSTEM_ALLOW_USER_ENTRY
void fuxi_ram_system_probe(void);
int fuxi_ram_system_user_init_path(const char **);
/* Definition lives in RamRootProbe.c so non-system builds still link. */
extern volatile unsigned fuxi_ram_system_user_entry;
#else
void fuxi_ram_system_probe(void) __attribute__((noreturn));
#endif
volatile unsigned fuxi_ram_system_stage;
#if RAM_SYSTEM_ALLOW_USER_ENTRY
static uint8_t file_buffer[4096];
#endif

int fuxi_ram_system_user_init_path(const char **path)
{
#if RAM_SYSTEM_ALLOW_USER_ENTRY
    if (!fuxi_ram_system_user_entry) return 1;
    *path = "/mnt1/sbin/launchd";
    return 0;
#else
    (void)path;
    panic("RAM_SYSTEM refused: mount-only init reached");
#endif
}

void fuxi_ram_system_probe(void)
{
    unsigned enabled=0, length=0;
    DTEntry entry;
    const void *value=NULL;
    UR_HANDOFF objects;
    if (!PE_parse_boot_argn("ram-system-probe",&enabled,sizeof(enabled)))
        panic("RAM_SYSTEM refused: explicit mount-only profile required");
#if RAM_SYSTEM_ALLOW_USER_ENTRY
    if (enabled!=1 && enabled!=2)
        panic("RAM_SYSTEM refused: unknown profile %u",enabled);
#else
    if (enabled!=1)
        panic("RAM_SYSTEM refused: explicit mount-only profile required");
#endif
    fuxi_ram_system_stage=1;
    if (SecureDTLookupEntry(NULL,"/chosen/memory-map",&entry)!=kSuccess ||
        SecureDTGetProperty(entry,"RamSystemObjects",&value,&length)!=kSuccess || length!=sizeof(objects))
        panic("RAM_SYSTEM refused: missing handoff descriptor");
    memcpy(&objects,value,sizeof(objects));
    if (!UrValid(&objects) || objects.Window!=gPhysBase || objects.WindowBytes!=gPhysSize)
        panic("RAM_SYSTEM refused: invalid owned layout");
    for(unsigned id=1;id<=2;id++) {
        const UR_OBJECT *object=&objects.Object[id-1];
        uint8_t digest[32];SHA256_CTX hash;
        const uint8_t *memory=(const uint8_t *)ml_static_ptovirt(object->Address);
        if(!memory) panic("RAM_SYSTEM refused: unmapped id=%u",id);
        fuxi_ram_system_stage=1+id;
        printf("RAM_SYSTEM hashing id=%u PA=%llx bytes=%llu\n",id,object->Address,object->Bytes);
        SHA256_Init(&hash);
        for(uint64_t at=0;at<object->Bytes;) {
            size_t n=(size_t)MIN(object->Bytes-at,8ULL<<20);
            SHA256_Update(&hash,memory+at,n);at+=n;
        }
        SHA256_Final(digest,&hash);
        if(memcmp(digest,UrDigest(id),32)) panic("RAM_SYSTEM actual RAM SHA256 mismatch id=%u",id);
        uint64_t bias=id==1?UR_OS_APFS_OFFSET:0;
        uint64_t bytes=id==1?UR_OS_APFS_BYTES:UR_SYSTEM_BYTES;
        dev_t device=fuxi_mdev_add_ro(id,(uint64_t)(uintptr_t)(memory+bias),bytes);
        uint64_t actual=0,size=0;
        if(device==NODEV || mdevgetrange(id,&actual,&size) || actual!=(uint64_t)(uintptr_t)(memory+bias) || size!=bytes)
            panic("RAM_SYSTEM read-only device refused id=%u bytes=%llu",id,bytes);
        printf("RAM_SYSTEM md%u bytes=%llu readonly=1 SHA256=OK\n",id,bytes);
        if (&fuxi_ram_system_publish) {
            int error=fuxi_ram_system_publish(id,object->Address+bias,bytes);
            if(error) panic("RAM_SYSTEM IOKit transport refused id=%u errno=%d",id,error);
        }
    }
    for(unsigned id=1;id<=2;id++) {
        const char *device=id==1?"/dev/md1":"/dev/md2";
        const char *path=id==1?"/mnt1":"/mnt2";
        vnode_t root=NULLVP;
        fuxi_ram_system_stage=3+id;
        int error=fuxi_ram_mount_ro(device,path);
        if(error) panic("RAM_SYSTEM APFS mount failed id=%u errno=%d",id,error);
        error=vnode_lookup(path,0,&root,vfs_context_kernel());
        if(error) panic("RAM_SYSTEM mounted root lookup failed id=%u errno=%d",id,error);
        mount_t mount=vnode_mount(root);
        int ok=mount && (vfs_flags(mount)&MNT_RDONLY) && !strcmp(vfs_statfs(mount)->f_fstypename,"apfs");
        vnode_put(root);
        if(!ok) panic("RAM_SYSTEM mounted wrong filesystem or writable volume id=%u",id);
        printf("RAM_SYSTEM %s mounted APFS readonly=1\n",path);
    }
    fuxi_ram_system_stage=6;
#if RAM_SYSTEM_ROOT_PREFLIGHT
    fuxi_ram_system_root_preflight(enabled);
#endif
#if RAM_SYSTEM_ROOT_SWITCH
    fuxi_ram_system_root_switch(enabled);
#if RAM_SYSTEM_ALLOW_USER_ENTRY
    return;
#else
    panic("RAM_SYSTEM refused: root switch returned");
#endif
#endif
#if RAM_SYSTEM_ALLOW_USER_ENTRY
    if (enabled==2) {
        vfs_context_t context=vfs_context_kernel();
        vnode_t file=NULLVP;
        off_t bytes=0;
        uint8_t digest[32];SHA256_CTX hash;
        int error=vnode_lookup("/mnt1/sbin/launchd",0,&file,context);
        if(error) panic("RAM_SYSTEM launchd lookup errno=%d",error);
        error=vnode_size(file,&bytes,context);
        if(error || bytes!=RAM_SYSTEM_LAUNCHD_BYTES) {
            vnode_put(file);
            panic("RAM_SYSTEM launchd size mismatch: errno=%d bytes=%lld",error,bytes);
        }
        SHA256_Init(&hash);
        for(off_t offset=0;offset<bytes;) {
            int count=(int)((bytes-offset)<(off_t)sizeof(file_buffer)?bytes-offset:(off_t)sizeof(file_buffer));
            int residual=count;
            error=vn_rdwr(UIO_READ,file,(caddr_t)file_buffer,count,offset,
                UIO_SYSSPACE,0,vfs_context_ucred(context),&residual,vfs_context_proc(context));
            if(error || residual) {
                vnode_put(file);
                panic("RAM_SYSTEM launchd read errno=%d residual=%d at=%lld",error,residual,offset);
            }
            SHA256_Update(&hash,file_buffer,(size_t)count);
            offset+=count;
        }
        SHA256_Final(digest,&hash);
        vnode_put(file);
        if(memcmp(digest,ram_system_launchd_sha256,sizeof(digest))) panic("RAM_SYSTEM launchd SHA256 mismatch");
        fuxi_ram_system_user_entry=1;
        fuxi_ram_system_stage=7;
        printf("RAM_SYSTEM user entry armed: /mnt1/sbin/launchd %lld bytes SHA256=OK\n",bytes);
        return;
    }
#endif
    panic("RAM_SYSTEM PASS\nOS/SystemOS actual RAM SHA256=OK\nAPFS readonly=1\n/mnt1 and /mnt2 mounted\nuserland not started; partition IO absent");
}
