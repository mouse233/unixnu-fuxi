#!/usr/bin/env python3
"""Verify the delivered receiver firmware, its source identity and host tests."""
import argparse
import hashlib
import json
import lzma
import struct
import uuid
import zlib
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=Path, default=Path('out/usb-ram'))
p.add_argument('--uefi', type=Path, required=True)
a = p.parse_args()
out, uefi = a.output.resolve(), a.uefi.resolve()
sha = lambda b: hashlib.sha256(b).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


boot = (out / 'Mu-fuxi-usb-ram.img').read_bytes()
candidate = json.loads((out / 'candidate.json').read_text())
require(sha(boot) == candidate['sha256'] and len(boot) == candidate['bytes'], 'image identity mismatch')
require(boot[:8] == b'ANDROID!' and struct.unpack_from('<I', boot, 40)[0] == 3, 'wrong boot header')
require(struct.unpack_from('<I', boot, 16)[0] == 0x220001a7, 'stock OS metadata lost')
fd = (out / 'SILICIUM_UEFI.fd').read_bytes()
unpacker = zlib.decompressobj(31)
size = struct.unpack_from('<I', boot, 8)[0]
body = unpacker.decompress(boot[4096:4096 + size])
require(unpacker.eof and body.endswith(fd) and unpacker.unused_data[:4] == bytes.fromhex('d00dfeed'),
        'firmware/DTB roundtrip mismatch')
guid = uuid.UUID('ee4e5898-3914-4259-9d6e-dc7bd79403cf').bytes_le
position, inner = 0, None
while True:
    position = fd.find(guid, position)
    if position < 0:
        break
    start = position - 4
    length = int.from_bytes(fd[start:start + 3], 'little')
    offset = struct.unpack_from('<H', fd, position + 16)[0]
    position += 16
    if fd[start + 3] == 2 and 24 <= offset < length and start + length <= len(fd):
        try:
            inner = lzma.decompress(fd[start + offset:start + length])
            break
        except lzma.LZMAError:
            pass
require(inner is not None, 'compressed DXE volume missing')
modules = {}
for name in ['FuxiUsbRamDxe', 'UsbfnDwc3Dxe']:
    code = (out / (name + '.efi')).read_bytes()
    require(code in inner, 'delivered module mismatch: ' + name)
    modules[name] = sha(code)
require(b'UNXRAM01' in inner and b'USBRAM WAIT' in inner, 'receiver absent')
sources = json.loads((out / 'sources.json').read_text())
for relative, digest in sources.items():
    require(sha((uefi / relative).read_bytes()) == digest, 'build source changed: ' + relative)
tests = json.loads((out / 'host-tests.json').read_text())
for name, digest in tests['source_sha256'].items():
    require(sources['Platforms/Xiaomi/fuxiPkg/Drivers/FuxiUsbRamDxe/' + name] == digest,
            'host tests ran different receiver sources')
require(tests['sender_sha256'] == sha(Path(__file__).with_name('usb-ram-send.py').read_bytes()),
        'sender changed since host tests')
if candidate.get('dma_audit_only') or candidate.get('usb_sid_route_enabled'):
    audit = json.loads((out / 'smmu-tests.json').read_text())
    for name, digest in audit['source_sha256'].items():
        require(sources['Platforms/Xiaomi/fuxiPkg/Library/FuxiUsbfnLib/' + name] == digest,
                'audit tests ran different sources')
    require(b'USBRAM AUDIT STOP: no STARTTRANSFER issued' in inner, 'audit gate absent')
    if candidate.get('dma_audit_only'):
        require(candidate.get('dma_transfer_enabled') is False, 'audit mode mismatch')
        require(b'USB ROUTE VERIFIED; first DMA permitted' not in inner, 'route trial present in audit-only firmware')
if candidate.get('usb_sid_route_enabled'):
    route = json.loads((out / 'route-tests.json').read_text())
    for name, digest in route['source_sha256'].items():
        require(sources['Platforms/Xiaomi/fuxiPkg/Library/FuxiUsbfnLib/' + name] == digest,
                'route tests ran different sources')
    require(b'USB ROUTE VERIFIED; first DMA permitted' in inner, 'route trial absent from delivered firmware')
    require(candidate.get('dma_transfer_enabled') is True and not candidate.get('dma_audit_only'),
            'route trial mode mismatch')
report = dict(boot_sha256=sha(boot), boot_bytes=len(boot), fd_sha256=sha(fd),
              delivered_modules=modules, source_sha256=sources,
              compressed_firmware_roundtrip=True, os_version_raw='0x220001a7',
              host_cases=tests['cases'], hardware_result='pending', receiver_only=True,
              kernel_handoff=False, partition_io=False,
              dma_audit_only=bool(candidate.get('dma_audit_only')),
              dma_transfer_enabled=candidate.get('dma_transfer_enabled', True),
              smmu_audit_host_cases=audit['cases'] if (candidate.get('dma_audit_only') or candidate.get('usb_sid_route_enabled')) else None,
              usb_sid_route_enabled=bool(candidate.get('usb_sid_route_enabled')),
              smmu_route_host_cases=route['cases'] if candidate.get('usb_sid_route_enabled') else None)
(out / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
