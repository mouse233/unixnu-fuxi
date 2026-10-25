"""Execute pinned original watchdog/client instructions with explicit KPI stubs."""
import hashlib,struct,sys
from pathlib import Path

def check_original(root,out):
 sys.path.insert(0,str(root/'../unixnu-source/kits/NativePrelink'))
 from native_prelink import macho,backed_offset,exports
 from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_CODE,UC_HOOK_MEM_UNMAPPED
 from unicorn import arm64_const as regs
 raw=(out/'cache/ipsw/21H461/kernelcache.decompressed').read_bytes()
 assert hashlib.sha256(raw).hexdigest()=='f18b4df865405f2a287b84977d7aca745e056d656d9fc9df1662970ce2969de7'
 m=macho(raw);names=exports(raw,m)
 ptr=lambda va:struct.unpack_from('<Q',raw,backed_offset(m['segments'],va,8,len(raw)))[0]
 # Every imported stub is bound via its actual ADRP/LDR GOT cell.
 calls={}
 for va in range(0xfffffff00663fb28,0xfffffff00663fef4,12):
  at=backed_offset(m['segments'],va,8,len(raw));w,v=struct.unpack_from('<II',raw,at)
  immediate=((w>>5)&0x7ffff)<<2|((w>>29)&3);immediate-=1<<21 if immediate&(1<<20) else 0
  got=(va&~4095)+(immediate<<12)+((v>>10)&0xfff)*8
  calls[va]=names.get(ptr(got),[hex(ptr(got))])[0]
 results=[]
 modes=['client-bad-type','client-busy','client-not-root','client-no-entitlement','client-false-entitlement','client-open','heartbeat','disable','enable']
 for mode in modes:
  u=Uc(UC_ARCH_ARM64,UC_MODE_ARM);u.mem_map(0x2000000,0x100000)
  obj,client,argument,result,stop=0x2010000,0x2020000,0x2030000,0x2040000,0x20f0000
  def putq(a,v):u.mem_write(a,struct.pack('<Q',v))
  mapped=set()
  def demand(machine,access,address,size,value,context):
   for page in range(address&~4095,(address+size+4095)&~4095,4096):
    if page in mapped:continue
    s=next((s for s in m['segments'] if s['va']<=page<s['va']+s['size']),None)
    if not s: raise AssertionError(f'{mode}: unmapped {address:#x}, PC={machine.reg_read(regs.UC_ARM64_REG_PC):#x}')
    machine.mem_map(page,4096);offset=page-s['va'];n=max(0,min(4096,s['filesize']-offset))
    if n:machine.mem_write(page,raw[s['fileoff']+offset:s['fileoff']+offset+n])
    mapped.add(page)
   return True
  u.hook_add(UC_HOOK_MEM_UNMAPPED,demand)
  # Explicit false entitlement singleton, whose original import is indirect.
  falsePointer=ptr(0xfffffff006f23958)
  demand(u,0,falsePointer,8,0,None);putq(falsePointer,0x2052000);putq(0x2052000,0x2050000)
  release=next(va for va,ns in names.items() if '__ZNK8OSObject7releaseEv' in ns)
  putq(0x2050000,0x2051000);putq(0x2050100,0x2051000);putq(0x2051000+0x28,release)
  putq(obj,0xfffffff006f247e8);putq(obj+0x90,1);putq(obj+0xa0,2);u.mem_write(obj+0x8b,b'\1')
  if mode=='client-busy':putq(obj+0x98,client)
  putq(argument+0x48,result)
  events=[];answer={};clock=10000000000
  def get(n):return u.reg_read(getattr(regs,'UC_ARM64_REG_'+n))
  def setr(n,v):u.reg_write(getattr(regs,'UC_ARM64_REG_'+n),v)
  def cstr(a):
   if a >= 0xffff000000000000: assert demand(u,0,a,180,0,None)
   b=bytearray()
   for i in range(180):
    c=bytes(u.mem_read(a+i,1))
    if c==b'\0':return b.decode()
    b+=c
   raise AssertionError('unbounded original string')
  def hook(machine,pc,size,context):
   if pc==stop:answer['returncode']=get('X0')&0xffffffff;u.emu_stop();return
   name=calls.get(pc)
   if name is None and not (0xfffffff00663c000<=pc<0xfffffff00663fb28):
    name=names.get(pc,[None])[0]
   if name is None:return
   def ret(v=0):setr('X0',v);setr('PC',get('LR'))
   if name in ('_IOLockLock','_IOLockUnlock','_IOSimpleLockLock','_IOSimpleLockUnlock','__ZNK11OSMetaClass19instanceConstructedEv','__ZNK8OSObject7releaseEv'):ret()
   elif name=='__ZN12IOUserClientC2EPK11OSMetaClass':ret(get('X0'))
   elif name=='_ml_set_interrupts_enabled':ret(1)
   elif name=='_mach_absolute_time':ret(clock)
   elif name=='_clock_interval_to_deadline':
    assert get('X1')==1000000000;putq(get('X2'),clock+get('X0')*1000000000);ret()
   elif name=='__ZN15OSMetaClassBase12safeMetaCastEPKS_PK11OSMetaClass':
    assert get('X1')==0xfffffff007b2d268;ret(obj)
   elif name=='__ZN12IOUserClient18clientHasPrivilegeEPvPKc':
    assert cstr(get('X1'))=='root';events.append('original root check');ret(int(mode=='client-not-root'))
   elif name=='__ZN12IOUserClient21copyClientEntitlementEP4taskPKc':
    assert cstr(get('X1'))=='com.apple.private.iowatchdog.user-access';events.append('original entitlement check')
    ret(0 if mode=='client-no-entitlement' else 0x2050000 if mode=='client-false-entitlement' else 0x2050100)
   elif name=='_OSObject_typed_operator_new':assert get('X1')==232;ret(client)
   elif name in ('__ZN8OSObject4initEv','__ZN12IOUserClient4initEv','__ZN9IOService6attachEPS_','__ZN12IOUserClient5startEP9IOService','__ZN9IOService5startEPS_'):ret(1)
   elif name=='__ZN9IOService15registerServiceEj':events.append('original client registers');ret()
   else:raise AssertionError(f'unmodelled original KPI {name} at {pc:#x}')
  u.hook_add(UC_HOOK_CODE,hook)
  for n,v in [('SP',0x2080000),('LR',stop),('X0',obj),('X1',42),('X2',43),('X3',2 if mode=='client-bad-type' else 1),('X4',0),('X5',result)]:setr(n,v)
  if mode.startswith('client-'):entry=0xfffffff00663ee14
  else:
   entry={'heartbeat':0xfffffff00663f0f8,'disable':0xfffffff00663f1d0,'enable':0xfffffff00663f248}[mode]
   setr('X2',argument);u.mem_write(obj+0xa8,b'\1');putq(obj+0xb0,123);putq(obj+0xb8,7)
  try:u.emu_start(entry,0,count=10000)
  except Exception as error:raise AssertionError(f'{mode} PC={get("PC"):#x}: {error}') from error
  assert 'returncode' in answer,'instruction bound reached'
  readq=lambda a:struct.unpack('<Q',bytes(u.mem_read(a,8)))[0]
  expected={'client-bad-type':0xe00002c2,'client-busy':0xe00002c5,'client-not-root':0xe00002c1,'client-no-entitlement':0xe00002c1,'client-false-entitlement':0xe00002c1}.get(mode,0)
  assert answer['returncode']==expected,(mode,answer)
  if mode=='client-open':assert readq(result)==client and readq(client+0xe0)==obj
  if mode=='heartbeat':assert readq(obj+0xb8)==8 and readq(obj+0xc8)==clock+90000000000
  if mode=='disable':assert bytes(u.mem_read(obj+0xa8,1))==b'\0' and readq(obj+0xb0)==0 and readq(obj+0xb8)==0
  if mode=='enable':assert bytes(u.mem_read(obj+0xa8,1))==b'\1' and readq(obj+0xb8)==0 and readq(obj+0xc8)==clock+90000000000
  results.append(dict(case=mode,events=events,**answer))
 return results
