#!/usr/bin/env python3
"""Qualify the original 21H461 watchdog ABI and build its software backend."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'kits/NativePrelink'))
from native_prelink import macho, backed_offset, exports, unserialize_xml

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--reference', required=True, type=Path)
p.add_argument('--kernelcache', required=True, type=Path)
p.add_argument('--output', required=True, type=Path)
a = p.parse_args()
out = a.output.resolve(); out.mkdir(parents=True, exist_ok=True)
sha = lambda b: hashlib.sha256(b).hexdigest()
raw = a.kernelcache.read_bytes()
assert sha(raw) == 'f18b4df865405f2a287b84977d7aca745e056d656d9fc9df1662970ce2969de7'
original = macho(raw)
read = lambda va, n: raw[backed_offset(original['segments'], va, n, len(raw)):][:n]
ptr = lambda va: struct.unpack('<Q', read(va, 8))[0]
assert read(0xfffffff0055adce4, 11) == b'IOWatchdog\0'
assert struct.unpack('<I', read(0xfffffff00663e450, 4))[0] == 0x52801f03  # class size 248
assert struct.unpack('<I', read(0xfffffff00663e3a0, 4))[0] == 0x52801d03  # client size 232
vptr = 0xfffffff006f247e8
native = {n: va for va, names in exports(raw, original).items() for n in names}
assert [ptr(vptr+i*8) for i in (168,169,170)] == [native['___cxa_pure_virtual']]*3
assert ptr(vptr+171*8) == 0
assert ptr(vptr+87*8) == 0xfffffff00663eb6c and ptr(vptr+140*8) == 0xfffffff00663ee14
# Exact original heartbeat fields/updates, before accepting the private offsets.
for va, word in [(0xfffffff00663f134,0x52800028), (0xfffffff00663f138,0x3902ae68),
                 (0xfffffff00663f144,0x91032262), (0xfffffff00663f160,0xf9005e68),
                 (0xfffffff00663f20c,0x3902a27f), (0xfffffff00663f298,0x3902a288),
                 (0xfffffff00663f29c,0x91032282)]:
    assert struct.unpack('<I', read(va,4))[0] == word, hex(va)
reference = a.reference.read_bytes(); ref = macho(reference)
plan = json.loads(a.reference.with_suffix('.json').read_text())
slide = plan['slide']; assert slide == -0x1000000
info = next(s for s in original['segments'] if s['name']=='__PRELINK_INFO')
entries = unserialize_xml(read(info['va'],info['filesize']))['_PrelinkInfoDictionary']
entry = next(e for e in entries if e['CFBundleIdentifier']=='com.apple.driver.AppleARMWatchdogTimer')
module = macho(raw, backed_offset(original['segments'],entry['_PrelinkExecutableLoadAddr'],32,len(raw)))
for s in module['segments']:
    if not s['filesize']: continue
    bound = next(e for e in plan['extents'] if e['module']==entry['CFBundleIdentifier'] and e['segment']==s['name'])
    assert bound['va']==s['va']+slide
text = next(s for s in module['segments'] if s['name']=='__TEXT_EXEC')
assert sha(read(text['va'],text['filesize'])) == plan['original_executable_sha256'][entry['CFBundleIdentifier']]
header = '''#pragma once
#include <IOKit/IOService.h>
#include <IOKit/IOUserClient.h>
class IOWatchdog : public IOService {
 OSDeclareAbstractStructors(IOWatchdog)
 unsigned char originalPrivate[0xf8-sizeof(IOService)];
public:
 void stop(IOService *) override;
 IOReturn newUserClient(task_t, void *, UInt32, OSDictionary *, IOUserClient **) override;
 virtual void enableBackend() = 0;
 virtual void disableBackend() = 0;
 virtual void restartBackend() = 0;
};
static_assert(sizeof(IOWatchdog)==0xf8,"Original watchdog storage");
'''
(out/'OriginalWatchdogABI.h').write_text(header)
source = ROOT/'kits/sheng/RamWatchdog.cpp'; obj = out/'RamWatchdog.o'
recipe = json.loads((ROOT/'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))
command=[]; i=0; arguments=recipe['arguments']
while i<len(arguments):
    v=arguments[i]
    if v==recipe['file']: command.append(str(source))
    elif v in ('-o','-MF','-MT','-MJ'):
        if v=='-o': command+=['-o',str(obj)]
        i+=1
    elif v=='-Xclang' and arguments[i+1].startswith('-W'): i+=1
    elif not v.startswith('-W'): command.append(v)
    i+=1
command+=['-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-c99-designator',
          '-Wno-reorder-init-list','-Wno-deprecated-enum-enum-conversion','-Wno-unnecessary-virtual-specifier',
          '-I',str(out),'-I',str(ROOT/'.cache/xnu-work/osfmk')]
(out/'compile-command.json').write_text(json.dumps(command,indent=2)+'\n')
subprocess.run(command,cwd=recipe['directory'],check=True)
compiled=obj.read_bytes(); image=macho(compiled)
sym=next(at for cmd,at,_ in image['commands'] if cmd==2)
at,n,strings,_=struct.unpack_from('<4I',compiled,sym+8);symbols=[]
for i in range(n):
    ix,kind,_,_,va=struct.unpack_from('<IBBHQ',compiled,at+i*16)
    symbols.append((compiled[strings+ix:compiled.index(b'\0',strings+ix)].decode(),va,kind))
table=next(va for name,va,_ in symbols if name=='__ZTV11RamWatchdog')
relocations={}
for segment in image['segments']:
    for section in segment['sections']:
        if section['va']<=table<section['va']+section['size']:
            at,n=struct.unpack_from('<2I',compiled,section['command_offset']+56)
            for i in range(n):
                offset,bits=struct.unpack_from('<2I',compiled,at+i*8)
                if bits>>27&1 and bits>>28==0 and bits>>25&3==3:
                    relocations[section['va']+offset]=symbols[bits&0xffffff][0]
slots=[relocations.get(table+16+i*8) for i in range(171)]
assert all(slots) and table+16+171*8 not in relocations
for i,name in enumerate(slots):
    if i in (0,1,7,14,86,87,140,168,169,170): assert '11RamWatchdog' in name,(i,name)
    else: assert native.get(name)==ptr(vptr+i*8),(i,name,hex(ptr(vptr+i*8)))
bindings={
 '__ZN10IOWatchdogC2EPK11OSMetaClass':0xfffffff00663e400,
 '__ZN10IOWatchdogD2Ev':0xfffffff00663e420,
 '__ZN10IOWatchdog10gMetaClassE':0xfffffff007b2d268,
 '__ZTV10IOWatchdog':vptr-16,
 '_fuxi_original_watchdog_start':0xfffffff00663e498}
undefined={name for name,_,kind in symbols if kind==1 and ('IOWatchdog' in name or name=='_fuxi_original_watchdog_start')}
assert undefined==set(bindings),undefined
asm=out/'original-bindings.S'
asm.write_text(''.join(f'.globl {name}\n.set {name}, {va+slide:#x}\n' for name,va in bindings.items()))
sdk=subprocess.check_output(['xcrun','--sdk','iphoneos','--show-sdk-path'],text=True).strip()
subprocess.run(['xcrun','clang','-target','arm64-apple-ios17.0','-isysroot',sdk,'-c',str(asm),'-o',str(out/'original-bindings.o')],check=True)
late=out/'RamWatchdog.late.o'
subprocess.run(['llvm-objcopy','--remove-section=__DATA,__mod_init_func','--globalize-symbol=__GLOBAL__sub_I_RamWatchdog.cpp',str(obj),str(late)],check=True)
for b in [compiled,late.read_bytes()]:
    section=next(s for g in macho(b)['segments'] for s in g['sections'] if s['name']=='__text')
    if b is compiled: original_text=b[section['fileoff']:section['fileoff']+section['size']]
    else: assert b[section['fileoff']:section['fileoff']+section['size']]==original_text
report=dict(source_sha256=sha(source.read_bytes()),object_sha256=sha(late.read_bytes()),bindings_sha256=sha((out/'original-bindings.o').read_bytes()),
            original_kernelcache_sha256=sha(raw),original_executable_sha256=plan['original_executable_sha256'][entry['CFBundleIdentifier']],
            reference_kernel_sha256=sha(reference),virtual_slots=171,original_base_size=248,original_client_size=232,
            bindings={name:hex(va+slide) for name,va in bindings.items()},native_slot_names=slots,
            hardware_watchdog_implemented=False,original_executable_modified=False)
(out/'watchdog-build.json').write_text(json.dumps(report,indent=2)+'\n')
print('PASS original watchdog ABI, 171 slots, heartbeat offsets, original client and late registration')
