#!/usr/bin/env python3
"""Offline execution of hash-pinned 21H461 APFS call paths; never uses a phone.

Requires Unicorn in a dedicated host environment. Kernel APIs are modeled and
recorded; original APFS executable bytes are mapped unchanged. This is an ABI
and integration audit, not a substitute for a complete XNU/APFS boot.
"""
import argparse
import hashlib
import json
import struct
import sys
from collections import deque
from pathlib import Path

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_INVALID
from unicorn import arm64_const as arm

APFS_TEXT_SHA='3192441dae74abc0d82e9069447194934598c1c63d2dacba0fb8599eef9996d7'
PARSER=0xfffffff0056d2cb8
MOUNT=0xfffffff0056c5fac
ARENA=0x10000000
STACK=0x30000000
STOP=0x50000000


class Machine:
    def __init__(self,kernel,base):
        sys.path.insert(0,str(base/'kits/NativePrelink'))
        import native_prelink as np
        self.np=np;self.data=kernel.read_bytes();self.image=np.macho(self.data)
        self.plan=json.loads(kernel.with_suffix('.json').read_text())
        assert hashlib.sha256(self.data).hexdigest()==self.plan['candidate_sha256']
        assert self.plan['original_executable_sha256']['com.apple.filesystems.apfs']==APFS_TEXT_SHA
        assert self.plan['slide']==-0x1000000
        self.names=np.exports(self.data,self.image)
        self.uc=Uc(UC_ARCH_ARM64,UC_MODE_ARM);self.pages=set();self.hooks={}
        self.events=[];self.recent=deque(maxlen=32);self.failure=None
        for e in self.plan['extents']:
            if e['module']!='com.apple.filesystems.apfs':continue
            self.map(e['va'],e['size']);self.uc.mem_write(e['va'],self.read_image(e['va'],e['size']))
            if e['segment']=='__TEXT_EXEC':assert hashlib.sha256(self.read_image(e['va'],e['size'])).hexdigest()==APFS_TEXT_SHA
        self.external={}
        for b in self.plan['bindings']:
            if b['module']=='com.apple.filesystems.apfs' and b['kind']=='kernel':
                self.map(b['new_target'],16);self.external[b['new_target']]=self.names.get(b['new_target'],[])
        self.map(ARENA,0x1000000);self.map(STACK,0x100000);self.map(STOP,0x1000)
        self.heap=ARENA+0x100000
        self.uc.hook_add(UC_HOOK_CODE,self.on_code)
        self.uc.hook_add(UC_HOOK_MEM_INVALID,self.on_bad_memory)
        self.install('_strlen',lambda:self.ret(len(self.string(self.x(0)))))
        self.install('_strlcpy',self.strlcpy)
        self.install('_strncmp',self.strncmp)

    def map(self,address,size):
        for page in range(address&~4095,(address+size+4095)&~4095,4096):
            if page not in self.pages:self.uc.mem_map(page,4096);self.pages.add(page)

    def read_image(self,address,size):
        at=self.np.backed_offset(self.image['segments'],address,size,len(self.data))
        return self.data[at:at+size]

    def x(self,n,value=None):
        reg=getattr(arm,f'UC_ARM64_REG_X{n}')
        if value is None:return self.uc.reg_read(reg)
        self.uc.reg_write(reg,value&((1<<64)-1))

    def string(self,address,limit=4096):
        if not address:return b''
        result=bytearray()
        for i in range(limit):
            b=self.uc.mem_read(address+i,1)[0]
            if not b:return bytes(result)
            result.append(b)
        raise ValueError('unterminated test string')

    def put(self,address,value):self.uc.mem_write(address,value)
    def qword(self,address,value):self.put(address,struct.pack('<Q',value))
    def alloc(self,size):
        address=self.heap;self.heap+=(size+15)&~15
        if self.heap>ARENA+0x1000000:raise ValueError('test arena exhausted')
        self.put(address,bytes(size));return address

    def ret(self,value=0):
        self.x(0,value);self.uc.reg_write(arm.UC_ARM64_REG_PC,self.x(30))

    def install(self,name,callback):
        addresses=[address for address,names in self.external.items() if name in names]
        assert addresses, 'missing original APFS import '+name
        for address in addresses:self.hooks[address]=callback

    def strlcpy(self):
        dst,src,size=self.x(0),self.x(1),self.x(2);s=self.string(src)
        if size:self.put(dst,s[:size-1]+b'\0')
        self.ret(len(s))

    def strncmp(self):
        a,b,n=self.x(0),self.x(1),self.x(2)
        left,right=self.string(a)[:n],self.string(b)[:n]
        self.ret((left>right)-(left<right))

    def on_code(self,uc,address,size,data):
        self.recent.append(hex(address))
        if address==STOP:uc.emu_stop();return
        if address in self.hooks:
            self.events.append(dict(pc=hex(address),names=self.external.get(address,[]),args=[hex(self.x(i)) for i in range(6)]))
            try:self.hooks[address]()
            except Exception as error:self.failure=repr(error);uc.emu_stop()
        elif address in self.external:
            self.failure=dict(unmodeled_import=self.external[address],pc=hex(address),args=[hex(self.x(i)) for i in range(6)])
            uc.emu_stop()

    def on_bad_memory(self,uc,access,address,size,value,data):
        self.failure=dict(unmapped=hex(address),pc=hex(uc.reg_read(arm.UC_ARM64_REG_PC)),size=size,access=access)
        return False

    def run(self,start,args):
        self.failure=None;self.events=[];self.recent.clear()
        self.uc.reg_write(arm.UC_ARM64_REG_SP,STACK+0xff000);self.x(30,STOP)
        for i,value in enumerate(args):self.x(i,value)
        try:self.uc.emu_start(start,STOP+4,count=100000)
        except Exception as error:
            if self.failure is None:self.failure=repr(error)
        if self.uc.reg_read(arm.UC_ARM64_REG_PC)!=STOP and self.failure is None:self.failure='instruction budget exhausted'
        return dict(return_value=self.x(0),failure=self.failure,events=self.events,recent=list(self.recent))

    def parse(self,path):
        src,dst,index=ARENA+0x1000,ARENA+0x2000,ARENA+0x3000
        self.put(src,path.encode()+b'\0');self.put(dst,bytes(256));self.put(index,b'\x55'*4)
        result=self.run(PARSER,[src,dst,128,index]);assert not result['failure'],result
        return dict(input=path,container_path=self.string(dst).decode(),volume_index=struct.unpack('<i',bytes(self.uc.mem_read(index,4)))[0])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel',type=Path,required=True);p.add_argument('--base',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    a=p.parse_args();m=Machine(a.kernel.resolve(),a.base.resolve())
    cases=[m.parse(path) for path in ['/dev/md1','/dev/md2','/dev/md1s1','/dev/md2s1','/dev/disk1s1','/dev/disk10s2s1','/dev/disk1s2','snap@/dev/disk1s1']]
    report=dict(kernel_sha256=hashlib.sha256(m.data).hexdigest(),original_apfs_text_sha256=APFS_TEXT_SHA,parser_cases=cases,
                scope='Original parser executed; strlen/strlcpy modeled. No phone or complete filesystem execution.')
    a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
