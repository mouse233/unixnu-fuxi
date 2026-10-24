#!/usr/bin/env python3
"""Verify the delivered USB system-mount candidate: the payload FAT the kernel
boots from, the read-only ADT handoff placeholders and the loader sources."""
import argparse
import hashlib
import json
import re
import struct
import subprocess
import tempfile
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--output', type=Path, default=Path('out/usb-system-mount-v24'))
p.add_argument('--base', type=Path, required=True, help='unixnu-source checkout')
a = p.parse_args()
out, base = a.output.resolve(), a.base.resolve()
sha = lambda b: hashlib.sha256(b).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


boot = (out / 'Mu-fuxi-usb-ram.img').read_bytes()
candidate = json.loads((out / 'candidate.json').read_text())
require(sha(boot) == candidate['sha256'] and len(boot) == candidate['bytes'], 'image identity mismatch')
require(candidate.get('system_handoff') is True and candidate.get('kernel_handoff') is True,
        'candidate is not a system-handoff build')

# Ramdisk location from the v3 boot header (page 4096, kernel size at offset 8).
kernel_size = struct.unpack_from('<I', boot, 8)[0]
ramdisk_offset = 4096 * (1 + -(-kernel_size // 4096))
ramdisk = boot[ramdisk_offset:ramdisk_offset + struct.unpack_from('<I', boot, 12)[0]]
payload_report = json.loads((out / 'work/payload/payload.json').read_text())
raw = (out / 'work/payload/payload.img').read_bytes()
require(len(raw) == payload_report['payload_bytes'], 'payload build record size mismatch')
require(sha(raw) == payload_report['payload_sha256'], 'payload build record digest mismatch')

# The firmware finds the volume by the FUXIPAYL tag in the FAT boot sector's OEM
# name field and by the BPB describing exactly the payload length
# (PayloadBounds.h, FuxiPayloadHeader). package-fuxi-xnu-boot.py is the only
# thing that stamps the tag, so check the delivered ramdisk the way the firmware
# does instead of pinning it to the unstamped build output.
manifest = json.loads((out / 'Mu-fuxi-usb-ram.json').read_text())
require(manifest['payload_tag'] == 'FUXIPAYL' and manifest['sha256'] == candidate['sha256'],
        'delivered image was not produced by package-fuxi-xnu-boot.py')
stamped = bytearray(raw)
stamped[3:11] = b'FUXIPAYL'
require(ramdisk == bytes(stamped), 'delivered ramdisk is not the stamped payload volume')
require(ramdisk[3:11] == b'FUXIPAYL', 'payload lacks the FUXIPAYL tag the firmware matches')
total_sectors = int.from_bytes(ramdisk[19:21], 'little') or int.from_bytes(ramdisk[32:36], 'little')
require(ramdisk[0] == 0xEB and ramdisk[2] == 0x90 and
        512 == int.from_bytes(ramdisk[11:13], 'little') and ramdisk[13] not in (0,) and
        1 <= int.from_bytes(ramdisk[14:16], 'little') <= 64 and
        1 <= int.from_bytes(ramdisk[16:18], 'little') <= 2 and
        ramdisk[21] == 0xF8 and ramdisk[510:512] == b'\x55\xaa' and total_sectors * 512 == len(ramdisk),
        'payload boot sector fails the structural checks PayloadBounds.h applies')


def fat(path):
    return subprocess.check_output(['mtype', '-i', str(out / 'Mu-fuxi-usb-ram.img') +
                                    f'@@{ramdisk_offset}', '::/EFI/UniXNU/' + path])


config = fat('unixnu.toml').decode()
require('ram-system-probe=1' in config and 'rd=md0' in config, 'missing mount-only guard args')
require('native-ufs-root=0' in config and 'native-ufs-probe=0' in config, 'storage root not disabled')
require('sheng-data-persist=0' in config and 'sheng-log=0' in config, 'sheng persistence not disabled')
require('initrd = "Payloads/scratch-data.img"' in config, 'wrong md0 input')
require(payload_report['kernel_args'].split() == [entry for entry in
        config.split('args = "')[1].split('"')[0].split()], 'kernel args changed')
arguments = payload_report['kernel_args'].split()
require(arguments.count('vm_compression_limit=131072') == 1 and
        arguments.count('-disable_freezer_cseg_acct') == 1,
        '12 GiB system-window boot needs the bounded early compressor configuration')

for name in ['kernel.macho', 'kernel.macho.adt', 'kernel.macho.trustcache', 'scratch-data.img']:
    digest = sha(fat('Payloads/' + name))
    require(digest == payload_report[{'kernel.macho': 'payload_kernel_sha256',
                                      'kernel.macho.adt': 'adt_sha256',
                                      'kernel.macho.trustcache': 'trust_cache_sha256',
                                      'scratch-data.img': 'data_ramdisk_sha256'}[name]],
            'payload digest mismatch: ' + name)
require(sha((out / 'work/kernel/kernel.macho').read_bytes()) == payload_report['kernel_sha256'],
        'delivered kernel is not the built prelink')

# The receiver publishes its window at (2 MiB-aligned raw base) + UR_WINDOW_SKEW, and
# DarwinBoot adopts that window only when its low 21 bits equal the kernel link
# base's, because the bootstrap translation is mapped in 2 MiB blocks. Kernel.Base is
# the lowest segment vmaddr of the delivered kernel.macho, so those two independently
# carried values must agree: otherwise the handoff is refused with
# EFI_COMPROMISED_DATA and the boot stops in the UniXNU menu.
def macho_segments(image):
    require(image[:4] == b'\xcf\xfa\xed\xfe', 'payload kernel is not a 64-bit little-endian Mach-O')
    offset, segments = 32, []
    for _ in range(struct.unpack_from('<I', image, 16)[0]):
        command, size = struct.unpack_from('<II', image, offset)
        if command == 0x19:                                   # LC_SEGMENT_64
            name = image[offset + 8:offset + 24].rstrip(b'\0').decode()
            vmaddr, vmsize = struct.unpack_from('<QQ', image, offset + 24)
            if vmsize:                                        # DarwinBoot skips empty segments
                segments.append((name, vmaddr, vmsize))
        offset += size
    require(segments, 'payload kernel has no non-empty segment')
    return segments


kernel_base = min(vmaddr for _, vmaddr, _ in macho_segments(fat('Payloads/kernel.macho')))
skew = int(re.search(r'#define UR_WINDOW_SKEW \(\(UR64\)(0x[0-9a-fA-F]+)\)',
                     (base / 'package/UniXNUPkg/Include/Protocol/UniXNURamObjects.h').read_text()).group(1), 16)
require(kernel_base & 0x1fffff == skew,
        'UR_WINDOW_SKEW %#x does not match kernel base %#x (low 21 bits %#x); DarwinBoot '
        'would refuse the handoff as EFI_COMPROMISED_DATA' % (skew, kernel_base, kernel_base & 0x1fffff))
require(payload_report['trust_cache_sha256'] ==
        '7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7', 'original trust bundle changed')
pins = json.loads((out / 'pins.json').read_text())
require(pins['stop_before_first_user_svc'] is True and pins['physical_storage_allowed'] is False,
        'mount-only pins missing')

adt = fat('Payloads/kernel.macho.adt')
nodes = {}
offset, total = 0, len(adt)


def node(offset, parent):
    count, children = struct.unpack_from('<II', adt, offset)
    offset += 8
    props = {}
    for _ in range(count):
        key, size = struct.unpack_from('<32sI', adt, offset)
        offset += 36
        props[key.rstrip(b'\0').decode()] = adt[offset:offset + size]
        offset += (size + 3) & ~3
    path = parent + '/' + props['name'].rstrip(b'\0').decode()
    nodes[path] = props
    for _ in range(children):
        offset = node(offset, path)
    return offset


require(node(0, '') == total, 'ADT stream does not consume the file')
chosen = nodes.get('/device-tree/chosen')
require(chosen is not None and len(chosen.get('darwinboot-memory-size', b'')) == 8,
        'ADT has no chosen node with a memory window')
require(struct.unpack('<Q', chosen['darwinboot-memory-size'])[0] == 0x300000000,
        'ADT window is not the unified 12 GiB window')
memory_map = nodes.get('/device-tree/chosen/memory-map')
require(memory_map is not None, 'memory-map node missing')
# RamSystemObjects placeholder: 136 bytes inside /chosen/memory-map, filled by DarwinBoot.
ram_system = memory_map.get('RamSystemObjects')
require(ram_system is not None and len(ram_system) == 136 and set(ram_system) == {0},
        'RamSystemObjects placeholder missing or not a 136-byte placeholder')

with tempfile.TemporaryDirectory(prefix='usb-system-mount-') as scratch:
    path = Path(scratch) / 'kernel.adt'
    path.write_bytes(adt)
    subprocess.run(['python3', str(Path(__file__).with_name('check-ram-memory-window.py')),
                    '--adt', str(path), '--bytes', '0x300000000'], check=True)

# Loader identity: the delivered DarwinBoot.efi is built from exactly these sources,
# it is present in the firmware, and it contains the adopted-window behaviour.
loader_sources = json.loads((out / 'loader-sources.json').read_text())
for relative, digest in loader_sources.items():
    require(sha((base / relative).read_bytes()) == digest, 'loader source changed: ' + relative)
darwin = (out / 'DarwinBoot.efi').read_bytes()
utf16 = lambda text: text.encode('utf-16-le')
require(utf16('adopted USB window') in darwin and utf16('final RAM SHA256 id=') in darwin,
        'delivered loader lacks the USB window adoption path')
receiver = (out / 'FuxiUsbRamDxe.efi').read_bytes()
require(b'USBRAM SYSTEM: immutable objects published' in receiver,
        'delivered receiver lacks the handoff publish path')

volume_nodes = b'/dev/md1s1\0' in fat('Payloads/kernel.macho') and b'/dev/md2s1\0' in fat('Payloads/kernel.macho')

report = dict(boot_sha256=candidate['sha256'], boot_bytes=candidate['bytes'],
              ramdisk_offset=ramdisk_offset, payload_sha256=sha(ramdisk),
              payload_source_sha256=payload_report['payload_sha256'], payload_tag=manifest['payload_tag'],
              payload_bytes=len(ramdisk), kernel_sha256=payload_report['kernel_sha256'],
              adt_sha256=payload_report['adt_sha256'], trust_cache_sha256=payload_report['trust_cache_sha256'],
              data_ramdisk_sha256=payload_report['data_ramdisk_sha256'],
              kernel_args=payload_report['kernel_args'], loader_sources=len(loader_sources),
              window_bytes='0x300000000', window_skew=hex(skew), kernel_base=hex(kernel_base),
              ram_volume_nodes=volume_nodes, hardware_result='pending')
(out / 'payload-verification.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
