#!/usr/bin/env python3
"""Exercise actual observer C and patched XNU wrappers; preserve calls/results."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def braced(source, marker):
    start = source.index(marker)
    opening = source.index('{', start)
    at, depth = opening + 1, 1
    while depth:
        depth += (source[at] == '{') - (source[at] == '}')
        at += 1
    return source[start:at]


API = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
typedef void *thread_t;
typedef struct fake_proc {int pid;} *proc_t;
typedef struct fake_mount {uint64_t mnt_flag;} *mount_t;
typedef struct fake_vnode {mount_t mount;int (*v_op[1])(void *);int ready;} *vnode_t;
typedef void *vfs_context_t;
typedef int errno_t;
#define MNT_ROOTFS 0x4000
#define MNT_CMDFLAGS 0xf
#define MNT_VISFLAGMASK 0xfffff0
#define ENOTTY 25
#define DKIOCISSOLIDSTATE 0x1234
#define DTRACE_FSINFO(...) ((void)0)
volatile unsigned fuxi_ram_root_stage=7,fuxi_ram_system_user_entry=1;
static struct fake_proc process={1};
static struct fake_mount target_fs={0x100001},foreign_fs={0};
static struct fake_vnode target_node,foreign_node;
static thread_t test_thread=(void *)1;
static unsigned boot_enabled=1,boot_profile=2,boot_present=1;
static int mount_reads,dispatches,dispatch_error,context_reads;
static int is64,solid;
static vnode_t rootvp;
static int rootvp_is_ssd;
static thread_t current_thread(void) {return test_thread;}
static proc_t current_proc(void) {return &process;}
static int proc_getpid(proc_t p) {if(!p) abort();return p->pid;}
static mount_t vnode_mount(vnode_t v) {mount_reads++;return v->mount;}
static int PE_parse_boot_argn(const char *key,void *out,size_t bytes) {
    if(bytes!=sizeof(unsigned)) abort();
    if(!strcmp(key,"ram-launch-observe")) *(unsigned *)out=boot_enabled;
    else if(!strcmp(key,"ram-system-probe")) *(unsigned *)out=boot_profile;
    else abort();
    return boot_present;
}
struct {int vdesc_offset;} vnop_ioctl_desc={0};
struct vnop_ioctl_args {void *a_desc;vnode_t a_vp;u_long a_command;caddr_t a_data;int a_fflag;vfs_context_t a_context;};
static vfs_context_t vfs_context_current(void) {context_reads++;return (void *)2;}
static int vfs_context_is64bit(vfs_context_t ctx) {if(!ctx) abort();return is64;}
static int vnode_ischr(vnode_t v) {(void)v;return 0;}
static int vnode_isblk(vnode_t v) {(void)v;return 0;}
static int vnode_vfs64bitready(vnode_t v) {return v->ready;}
static int dispatch(void *opaque) {
    struct vnop_ioctl_args *a=opaque;
    if(a->a_vp!=&target_node || a->a_command!=0x80084118UL || a->a_fflag!=0 || !a->a_context) abort();
    dispatches++;return dispatch_error;
}
'''

TEST = r'''
#define REQUIRE(v) do {if(!(v)) {fprintf(stderr,"line %d: %s\n",__LINE__,#v);exit(1);}} while(0)
static unsigned cases;
static void reset(void) {
    owner=NULL;target_vnode=NULL;target_mount=NULL;active=finished=0;
    flag_reads=ioctl_calls=flags_changed=ioctl_changed=0;last_flags=0;last_ioctl=0;observation[0]=0;
    fuxi_ram_root_stage=7;fuxi_ram_system_user_entry=1;process.pid=1;test_thread=(void *)1;
    boot_enabled=1;boot_profile=2;boot_present=1;mount_reads=dispatches=context_reads=0;
    is64=solid=rootvp_is_ssd=0;rootvp=NULL;dispatch_error=1;
    target_fs.mnt_flag=0x100001;target_node.mount=&target_fs;target_node.v_op[0]=dispatch;target_node.ready=1;
    foreign_node.mount=&foreign_fs;
}
static void refused(void) {REQUIRE(!fuxi_ram_launch_begin(&process,&target_node,"AMFI"));REQUIRE(!active);cases++;}
static int calls,raw_error=13;
static char original_policy_reason[]="original policy reason";
static struct launch_constraint_data {int launch_type;} test_lcd;
static int policy_hook(proc_t p,int parent,int responsible,void *attr,size_t bytes,
    struct launch_constraint_data *lcd,char **description,size_t *length) {
    REQUIRE(p==&process && parent==0 && responsible==0 && !attr && !bytes && lcd->launch_type==0);
    calls++;REQUIRE(vfs_flags(&target_fs)==0x100001);
    REQUIRE(VNOP_IOCTL(&target_node,0x80084118UL,NULL,0,(void *)2)==dispatch_error);
    *description=original_policy_reason;*length=raw_error?23:0;return raw_error;
}
static int mac_error_select(int next,int previous) {return previous ? previous : next;}
static int policy(void) {
    proc_t curp=&process;vnode_t vp=&target_node;
    struct {const char *mpc_name;} policy_conf={"AMFI"},*mpc=&policy_conf;
    (void)vp;(void)mpc;
    int original_parent_id=0,responsible_pid=0,error=5;
    void *spawnattr=NULL;size_t spawnattrlen=0,fatal_failure_desc_len=0;
    char *fatal_failure_desc=NULL;struct launch_constraint_data lcd={0};
    int (*hook)(proc_t,int,int,void *,size_t,struct launch_constraint_data *,char **,size_t *)=policy_hook;
POLICY_BODY
    REQUIRE(fatal_failure_desc==original_policy_reason);
    return error;
}
int main(void) {
    reset();REQUIRE(!*fuxi_ram_launch_info());
    fuxi_ram_launch_flags(&target_fs,1);fuxi_ram_launch_ioctl(&target_node,0x80084118UL,1);REQUIRE(!flag_reads&&!ioctl_calls);cases++;
    boot_enabled=0;refused();boot_enabled=2;refused();boot_enabled=1;
    boot_present=0;refused();boot_present=1;boot_profile=1;refused();boot_profile=3;refused();boot_profile=2;
    fuxi_ram_root_stage=8;refused();fuxi_ram_root_stage=7;fuxi_ram_system_user_entry=0;refused();fuxi_ram_system_user_entry=1;
    process.pid=2;refused();process.pid=1;target_node.mount=NULL;refused();target_node.mount=&target_fs;
    REQUIRE(!fuxi_ram_launch_begin(NULL,&target_node,"AMFI"));REQUIRE(!fuxi_ram_launch_begin(&process,NULL,"AMFI"));
    REQUIRE(!fuxi_ram_launch_begin(&process,&target_node,"Sandbox"));cases+=3;
    REQUIRE(fuxi_ram_launch_begin(&process,&target_node,"AMFI")==1);REQUIRE(mount_reads==2); // one null mount, one valid mount
    REQUIRE(!fuxi_ram_launch_begin(&process,&target_node,"AMFI"));cases++;
    test_thread=(void *)3;fuxi_ram_launch_flags(&target_fs,99);fuxi_ram_launch_ioctl(&target_node,0x80084118UL,0);
    fuxi_ram_launch_end(1,0,0);REQUIRE(active&&!finished&&!flag_reads&&!ioctl_calls);test_thread=(void *)1;cases++;
    fuxi_ram_launch_flags(&foreign_fs,0);fuxi_ram_launch_ioctl(&foreign_node,0x80084118UL,0);
    fuxi_ram_launch_ioctl(&target_node,0x1234,0);REQUIRE(!flag_reads&&!ioctl_calls);cases++;
    REQUIRE(vfs_flags(&target_fs)==0x100001);REQUIRE(VNOP_IOCTL(&target_node,0x80084118UL,NULL,0,(void *)2)==1);
    REQUIRE(dispatches==1 && target_fs.mnt_flag==0x100001);cases++;
#if XNU_RAM_LAUNCH_OBSERVE
    REQUIRE(flag_reads==1 && ioctl_calls==1 && last_ioctl==1);
#else
    REQUIRE(!flag_reads && !ioctl_calls);
#endif
    fuxi_ram_launch_end(0,0,0);REQUIRE(active);fuxi_ram_launch_end(1,13,23);REQUIRE(!active&&finished);
#if XNU_RAM_LAUNCH_OBSERVE
    REQUIRE(strstr(fuxi_ram_launch_info(),"rootfs=0 reads=1") && strstr(fuxi_ram_launch_info(),"errno=1 calls=1"));
#else
    REQUIRE(strstr(fuxi_ram_launch_info(),"flags=unseen") && strstr(fuxi_ram_launch_info(),"unseen calls=0"));
#endif
    REQUIRE(!fuxi_ram_launch_begin(&process,&target_node,"AMFI"));cases++;
    reset();REQUIRE(fuxi_ram_launch_begin(&process,&target_node,"AMFI"));fuxi_ram_launch_end(1,13,23);
    REQUIRE(strstr(fuxi_ram_launch_info(),"flags=unseen") && strstr(fuxi_ram_launch_info(),"unseen calls=0"));cases++;
    reset();REQUIRE(fuxi_ram_launch_begin(&process,&target_node,"AMFI"));
    fuxi_ram_launch_flags(&target_fs,1);fuxi_ram_launch_flags(&target_fs,0x4001);
    fuxi_ram_launch_ioctl(&target_node,0x80084118UL,1);fuxi_ram_launch_ioctl(&target_node,0x80084118UL,25);
    fuxi_ram_launch_end(1,13,23);REQUIRE(flags_changed && ioctl_changed && strstr(observation,"rootfs=1"));cases++;
    reset();REQUIRE(fuxi_ram_launch_begin(&process,&target_node,"AMFI"));is64=1;target_node.ready=0;int data=0;
    REQUIRE(VNOP_IOCTL(&target_node,0x80084118UL,(caddr_t)&data,0,(void *)2)==ENOTTY && dispatches==0);
#if XNU_RAM_LAUNCH_OBSERVE
    REQUIRE(ioctl_calls==1 && last_ioctl==ENOTTY);
#endif
    cases++;
    reset();REQUIRE(fuxi_ram_launch_begin(&process,&target_node,"AMFI"));rootvp=&target_node;rootvp_is_ssd=1;
    REQUIRE(VNOP_IOCTL(&target_node,DKIOCISSOLIDSTATE,(caddr_t)&data,0,(void *)2)==0 && data==1 && dispatches==0 && !ioctl_calls);cases++;
    reset();REQUIRE(VNOP_IOCTL(&target_node,0x80084118UL,NULL,0,NULL)==1 && context_reads==1 && dispatches==1);cases++;
    for(int error=0;error<=25;error+=5) {
        reset();dispatch_error=error;REQUIRE(VNOP_IOCTL(&target_node,0x80084118UL,NULL,0,(void *)2)==error && dispatches==1);cases++;
    }
    reset();calls=0;raw_error=13;REQUIRE(policy()==5 && calls==1 && dispatches==1 && target_fs.mnt_flag==0x100001);
#if XNU_RAM_LAUNCH_OBSERVE
    REQUIRE(strstr(observation,"err=13 fatal=1") && flag_reads==1 && ioctl_calls==1);
#else
    REQUIRE(!active&&!finished&&!flag_reads&&!ioctl_calls);
#endif
    cases++;
    reset();calls=0;raw_error=0;REQUIRE(policy()==5 && calls==1 && dispatches==1);cases++;
    REQUIRE(test_lcd.launch_type==0);
    printf("PASS: observer mode %d, %u scope/refusal/passthrough/policy cases; no additional ioctl or flag write\n",XNU_RAM_LAUNCH_OBSERVE,cases);
    return 0;
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--source-only', action='store_true')
    args = parser.parse_args()
    out, base = args.output.resolve(), args.base.resolve()
    root = Path(__file__).resolve().parents[1]
    patch = base / 'patches/xnu/zzzzzzzz-ram-launch-constraint-observe.patch'
    paths = ['security/mac_process.c', 'bsd/vfs/kpi_vfs.c', 'bsd/kern/kern_sig.c']
    parent = args.parent.resolve() / 'work/tree/.cache/xnu-work'
    source = root / 'package/RamLaunchConstraintProbe.c'
    results = []
    with tempfile.TemporaryDirectory(prefix='ram-launch-observer-') as temp:
        work = Path(temp)
        for relative in paths:
            dest = work / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((parent / relative).read_bytes())
        subprocess.run(['git', 'apply', str(patch)], cwd=work, check=True)
        kpi = (work / paths[1]).read_text()
        functions = braced(kpi, 'uint64_t\nvfs_flags(') + '\n' + braced(kpi, 'errno_t\nVNOP_IOCTL(')
        mac = (work / paths[0]).read_text()
        at = mac.index('int ram_launch_token')
        start = mac.rfind('#if defined(XNU_RAM_LAUNCH_OBSERVE)', 0, at)
        end = mac.index('\n\t\t/*\n\t\t * Early exit', at)
        policy = mac[start:end]
        body = '\n'.join(line for line in source.read_text().splitlines() if not line.startswith('#include'))
        fixture = API + '\n' + body + '\n' + functions + '\n' + TEST.replace('POLICY_BODY', policy)
        fixture = fixture.replace('__attribute__((weak_import))', '')
        (work / 'test.c').write_text(fixture)
        for mode in (0, 1):
            command = ['clang', '-Wall', '-Wextra', '-Werror', '-Wno-pointer-bool-conversion',
                       '-fsanitize=address,undefined', f'-DXNU_RAM_LAUNCH_OBSERVE={mode}',
                       str(work / 'test.c'), '-o', str(work / 'test')]
            subprocess.run(command, check=True)
            results.append(subprocess.check_output([str(work / 'test')], text=True).strip())
        if not args.source_only:
            tree = out / 'work/tree/.cache/xnu-work'
            for relative in paths:
                assert (tree / relative).read_bytes() == (work / relative).read_bytes(), relative
            from ram_probe_policy import inspect_system_probe
            assert inspect_system_probe(out)['launch_observer'] is True
        sig = (work / paths[2]).read_text()
        assert 'signal_reason->osr_namespace, signal_reason->osr_code,' in sig
        assert 'launchd_exit_reason_desc ? launchd_exit_reason_desc : "none"' in sig
        assert 'fuxi_ram_launch_info()' in sig
        sources = {str(source): hashlib.sha256(source.read_bytes()).hexdigest(),
                   str(patch): hashlib.sha256(patch.read_bytes()).hexdigest()}
    report = dict(passed=results, source_sha256=sources, no_extra_ioctl=True,
                  verdict_passthrough=True, scope='current AMFI thread, exact vnode/mount, exact ioctl',
                  hardware_result='pending')
    if not args.source_only:
        report['boot_sha256'] = hashlib.sha256((out / 'Mu-fuxi-usb-ram.img').read_bytes()).hexdigest()
    (out / 'launch-observer-tests.json').write_text(json.dumps(report, indent=2) + '\n')
    print('\n'.join(results))


if __name__ == '__main__':
    main()
