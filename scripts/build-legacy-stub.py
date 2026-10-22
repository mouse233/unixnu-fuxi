#!/usr/bin/env python3
"""Build the legacy display stub archive.

bsd_init references native_legacy_display_probe() and
native_legacy_display_late_probe() unconditionally, but the real archive
(build-native-legacy-display.py) needs a prelink candidate that cannot exist
before the kernel links.

Stage A of the bootstrap links a kernel without the storage and display stacks;
this archive supplies the display hooks and an optional passive UFS observer
fallback so it can link. CONFIG_XNU_EXTRA_LD_ARCHIVES
points at the result.
"""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/legacy-stub'
OUT.mkdir(parents=True, exist_ok=True)

record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/kern_exec.o.json')
                    .read_text().strip().rstrip(','))
source = ROOT / 'kits/sheng/LegacyDisplayStub.c'
obj = OUT / 'LegacyDisplayStub.o'

command = []
args = record['arguments']
i = 0
while i < len(args):
    arg = args[i]
    if arg == record['file']:
        command.append(str(source))
    elif arg in ('-o', '-MF', '-MT', '-MJ'):
        if arg == '-o':
            command += ['-o', str(obj)]
        i += 1
    elif arg == '-Xclang' and args[i + 1].startswith('-W'):
        i += 1
    elif not arg.startswith('-W'):
        command.append(arg)
    i += 1
command += ['-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror']
subprocess.run(command, cwd=record['directory'], check=True, timeout=90)

archive = OUT / 'libLegacyStub.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', str(archive), str(obj)], check=True, timeout=30)
print(f'legacy stub archive: {archive}')

# The UFS and RAM-root profiles explicitly force-load this archive once.
# Injecting its strong display definitions into every force-loaded archive would
# produce duplicate symbols. Retain the old Stage A fallback for other profiles.
if os.environ.get('XNU_NATIVE_UFS') == '1' or os.environ.get('XNU_RAM_ROOT_PROBE') == '1':
    print('Explicit diagnostic archive link: skip Stage A cross-archive injection')
else:
    # Which archive ends up on the kernel link line is not something this device
    # repository controls, and a static library listed before the object that needs
    # it is never searched twice. Put the stub object into every archive we built
    # so the symbol is found whichever one the link pulls in.
    added = 0
    for other in sorted((ROOT / 'build').glob('*/lib*.a')):
        if other == archive:
            continue
        subprocess.run(['xcrun', 'ar', 'rcs', str(other), str(obj)], check=True, timeout=30)
        added += 1
    print(f'legacy stub object added to {added} other archives')
