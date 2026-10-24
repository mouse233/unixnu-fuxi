// Read-only OS/SystemOS mount checkpoint; never starts userland.
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

extern unsigned long gPhysBase, gPhysSize;
extern dev_t fuxi_mdev_add_ro(int, uint64_t, uint64_t);
extern int mdevgetrange(int, uint64_t *, uint64_t *);
extern int fuxi_ram_mount_ro(const char *, const char *);
extern int fuxi_ram_system_publish(unsigned, uint64_t, uint64_t) __attribute__((weak_import));
void fuxi_ram_system_probe(void) __attribute__((noreturn));
volatile unsigned fuxi_ram_system_stage;

void fuxi_ram_system_probe(void)
{
    unsigned enabled=0, length=0;
    DTEntry entry;
    const void *value=NULL;
    UR_HANDOFF objects;
    if (!PE_parse_boot_argn("ram-system-probe",&enabled,sizeof(enabled)) || enabled!=1)
        panic("RAM_SYSTEM refused: explicit mount-only profile required");
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
    panic("RAM_SYSTEM PASS\nOS/SystemOS actual RAM SHA256=OK\nAPFS readonly=1\n/mnt1 and /mnt2 mounted\nuserland not started; partition IO absent");
}
