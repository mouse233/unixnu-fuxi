#!/usr/bin/env python3
"""Run the system candidate checks with real exit codes and image-bound logs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import shlex
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'base', 'uefi', 'spi-baseline', 'trace-parent', 'witness-parent'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--ramdisk', type=Path)
    parser.add_argument('--launchd', type=Path)
    parser.add_argument('--dyld', type=Path)
    parser.add_argument('--init-defect-baseline', type=Path)
    parser.add_argument('--observer-parent', type=Path, help='Parent without the launch observation patch')
    args = parser.parse_args()
    out = args.output.resolve()
    image = out / 'Mu-fuxi-usb-ram.img'
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    root = Path(__file__).resolve().parents[1]
    board = json.loads((out / 'board-effective.json').read_text())
    archives = [shlex.split(x) for x in board['kernel']['archives']
                if shlex.split(x)[0] == 'build-ram-root-probe.py']
    if len(archives) != 1:
        parser.error('require exactly one RAM probe archive')
    main_entry, progress, native = (flag in archives[0] for flag in
                                   ('--main-entry', '--progress', '--system-root-switch'))
    cache = '--system-cache' in archives[0]
    if cache and not args.dyld:
        parser.error('system-cache checks require the original --dyld')
    selector = '4' if progress else '3' if main_entry else '2'
    if [x for x in board['payload']['kernel_args'] if x.startswith('ram-root-probe=')] != ['ram-root-probe=' + selector]:
        parser.error('probe archive and boot selector disagree')
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
                '--system-entry', '--output', str(out)] +
                (['--progress'] if progress else []) + (['--native-root'] if native else [])),
        ]
    if observer:
        checks.append(('launch-observer', 'check-ram-launch-observer.py',
                       shared + ['--parent', str(args.observer_parent.resolve())]))
    if 'ram-system-root-preflight=1' in board['payload']['kernel_args']:
        checks.append(('root-preflight', 'check-ram-system-root-preflight.py', shared))
    if 'ram-system-root-switch=1' in board['payload']['kernel_args']:
        switch_archives = [s for s in board['kernel']['archives']
                           if s.startswith('build-ram-root-probe.py')]
        if any('--progress' in s for s in switch_archives):
            switch_mode = ['--mode', 'progress']
        elif any('--system-user-entry' in s for s in switch_archives):
            switch_mode = ['--mode', 'user-entry']
        else:
            switch_mode = ['--mode', 'mount']
        checks.append(('root-switch', 'check-ram-system-root-switch.py', shared + switch_mode))
    if cache:
        checks.append(('system-cache', 'check-ram-system-cache.py', shared + ['--dyld', str(args.dyld.resolve())]))
        checks.append(('cache-admission', 'check-ram-cache-admission.py', shared))
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    logs = out / 'check-runs' / run_id
    logs.mkdir(parents=True, exist_ok=False)
    report = dict(run_id=run_id, boot_sha256=digest, complete=False, checks=[])
    target = out / 'check-suite.json'
    # Preserve reports left by the pre-history runner, as well as every new run.
    if target.exists():
        (logs / 'previous-check-suite.json').write_bytes(target.read_bytes())
    for name, script, options in checks:
        command = [sys.executable, str(root/'scripts'/script), *options]
        print('CHECK ' + name, flush=True)
        with (logs / ('check-' + name + '.log')).open('w') as log:
            result = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT)
        report['checks'].append(dict(name=name, command=command, returncode=result.returncode,
                                    script_sha256=hashlib.sha256((root/'scripts'/script).read_bytes()).hexdigest()))
        target.write_text(json.dumps(report, indent=2) + '\n')
        (logs / 'check-suite.json').write_text(json.dumps(report, indent=2) + '\n')
        if result.returncode:
            raise SystemExit(f'{name} failed ({result.returncode}); see {logs / ("check-" + name + ".log")}')
        print('PASS ' + name, flush=True)
    if hashlib.sha256(image.read_bytes()).hexdigest() != digest:
        raise SystemExit('image changed during checks')
    report['complete'] = True
    target.write_text(json.dumps(report, indent=2) + '\n')
    (logs / 'check-suite.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'PASS: {len(checks)} checks completed for {digest}', flush=True)


if __name__ == '__main__':
    main()
