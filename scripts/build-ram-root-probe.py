#!/usr/bin/env python3
"""Compile a bounded RAM-root diagnostic against this kernel's actual ABI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
from ram_probe_policy import validate_stage_policy

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--pins', type=Path, required=True)
parser.add_argument('--user-entry', action='store_true', help='Permit original PID1 until its first EL0 SVC; never dispatch it')
parser.add_argument('--main-entry', action='store_true', help='Observe original LC_MAIN+4 with hardware debug')
parser.add_argument('--progress', action='store_true', help='Continue past main into follow mode: children run, mount/reboot/ptrace stay blocked')
parser.add_argument('--restore-trust-cache', type=Path, help='Exact original v1 restore module, embedded unchanged')
parser.add_argument('--system-objects', action='store_true', help='Stop after read-only OS/SystemOS mounts, before userland')
parser.add_argument('--system-user-entry', action='store_true', help='After the system mounts, exec /mnt1/sbin/launchd and stop at its first EL0 SVC')
parser.add_argument('--launch-constraints-observer', action='store_true', help='Observe existing AMFI vnode/flags/ioctl calls without changing policy')
parser.add_argument('--system-root-preflight', action='store_true', help='Issue native root authentication on mounted RAM volumes and stop without switching root or starting userland')
parser.add_argument('--system-root-switch', action='store_true', help='Pivot md0 to the OS volume with native vfs_switch_root; mount-only builds stop after verification, user-entry builds arm the bounded native-root init')
parser.add_argument('--system-cache', action='store_true', help='Use the verified RAM cryptex and original no-ignition dyld startup')
args = parser.parse_args()
if args.system_cache and not (args.system_root_switch and args.system_user_entry and args.progress):
    parser.error('--system-cache requires native root switch and progress')

if args.system_root_preflight and args.system_root_switch:
    parser.error('--system-root-preflight and --system-root-switch are exclusive archive modes')
if args.system_root_preflight and (not args.system_objects or args.system_user_entry or args.main_entry or args.progress or args.launch_constraints_observer):
    parser.error('--system-root-preflight requires mount-only system objects')
if args.system_root_switch and (not args.system_objects or args.launch_constraints_observer):
    parser.error('--system-root-switch requires system objects without launch observation')
if args.launch_constraints_observer and (not args.system_user_entry or not args.main_entry or args.progress):
    parser.error('--launch-constraints-observer requires bounded system main-entry')
if args.system_objects and (not args.user_entry or (args.progress and not args.system_root_switch)):
    parser.error('--system-objects requires --user-entry; progress additionally requires --system-root-switch')
if args.system_user_entry and not args.system_objects:
    parser.error('--system-user-entry requires --system-objects')
if args.restore_trust_cache and not args.progress:
    parser.error('--restore-trust-cache requires --progress')
if args.progress and not args.main_entry:
    parser.error('--progress requires --main-entry')
if args.main_entry and not args.user_entry:
    parser.error('--main-entry requires --user-entry')
pins = json.loads(args.pins.read_text())
if pins.get('system_cache', False) is not args.system_cache:
    raise SystemExit('System cache pins disagree with archive mode')
validate_stage_policy(pins, args.user_entry, args.main_entry, args.progress)
if pins.get('observe_launch_constraints', False) is not args.launch_constraints_observer:
    raise SystemExit('Launch observation pins disagree with the archive mode')
if pins.get('system_root_preflight', False) is not args.system_root_preflight:
    raise SystemExit('Root preflight pins disagree with the archive mode')
if args.system_root_preflight and pins.get('system_userland_allowed') is not False:
    raise SystemExit('Root preflight must explicitly prohibit system userland')
if pins.get('system_root_switch', False) is not args.system_root_switch:
    raise SystemExit('Root switch pins disagree with the archive mode')
if pins.get('follow_ptrace_deny_attach', False) and not args.progress:
    raise SystemExit('Deny-attach follow allowance requires the progress archive')
if args.system_root_switch and pins.get('system_userland_allowed') is not False:
    raise SystemExit('Root switch must explicitly prohibit system userland')
if (pins['image_bytes'] != 184549376 or pins['file_bytes'] != 561856 or
        pins['image_sha256'] != 'aadb59ed21a8f0218a348f070913ab5a7679805c58408534d811170871f1fc05' or
        pins['file_sha256'] != '8cec9a3c44a443b08e977ccc5d577850316ad428c575dc01f09b018dbd5b6a25' or
        pins['file_path'] != '/sbin/launchd' or pins['physical_storage_allowed'] or
        pins['start_userland_allowed'] != args.user_entry or
        (args.user_entry and not args.main_entry and pins.get('stop_before_first_user_svc') is not True) or
        (args.main_entry and (pins.get('stop_at_launchd_main') != (not args.progress) or
                             pins.get('continue_after_main', False) != args.progress or
                             pins.get('block_process_creation', True) != (not args.progress) or
                             pins.get('follow_process_creation', False) != args.progress or
                             pins.get('main_entry_offset') != 85896 or pins.get('main_entry_bytes') != 'ff8305d1fc6f12a9f65713a9f44f14a9'))):
    raise SystemExit('Require exact inspected RAM disk and bounded stage policy')
if args.system_user_entry and (
        pins.get('system_file_path') != '/mnt1/sbin/launchd' or
        not pins.get('system_file_bytes') or
        len(bytes.fromhex(pins.get('system_file_sha256', ''))) != 32):
    raise SystemExit('Require exact system launchd pins for --system-user-entry')
if args.system_root_switch and (
        pins.get('system_file_path') != '/mnt1/sbin/launchd' or
        pins.get('system_file_bytes') != 561856 or
        pins.get('system_file_sha256') != '8cec9a3c44a443b08e977ccc5d577850316ad428c575dc01f09b018dbd5b6a25'):
    raise SystemExit('Require the photo-verified system launchd pins for --system-root-switch')
if args.system_user_entry and args.main_entry and pins['system_file_sha256'] != pins['file_sha256']:
    # The hardware checkpoint offset is pinned from the restore ramdisk binary;
    # the system entry is only covered by it while the two files are identical.
    raise SystemExit('--main-entry system checkpoint requires byte-identical system launchd')
for key, enabled in [('XNU_RAM_USER_ENTRY', args.user_entry), ('XNU_RAM_MAIN_PROBE', args.main_entry), ('XNU_RAM_PROGRESS', args.progress), ('XNU_RAM_RESTORE_TRUST', bool(args.restore_trust_cache)), ('XNU_RAM_LAUNCH_OBSERVE', args.launch_constraints_observer)]:
    if key in os.environ and os.environ[key] != str(int(enabled)):
        raise SystemExit(key + ' disagrees with requested diagnostic archive mode')
out = ROOT / 'build/ram-root-probe'
out.mkdir(parents=True, exist_ok=True)
header = '#define RAM_ROOT_IMAGE_BYTES UINT64_C(184549376)\n#define RAM_ROOT_FILE_BYTES 561856\n'
header += '#define RAM_ROOT_ALLOW_USER_ENTRY ' + str(int(args.user_entry)) + '\n'
header += '#define RAM_ROOT_ALLOW_MAIN_ENTRY ' + str(int(args.main_entry)) + '\n'
header += '#define RAM_ROOT_PROGRESS ' + str(int(args.progress)) + '\n'
deadline = pins.get('progress_deadline_seconds', 90)
if deadline not in (90, 300, 900) or (deadline in (300, 900) and not (args.system_cache and pins.get('native_cs_hash_cache'))):
    raise SystemExit('Extended deadline requires verified system cache integrity mode')
svc_limit = pins.get('progress_svc_limit', 131072)
if svc_limit not in (131072, 1048576):
    raise SystemExit('Unsupported progress SVC observation limit')
if svc_limit != 131072 and not args.progress:
    raise SystemExit('Raised SVC observation limit requires the progress archive')
header += '#define RAM_ROOT_PROGRESS_SECONDS %d\n' % deadline
header += '#define RAM_ROOT_PROGRESS_SVC_LIMIT %dU\n' % svc_limit
header += '#define RAM_ROOT_NATIVE_HASH_CACHE %d\n' % int(pins.get('native_cs_hash_cache', False))
header += '#define RAM_ROOT_FOLLOW_DENY_ATTACH %d\n' % int(bool(pins.get('follow_ptrace_deny_attach', False)))
header += '#define RAM_ROOT_RESTORE_TRUST ' + str(int(bool(args.restore_trust_cache))) + '\n'
if args.main_entry:
    header += '#define RAM_ROOT_MAIN_OFFSET UINT64_C(85896)\n'
    header += 'static const uint8_t ram_root_main_bytes[16] = {' + ','.join(hex(x) for x in bytes.fromhex(pins['main_entry_bytes'])) + '};\n'
for name, key in [('image', 'image_sha256'), ('file', 'file_sha256')]:
    value = bytes.fromhex(pins[key])
    if len(value) != 32: raise SystemExit('Expected SHA256 pins')
    header += 'static const uint8_t ram_root_' + name + '_sha256[32] = {' + ','.join(hex(x) for x in value) + '};\n'
(out / 'RamRootProbePins.h').write_text(header)
system_header = '#define RAM_SYSTEM_ALLOW_USER_ENTRY ' + str(int(args.system_user_entry)) + '\n'
system_header += '#define RAM_SYSTEM_ROOT_PREFLIGHT ' + str(int(args.system_root_preflight)) + '\n'
system_header += '#define RAM_SYSTEM_ROOT_SWITCH ' + str(int(args.system_root_switch)) + '\n'
if args.system_user_entry or args.system_root_switch:
    system_header += '#define RAM_SYSTEM_LAUNCHD_BYTES UINT64_C(%d)\n' % pins['system_file_bytes']
    system_digest = bytes.fromhex(pins['system_file_sha256'])
    system_header += 'static const uint8_t ram_system_launchd_sha256[32] = {' + ','.join(hex(x) for x in system_digest) + '};\n'
system_header += '#define RAM_SYSTEM_CACHE ' + str(int(args.system_cache)) + '\n'
if args.system_cache:
    system_header += '#define RAM_SYSTEM_DYLD_BYTES UINT64_C(%d)\n' % pins['system_dyld_bytes']
    system_header += 'static const uint8_t ram_system_dyld_sha256[32] = {' + ','.join(hex(x) for x in bytes.fromhex(pins['system_dyld_sha256'])) + '};\n'
(out / 'RamSystemProbePins.h').write_text(system_header)
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
objects = [obj]
if args.system_objects:
    system_source=ROOT/'kits/sheng/RamSystemProbe.c'
    system_object=out/'RamSystemProbe.o'
    system_command=[str(system_source) if value==str(source) else str(system_object) if value==str(obj) else value for value in command]
    system_command += ['-I',str(ROOT/'package/UniXNUPkg/Include')]
    (out/'system-compile-command.json').write_text(json.dumps(system_command,indent=2)+'\n')
    result=subprocess.run(system_command,cwd=record['directory'],capture_output=True,text=True)
    (out/'system-compile.log').write_text(result.stdout+result.stderr)
    if result.returncode: raise SystemExit(result.stdout+result.stderr)
    objects.append(system_object)
if args.system_root_preflight:
    preflight_source = ROOT / 'kits/sheng/RamSystemRootPreflight.c'
    preflight_object = out / 'RamSystemRootPreflight.o'
    preflight_command = [str(preflight_source) if value == str(source) else str(preflight_object) if value == str(obj) else value for value in command]
    (out / 'preflight-compile-command.json').write_text(json.dumps(preflight_command, indent=2) + '\n')
    result = subprocess.run(preflight_command, cwd=record['directory'], capture_output=True, text=True)
    (out / 'preflight-compile.log').write_text(result.stdout + result.stderr)
    if result.returncode: raise SystemExit(result.stdout + result.stderr)
    objects.append(preflight_object)
if args.system_root_switch:
    switch_source = ROOT / 'kits/sheng/RamSystemRootSwitch.c'
    switch_object = out / 'RamSystemRootSwitch.o'
    switch_command = [str(switch_source) if value == str(source) else str(switch_object) if value == str(obj) else value for value in command]
    (out / 'switch-compile-command.json').write_text(json.dumps(switch_command, indent=2) + '\n')
    result = subprocess.run(switch_command, cwd=record['directory'], capture_output=True, text=True)
    (out / 'switch-compile.log').write_text(result.stdout + result.stderr)
    if result.returncode: raise SystemExit(result.stdout + result.stderr)
    objects.append(switch_object)
if args.system_cache:
    cache_source = ROOT / 'kits/sheng/RamSystemCache.c'
    cache_object = out / 'RamSystemCache.o'
    cache_command = [str(cache_source) if value == str(source) else str(cache_object) if value == str(obj) else value for value in command]
    result = subprocess.run(cache_command, cwd=record['directory'], capture_output=True, text=True)
    (out / 'cache-compile-command.json').write_text(json.dumps(cache_command, indent=2) + '\n')
    (out / 'cache-compile.log').write_text(result.stdout + result.stderr)
    if result.returncode: raise SystemExit(result.stdout + result.stderr)
    objects.append(cache_object)
if args.main_entry:
    main_source = ROOT / 'kits/sheng/RamLaunchdProbe.c'
    main_object = out / 'RamLaunchdProbe.o'
    main_command = [str(main_source) if value == str(source) else str(main_object) if value == str(obj) else value for value in command]
    (out / 'main-compile-command.json').write_text(json.dumps(main_command, indent=2) + '\n')
    result = subprocess.run(main_command, cwd=record['directory'], capture_output=True, text=True)
    (out / 'main-compile.log').write_text(result.stdout + result.stderr)
    if result.returncode: raise SystemExit(result.stdout + result.stderr)
    if struct.unpack_from('<4I', main_object.read_bytes()) != (0xfeedfacf, 0x100000c, 0, 1):
        raise SystemExit('Expected ordinary ARM64 main probe object')
    objects.append(main_object)
if args.launch_constraints_observer:
    observation_source = ROOT / 'kits/sheng/RamLaunchConstraintProbe.c'
    observation_object = out / 'RamLaunchConstraintProbe.o'
    observation_command = [str(observation_source) if value == str(source) else str(observation_object) if value == str(obj) else value for value in command]
    (out / 'observation-compile-command.json').write_text(json.dumps(observation_command, indent=2) + '\n')
    result = subprocess.run(observation_command, cwd=record['directory'], capture_output=True, text=True)
    (out / 'observation-compile.log').write_text(result.stdout + result.stderr)
    if result.returncode: raise SystemExit(result.stdout + result.stderr)
    objects.append(observation_object)
if args.restore_trust_cache:
    restore = args.restore_trust_cache.read_bytes()
    if (len(restore) != 5898 or struct.unpack_from('<I', restore)[0] != 1 or
            struct.unpack_from('<I', restore, 20)[0] != 267 or
            hashlib.sha256(restore).hexdigest() != '54e80085f2de19d76f870aa63366ef520a905913dc0208381010864f7d1fbb09'):
        raise SystemExit('Require the entire unchanged original RestoreTrustCache v1')
    restore_header = 'static const uint8_t ram_restore_original[5898] __attribute__((aligned(4))) = {' + ','.join(hex(x) for x in restore) + '};\n'
    restore_header += 'static const uint8_t ram_restore_sha256[32] = {' + ','.join(hex(x) for x in hashlib.sha256(restore).digest()) + '};\n'
    (out / 'RamRestoreTrustPins.h').write_text(restore_header)
    restore_source = ROOT / 'kits/sheng/RamRestoreTrust.c'
    restore_object = out / 'RamRestoreTrust.o'
    restore_command = [str(restore_source) if value == str(source) else str(restore_object) if value == str(obj) else value for value in command]
    (out / 'restore-compile-command.json').write_text(json.dumps(restore_command, indent=2) + '\n')
    result = subprocess.run(restore_command, cwd=record['directory'], capture_output=True, text=True)
    (out / 'restore-compile.log').write_text(result.stdout + result.stderr)
    if result.returncode: raise SystemExit(result.stdout + result.stderr)
    if struct.unpack_from('<4I', restore_object.read_bytes()) != (0xfeedfacf, 0x100000c, 0, 1):
        raise SystemExit('Expected ordinary ARM64 restore module object')
    objects.append(restore_object)
archive = out / 'libRamRootProbe.a'
archive.unlink(missing_ok=True)
subprocess.run(['xcrun', 'ar', 'rcs', archive, *objects], check=True)
(out / 'build-report.json').write_text(json.dumps(dict(pins=pins,
    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
    user_entry=args.user_entry, main_entry=args.main_entry, progress=args.progress,
    system_objects=args.system_objects, system_user_entry=args.system_user_entry,
    system_source_sha256=hashlib.sha256(system_source.read_bytes()).hexdigest() if args.system_objects else None,
    main_source_sha256=hashlib.sha256(main_source.read_bytes()).hexdigest() if args.main_entry else None,
    launch_constraints_observer=args.launch_constraints_observer,
    observation_source_sha256=hashlib.sha256(observation_source.read_bytes()).hexdigest() if args.launch_constraints_observer else None,
    system_root_preflight=args.system_root_preflight,
    preflight_source_sha256=hashlib.sha256(preflight_source.read_bytes()).hexdigest() if args.system_root_preflight else None,
    system_root_switch=args.system_root_switch, system_cache=args.system_cache,
    cache_source_sha256=hashlib.sha256(cache_source.read_bytes()).hexdigest() if args.system_cache else None,
    switch_source_sha256=hashlib.sha256(switch_source.read_bytes()).hexdigest() if args.system_root_switch else None,
    restore_trust=bool(args.restore_trust_cache),
    restore_trust_sha256=hashlib.sha256(restore).hexdigest() if args.restore_trust_cache else None,
    restore_source_sha256=hashlib.sha256(restore_source.read_bytes()).hexdigest() if args.restore_trust_cache else None,
    hardware_tested=False), indent=2) + '\n')
print('PASS: bounded RAM-root diagnostic, ordinary ARM64, strict kernel-ABI compilation')
