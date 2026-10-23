#!/usr/bin/env python3
"""Test the actual SID 0x40 transaction with failure-injecting register peers."""
import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--uefi', type=Path, required=True)
p.add_argument('--report', type=Path, required=True)
a = p.parse_args()
a.report.parent.mkdir(parents=True, exist_ok=True)
a.report.unlink(missing_ok=True)
source = a.uefi.resolve() / 'Platforms/Xiaomi/fuxiPkg/Library/FuxiUsbfnLib'
test = r'''
#include "UsbSmmuRoute.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static FUA32 regs[1024],before[1024],writes,offsets[20],values[20],mode,smr_reads,s2cr_reads;
static FUA32 rd(void *ctx,FUA32 at) {
  (void)ctx;assert(at<0x1000 && !(at&3));
  if(at==0x804) {
    smr_reads++;
    if(mode==4 && smr_reads==2) regs[at/4]=0x80000222;
  }
  if(at==0xc04) {
    s2cr_reads++;
    if(mode==5 && s2cr_reads==4)regs[at/4]=0x200ff;
  }
  return regs[at/4];
}
static void wr(void *ctx,FUA32 at,FUA32 value) {
  (void)ctx;assert(at==0x804 || at==0xc04);assert(writes<20);
  offsets[writes]=at;values[writes++]=value;
  if(mode==1 && at==0xc04 && value==0x100ff){regs[at/4]=0x200ff;return;}
  if(mode==1 && at==0xc04 && (value>>16)==2)return;
  if(mode==2 && at==0x804 && value==0x80000040)return;
  if(mode==5 && at==0x804 && value!=0x80000040 && writes>2)return;
  regs[at/4]=value;
}
static void init(void) {
  memset(regs,0,sizeof(regs));mode=writes=smr_reads=s2cr_reads=0;
  regs[0x20/4]=(1u<<27)|8;regs[0x24/4]=0x60000046;regs[0]=0x00290406;
  for(unsigned i=0;i<8;i++){regs[(0x800+4*i)/4]=0x80000100+i;regs[(0xc00+4*i)/4]=0x20000+i;}
  regs[0x804/4]=0x1234;regs[0xc04/4]=0x200ff;
  memcpy(before,regs,sizeof(regs));
}
static void others(void) {
  for(unsigned i=0;i<1024;i++)if(i!=0x804/4 && i!=0xc04/4)assert(regs[i]==before[i]);
}
int main(void) {
 FUR_STATE s={0};FUA32 result;
 init();assert(FurInstall(rd,wr,0,&s)==FurOk && s.Active && writes==2);
 assert(offsets[0]==0xc04 && values[0]==0x100ff && offsets[1]==0x804 && values[1]==0x80000040);
 assert(s.TestSmr==0x80000040 && s.TestS2cr==0x100ff);others();
 puts("only an unused slot gains exact SID0040; S2CR precedes SMR activation");
 assert(FurRemove(rd,wr,0,&s)==FurOk && !s.Active && s.RestoreOk);
 assert(offsets[2]==0x804 && offsets[3]==0xc04);assert(!memcmp(regs,before,sizeof(regs)));
 puts("removal restores exact saved state with SMR invalidated before S2CR restore");
 memset(&s,0,sizeof(s));init();mode=1;
 assert(FurInstall(rd,wr,0,&s)==FurBypassRejected && !s.Active && s.RestoreOk);
 assert(s.TestS2cr==0x200ff && !memcmp(regs,before,sizeof(regs)));others();
 puts("Qualcomm BYPASS-to-FAULT emulation stops before activation and verifies rollback");
 memset(&s,0,sizeof(s));init();mode=1;regs[0xc04/4]=0x20055;
 assert(FurInstall(rd,wr,0,&s)==FurRestoreFailed && !s.Active && !s.RestoreOk);
 assert(regs[0x804/4]==0x1234);
 puts("ignored FAULT restore is reported as rollback failure, with match still invalid");
 memset(&s,0,sizeof(s));init();mode=2;
 assert(FurInstall(rd,wr,0,&s)==FurSmrRejected && s.RestoreOk && !s.Active);
 assert(!memcmp(regs,before,sizeof(regs)));
 puts("rejected SMR activation rolls back and cannot permit DMA");
 memset(&s,0,sizeof(s));init();regs[0x808/4]=0x800f0040;
 assert(FurInstall(rd,wr,0,&s)==FurChanged && !writes);
 puts("existing overlapping mask refuses writes even when an unused slot exists");
 memset(&s,0,sizeof(s));init();regs[0x804/4]=0x80000200;
 assert(FurInstall(rd,wr,0,&s)==FurNoSlot && !writes);
 puts("no unused stream slot refuses writes");
 for(unsigned i=0;i<4;i++) {
   memset(&s,0,sizeof(s));init();
   if(i==0)regs[0x20/4]=(1u<<27)|129;
   if(i==1)regs[0]|=1;
   if(i==2)regs[0]|=8;
   if(i==3)regs[0]&=~(1u<<10);
   assert(FurInstall(rd,wr,0,&s)==FurUnsafe && !writes);
 }
 puts("partial scan, disabled client, extended IDs and unexpected default policy refuse writes");
 memset(&s,0,sizeof(s));init();mode=4;
 assert(FurInstall(rd,wr,0,&s)==FurChanged && !writes && regs[0x804/4]==0x80000222);
 puts("a slot changed since scan is not overwritten or rolled back");
 memset(&s,0,sizeof(s));init();assert(FurInstall(rd,wr,0,&s)==FurOk);
 regs[0x804/4]=0x80000222;writes=0;
 assert(FurRemove(rd,wr,0,&s)==FurChanged && !writes && s.Active);
 puts("removal refuses a route whose ownership no longer matches");
 memset(&s,0,sizeof(s));init();mode=5;result=FurInstall(rd,wr,0,&s);
 assert(result==FurRestoreFailed && s.Active && !s.RestoreOk && regs[0x804/4]==0x80000040);
 puts("post-activation route change and failed invalidation retain conservative ownership");
 writes=0;assert(FurInstall(rd,wr,0,&s)==FurChanged && !writes);
 puts("active or uncertain ownership cannot be installed over");
 return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='usb-smmu-route-') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(test)
    binary = work / 'route-test'
    subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-g', '-O1',
                    '-fsanitize=address,undefined', '-I', str(source), str(source / 'UsbSmmuRoute.c'),
                    str(work / 'test.c'), '-o', str(binary)], check=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(result.stdout + result.stderr)
report = dict(cases=len(result.stdout.splitlines()), passed=result.stdout.splitlines(),
              engine='production UsbSmmuRoute.c', hardware_tested=False,
              sanitizers=['address', 'undefined'],
              source_sha256={name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                             for name in ['UsbSmmuRoute.c', 'UsbSmmuRoute.h', 'UsbSmmuAudit.h']})
a.report.parent.mkdir(parents=True, exist_ok=True)
a.report.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
