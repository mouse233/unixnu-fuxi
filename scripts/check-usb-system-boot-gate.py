#!/usr/bin/env python3
"""Exercise the temporary-boot gate with mocked fastboot; never use a device."""
import argparse
import contextlib
import hashlib
import io
import json
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    script = Path(__file__).with_name('boot-ram-window-once.py')
    results = []
    names = ['valid', 'missing-nodes', 'wrong-image', 'wrong-kernel', 'unverified-links',
             'empty-checks', 'short-kmem', 'wrong-spi', 'legacy-without-nodes']
    for name in names:
        with tempfile.TemporaryDirectory(prefix='ram-boot-gate-') as tmp:
            root = Path(tmp).resolve()
            image = root / 'boot.img'
            image.write_bytes(b'offline boot gate fixture')
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            kernel = '5' * 64
            reports = {
                'verification': dict(boot_sha256=digest, compressed_firmware_roundtrip=True,
                                     system_handoff=True, kernel_handoff=True, receiver_only=False,
                                     delivered_modules={'FuxiUsbRamDxe':'x','UsbfnDwc3Dxe':'y'}),
                'payload-verification': dict(boot_sha256=digest,kernel_sha256=kernel,payload_tag='FUXIPAYL',ram_volume_nodes=True),
                'kmem-tests': dict(boot_sha256=digest,kernel_sha256=kernel,remaining_va_lower_bound=600*2**20),
                'mount-spi-tests': dict(boot_sha256=digest,kernel_sha256=kernel),
                'volume-nodes-tests': dict(boot_sha256=digest,kernel_sha256=kernel,read_only_links_verified=True,passed=['actual C checks'])}
            if name in ('missing-nodes','legacy-without-nodes'):
                del reports['volume-nodes-tests']
            if name == 'legacy-without-nodes': reports['payload-verification']['ram_volume_nodes'] = False
            if name == 'wrong-image': reports['volume-nodes-tests']['boot_sha256'] = '0'*64
            if name == 'wrong-kernel': reports['volume-nodes-tests']['kernel_sha256'] = '0'*64
            if name == 'unverified-links': reports['volume-nodes-tests']['read_only_links_verified'] = False
            if name == 'empty-checks': reports['volume-nodes-tests']['passed'] = []
            if name == 'short-kmem': reports['kmem-tests']['remaining_va_lower_bound'] = 1
            if name == 'wrong-spi': reports['mount-spi-tests']['kernel_sha256'] = '0'*64
            for key, value in reports.items():
                (root / (key+'.json')).write_text(json.dumps(value))
            commands = []
            def fake_run(command, **kwargs):
                commands.append(command)
                if command[-1] == 'devices': output='fcde91e\tfastboot\n'
                elif command[-2:] == ['getvar','product']: output='product: fuxi\n'
                elif command[-2:] == ['getvar','unlocked']: output='unlocked: yes\n'
                elif command[-2:] == ['getvar','current-slot']: output='current-slot: b\n'
                elif command[-2:] == ['getvar','max-download-size']: output='max-download-size: 805306368\n'
                else:
                    assert command[-2:] == ['boot',str(image)]
                    output='Booting OKAY\n'
                return subprocess.CompletedProcess(command,0,output,'')
            argv = [str(script),'--image',str(image),'--expected-sha256',digest,'--profile','usb-system']
            with patch.object(sys,'argv',argv), patch('subprocess.run',fake_run), contextlib.redirect_stdout(io.StringIO()):
                try:
                    runpy.run_path(str(script),run_name='__main__')
                except SystemExit as error:
                    code=error.code
                else:
                    raise AssertionError('gate did not exit')
            allowed = name in ('valid','legacy-without-nodes')
            assert (code == 0) == allowed, (name,code)
            assert len(commands) == (6 if allowed else 0), (name,commands)
            results.append(dict(case=name,allowed=allowed,fastboot_mock_calls=len(commands)))
    out = a.output.resolve()
    report = dict(passed=results,fastboot_is_mocked=True,gate_source_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
                  boot_sha256=hashlib.sha256((out/'Mu-fuxi-usb-ram.img').read_bytes()).hexdigest())
    (out/'boot-gate-tests.json').write_text(json.dumps(report,indent=2)+'\n')
    print('PASS: 9 temporary-boot gate cases; no real fastboot commands')


if __name__ == '__main__': main()
