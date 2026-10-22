#!/usr/bin/env python3
"""Compile a bounded RAM-root diagnostic against this kernel's actual ABI."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--pins', type=Path, required=True)
parser.add_argument('--user-entry', action='store_true', help='Permit original PID1 until its first EL0 SVC; never dispatch it')
args = parser.parse_args()
pins = json.loads(args.pins.read_text())
if (pins['image_bytes'] != 184549376 or pins['file_bytes'] != 561856 or
        pins['image_sha256'] != 'aadb59ed21a8f0218a348f070913ab5a7679805c58408534d811170871f1fc05' or
        pins['file_sha256'] != '8cec9a3c44a443b08e977ccc5d577850316ad428c575dc01f09b018dbd5b6a25' or
        pins['file_path'] != '/sbin/launchd' or pins['physical_storage_allowed'] or
        pins['start_userland_allowed'] != args.user_entry or
        (args.user_entry and pins.get('stop_before_first_user_svc') is not True)):
    raise SystemExit('Require exact inspected RAM disk and bounded stage policy')
out = ROOT / 'build/ram-root-probe'
out.mkdir(parents=True, exist_ok=True)
header = '#define RAM_ROOT_IMAGE_BYTES UINT64_C(184549376)\n#define RAM_ROOT_FILE_BYTES 561856\n'
header += '#define RAM_ROOT_ALLOW_USER_ENTRY ' + str(int(args.user_entry)) + '\n'
for name, key in [('image', 'image_sha256'), ('file', 'file_sha256')]:
    value = bytes.fromhex(pins[key])
    if len(value) != 32: raise SystemExit('Expected SHA256 pins')
    header += 'static const uint8_t ram_root_' + name + '_sha256[32] = {' + ','.join(hex(x) for x in value) + '};\n'
(out / 'RamRootProbePins.h').write_text(header)
record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/kern_exec.o.json').read_text().strip().rstrip(','))
source = ROOT / 'kits/sheng/RamRootProbe.c'
obj = out / 'RamRootProbe.o'
command, arguments, i = [], record['arguments'], 0
while i < len(arguments):
    value = arguments[i]
    if value == record['file']: command.append(str(source))
    elif value in ('-o', '-MF', '-MT', '-MJ'):
        if value == '-o': command += ['-o', str(obj)]
        i += 1
    elif value == '-Xclang' and arguments[i + 1].startswith('-W'): i += 1
    elif not value.startswith('-W') and value not in ('-MD', '-MP'): command.append(value)
    i += 1
command += ['-I', str(out), '-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror']
(out / 'compile-command.json').write_text(json.dumps(command, indent=2) + '\n')
result = subprocess.run(command, cwd=record['directory'], capture_output=True, text=True)
(out / 'compile.log').write_text(result.stdout + result.stderr)
if result.returncode: raise SystemExit(result.stdout + result.stderr)
if struct.unpack_from('<4I', obj.read_bytes()) != (0xfeedfacf, 0x100000c, 0, 1):
    raise SystemExit('Expected ordinary ARM64 Mach-O object')
archive = out / 'libRamRootProbe.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', archive, obj], check=True)
(out / 'build-report.json').write_text(json.dumps(dict(pins=pins,
    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), hardware_tested=False), indent=2) + '\n')
print('PASS: bounded RAM-root diagnostic, ordinary ARM64, strict kernel-ABI compilation')
