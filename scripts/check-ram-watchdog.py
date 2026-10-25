#!/usr/bin/env python3
"""Exercise the real watchdog backend, and bind native ABI/client to delivery."""
import argparse,hashlib,json,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path);p.add_argument('--model-only',action='store_true');a=p.parse_args();out=a.output.resolve()
sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
source=ROOT/'package/RamWatchdog.cpp';body='\n'.join(l for l in source.read_text().splitlines() if not l.startswith('#include'))
fixtures=ROOT/'diagnostics/ram-root/tests/watchdog';shim=(fixtures/'shim.h').read_text();tests=(fixtures/'test.cpp').read_text()
with tempfile.TemporaryDirectory(prefix='fuxi-watchdog-test-') as temporary:
 work=Path(temporary)
 def run(b):
  (work/'test.cpp').write_text(shim+'\n'+b+'\n'+tests)
  subprocess.run(['clang++','-std=c++17','-fsanitize=address,undefined','-Wall','-Wextra','-Wno-unused-parameter','-Wno-unused-private-field',str(work/'test.cpp'),'-o',str(work/'test')],check=True,capture_output=True,text=True)
  return subprocess.run([str(work/'test')],capture_output=True,text=True)
 good=run(body);assert good.returncode==0,good.stdout+good.stderr
 mutants=[body.replace('now >= deadline','false'),body.replace('return result;','return kIOReturnSuccess;'),body.replace('if (before != 0)','if (false)'),
          body.replace('IOService::registerService((options & ~IOOptionBits(kIOServiceAsynchronous)) | kIOServiceSynchronous);','IOService::registerService(options);')]
 for b in mutants:
  assert b!=body;bad=run(b);assert bad.returncode!=0,'mutation survived'
report=dict(source_sha256=sha(source),passed=good.stdout.strip(),mutation_rejections=4,hardware_watchdog_implemented=False,hardware_result='pending')
if not a.model_only:
 from ram_watchdog_original import check_original
 report['original_instruction_cases']=check_original(ROOT,out)
 build=out/'work/tree/build/ram-root-probe';r=json.loads((build/'watchdog-build.json').read_text())
 assert r['source_sha256']==sha(source) and r['virtual_slots']==171 and r['original_base_size']==248 and r['original_client_size']==232
 assert sha(build/'RamWatchdog.late.o')==r['object_sha256'] and sha(build/'original-bindings.o')==r['bindings_sha256']
 pins=json.loads((out/'pins.json').read_text());board=json.loads((out/'board-effective.json').read_text())
 assert pins['system_watchdog'] and board['payload']['kernel_args'].count('ram-system-watchdog=1')==1
 assert '#define RAM_SYSTEM_WATCHDOG 1' in (build/'RamSystemProbePins.h').read_text()
 assert 'RamWatchdog.late.o' in subprocess.check_output(['xcrun','ar','t',str(build/'libRamRootProbe.a')],text=True)
 assert sha(out/'work/tree/kits/sheng/RamWatchdog.cpp')==sha(source)
 symbols=subprocess.check_output(['/opt/homebrew/opt/llvm/bin/llvm-nm',str(out/'work/kernel/kernel.macho')],text=True)
 for name in ('_fuxi_ram_watchdog_prepare','_fuxi_ram_watchdog_state','__ZTV11RamWatchdog'):
  assert any(line.split()[-1:]==[name] and ' U ' not in line for line in symbols.splitlines()),name
 cache_symbols=subprocess.check_output(['/opt/homebrew/opt/llvm/bin/llvm-nm',str(build/'RamSystemCache.o')],text=True)
 assert '_fuxi_ram_watchdog_prepare' in cache_symbols
 report['original_model_sha256']=sha(ROOT/'scripts/ram_watchdog_original.py')
 report['build_script_sha256']=sha(ROOT/'scripts/build-ram-watchdog.py')
 assert sha(out/'work/tree/scripts/build-ram-watchdog.py')==report['build_script_sha256']
 header=(out/'work/tree/.cache/xnu-work/iokit/IOKit/IOService.h').read_text()
 assert 'kIOServiceSynchronous   = 0x00000002' in header and 'kIOServiceAsynchronous  = 0x00000008' in header
 assert 'kIOServiceSynchronous=0x2,kIOServiceAsynchronous=0x8' in shim
 report['fixture_sha256']={f.name:sha(f) for f in (fixtures/'shim.h',fixtures/'test.cpp')}
 report.update(boot_sha256=sha(out/'Mu-fuxi-usb-ram.img'),abi=r,archive_sha256=sha(build/'libRamRootProbe.a'))
(out/'watchdog-tests.json').write_text(json.dumps(report,indent=2)+'\n')
print(good.stdout.strip());print('PASS 4 behavior mutation rejections including delayed matching; original client kept, real software expiry')
