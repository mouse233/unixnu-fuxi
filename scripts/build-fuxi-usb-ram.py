#!/usr/bin/env python3
"""Build the opt-in USB RAM receiver in the existing arm64 Linux environment.

The result is a small, receiver-only Android boot image: it has no OS payload
and never starts XNU. No phone commands are issued here.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
for name in ['base', 'uefi', 'edk2']:
    p.add_argument('--' + name, type=Path, required=True)
p.add_argument('--output', type=Path, default=Path('out/usb-ram'))
a = p.parse_args()
device = Path(__file__).resolve().parents[1]
base, uefi, edk2, out = [x.resolve() for x in [a.base, a.uefi, a.edk2, a.output]]
sys.path.insert(0, str(base / 'scripts'))
from unixnu import cli, firmware, bootimg, tree
from unixnu.config import Context
ctx = Context(cli.parser().parse_args(['build', 'firmware', '-D', str(device), '--out', str(out),
                                      '--uefi', str(uefi), '--edk2', str(edk2), '-j', '10']))
ctx.work, ctx.logs = out / 'work', out / 'logs'
ctx.tree = ctx.work / 'tree'
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
ctx.board['firmware']['submodules'] = []
ctx.board['firmware']['packages'].append('{uefi}/Silicon/Samsung')
ctx.board['firmware']['defines']['FUXI_USB_RAM_RECEIVER'] = '1'
ctx.board['firmware']['defines']['FUXI_PAYLOAD_DIAGNOSTIC'] = '0'
ctx.uefi = uefi
fd = firmware.build(ctx, {}, uefi, edk2, 'DEBUG')
shutil.copyfile(fd, out / 'SILICIUM_UEFI.fd')
artifacts = uefi / 'Build/fuxiPkg/DEBUG_CLANGPDB/AARCH64'
for name in ['FuxiUsbRamDxe', 'UsbfnDwc3Dxe']:
    shutil.copyfile(artifacts / (name + '.efi'), out / (name + '.efi'))
paths = [*sorted((uefi / 'Platforms/Xiaomi/fuxiPkg/Drivers/FuxiUsbRamDxe').glob('*.[ch]')),
         *sorted((uefi / 'Platforms/Xiaomi/fuxiPkg/Library/FuxiUsbfnLib').glob('*.[ch]')),
         uefi / 'Platforms/Xiaomi/fuxiPkg/Drivers/FuxiUsbRamDxe/FuxiUsbRamDxe.inf',
         uefi / 'Platforms/Xiaomi/fuxiPkg/Library/FuxiUsbfnLib/FuxiUsbfnLib.inf',
         uefi / 'Silicon/Samsung/SamsungPkg/Drivers/UsbfnDwc3Dxe/UsbfnDwc3Dxe.inf',
         *sorted((uefi / 'Silicon/Samsung/SamsungPkg/Drivers/UsbfnDwc3Dxe').glob('*.[ch]')),
         uefi / 'Platforms/Xiaomi/fuxiPkg/fuxi.dsc', uefi / 'Platforms/Xiaomi/fuxiPkg/Include/DXE.inc']
(out / 'sources.json').write_text(json.dumps({str(x.relative_to(uefi)):
    hashlib.sha256(x.read_bytes()).hexdigest() for x in paths}, indent=2) + '\n')
dummy = out / 'dummy-ramdisk.img'
dummy.write_bytes(b'USBRAM diagnostic')
image = bootimg.build(ctx, uefi, fd, uefi / 'Resources/DTBs/fuxi.dtb', dummy, out / 'Mu-fuxi-usb-ram.img')
report = dict(image=str(image), sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
              bytes=image.stat().st_size, mode='UEFI read-only USB SMMU audit before first DMA',
              dma_audit_only=True, dma_transfer_enabled=False,
              controller='DWC3 USB2, SM8550 inherited ABL PHY/clocks', hardware_result='pending',
              kernel_handoff=False, partition_io=False)
(out / 'candidate.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
