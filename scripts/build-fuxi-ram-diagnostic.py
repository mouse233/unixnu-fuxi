#!/usr/bin/env python3
"""Build isolated RAM diagnostics; never flash or start a phone."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['base', 'inputs', 'ipsw', 'uefi']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--stage', choices=['mount', 'user-entry', 'main-entry', 'progress', 'system-mount'], default='mount')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--restore-trust-cache', type=Path, help='Exact original v1 module (progress defaults to INPUTS/original-restore.trustcache)')
    parser.add_argument('--unique-chip-id', required=True)
    parser.add_argument('--owned-memory-size', type=lambda value: int(value, 0),
                        help='Override the darwinboot-memory-size window (256 MiB..16 GiB, 2 MiB aligned)')
    parser.add_argument('-j', '--jobs', type=int, default=10)
    parser.add_argument('--plan-only', action='store_true', help='Write effective config/board without building')
    args = parser.parse_args()
    if args.owned_memory_size is not None and (
            not 0x10000000 <= args.owned_memory_size <= 0x400000000 or args.owned_memory_size % 0x200000):
        parser.error('--owned-memory-size must be 256 MiB..16 GiB and 2 MiB aligned')
    device = Path(__file__).resolve().parents[1]
    base, inputs = args.base.expanduser().resolve(), args.inputs.expanduser().resolve()
    out = (args.output or device / 'out' / ('ram-' + args.stage)).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(base / 'scripts'))
    from unixnu import cli, kernel, payload
    from unixnu.config import Context, parse_config
    spec = importlib.util.spec_from_file_location('packed_ramdisk', base / 'drivers/DarwinBoot/Test/packed_ramdisk.py')
    packed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packed)
    pins = json.loads((device / 'diagnostics/ram-root/pins.json').read_text())
    if args.restore_trust_cache and args.stage != 'progress':
        parser.error('--restore-trust-cache requires --stage progress')
    restore = (args.restore_trust_cache.expanduser().resolve() if args.restore_trust_cache else
               inputs / 'original-restore.trustcache' if args.stage == 'progress' else None)
    user_entry = args.stage != 'mount'
    system_mount = args.stage == 'system-mount'
    progress = args.stage == 'progress'
    main_entry = args.stage in ('main-entry', 'progress')
    pins.update(start_userland_allowed=user_entry)
    if user_entry:
        pins['stop_before_first_user_svc'] = not main_entry
    if main_entry:
        pins.update(stop_at_launchd_main=not progress, continue_after_main=progress,
            block_process_creation=not progress, follow_process_creation=progress,
            main_entry_offset=85896, main_entry_bytes='ff8305d1fc6f12a9f65713a9f44f14a9')
    pins_path = out / 'pins.json'
    pins_path.write_text(json.dumps(pins, indent=2) + '\n')
    encoded = out / 'restore.dbr'
    if not args.plan_only:
        if not encoded.exists():
            metadata = packed.pack(inputs / 'restore.dmg', encoded)
        else:
            metadata, _ = packed.inspect(encoded)
        if metadata['sha256'] != pins['image_sha256']:
            raise ValueError('RAM-disk reconstruction differs from original')
        (out / 'ramdisk.json').write_text(json.dumps(metadata, indent=2) + '\n')
    options = ['build', 'kernel', '-D', str(device), '--out', str(out / 'cache'),
        '--ipsw', str(args.ipsw.expanduser().resolve()),
        '--uefi', str(args.uefi.expanduser().resolve()), '--data-ramdisk', str(encoded),
        '--unique-chip-id', args.unique_chip_id, '-j', str(args.jobs)]
    if user_entry:
        trust = inputs / 'original-static-system.bundle'
        if hashlib.sha256(trust.read_bytes()).hexdigest() != '7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7':
            raise ValueError('Require the two unchanged original trust modules')
        options += ['--trust-cache', str(trust)]
    ctx = Context(cli.parser().parse_args(options))
    # Each stage owns its cache as well as its profile, logs and products.
    ctx.work, ctx.logs, ctx.config_path = out / 'work', out / 'logs', out / 'config'
    ctx.tree = ctx.work / 'tree'
    ctx.config_path.write_text((device / 'configs/fuxi_ramroot_defconfig').read_text() +
        '\nCONFIG_XNU_BUILD_ARCHIVES=y\nCONFIG_XNU_RAM_USER_ENTRY=' + ('y' if user_entry else 'n') +
        '\nCONFIG_XNU_RAM_MAIN_PROBE=' + ('y' if main_entry else 'n') +
        '\nCONFIG_XNU_RAM_PROGRESS=' + ('y' if progress else 'n') +
        '\nCONFIG_XNU_RAM_RESTORE_TRUST=' + ('y' if restore else 'n') + '\n')
    ctx.config = parse_config(ctx.config_path.read_text())
    ctx.board = copy.deepcopy(ctx.board)
    ctx.board['kernel']['archives'] = [
        'build-corecrypto-14.py', 'build-corecrypto-14.py --namespace',
        'build-arm-driver.py ArmDTPlatform', 'build-arm-driver.py ArmCpu',
        'build-arm-driver.py NativeAES', 'build-native-aes-bindings.py',
        'build-sheng-platform-power.py', 'build-libpthread-kernel.py', 'build-legacy-stub.py',
        'build-ram-root-probe.py ' + ('--user-entry ' if user_entry else '') +
        ('--main-entry ' if main_entry else '') + ('--progress ' if progress else '') +
        ('--restore-trust-cache ' + shlex.quote(str(restore)) + ' ' if restore else '') +
        ('--system-objects ' if system_mount else '') + '--pins ' + shlex.quote(str(pins_path)),
    ]
    ctx.board['payload']['title'] = 'fuxi RAM diagnostic: ' + args.stage
    ctx.board['payload']['kernel_args'] = [
        '-v', 'serial=3', 'debug=0x14e', 'kt_fixed_zones=120', 'rd=md0',
        'ram-root-probe=' + ('4' if progress else '3' if main_entry else '2' if user_entry else '1'), 'offline-apfs-load=1', 'sheng-pmgr=2',
        'native-ufs-root=0', 'native-ufs-probe=0', 'sheng-data-persist=0', 'sheng-log=0', 'boot-progress=0',
    ] + (['launchdsuffix=release'] if user_entry else [])
    ctx.board['payload']['adt'].update(packed_data_ramdisk=False, os_environment='ramdisk')
    ctx.board['payload']['adt'].pop('root_match_volume_uuid', None)
    if system_mount:
        ctx.board['payload']['kernel_args'].append('ram-system-probe=1')
        ctx.board['payload']['adt'].update(owned_memory_size='0x300000000',ram_system_objects=True)
    if args.owned_memory_size is not None:
        ctx.board['payload']['adt']['owned_memory_size'] = hex(args.owned_memory_size)
    (out / 'board-effective.json').write_text(json.dumps(ctx.board, indent=2) + '\n')
    (out / 'build-layout.json').write_text(json.dumps(dict(stage=args.stage,
        base=str(base), device=str(device), output=str(out), cache=str(ctx.out_root),
        tree=str(ctx.tree), uefi=str(args.uefi.expanduser().resolve()),
        owned_memory_size=ctx.board['payload']['adt'].get('owned_memory_size')), indent=2) + '\n')
    if args.plan_only:
        print('Plan written to', out)
        return
    # This profile owns a fresh source cache; do not inherit hand-edited overlays.
    os.environ['XNU_FRESH_PATCHES'] = '1'
    modules = cli.prepare(ctx, ['kernel'])
    kernel.build(ctx, modules)
    kernel.prelink(ctx)
    ctx.uefi = args.uefi.expanduser().resolve()
    payload.build(ctx)


if __name__ == '__main__':
    main()
