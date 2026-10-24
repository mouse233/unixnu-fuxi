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
p.add_argument('--try-usb-route', action='store_true', help='try exact SID0040 route with readback/rollback; default only audits')
p.add_argument('--keep-phy', action='store_true', help='keep the ABL USB2 PHY state instead of pulsing PHYSOFTRST')
p.add_argument('--system-handoff', action='store_true', help='reserve the unified OS window and publish immutable objects after verified stop')
a = p.parse_args()
if a.system_handoff and not (a.try_usb_route and a.keep_phy):
    p.error('--system-handoff requires --try-usb-route --keep-phy')
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
ctx.board['firmware']['defines']['FUXI_USB_TRY_SID_ROUTE'] = '1' if a.try_usb_route else '0'
ctx.board['firmware']['defines']['FUXI_USB_KEEP_PHY'] = '1' if a.keep_phy else '0'
ctx.board['firmware']['defines']['FUXI_PAYLOAD_DIAGNOSTIC'] = '1' if a.system_handoff else '0'
ctx.board['firmware']['defines']['FUXI_USB_RAM_SYSTEM_HANDOFF'] = '1' if a.system_handoff else '0'
ctx.uefi = uefi
fd = firmware.build(ctx, {}, uefi, edk2, 'DEBUG')
shutil.copyfile(fd, out / 'SILICIUM_UEFI.fd')
artifacts = uefi / 'Build/fuxiPkg/DEBUG_CLANGPDB/AARCH64'
for name in ['FuxiUsbRamDxe', 'UsbfnDwc3Dxe'] + (['DarwinBoot'] if a.system_handoff else []):
    shutil.copyfile(artifacts / (name + '.efi'), out / (name + '.efi'))
paths = [*sorted((uefi / 'Platforms/Xiaomi/fuxiPkg/Drivers/FuxiUsbRamDxe').glob('*.[ch]')),
         *sorted((uefi / 'Platforms/Xiaomi/fuxiPkg/Library/FuxiUsbfnLib').glob('*.[ch]')),
         uefi / 'Platforms/Xiaomi/fuxiPkg/Drivers/FuxiUsbRamDxe/FuxiUsbRamDxe.inf',
         *sorted((uefi / 'Platforms/Xiaomi/fuxiPkg/Library/FuxiUsbfnLib').glob('*.inf')),
         uefi / 'Silicon/Samsung/SamsungPkg/Drivers/UsbfnDwc3Dxe/UsbfnDwc3Dxe.inf',
         *sorted((uefi / 'Silicon/Samsung/SamsungPkg/Drivers/UsbfnDwc3Dxe').glob('*.[ch]')),
         uefi / 'Silicon/Samsung/SamsungPkg/Include/Device/Dwc3.h',
         uefi / 'Platforms/Xiaomi/fuxiPkg/fuxi.dsc', uefi / 'Platforms/Xiaomi/fuxiPkg/Include/DXE.inc']
(out / 'sources.json').write_text(json.dumps({str(x.relative_to(uefi)):
    hashlib.sha256(x.read_bytes()).hexdigest() for x in paths}, indent=2) + '\n')
if a.system_handoff:
    paths=[base/'package/UniXNUPkg/Include/Protocol/UniXNURamObjects.h',
           *sorted((base/'drivers/DarwinBoot/DarwinBootPkg/Driver').glob('*'))]
    (out/'loader-sources.json').write_text(json.dumps({str(x.relative_to(base)):
        hashlib.sha256(x.read_bytes()).hexdigest() for x in paths if x.is_file()},indent=2)+'\n')
dummy = out / 'dummy-ramdisk.img'
# A placeholder the payload packager replaces. package-fuxi-xnu-boot.py accepts
# only this content, so the same bytes the ram-window candidates use.
dummy.write_bytes(b'dummy')
image = bootimg.build(ctx, uefi, fd, uefi / 'Resources/DTBs/fuxi.dtb', dummy, out / 'Mu-fuxi-usb-ram.img')
report = dict(image=str(image), sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
              bytes=image.stat().st_size,
              mode='UEFI exact USB SID route trial' if a.try_usb_route else 'UEFI read-only USB SMMU audit before first DMA',
              dma_audit_only=not a.try_usb_route, dma_transfer_enabled=a.try_usb_route,
              usb_sid_route_enabled=a.try_usb_route,
              usb_sid_route_strategy='existing-disabled-context' if a.try_usb_route else None,
              usb_keep_phy=a.keep_phy,
              usb_eusb2_phy_init=bool(a.try_usb_route),
              controller='DWC3 USB2, SM8550 inherited ABL PHY/clocks', hardware_result='pending',
              kernel_handoff=bool(a.system_handoff), receiver_only=not a.system_handoff,
              partition_io=False, system_handoff=bool(a.system_handoff))
(out / 'candidate.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
