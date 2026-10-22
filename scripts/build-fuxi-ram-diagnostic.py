#!/usr/bin/env python3
"""Build a mount-only or first-EL0-SVC diagnostic without changing normal board defaults."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['base', 'inputs', 'ipsw', 'uefi']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--stage', choices=['mount', 'user-entry'], default='mount')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--unique-chip-id', required=True)
    parser.add_argument('-j', '--jobs', type=int, default=10)
    parser.add_argument('--plan-only', action='store_true', help='Write effective config/board without building')
    args = parser.parse_args()
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
    user_entry = args.stage == 'user-entry'
    pins.update(start_userland_allowed=user_entry)
    if user_entry:
        pins['stop_before_first_user_svc'] = True
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
    options = ['build', 'kernel', '-D', str(device), '--ipsw', str(args.ipsw.expanduser().resolve()),
        '--uefi', str(args.uefi.expanduser().resolve()), '--data-ramdisk', str(encoded),
        '--unique-chip-id', args.unique_chip_id, '-j', str(args.jobs)]
    if user_entry:
        trust = inputs / 'original-static-system.bundle'
        if hashlib.sha256(trust.read_bytes()).hexdigest() != '7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7':
            raise ValueError('Require the two unchanged original trust modules')
        options += ['--trust-cache', str(trust)]
    ctx = Context(cli.parser().parse_args(options))
    # Keep the existing ctx.tree cache; isolate profiles, logs and final products.
    ctx.work, ctx.logs, ctx.config_path = out / 'work', out / 'logs', out / 'config'
    ctx.config_path.write_text((device / 'configs/fuxi_ramroot_defconfig').read_text() +
        '\nCONFIG_XNU_RAM_USER_ENTRY=' + ('y' if user_entry else 'n') + '\n')
    ctx.config = parse_config(ctx.config_path.read_text())
    ctx.board = copy.deepcopy(ctx.board)
    ctx.board['kernel']['archives'] = [
        'build-corecrypto-14.py', 'build-corecrypto-14.py --namespace',
        'build-arm-driver.py ArmDTPlatform', 'build-arm-driver.py ArmCpu',
        'build-arm-driver.py NativeAES', 'build-native-aes-bindings.py',
        'build-sheng-platform-power.py', 'build-libpthread-kernel.py', 'build-legacy-stub.py',
        'build-ram-root-probe.py ' + ('--user-entry ' if user_entry else '') + '--pins ' + shlex.quote(str(pins_path)),
    ]
    ctx.board['payload']['title'] = 'fuxi RAM diagnostic: ' + args.stage
    ctx.board['payload']['kernel_args'] = [
        '-v', 'serial=3', 'debug=0x14e', 'kt_fixed_zones=120', 'rd=md0',
        'ram-root-probe=' + ('2' if user_entry else '1'), 'offline-apfs-load=1', 'sheng-pmgr=2',
        'native-ufs-root=0', 'native-ufs-probe=0', 'sheng-data-persist=0', 'sheng-log=0', 'boot-progress=0',
    ] + (['launchdsuffix=release'] if user_entry else [])
    ctx.board['payload']['adt'].update(packed_data_ramdisk=False, os_environment='ramdisk')
    ctx.board['payload']['adt'].pop('root_match_volume_uuid', None)
    (out / 'board-effective.json').write_text(json.dumps(ctx.board, indent=2) + '\n')
    if args.plan_only:
        print('Plan written to', out)
        return
    modules = cli.prepare(ctx, ['kernel'])
    kernel.build(ctx, modules)
    kernel.prelink(ctx)
    ctx.uefi = args.uefi.expanduser().resolve()
    payload.build(ctx)


if __name__ == '__main__':
    main()
