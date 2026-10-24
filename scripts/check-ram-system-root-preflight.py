#!/usr/bin/env python3
"""Exercise the real preflight C and bind its stop-before-userland archive."""
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
typedef struct mount { uint64_t flags; struct { char f_fstypename[8]; } stat; } *mount_t;
typedef struct vnode { mount_t mount; int type, isroot, refs; } *vnode_t;
static struct mount m[3];
static struct vnode nodes[3], children[2][4], foreign;
vnode_t rootvnode;
dev_t rootdev;
volatile unsigned fuxi_ram_root_stage, fuxi_ram_system_stage, fuxi_ram_system_user_entry;
static int profile_arg, present, pid, task, lookup_error, directory_error, bad_directory;
static int io[2], auth_result[2], mutate_root, mutate_flags, lookups;
static char message[1024];
static jmp_buf done;
static void panic(const char *fmt, ...) __attribute__((noreturn));
static void panic(const char *fmt, ...) { va_list ap;va_start(ap,fmt);vsnprintf(message,sizeof(message),fmt,ap);va_end(ap);longjmp(done,1); }
static void *kernel_task=(void *)1;
static void *current_task(void) { return task ? kernel_task : NULL; }
static void *current_proc(void) { return NULL; }
static int proc_getpid(void *p) { (void)p;return pid; }
static dev_t mdevlookup(int id) { assert(id==0); return 101; }
static int PE_parse_boot_argn(const char *s, unsigned *v, size_t n) { assert(!strcmp(s,"ram-system-root-preflight") && n==sizeof(*v));*v=profile_arg;return present; }
static void *vfs_context_kernel(void) { return (void *)2; }
static int vnode_lookup(const char *path, int flags, vnode_t *vp, void *ctx) {
 assert(flags==0 && ctx==vfs_context_kernel());
 if (!strcmp(path,"/")) { if(lookup_error==1)return EIO; *vp=&nodes[0]; }
 else if (!strcmp(path,"/mnt1") || !strcmp(path,"/mnt2")) { int id=path[4]-'0';if(lookup_error==id+1)return EIO;*vp=&nodes[id]; }
 else {
  int id=path[4]-'1';assert(id>=0 && id<2);
  const char *names[]={"dev","private/var","System/Volumes/Restore","System/Volumes/BaseSystem"};
  int k=0;for(;k<4;k++)if(!strcmp(path+6,names[k]))break;assert(k<4);lookups++;
  if(directory_error && id==0 && k==1)return directory_error;
  *vp=&children[id][k];
  if(bad_directory==1 && id==0 && k==0)(*vp)->type=1;
  if(bad_directory==2 && id==0 && k==0)(*vp)->mount=&m[0];
 }
 (*vp)->refs++;return 0;
}
static mount_t vnode_mount(vnode_t vp) { return vp->mount; }
static uint64_t vfs_flags(mount_t mp) { return mp->flags; }
#define vfs_statfs(mp) (&(mp)->stat)
static int vnode_vtype(vnode_t vp) { return vp->type; }
static int vnode_isvroot(vnode_t vp) { return vp->isroot; }
static void vnode_put(vnode_t vp) { assert(vp->refs>0);vp->refs--; }
static int VNOP_IOCTL(vnode_t vp, unsigned long cmd, void *data, int flags, void *ctx) {
 assert(cmd==FSIOC_KERNEL_ROOTAUTH && data==NULL && flags==0 && ctx==vfs_context_kernel());
 int id=vp==&nodes[1]?0:vp==&nodes[2]?1:-1;assert(id>=0);
 assert(lookups==8 && io[id]==0);io[id]++;
 if(mutate_root)rootvnode=&foreign;
 if(mutate_flags)m[id+1].flags|=0x10000000;
 return auth_result[id];
}
static void reset(void) {
 memset(m,0,sizeof(m));memset(nodes,0,sizeof(nodes));memset(children,0,sizeof(children));
 for(int i=0;i<3;i++){strcpy(m[i].stat.f_fstypename,"apfs");m[i].flags=1|(i==0?0x4000:0);nodes[i]=(struct vnode){&m[i],VDIR,1,0};}
 for(int i=0;i<2;i++)for(int j=0;j<4;j++)children[i][j]=(struct vnode){&m[i+1],VDIR,0,0};
 rootvnode=&nodes[0];rootdev=101;fuxi_ram_root_stage=7;fuxi_ram_system_stage=6;fuxi_ram_system_user_entry=0;
 profile_arg=present=task=1;pid=lookup_error=directory_error=bad_directory=mutate_root=mutate_flags=lookups=0;
 memset(io,0,sizeof(io));auth_result[0]=80;auth_result[1]=0;message[0]=0;
}
'''

TESTS = r'''
static int run(unsigned profile) { if(!setjmp(done))fuxi_ram_system_root_preflight(profile);return 0; }
int main(void) {
 int cases=0;
 for(int which=0;which<23;which++) {
  reset();unsigned profile=1;
  switch(which) {
   case 0:profile=2;break;case 1:profile=3;break;case 2:profile_arg=0;break;
   case 3:present=0;break;case 4:task=0;break;case 5:pid=1;break;
   case 6:fuxi_ram_root_stage=6;break;case 7:fuxi_ram_system_stage=5;break;
   case 8:fuxi_ram_system_user_entry=1;break;case 9:lookup_error=1;break;
   case 10:rootdev=102;break;case 11:rootvnode=&foreign;break;
   case 12:m[0].flags=1;break;case 13:m[0].flags=0x4000;break;
   case 14:lookup_error=2;break;case 15:lookup_error=3;break;
   case 16:nodes[1].mount=&m[0];break;case 17:nodes[2].mount=&m[1];break;
   case 18:nodes[1].type=1;break;case 19:nodes[2].isroot=0;break;
   case 20:m[1].flags=0;break;case 21:m[2].flags|=0x4000;break;
   case 22:strcpy(m[2].stat.f_fstypename,"hfs");break;
  }
  run(profile);assert(strstr(message,"refused") && io[0]==0 && io[1]==0);cases++;
 }
 for(int which=0;which<7;which++) {
  reset();
  if(which==1){auth_result[0]=0;auth_result[1]=80;}
  if(which==2){auth_result[0]=22;auth_result[1]=22;}
  if(which==3)directory_error=ENOENT;
  if(which==4)bad_directory=1;
  if(which==5)bad_directory=2;
  if(which==6){mutate_root=1;mutate_flags=1;}
  run(1);assert(strstr(message,"RAM_ROOT_PREFLIGHT observed") && strstr(message,"userland=0"));
  assert(io[0]==1 && io[1]==1 && fuxi_ram_system_user_entry==0);
  assert(nodes[0].refs==0 && nodes[1].refs==0 && nodes[2].refs==0);
  for(int i=0;i<2;i++)for(int j=0;j<4;j++)assert(children[i][j].refs==0);
  assert(strstr(message,"NO root switch"));
  if(which==0)assert(strstr(message,"OS auth=80 flags=1/1") && strstr(message,"SystemOS auth=0 flags=1/1"));
  if(which==1)assert(strstr(message,"OS auth=0 flags=1/1") && strstr(message,"SystemOS auth=80 flags=1/1"));
  if(which==2)assert(strstr(message,"OS auth=22") && strstr(message,"SystemOS auth=22"));
  if(which==3)assert(strstr(message,"dirs dev=0 var=2 restore=0 base=0"));
  if(which==4)assert(strstr(message,"dirs dev=20 var=0"));
  if(which==5)assert(strstr(message,"dirs dev=18 var=0"));
  if(which==6)assert(strstr(message,"unchanged=0") && strstr(message,"flags=1/10000001"));
  else assert(strstr(message,"unchanged=1"));
  cases++;
 }
 reset();const char *path="/sbin/launchd";
 if(!setjmp(done))fuxi_ram_system_user_init_path(&path);
 assert(strstr(message,"mount-only init reached") && !strcmp(path,"/sbin/launchd") && !io[0] && !io[1]);cases++;
 printf("PASS: %d real preflight C cases\n",cases);return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--base', type=Path, required=True)
    args = p.parse_args()
    out = args.output.resolve()
    device = Path(__file__).resolve().parents[1]
    source = device / 'package/RamSystemRootPreflight.c'
    body = '\n'.join(x for x in source.read_text().splitlines() if not x.startswith('#include'))
    system = device/'package/RamSystemProbe.c'
    text = system.read_text()
    start = text.index('int fuxi_ram_system_user_init_path(const char **path)\n{')
    end = text.index('\n}\n',start)+3
    init_guard = '\n#define RAM_SYSTEM_ALLOW_USER_ENTRY 0\n'+text[start:end]+'\n'
    with tempfile.TemporaryDirectory(prefix='root-preflight-') as tmp:
        tmp = Path(tmp)
        test = tmp / 'test.c'
        test.write_text(API + body + init_guard + TESTS)
        command = ['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', str(test), '-o', str(tmp/'test')]
        subprocess.run(command, check=True)
        result = subprocess.check_output([str(tmp/'test')], text=True).strip()
        # These mutations must be rejected by the same real-C behavior checks.
        mutations = [body.replace('profile != 1', '(profile != 1 && profile != 3)'),
                     body.replace('auth[i] = VNOP_IOCTL(', 'auth[i] = 0 * VNOP_IOCTL(')]
        for mutated in mutations:
            assert mutated != body
            test.write_text(API + mutated + init_guard + TESTS)
            subprocess.run(command, check=True)
            failure = subprocess.run([str(tmp/'test')], capture_output=True, text=True)
            assert failure.returncode != 0, 'preflight guard/result mutation was not detected'
    board = json.loads((out/'board-effective.json').read_text())
    pins = json.loads((out/'pins.json').read_text())
    validate_stage_policy(pins, True, False, False)
    assert pins['system_root_preflight'] is True and pins['system_userland_allowed'] is False
    assert pins.get('observe_launch_constraints', False) is False
    archives = [shlex.split(s) for s in board['kernel']['archives'] if shlex.split(s)[0]=='build-ram-root-probe.py']
    assert len(archives)==1
    entry = archives[0]
    assert set(entry[1:]) == {'--user-entry','--system-objects','--system-root-preflight','--pins',str(out/'pins.json')}
    args = board['payload']['kernel_args']
    for prefix, expected in [('ram-root-probe=', ['ram-root-probe=2']),('ram-system-probe=', ['ram-system-probe=1']),
                             ('ram-system-root-preflight=', ['ram-system-root-preflight=1']),('ram-launch-observe=', [])]:
        assert [s for s in args if s.startswith(prefix)] == expected
    assert args.count('rd=md0')==1
    config = (out/'config').read_text().splitlines()
    for key, value in [('XNU_RAM_ROOT_PROBE','y'),('XNU_RAM_USER_ENTRY','y'),('XNU_RAM_MAIN_PROBE','n'),
                       ('XNU_RAM_PROGRESS','n'),('XNU_RAM_RESTORE_TRUST','n'),('XNU_RAM_LAUNCH_OBSERVE','n')]:
        assert [s for s in config if s.startswith('CONFIG_'+key+'=')] == ['CONFIG_'+key+'='+value]
    tree = out/'work/tree'
    build = tree/'build/ram-root-probe'
    record = json.loads((build/'build-report.json').read_text())
    assert record['pins']==pins and record['system_root_preflight'] is True
    assert record['user_entry'] is True and record['system_objects'] is True
    for key in ('main_entry','progress','system_user_entry','launch_constraints_observer','restore_trust'):
        assert record[key] is False
    assert sha(source)==record['preflight_source_sha256']==sha(tree/'kits/sheng'/source.name)
    assert sha(system)==record['system_source_sha256']==sha(tree/'kits/sheng'/system.name)
    assert sha(build/'libRamRootProbe.a')==record['archive_sha256']
    header = (build/'RamSystemProbePins.h').read_text()
    assert '#define RAM_SYSTEM_ROOT_PREFLIGHT 1' in header and '#define RAM_SYSTEM_ALLOW_USER_ENTRY 0' in header
    text = system.read_text()
    assert text.index('fuxi_ram_system_stage=6;') < text.index('fuxi_ram_system_root_preflight(enabled);') < text.index('if (enabled==2)')
    symbols = subprocess.check_output([llvm_tool('llvm-nm'),str(build/'RamSystemProbe.o')],text=True)
    assert '_fuxi_ram_system_root_preflight' in symbols and '_fuxi_ram_system_user_init_path' in symbols
    disassembly = subprocess.check_output([llvm_tool('llvm-objdump'), '--macho', '--disassemble',
        '--no-show-raw-insn', '--symbolize-operands', str(build/'RamSystemProbe.o')], text=True)
    assert '_fuxi_ram_system_root_preflight' in disassembly
    members = subprocess.check_output(['xcrun','ar','t',str(build/'libRamRootProbe.a')],text=True).splitlines()
    assert members==['__.SYMDEF SORTED','RamRootProbe.o','RamSystemProbe.o','RamSystemRootPreflight.o']
    symbols = subprocess.check_output([llvm_tool('llvm-nm'),str(build/'RamSystemRootPreflight.o')],text=True)
    assert '_VNOP_IOCTL' in symbols and '_vfs_flags' in symbols and '_vfs_switch_root' not in symbols
    compile_command = json.loads((build/'preflight-compile-command.json').read_text())
    assert str(tree/'kits/sheng'/source.name) in compile_command and '-Werror' in compile_command
    objdir = tree/'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
    assert record['archive_sha256'] in (objdir/'.native-aes-link-input').read_text()
    init_evidence = inspect_compiled_init(out)
    native_objects = {}
    for relative, defines in [
            ('bsd/DEVELOPMENT/bsd_init.o', {'XNU_RAM_ROOT_PROBE':1,'XNU_RAM_USER_ENTRY':1}),
            ('bsd/DEVELOPMENT/systemcalls.o', {'XNU_RAM_MAIN_PROBE':0}),
            ('osfmk/DEVELOPMENT/sleh.o', {'XNU_RAM_USER_ENTRY':1,'XNU_RAM_MAIN_PROBE':0}),
            ('security/DEVELOPMENT/mac_process.o', {'XNU_RAM_LAUNCH_OBSERVE':0}),
            ('bsd/DEVELOPMENT/kpi_vfs.o', {'XNU_RAM_LAUNCH_OBSERVE':0}),
            ('bsd/DEVELOPMENT/kern_sig.o', {'XNU_RAM_LAUNCH_OBSERVE':0})]:
        obj = objdir/relative
        arguments = json.loads(obj.with_suffix('.o.json').read_text().strip().rstrip(','))['arguments']
        for name, value in defines.items():
            expected = name+'='+str(value)
            assert arguments.count(expected)+arguments.count('-D'+expected)==1, (relative,expected)
        native_objects[relative] = dict(object_sha256=sha(obj),defines=defines)
    root = tree/'.cache/xnu-work'
    fsctl = (root/'bsd/sys/fsctl.h').read_text()
    assert re.search(r"#define\s+FSIOC_KERNEL_ROOTAUTH\s+_IOW\('h',\s*51,\s*uint32_t\)", fsctl)
    bsd = (root/'bsd/kern/bsd_init.c').read_text()
    assert 'VNOP_IOCTL(rootvnode, FSIOC_KERNEL_ROOTAUTH, NULL, 0, vfs_context_kernel())' in bsd
    plan = json.loads((out/'work/kernel/kernel.json').read_text())
    parent = json.loads((out/'parent.json').read_text())
    parent_plan = json.loads((Path(parent['path'])/'work/kernel/kernel.json').read_text())
    for module in ('com.apple.filesystems.apfs','com.apple.driver.AppleMobileFileIntegrity','com.apple.iokit.IOStorageFamily'):
        assert plan['original_executable_sha256'][module] == parent_plan['original_executable_sha256'][module]
    kernel = out/'work/kernel/kernel.macho'
    payload = json.loads((out/'payload-verification.json').read_text())
    assert sha(kernel)==payload['kernel_sha256'] and b'RAM_ROOT_PREFLIGHT observed' in kernel.read_bytes()
    report = dict(passed=[result],mutation_checks=2,sanitizers=['address','undefined'],
                  system_userland_allowed=False,root_switch_performed=False,archive_members=members,
                  boot_sha256=sha(out/'Mu-fuxi-usb-ram.img'),kernel_sha256=sha(kernel),
                  preflight_source_sha256=sha(source),archive_sha256=record['archive_sha256'],
                  preflight_object_sha256=sha(build/'RamSystemRootPreflight.o'),
                  system_object_sha256=sha(build/'RamSystemProbe.o'),
                  native_objects=native_objects,
                  original_executable_sha256=plan['original_executable_sha256'],
                  native_request='FSIOC_KERNEL_ROOTAUTH=0x80046833; data=NULL; fflag=0; kernel context',
                  hardware_result='pending',**init_evidence)
    (out/'root-preflight-tests.json').write_text(json.dumps(report,indent=2)+'\n')
    print(result)
    print('PASS: preflight stop, original return preservation, archive/init/image bindings and mutation checks')


if __name__=='__main__':
    main()
