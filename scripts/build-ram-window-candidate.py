#!/usr/bin/env python3
"""Repackage an existing RAM progress kernel with a larger, probed window.

Run firmware in the arm64 Linux build container, then payload on the Mac.
The baseline images and kernel are retained. This script never runs fastboot.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase', choices=['firmware', 'payload'], required=True)
    for name in ['base', 'uefi', 'baseline', 'output']:
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--edk2', type=Path)
    p.add_argument('--unique-chip-id')
    p.add_argument('--owned-memory-size', type=lambda s: int(s, 0), default=0x300000000)
    a = p.parse_args()
    if not 0x10000000 <= a.owned_memory_size <= 0x400000000 or a.owned_memory_size % 0x200000:
        p.error('window must be 256 MiB..16 GiB, aligned to 2 MiB')
    device = Path(__file__).resolve().parents[1]
    base, uefi, baseline, out = [x.resolve() for x in [a.base, a.uefi, a.baseline, a.output]]
    if out == baseline:
        p.error('output must be separate from the baseline')
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(base / 'scripts'))
    from unixnu import cli, firmware, bootimg, payload, tree
    from unixnu.config import Context, parse_config
    ctx = Context(cli.parser().parse_args(['build', 'firmware', '-D', str(device),
                  '--out', str(out), '--uefi', str(uefi), '-j', '10']))
    ctx.work, ctx.logs = out / 'work', out / 'logs'
    ctx.tree, ctx.config_path = ctx.work / 'tree', baseline / 'config'
    ctx.config = parse_config(ctx.config_path.read_text())
    ctx.board = json.loads((baseline / 'board-effective.json').read_text())
    ctx.board['payload']['adt']['owned_memory_size'] = hex(a.owned_memory_size)
    ctx.board['payload']['title'] = f'fuxi RAM window {a.owned_memory_size / 2**30:g} GiB'
    if ctx.board['firmware']['defines'].get('FUXI_PAYLOAD_DIAGNOSTIC') != '1':
        raise ValueError('baseline must publish the diagnostic FAT volume')
    ctx.board['firmware']['submodules'] = []  # Already initialized, pinned local checkouts.
    ctx.uefi = uefi
    (out / 'board-effective.json').write_text(json.dumps(ctx.board, indent=2) + '\n')
    ctx.tree.mkdir(parents=True, exist_ok=True)
    ctx.logs.mkdir(parents=True, exist_ok=True)
    for repo in [base, device]:
        tree._copy_scripts(repo / 'scripts', ctx.tree / 'scripts')
    for name in ['package', 'drivers']:
        link = ctx.tree / name
        if link.is_symlink():
            link.unlink()
        if not link.exists():
            link.symlink_to(os.path.relpath(base / name, ctx.tree), target_is_directory=True)
    if a.phase == 'firmware':
        if not a.edk2:
            p.error('--edk2 is required for firmware')
        previous = firmware.fd_path(ctx, uefi)
        backup = out / 'previous-6g.fd'
        if previous.exists() and not backup.exists():
            shutil.copyfile(previous, backup)
        fd = firmware.build(ctx, {}, uefi, a.edk2.resolve(), 'DEBUG')
        shutil.copyfile(fd, out / 'SILICIUM_UEFI.fd')
        loader = uefi / 'Build/fuxiPkg/DEBUG_CLANGPDB/AARCH64/DarwinBootPkg/Driver/DarwinBoot/DEBUG'
        for name in ['DarwinBoot.efi', 'DarwinBoot.map']:
            shutil.copyfile(loader / name, out / name)
        dummy = out / 'dummy-ramdisk.img'
        dummy.write_bytes(b'dummy')
        bootimg.build(ctx, uefi, fd, uefi / 'Resources/DTBs/fuxi.dtb', dummy, out / 'base.img')
        source = base / 'drivers/DarwinBoot/DarwinBootPkg/Driver'
        paths = [source / name for name in ['DarwinBoot.c', 'WindowSize.h', 'WindowProbe.h', 'DarwinBoot.inf']]
        (out / 'firmware-sources.json').write_text(json.dumps({
            str(x.relative_to(base)): hashlib.sha256(x.read_bytes()).hexdigest() for x in paths
        }, indent=2) + '\n')
    else:
        if not a.unique_chip_id:
            p.error('--unique-chip-id is required for payload')
        # Reuse the exact kernel: the new memory test lives entirely in firmware.
        archive = json.loads((baseline / 'work/tree/build/ram-root-probe/build-report.json').read_text())
        for name, key in [('RamRootProbe.c', 'source_sha256'), ('RamLaunchdProbe.c', 'main_source_sha256'),
                          ('RamRestoreTrust.c', 'restore_source_sha256')]:
            if hashlib.sha256((device / 'package' / name).read_bytes()).hexdigest() != archive[key]:
                raise ValueError('baseline probe source no longer matches: ' + name)
        ctx.args.data_ramdisk = str(baseline / 'restore.dbr')
        ctx.args.trust_cache = str(device.parent / 'inputs/original-static-system.bundle')
        ctx.args.unique_chip_id = a.unique_chip_id
        payload.build(ctx, baseline / 'work/kernel/kernel.macho')


if __name__ == '__main__':
    main()
