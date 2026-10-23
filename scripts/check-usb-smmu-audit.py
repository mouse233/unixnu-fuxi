#!/usr/bin/env python3
"""Run the production read-only SMMUv2 capture against bounded register peers."""
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
source = a.uefi.resolve() / 'Platforms/Xiaomi/fuxiPkg/Library/FuxiUsbfnLib'
test = r'''
#include "UsbSmmuAudit.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static FUA32 regs[0x100000/4],limit,reads,max_stream;
static FUA32 rd(void *ctx,FUA32 at) {
  (void)ctx;assert(!(at&3));assert(at<=limit-4);reads++;
  if(at>=0x800 && at<0x1000 && at>max_stream)max_stream=at;
  return regs[at/4];
}
static void init(void) {
  memset(regs,0,sizeof(regs));limit=sizeof(regs);reads=max_stream=0;
  regs[0x20/4]=(1u<<27)|64;regs[0x24/4]=(3u<<28)|8;
}
static void route(unsigned slot,unsigned sid,unsigned mask,unsigned s2cr) {
  regs[(0x800+4*slot)/4]=(1u<<31)|(mask<<16)|sid;
  regs[(0xc00+4*slot)/4]=s2cr;
}
int main(void) {
  FUA_REPORT r;
  init();FuaCapture(rd,0,limit,0x40,&r);
  assert(r.Matches==0 && !r.Unsupported && !r.Partial);
  puts("no matching stream is reported without inventing a route");
  init();route(7,0x40,0,2);regs[0x12000/4]=1;
  regs[(0x1000+2*4)/4]=0x10000;regs[(0x1800+2*4)/4]=1;
  regs[(0x12000+0x20)/4]=0xc0000000;regs[(0x12000+0x24)/4]=0x1234;
  regs[(0x12000+0x58)/4]=2;regs[(0x12000+0x60)/4]=0xc67bf000;
  FuaCapture(rd,0,limit,0x40,&r);
  assert(r.Stored==1 && r.Match[0].Slot==7 && r.Match[0].ContextRead);
  assert(r.Match[0].Sctlr==1 && r.Match[0].Cbar==0x10000 && r.Match[0].Cba2r==1);
  assert(r.Match[0].Ttbr0==0x1234c0000000ULL && r.Match[0].Far==0xc67bf000);
  puts("exact SID match captures inherited translation context and 64-bit registers");
  init();route(0,0x40,0xf,1u<<16);FuaCapture(rd,0,limit,0x4a,&r);
  assert(r.Matches==1 && !r.Match[0].ContextRead);
  puts("masked SID match with BYPASS encoding does not dereference a context");
  init();route(0,0x40,0,2u<<16);FuaCapture(rd,0,limit,0x40,&r);
  assert(r.Matches==1 && !r.Match[0].ContextRead);
  puts("FAULT encoding does not dereference a context");
  init();route(0,0x40,0,255);FuaCapture(rd,0,limit,0x40,&r);
  assert(r.InvalidContext && !r.Match[0].ContextRead);
  puts("invalid context index cannot cause a context register read");
  init();regs[0x24/4]=(7u<<28)|8;route(0,0x40,0,0);
  FuaCapture(rd,0,limit,0x40,&r);assert(r.InvalidContext && !r.Match[0].ContextRead);
  puts("context outside advertised MMIO aperture is rejected before read");
  init();regs[0x20/4]=(1u<<27)|200;route(150,0x40,0,0);
  FuaCapture(rd,0,limit,0x40,&r);
  assert(r.Scanned==128 && r.Partial && !r.Matches && max_stream==0xdfc);
  puts("groups above 127 are never touched and incomplete scan remains explicit");
  init();for(unsigned i=0;i<5;i++)route(i,0x40,0,1u<<16);
  FuaCapture(rd,0,limit,0x40,&r);assert(r.Matches==5 && r.Stored==4 && r.Partial);
  puts("ambiguous and excess matches are retained as incomplete evidence");
  init();route(0,0x41,0,0);regs[0x800/4+1]=0x40;
  FuaCapture(rd,0,limit,0x40,&r);assert(!r.Matches);
  puts("wrong SID and invalid SMR cannot be accepted");
  init();regs[0x20/4]|=1u<<8;regs[0]=1u<<3;
  regs[0x800/4]=0x40;regs[0xc00/4]=(1u<<10)|(1u<<16);
  FuaCapture(rd,0,limit,0x40,&r);assert(r.Matches==1 && !r.Match[0].ContextRead);
  puts("extended stream validity is taken from S2CR not SMR bit 31");
  init();regs[0]=1u<<3;FuaCapture(rd,0,limit,0x40,&r);
  assert(r.Unsupported && !r.Matches);
  puts("inconsistent extended-ID capability is rejected");
  init();regs[0x24/4]=(1u<<31)|(2u<<28)|8;route(0,0x40,0,3);
  regs[0xb0000/4]=0x101;FuaCapture(rd,0,limit,0x40,&r);
  assert(r.PageShift==16 && r.ContextBase==0x80000 && r.Match[0].Sctlr==0x101);
  puts("64 KiB SMMU register pages use the correct context offset");
  init();regs[0x20/4]=0xffffffff;FuaCapture(rd,0,limit,0x40,&r);
  assert(r.Unsupported && reads==4);
  puts("inaccessible ID registers do not trigger a stream scan");
  init();FuaCapture(rd,0,0x800,0x40,&r);assert(r.Unsupported && reads==0);
  puts("undersized aperture is rejected without register access");
  return 0;
}
'''
with tempfile.TemporaryDirectory(prefix='usb-smmu-audit-') as directory:
    work = Path(directory)
    (work / 'test.c').write_text(test)
    binary = work / 'audit-test'
    subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-g', '-O1',
                    '-fsanitize=address,undefined', '-I', str(source), str(source / 'UsbSmmuAudit.c'),
                    str(work / 'test.c'), '-o', str(binary)], check=True)
    result = subprocess.run([str(binary)], capture_output=True, text=True, check=True)
report = dict(cases=len(result.stdout.splitlines()), passed=result.stdout.splitlines(),
              engine='production UsbSmmuAudit.c', register_io='read callback only',
              hardware_tested=False, sanitizers=['address', 'undefined'],
              source_sha256={name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                             for name in ['UsbSmmuAudit.c', 'UsbSmmuAudit.h']})
a.report.parent.mkdir(parents=True, exist_ok=True)
a.report.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
