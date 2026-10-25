#!/usr/bin/env python3
"""Exercise the real root-switch C and bind its pivot-stop archive."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
from ram_probe_policy import inspect_compiled_init, llvm_tool, validate_stage_policy

sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
LAUNCHD_BYTES = 561856
LAUNCHD_PIN = hashlib.sha256(bytes([0]) * LAUNCHD_BYTES).hexdigest()
SYSTEM_LAUNCHD_SHA = '8cec9a3c44a443b08e977ccc5d577850316ad428c575dc01f09b018dbd5b6a25'

API = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdarg.h>
#include <setjmp.h>
#include <sys/types.h>
#include <errno.h>
#define NULLVP NULL
#define MNT_RDONLY 1
#define MNT_ROOTFS 0x4000
#define FSIOC_KERNEL_ROOTAUTH 0x80046833UL
#define VDIR 2
#define VREG 1
#define UIO_READ 1
#define UIO_SYSSPACE 1
#define RAM_SYSTEM_LAUNCHD_BYTES UINT64_C(@LAUNCHD@)
static const uint8_t ram_system_launchd_sha256[32] = {@PIN@};
typedef void *vfs_context_t;
/* Compact SHA-256 so the model's launchd hashing is real, not faked. */
typedef struct { uint32_t h[8]; uint64_t bits; uint8_t buf[64]; size_t len; } SHA256_CTX;
static const uint32_t K256[64] = {
0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
static uint32_t ror(uint32_t x,int n){return (x>>n)|(x<<(32-n));}
static void sha256_block(SHA256_CTX *c,const uint8_t *p) {
 uint32_t w[64];
 for(int i=0;i<16;i++)w[i]=((uint32_t)p[i*4]<<24)|((uint32_t)p[i*4+1]<<16)|((uint32_t)p[i*4+2]<<8)|(uint32_t)p[i*4+3];
 for(int i=16;i<64;i++){uint32_t s0=ror(w[i-15],7)^ror(w[i-15],18)^(w[i-15]>>3),s1=ror(w[i-2],17)^ror(w[i-2],19)^(w[i-2]>>10);w[i]=w[i-16]+s0+w[i-7]+s1;}
 uint32_t a=c->h[0],b=c->h[1],d=c->h[3],e=c->h[4],f=c->h[5],g=c->h[6],h=c->h[7],cc=c->h[2];
 for(int i=0;i<64;i++){
  uint32_t S1=ror(e,6)^ror(e,11)^ror(e,25),ch=(e&f)^((~e)&g),t1=h+S1+ch+K256[i]+w[i];
  uint32_t S0=ror(a,2)^ror(a,13)^ror(a,22),mj=(a&b)^(a&cc)^(b&cc),t2=S0+mj;
  h=g;g=f;f=e;e=d+t1;d=cc;cc=b;b=a;a=t1+t2;}
 c->h[0]+=a;c->h[1]+=b;c->h[2]+=cc;c->h[3]+=d;c->h[4]+=e;c->h[5]+=f;c->h[6]+=g;c->h[7]+=h;
}
static void SHA256_Init(SHA256_CTX *c) {
 c->h[0]=0x6a09e667;c->h[1]=0xbb67ae85;c->h[2]=0x3c6ef372;c->h[3]=0xa54ff53a;
 c->h[4]=0x510e527f;c->h[5]=0x9b05688c;c->h[6]=0x1f83d9ab;c->h[7]=0x5be0cd19;c->bits=0;c->len=0;
}
static void SHA256_Update(SHA256_CTX *c,const void *data,size_t n) {
 const uint8_t *p=data;
 c->bits+=n*8;
 while(n){size_t take=64-c->len;if(take>n)take=n;memcpy(c->buf+c->len,p,take);c->len+=take;p+=take;n-=take;
  if(c->len==64){sha256_block(c,c->buf);c->len=0;}}
}
static void SHA256_Final(uint8_t *out,SHA256_CTX *c) {
 uint64_t bits=c->bits;
 uint8_t pad=0x80;c->buf[c->len++]=0x80;
 while(c->len!=56){if(c->len==64){sha256_block(c,c->buf);c->len=0;}if(c->len==56)break;c->buf[c->len++]=0;}
 (void)pad;
 uint8_t tail[8];for(int i=0;i<8;i++)tail[i]=(bits>>(56-8*i))&0xff;
 memcpy(c->buf+56,tail,8);sha256_block(c,c->buf);
 for(int i=0;i<8;i++){out[i*4]=c->h[i]>>24;out[i*4+1]=c->h[i]>>16;out[i*4+2]=c->h[i]>>8;out[i*4+3]=c->h[i];}
}
typedef struct mount { uint64_t flags; struct { char f_fstypename[8]; } stat; } *mount_t;
typedef struct vnode { mount_t mount; int type, isroot, refs; } *vnode_t;
vnode_t rootvnode;
dev_t rootdev;
volatile unsigned fuxi_ram_root_stage, fuxi_ram_system_stage, fuxi_ram_system_user_entry;
volatile unsigned fuxi_ram_native_root_entry;
volatile int fuxi_ram_switch_auth = -1;
volatile unsigned long long fuxi_ram_switch_flags = 0;
static int profile_arg, present, task, pid, lookup_error;
static int switches, io_count, auth_result, switch_errno, place_errno, dev_errno;
static int wrong_size, wrong_hash, post_wrong_hash, pivoted, lookups;
static int no_incoming_rootfs, keep_outgoing_rootfs;
static char message[1024];
static jmp_buf done;
static void panic(const char *fmt, ...) __attribute__((noreturn));
static void panic(const char *fmt, ...) { va_list ap;va_start(ap,fmt);vsnprintf(message,sizeof(message),fmt,ap);va_end(ap);longjmp(done,1); }
static void *kernel_task=(void *)1;
static void *current_task(void) { return task ? kernel_task : NULL; }
static void *current_proc(void) { return NULL; }
static int proc_getpid(void *p) { (void)p;return pid; }
static dev_t mdevlookup(int id) { assert(id==0); return 101; }
static int PE_parse_boot_argn(const char *s, unsigned *v, size_t n) { assert(!strcmp(s,"ram-system-root-switch") && n==sizeof(*v));*v=profile_arg;return present; }
static void *vfs_context_kernel(void) { return (void *)2; }
static void *vfs_context_ucred(void *c) { assert(c==vfs_context_kernel()); return (void *)3; }
static void *vfs_context_proc(void *c) { assert(c==vfs_context_kernel()); return NULL; }
static struct mount m[3], m_dev, m_foreign;
static struct vnode nodes[3], dev_dir, place_dir, launchd, placed, devnode, foreign;
static mount_t vnode_mount(vnode_t vp) { return vp->mount; }
static uint64_t vfs_flags(mount_t mp) { return mp->flags; }
#define vfs_statfs(mp) (&(mp)->stat)
static int vnode_vtype(vnode_t vp) { return vp->type; }
static int vnode_isvroot(vnode_t vp) { return vp->isroot; }
static void vnode_put(vnode_t vp) { assert(vp && vp->refs>0);vp->refs--; }
static int vnode_lookup(const char *path, int flags, vnode_t *vp, void *ctx) {
 assert(flags==0 && ctx==vfs_context_kernel());lookups++;
 if (!strcmp(path,"/")) { if(lookup_error==1)return EIO; *vp=pivoted?&nodes[1]:&nodes[0]; }
 else if (!strcmp(path,"/mnt1") || !strcmp(path,"/mnt2")) {
  if (pivoted) return ENOENT;
  int id=path[4]-'0';if(lookup_error==id+1)return EIO;*vp=&nodes[id];
 }
 else if (!strcmp(path,"/mnt1/dev")) { if(lookup_error==4)return EIO;*vp=&dev_dir; }
 else if (!strcmp(path,"/mnt1/Developer")) { if(place_errno)return place_errno;*vp=&place_dir; }
 else if (!strcmp(path,"/mnt1/sbin/launchd")) { if(lookup_error==5)return EIO;*vp=&launchd; }
 else if (!strcmp(path,"/Developer") && pivoted) { *vp=&placed; }
 else if (!strcmp(path,"/dev") && pivoted) { if(dev_errno)return dev_errno;*vp=&devnode; }
 else if (!strcmp(path,"/sbin/launchd") && pivoted) { *vp=&launchd; }
 else assert(!"unexpected lookup path");
 (*vp)->refs++;return 0;
}
static int vnode_size(vnode_t vp, off_t *bytes, void *ctx) {
 assert(ctx==vfs_context_kernel() && vp==&launchd);
 *bytes=wrong_size?(off_t)RAM_SYSTEM_LAUNCHD_BYTES+1:(off_t)RAM_SYSTEM_LAUNCHD_BYTES;return 0;
}
static int vn_rdwr(int rw, vnode_t vp, char *buf, int count, off_t offset, int space,
 int unknown, void *cred, int *residual, void *proc) {
 assert(rw==UIO_READ && vp==&launchd && space==UIO_SYSSPACE && !unknown && cred==(void *)3 && !proc);
 assert(count>0 && offset>=0 && offset+count<=(off_t)RAM_SYSTEM_LAUNCHD_BYTES);
 int bad=wrong_hash || (pivoted && post_wrong_hash);
 memset(buf, bad?0xAB:0, (size_t)count);*residual=0;return 0;
}
static int VNOP_IOCTL(vnode_t vp, unsigned long cmd, void *data, int flags, void *ctx) {
 assert(cmd==FSIOC_KERNEL_ROOTAUTH && data==NULL && flags==0 && ctx==vfs_context_kernel());
 assert(pivoted && vp==rootvnode && vp==&nodes[1] && io_count==0);io_count++;
 return auth_result;
}
static int vfs_switch_root(const char *in, const char *out, uint32_t flags) {
 assert(!strcmp(in,"/mnt1") && !strcmp(out,"Developer") && flags==0 && !pivoted);switches++;
 if (switch_errno) return switch_errno;
 pivoted=1;rootvnode=&nodes[1];
 if(!no_incoming_rootfs)m[1].flags|=MNT_ROOTFS;
 if(!keep_outgoing_rootfs)m[0].flags&=~(uint64_t)MNT_ROOTFS;
 return 0;
}
#define RAM_SYSTEM_LAUNCHD_BYTES UINT64_C(@LAUNCHD@)
static void reset(void) {
 memset(m,0,sizeof(m));memset(nodes,0,sizeof(nodes));memset(&m_dev,0,sizeof(m_dev));
 memset(&m_foreign,0,sizeof(m_foreign));memset(&foreign,0,sizeof(foreign));
 strcpy(m[0].stat.f_fstypename,"apfs");m[0].flags=1|0x4000;
 strcpy(m[1].stat.f_fstypename,"apfs");m[1].flags=1;
 strcpy(m[2].stat.f_fstypename,"apfs");m[2].flags=1;
 strcpy(m_dev.stat.f_fstypename,"devfs");m_dev.flags=1;
 strcpy(m_foreign.stat.f_fstypename,"apfs");m_foreign.flags=1|0x4000;
 nodes[0]=(struct vnode){&m[0],VDIR,1,0};nodes[1]=(struct vnode){&m[1],VDIR,1,0};nodes[2]=(struct vnode){&m[2],VDIR,1,0};
 dev_dir=(struct vnode){&m[1],VDIR,0,0};place_dir=(struct vnode){&m[1],VDIR,0,0};
 launchd=(struct vnode){&m[1],VREG,0,0};placed=(struct vnode){&m[0],VDIR,0,0};devnode=(struct vnode){&m_dev,VDIR,0,0};
 foreign=(struct vnode){&m_foreign,VDIR,1,0};
 rootvnode=&nodes[0];rootdev=101;fuxi_ram_root_stage=7;fuxi_ram_system_stage=6;fuxi_ram_system_user_entry=0;
 fuxi_ram_native_root_entry=0;fuxi_ram_switch_auth=-1;fuxi_ram_switch_flags=0;
 profile_arg=present=task=1;pid=lookup_error=0;
 switches=io_count=0;auth_result=80;switch_errno=place_errno=dev_errno=0;
 wrong_size=wrong_hash=post_wrong_hash=pivoted=0;lookups=0;message[0]=0;
 no_incoming_rootfs=keep_outgoing_rootfs=0;
}
'''

TESTS = r'''
static int run(unsigned profile) { if(!setjmp(done))fuxi_ram_system_root_switch(profile);return 0; }
#define REFUSE (assert(strstr(message,"refused") && switches==0 && io_count==0))
int main(void) {
 int cases=0;
 for(int which=0;which<31;which++) {
  reset();unsigned profile=1;
  switch(which) {
   case 0:profile=2;break;case 1:profile=3;break;case 2:profile_arg=0;break;
   case 3:present=0;break;case 4:task=0;break;case 5:pid=1;break;
   case 6:fuxi_ram_root_stage=6;break;case 7:fuxi_ram_system_stage=5;break;
   case 8:fuxi_ram_system_user_entry=1;break;case 9:lookup_error=1;break;
   case 10:rootdev=102;break;case 11:rootvnode=&foreign;break;
   case 12:m[0].flags=1;break;case 13:m[0].flags=0x4000;break;
   case 14:strcpy(m[0].stat.f_fstypename,"hfs");break;
   case 15:lookup_error=2;break;case 16:lookup_error=3;break;
   case 17:nodes[1].mount=&m[0];break;case 18:nodes[2].mount=&m[1];break;
   case 19:nodes[1].type=1;break;case 20:nodes[2].isroot=0;break;
   case 21:m[1].flags=0;break;case 22:m[2].flags|=0x4000;break;
   case 23:strcpy(m[1].stat.f_fstypename,"hfs");break;
   case 24:place_errno=ENOENT;break;case 25:place_dir.mount=&m[0];break;case 26:place_dir.type=1;break;
   case 27:lookup_error=4;break;case 28:lookup_error=5;break;case 29:wrong_size=1;break;
   case 30:wrong_hash=1;break;
  }
  run(profile);REFUSE;cases++;
 }
 reset();switch_errno=22;run(1);
 assert(strstr(message,"pivot failed: errno=22") && switches==1 && io_count==0);cases++;
 for(int which=0;which<6;which++) {
  reset();switch(which) {
   case 0:no_incoming_rootfs=1;break;  /* model: switch forgets MNT_ROOTFS on incoming */
   case 1:keep_outgoing_rootfs=1;break; /* model: switch forgets to clear it on outgoing */
   case 2:placed.mount=&m[1];break;     /* model: /Developer lands on the wrong mount */
   case 3:dev_errno=ENOENT;break;
   case 4:strcpy(m_dev.stat.f_fstypename,"hfs");break;
   case 5:post_wrong_hash=1;break;
  }
  run(1);assert(strstr(message,"refused") && switches==1);cases++;
 }
 for(int which=0;which<@SUCCESSES@;which++) {
  reset();auth_result=which==1?0:which==2?22:80;
  run(1);@SUCCESS_ASSERTS@
  cases++;
 }
@INIT_CASE@
 printf("PASS: %d real root-switch C cases\n",cases);return 0;
}
'''


MOUNT_SUCCESS = r'''assert(strstr(message,"RAM_ROOT_SWITCH observed") && strstr(message,"userland=0"));
  assert(strstr(message,"pivot=OK old=md0 newroot=apfs"));
  assert(strstr(message,"launchd=/sbin/launchd @LAUNCHD@ bytes SHA256=OK"));
  char expected[32];snprintf(expected,sizeof(expected),"auth=%d",auth_result);
  assert(strstr(message,expected) && switches==1 && io_count==1);
  assert(rootvnode==&nodes[1] && (m[1].flags&0x4000) && !(m[0].flags&0x4000));
  assert(nodes[0].refs==0 && nodes[1].refs==0 && nodes[2].refs==0);
  assert(dev_dir.refs==0 && place_dir.refs==0 && launchd.refs==0 && placed.refs==0 && devnode.refs==0);
  assert(strstr(message,"outgoing=/Developer dev=devfs") && strstr(message,"rootfs=1"));'''

MOUNT_INIT = r'''reset();const char *path="/sbin/launchd";
 if(!setjmp(done))fuxi_ram_system_user_init_path(&path);
 assert(strstr(message,"mount-only init reached") && !strcmp(path,"/sbin/launchd") && !switches && !io_count);cases++;'''

USER_SUCCESS = r'''assert(message[0]==0 && switches==1 && io_count==1);
  assert(fuxi_ram_native_root_entry==1 && fuxi_ram_switch_auth==auth_result);
  assert(fuxi_ram_system_stage==7 && fuxi_ram_root_stage==7 && !fuxi_ram_system_user_entry);
  assert(rootvnode==&nodes[1] && (m[1].flags&0x4000) && !(m[0].flags&0x4000));
  assert(nodes[0].refs==0 && nodes[1].refs==0 && nodes[2].refs==0);
  assert(dev_dir.refs==0 && place_dir.refs==0 && launchd.refs==0 && placed.refs==0 && devnode.refs==0);'''

USER_INIT = r'''reset();const char *path="/sbin/launchd";
 int override=fuxi_ram_system_user_init_path(&path);
 assert(override==1 && !strcmp(path,"/sbin/launchd") && fuxi_ram_native_root_entry==0);cases++;
 fuxi_ram_system_user_entry=1;const char *second="/sbin/launchd";
 override=fuxi_ram_system_user_init_path(&second);
 assert(override==0 && !strcmp(second,"/mnt1/sbin/launchd"));cases++;'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--mode', choices=['mount', 'user-entry', 'progress'], default='mount',
                   help='mount: stop after verification; user-entry: arm the bounded native-root init; '
                        'progress: arm it with main-entry/progress follow mode')
    args = p.parse_args()
    out = args.output.resolve()
    device = Path(__file__).resolve().parents[1]
    source = device / 'package/RamSystemRootSwitch.c'
    body = '\n'.join(x for x in source.read_text().splitlines() if not x.startswith('#include'))
    system = device/'package/RamSystemProbe.c'
    text = system.read_text()
    start = text.index('int fuxi_ram_system_user_init_path(const char **path)\n{')
    end = text.index('\n}\n',start)+3
    init_guard = ('\n#define RAM_SYSTEM_ALLOW_USER_ENTRY 1\n' if args.mode != 'mount'
                  else '\n#define RAM_SYSTEM_ALLOW_USER_ENTRY 0\n') + text[start:end] + '\n'
    preamble = '#define RAM_SYSTEM_ALLOW_USER_ENTRY 1\n' if args.mode != 'mount' else ''
    pin_bytes = ','.join('0x%02x' % b for b in bytes.fromhex(LAUNCHD_PIN))
    api = API.replace('@LAUNCHD@', str(LAUNCHD_BYTES)).replace('@PIN@', pin_bytes)
    success = MOUNT_SUCCESS if args.mode == 'mount' else USER_SUCCESS
    init_case = MOUNT_INIT if args.mode == 'mount' else USER_INIT
    tests = (TESTS.replace('@LAUNCHD@', str(LAUNCHD_BYTES))
                  .replace('@SUCCESSES@', '3')
                  .replace('@SUCCESS_ASSERTS@', success.replace('@LAUNCHD@', str(LAUNCHD_BYTES)))
                  .replace('@INIT_CASE@', init_case))
    with tempfile.TemporaryDirectory(prefix='root-switch-') as tmp:
        tmp = Path(tmp)
        test = tmp / 'test.c'
        test.write_text(api + preamble + body + init_guard + tests)
        command = ['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', str(test), '-o', str(tmp/'test')]
        subprocess.run(command, check=True)
        result = subprocess.check_output([str(tmp/'test')], text=True).strip()
        # These mutations must be rejected by the same real-C behavior checks.
        mutations = [body.replace('profile != 1', '(profile != 1 && profile != 3)'),
                     body.replace('int pivot = vfs_switch_root(', 'int pivot = 0 * vfs_switch_root('),
                     body.replace('(flags & (MNT_ROOTFS | MNT_RDONLY)) != (MNT_ROOTFS | MNT_RDONLY) ||',
                                  '(flags & (MNT_ROOTFS | MNT_RDONLY)) != (MNT_ROOTFS | MNT_RDONLY) &&')]
        for mutated in mutations:
            assert mutated != body
            test.write_text(api + mutated + init_guard + tests)
            compile_result = subprocess.run(command, capture_output=True, text=True)
            if compile_result.returncode:
                continue  # a rejected compile is also a detected mutation
            failure = subprocess.run([str(tmp/'test')], capture_output=True, text=True)
            assert failure.returncode != 0, 'root-switch guard/pivot/identity mutation was not detected'
    board = json.loads((out/'board-effective.json').read_text())
    pins = json.loads((out/'pins.json').read_text())
    validate_stage_policy(pins, True, args.mode == 'progress', args.mode == 'progress')
    assert pins['system_root_switch'] is True and pins.get('system_root_preflight') is not True
    assert pins['system_userland_allowed'] is False
    assert pins.get('observe_launch_constraints', False) is False
    assert pins['system_file_path'] == '/mnt1/sbin/launchd' and pins['system_file_bytes'] == LAUNCHD_BYTES
    assert pins['system_file_sha256'] == SYSTEM_LAUNCHD_SHA
    if args.mode == 'progress':
        assert pins['main_entry_offset'] == 85896 and pins['main_entry_bytes'] == 'ff8305d1fc6f12a9f65713a9f44f14a9'
        assert pins['stop_before_first_user_svc'] is False and pins['continue_after_main'] is True
        assert pins['follow_process_creation'] is True and pins['block_process_creation'] is False
    archives = [shlex.split(s) for s in board['kernel']['archives'] if shlex.split(s)[0]=='build-ram-root-probe.py']
    assert len(archives)==1
    entry = archives[0]
    cache = pins.get('system_cache', False)
    if cache:
        assert args.mode == 'progress' and '--system-cache' in entry
        entry = [x for x in entry if x != '--system-cache']
    if args.mode == 'mount':
        assert set(entry[1:]) == {'--user-entry','--system-objects','--system-root-switch','--pins',str(out/'pins.json')}
    elif args.mode == 'user-entry':
        assert set(entry[1:]) == {'--user-entry','--system-objects','--system-user-entry','--system-root-switch','--pins',str(out/'pins.json')}
    else:
        assert set(entry[1:]) == {'--user-entry','--system-objects','--system-user-entry','--system-root-switch',
                                  '--main-entry','--progress','--pins',str(out/'pins.json')}
    args_list = board['payload']['kernel_args']
    for prefix, expected in [('ram-root-probe=', ['ram-root-probe=%d' % (4 if args.mode=='progress' else 2)]),
                             ('ram-system-probe=', ['ram-system-probe=1']),
                             ('ram-system-root-switch=', ['ram-system-root-switch=1']),
                             ('ram-system-root-preflight=', []),('ram-launch-observe=', [])]:
        assert [s for s in args_list if s.startswith(prefix)] == expected
    assert args_list.count('rd=md0')==1
    config = (out/'config').read_text().splitlines()
    for key, value in [('XNU_RAM_ROOT_PROBE','y'),('XNU_RAM_USER_ENTRY','y'),
                       ('XNU_RAM_MAIN_PROBE','y' if args.mode=='progress' else 'n'),
                       ('XNU_RAM_PROGRESS','y' if args.mode=='progress' else 'n'),
                       ('XNU_RAM_RESTORE_TRUST','n'),('XNU_RAM_LAUNCH_OBSERVE','n')]:
        assert [s for s in config if s.startswith('CONFIG_'+key+'=')] == ['CONFIG_'+key+'='+value]
    tree = out/'work/tree'
    build = tree/'build/ram-root-probe'
    record = json.loads((build/'build-report.json').read_text())
    assert record['pins']==pins and record['system_root_switch'] is True and record['system_root_preflight'] is False
    assert record['user_entry'] is True and record['system_objects'] is True
    assert record['system_user_entry'] is (args.mode != 'mount')
    assert record['main_entry'] is (args.mode == 'progress') and record['progress'] is (args.mode == 'progress')
    for key in ('launch_constraints_observer','restore_trust'):
        assert record[key] is False
    assert sha(source)==record['switch_source_sha256']==sha(tree/'kits/sheng'/source.name)
    assert sha(system)==record['system_source_sha256']==sha(tree/'kits/sheng'/system.name)
    assert sha(build/'libRamRootProbe.a')==record['archive_sha256']
    header = (build/'RamSystemProbePins.h').read_text()
    assert '#define RAM_SYSTEM_ROOT_SWITCH 1' in header
    assert ('#define RAM_SYSTEM_ALLOW_USER_ENTRY %d' % (0 if args.mode=='mount' else 1)) in header
    assert '#define RAM_SYSTEM_ROOT_PREFLIGHT 0' in header
    assert ('#define RAM_SYSTEM_LAUNCHD_BYTES UINT64_C(%d)' % LAUNCHD_BYTES) in header
    probe_text = system.read_text()
    assert probe_text.index('fuxi_ram_system_stage=6;') < probe_text.index('fuxi_ram_system_root_switch(enabled);') < probe_text.index('if (enabled==2)')
    symbols = subprocess.check_output([llvm_tool('llvm-nm'),str(build/'RamSystemProbe.o')],text=True)
    assert '_fuxi_ram_system_root_switch' in symbols and '_fuxi_ram_system_user_init_path' in symbols
    disassembly = subprocess.check_output([llvm_tool('llvm-objdump'), '--macho', '--disassemble',
        '--no-show-raw-insn', '--symbolize-operands', str(build/'RamSystemProbe.o')], text=True)
    assert '_fuxi_ram_system_root_switch' in disassembly
    members = subprocess.check_output(['xcrun','ar','t',str(build/'libRamRootProbe.a')],text=True).splitlines()
    expected_members = ['__.SYMDEF SORTED','RamRootProbe.o','RamSystemProbe.o','RamSystemRootSwitch.o']
    if cache: expected_members.append('RamSystemCache.o')
    if record['pins'].get('system_watchdog'):
        assert cache and args.mode == 'progress'
        expected_members += ['RamWatchdog.late.o','original-bindings.o']
    if args.mode == 'progress': expected_members.append('RamLaunchdProbe.o')
    assert members==expected_members
    symbols = subprocess.check_output([llvm_tool('llvm-nm'),str(build/'RamSystemRootSwitch.o')],text=True)
    assert '_vfs_switch_root' in symbols and '_VNOP_IOCTL' in symbols and '_vn_rdwr' in symbols
    root_symbols = subprocess.check_output([llvm_tool('llvm-nm'),str(build/'RamRootProbe.o')],text=True)
    if args.mode != 'mount':
        assert '_fuxi_ram_native_root_entry' in symbols      # referenced by the switch probe
        defined = [l for l in root_symbols.splitlines() if l.endswith('_fuxi_ram_native_root_entry')]
        assert defined and not defined[0].startswith('U')    # defined by RamRootProbe.o, not imported
        assert '_fuxi_ram_switch_auth' in root_symbols and '_fuxi_ram_switch_flags' in root_symbols
    else:
        assert '_fuxi_ram_native_root_entry' not in symbols  # mount-only never arms the entry
    compile_command = json.loads((build/'switch-compile-command.json').read_text())
    assert str(tree/'kits/sheng'/source.name) in compile_command and '-Werror' in compile_command
    objdir = tree/'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
    assert record['archive_sha256'] in (objdir/'.native-aes-link-input').read_text()
    init_evidence = inspect_compiled_init(out)
    native_objects = {}
    main_flag = 1 if args.mode == 'progress' else 0
    for relative, defines in [
            ('bsd/DEVELOPMENT/bsd_init.o', {'XNU_RAM_ROOT_PROBE':1,'XNU_RAM_USER_ENTRY':1}),
            ('bsd/DEVELOPMENT/systemcalls.o', {'XNU_RAM_MAIN_PROBE':main_flag}),
            ('osfmk/DEVELOPMENT/sleh.o', {'XNU_RAM_USER_ENTRY':1,'XNU_RAM_MAIN_PROBE':main_flag}),
            ('security/DEVELOPMENT/mac_process.o', {'XNU_RAM_LAUNCH_OBSERVE':0}),
            ('bsd/DEVELOPMENT/kpi_vfs.o', {'XNU_RAM_LAUNCH_OBSERVE':0}),
            ('bsd/DEVELOPMENT/kern_sig.o', {'XNU_RAM_LAUNCH_OBSERVE':0})]:
        obj = objdir/relative
        arguments = json.loads(obj.with_suffix('.o.json').read_text().strip().rstrip(','))['arguments']
        for name, value in defines.items():
            expected_define = name+'='+str(value)
            assert arguments.count(expected_define)+arguments.count('-D'+expected_define)==1, (relative,expected_define)
        native_objects[relative] = dict(object_sha256=sha(obj),defines=defines)
    root = tree/'.cache/xnu-work'
    mount_internal = (root/'bsd/sys/mount_internal.h').read_text()
    assert re.search(r"typedef\s+uint32_t\s+vfs_switch_root_flags_t;", mount_internal)
    assert re.search(r"int\s+vfs_switch_root\(const char \*, const char \*, vfs_switch_root_flags_t\);", mount_internal)
    subr = (root/'bsd/vfs/vfs_subr.c').read_text()
    assert 'incoming->mnt_flag |= MNT_ROOTFS;' in subr
    assert 'outgoing->mnt_flag &= ~MNT_ROOTFS;' in subr
    assert 'set_rootvnode(incoming_rootvnode);' in subr
    fsctl = (root/'bsd/sys/fsctl.h').read_text()
    assert re.search(r"#define\s+FSIOC_KERNEL_ROOTAUTH\s+_IOW\('h',\s*51,\s+uint32_t\)", fsctl)
    plan = json.loads((out/'work/kernel/kernel.json').read_text())
    parent = json.loads((out/'parent.json').read_text())
    parent_plan = json.loads((Path(parent['path'])/'work/kernel/kernel.json').read_text())
    for module in ('com.apple.filesystems.apfs','com.apple.driver.AppleMobileFileIntegrity','com.apple.iokit.IOStorageFamily'):
        assert plan['original_executable_sha256'][module] == parent_plan['original_executable_sha256'][module]
    kernel = out/'work/kernel/kernel.macho'
    payload = json.loads((out/'payload-verification.json').read_text())
    assert sha(kernel)==payload['kernel_sha256']
    if args.mode == 'mount':
        assert b'RAM_ROOT_SWITCH observed' in kernel.read_bytes()
    else:
        kernel_bytes = kernel.read_bytes()
        assert b'RAM_NATIVE_ROOT_USER PASS' in kernel_bytes and b'native root entry armed' in kernel_bytes
        assert b'RAM_ROOT_SWITCH observed' not in kernel_bytes
        if args.mode == 'progress':
            assert b'RAM_PROGRESS stage=' in kernel_bytes
            assert b'root=switched OS-volume apfs readonly rootfs=1' in kernel_bytes
            assert b'RAM_FOLLOW blocked syscall' in kernel_bytes
    report = dict(passed=[result],mutation_checks=3,sanitizers=['address','undefined'],
                  mode=args.mode,
                  system_userland_allowed=False,archive_members=members,
                  boot_sha256=sha(out/'Mu-fuxi-usb-ram.img'),kernel_sha256=sha(kernel),
                  switch_source_sha256=sha(source),archive_sha256=record['archive_sha256'],
                  switch_object_sha256=sha(build/'RamSystemRootSwitch.o'),
                  system_object_sha256=sha(build/'RamSystemProbe.o'),
                  native_objects=native_objects,
                  original_executable_sha256=plan['original_executable_sha256'],
                  native_request='vfs_switch_root("/mnt1","Developer",0) then FSIOC_KERNEL_ROOTAUTH on the switched root',
                  hardware_result='pending',**init_evidence)
    (out/'root-switch-tests.json').write_text(json.dumps(report,indent=2)+'\n')
    print(result)
    print('PASS: root-switch pivot, post-switch verification, archive/init/image bindings and mutation checks')


if __name__=='__main__':
    main()
