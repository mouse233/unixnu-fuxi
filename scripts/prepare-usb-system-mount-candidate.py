#!/usr/bin/env python3
"""Clone a system-mount candidate and configure a bounded early compressor.

Build products stay in their own candidate directory. Run its build-kernel.py
and finalize.py after preparing; this script never accesses the phone.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    parent, out = a.parent.resolve(), a.output.resolve()
    if out.exists() or out.parent != parent.parent:
        p.error('output must be a new sibling of the parent candidate')
    candidate = json.loads((parent / 'candidate.json').read_text())
    if not candidate.get('system_handoff'):
        p.error('parent must be a system-handoff candidate')
    if hashlib.sha256((parent / 'Mu-fuxi-usb-ram.img').read_bytes()).hexdigest() != candidate['sha256']:
        p.error('parent image does not match its candidate manifest')
    # APFS clones preserve the isolated source/build cache without duplicating
    # gigabytes of data. Do not use hardlinks: patching would change the parent.
    subprocess.run(['cp', '-cR', str(parent), str(out)], check=True)
    configure(parent, out)


def configure(parent, out):
    for path in out.glob('attempt-*'):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    # Hardware conclusions and delivered-image checks must be produced again.
    for name in ('device-test.json', 'verification.json', 'payload-verification.json',
                 'kmem-tests.json', 'mount-spi-tests.json', 'mount-trace-tests.json',
                 'state.json', 'restore-mountpoints.json'):
        (out / name).unlink(missing_ok=True)
    for name in ('board-effective.json', 'pins.json'):
        path = out / name
        path.write_text(path.read_text().replace(str(parent), str(out)))
    board_path = out / 'board-effective.json'
    board = json.loads(board_path.read_text())
    args = board['payload']['kernel_args']
    args = [x for x in args if x.split('=')[0] not in
            ('vm_compression_limit', '-disable_freezer_cseg_acct')]
    # This kernel uses 4 KiB software pages: 131072 pages = 512 MiB.
    # Disable only the freezer's extra swap-accounting reserve. Compression,
    # the fixed zone VA reservation and the allocator's failures stay active.
    args += ['vm_compression_limit=131072', '-disable_freezer_cseg_acct']
    board['payload']['kernel_args'] = args
    board_path.write_text(json.dumps(board, indent=2) + '\n')
    (out / 'REVIEW.md').write_text('# USB system mount: early compressor budget\n\nBuild and hardware validation pending.\n')
    (out / 'parent.json').write_text(json.dumps({
        'path': str(parent),
        'image_sha256': json.loads((parent / 'candidate.json').read_text())['sha256'],
        'configuration': 'early freezer accounting boot argument and 512 MiB compressor pool',
        'change': 'implementation changes and validation must be recorded in REVIEW.md',
        'hardware_result': 'pending',
    }, indent=2) + '\n')


if __name__ == '__main__':
    main()
