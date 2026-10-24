#!/usr/bin/env python3
"""Run the system candidate checks with real exit codes and image-bound logs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'base', 'uefi', 'spi-baseline', 'trace-parent', 'witness-parent'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--ramdisk', type=Path)
    parser.add_argument('--launchd', type=Path)
    parser.add_argument('--init-defect-baseline', type=Path)
    parser.add_argument('--observer-parent', type=Path, help='Parent without the launch observation patch')
    args = parser.parse_args()
    out = args.output.resolve()
    image = out / 'Mu-fuxi-usb-ram.img'
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    root = Path(__file__).resolve().parents[1]
    board = json.loads((out / 'board-effective.json').read_text())
    main_entry = 'ram-root-probe=3' in board['payload']['kernel_args']
    if main_entry and (not args.ramdisk or not args.launchd):
        parser.error('main-entry checks require --ramdisk and --launchd')
    observer = 'ram-launch-observe=1' in board['payload']['kernel_args']
    if observer and not args.observer_parent:
        parser.error('launch observation checks require --observer-parent')
    shared = ['--output', str(out), '--base', str(args.base.resolve())]
    checks = [
        ('smmu', 'check-usb-smmu-audit.py', ['--uefi', str(args.uefi.resolve()), '--report', str(out/'smmu-tests.json')]),
        ('route', 'check-usb-smmu-route.py', ['--uefi', str(args.uefi.resolve()), '--report', str(out/'route-tests.json')]),
        ('receiver', 'check-usb-ram.py', ['--uefi', str(args.uefi.resolve()), '--report', str(out/'host-tests.json')]),
        ('image', 'check-fuxi-usb-ram-image.py', ['--uefi', str(args.uefi.resolve()), '--output', str(out)]),
        ('mount', 'check-usb-system-mount.py', shared),
        ('kmem', 'check-usb-system-kmem.py', shared),
        ('spi', 'check-usb-system-mount-spi.py', shared + ['--baseline', str(args.spi_baseline.resolve())]),
        ('trace', 'check-usb-system-mount-trace.py', shared + ['--parent', str(args.trace_parent.resolve())]),
        ('volume-args', 'check-usb-system-mount-volume-args.py', shared),
        ('witness', 'check-usb-system-mount-witness.py', shared + ['--parent', str(args.witness_parent.resolve())]),
        ('iokit', 'check-usb-system-iokit.py', shared),
        ('gate', 'check-usb-system-boot-gate.py', ['--output', str(out)]),
    ]
    if main_entry:
        policy = ['--output', str(out), '--launchd', str(args.launchd.resolve())]
        if args.init_defect_baseline:
            policy += ['--baseline', str(args.init_defect_baseline.resolve())]
        checks += [
            ('probe-policy', 'check-ram-probe-policy.py', policy),
            ('probe-guards', 'check-ram-launchd-guards.py', [
                '--ramdisk', str(args.ramdisk.resolve()), '--launchd', str(args.launchd.resolve()),
                '--syscall-header', str(out/'work/tree/build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/sys/syscall.h'),
                '--system-entry', '--output', str(out)]),
        ]
    if observer:
        checks.append(('launch-observer', 'check-ram-launch-observer.py',
                       shared + ['--parent', str(args.observer_parent.resolve())]))
    if 'ram-system-root-preflight=1' in board['payload']['kernel_args']:
        checks.append(('root-preflight', 'check-ram-system-root-preflight.py', shared))
    if 'ram-system-root-switch=1' in board['payload']['kernel_args']:
        checks.append(('root-switch', 'check-ram-system-root-switch.py', shared))
    logs = out / 'logs'
    logs.mkdir(exist_ok=True)
    report = dict(boot_sha256=digest, complete=False, checks=[])
    target = out / 'check-suite.json'
    for name, script, options in checks:
        command = [sys.executable, str(root/'scripts'/script), *options]
        print('CHECK ' + name, flush=True)
        with (logs / ('check-' + name + '.log')).open('w') as log:
            result = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT)
        report['checks'].append(dict(name=name, command=command, returncode=result.returncode,
                                    script_sha256=hashlib.sha256((root/'scripts'/script).read_bytes()).hexdigest()))
        target.write_text(json.dumps(report, indent=2) + '\n')
        if result.returncode:
            raise SystemExit(f'{name} failed ({result.returncode}); see {logs / ("check-" + name + ".log")}')
        print('PASS ' + name, flush=True)
    if hashlib.sha256(image.read_bytes()).hexdigest() != digest:
        raise SystemExit('image changed during checks')
    report['complete'] = True
    target.write_text(json.dumps(report, indent=2) + '\n')
    print(f'PASS: {len(checks)} checks completed for {digest}', flush=True)


if __name__ == '__main__':
    main()
