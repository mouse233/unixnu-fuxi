#!/usr/bin/env python3
"""Read the delivered boot FAT and bind verification to this exact candidate."""
import argparse
import hashlib
import json
import lzma
import shutil
import struct
import subprocess
import tempfile
import tomllib
import uuid
from pathlib import Path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image', type=Path, required=True)
    p.add_argument('--baseline-image', type=Path, required=True)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--bytes', type=lambda s: int(s, 0), default=0x300000000)
    a = p.parse_args()
    image, old, base = a.image.resolve(), a.baseline_image.resolve(), a.base.resolve()
    out = image.parent
    manifest = json.loads(image.with_suffix('.json').read_text())
    data = image.read_bytes()
    require(sha(data) == manifest['sha256'], 'candidate digest mismatch')
    require(len(data) == manifest['bytes'], 'candidate size mismatch')
    require(struct.unpack_from('<I', data, 40)[0] == 3, 'expected boot header v3')
    require(struct.unpack_from('<I', data, 16)[0] == 0x220001a7, 'stock OS metadata lost')
    start, size = manifest['ramdisk_offset'], manifest['ramdisk_bytes']
    require(sha(data[start:start + size]) == manifest['payload_sha256'], 'payload digest mismatch')
    require(size == 192 * 2**20, 'diagnostic FAT size changed')

    def fat(boot, path):
        m = json.loads(boot.with_suffix('.json').read_text())
        return subprocess.check_output([shutil.which('mtype') or 'mtype', '-i',
            str(boot) + '@@' + str(m['ramdisk_offset']), '::/EFI/UniXNU/' + path])

    require(sha(old.read_bytes()) == json.loads(old.with_suffix('.json').read_text())['sha256'],
            'baseline digest mismatch')
    file_hashes = {}
    for name in ['kernel.macho', 'scratch-data.img', 'kernel.macho.trustcache']:
        contents = fat(image, 'Payloads/' + name)
        require(contents == fat(old, 'Payloads/' + name), name + ' differs from the tested baseline')
        file_hashes[name] = sha(contents)
    require(file_hashes['kernel.macho.trustcache'] ==
            '7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7', 'original trust bundle changed')
    settings = tomllib.loads(fat(image, 'unixnu.toml').decode())
    old_settings = tomllib.loads(fat(old, 'unixnu.toml').decode())
    entry = settings['entry'][0]
    require(entry['args'] == old_settings['entry'][0]['args'], 'kernel arguments changed')
    require(entry['initrd'] == 'Payloads/scratch-data.img', 'wrong md0 input')
    for arg in ['rd=md0', 'ram-root-probe=4', 'native-ufs-root=0', 'native-ufs-probe=0',
                'sheng-data-persist=0', 'sheng-log=0']:
        require(arg in entry['args'].split(), 'missing diagnostic guard: ' + arg)
    adt = fat(image, 'Payloads/kernel.macho.adt')
    file_hashes['kernel.macho.adt'] = sha(adt)
    with tempfile.TemporaryDirectory(prefix='ram-window-check-') as scratch:
        path = Path(scratch) / 'kernel.adt'
        path.write_bytes(adt)
        subprocess.run(['python3', str(Path(__file__).with_name('check-ram-memory-window.py')),
                        '--adt', str(path), '--bytes', hex(a.bytes)], check=True)
    sources = json.loads((out / 'firmware-sources.json').read_text())
    for relative, digest in sources.items():
        require(sha((base / relative).read_bytes()) == digest, 'firmware source changed: ' + relative)
    fd = (out / 'SILICIUM_UEFI.fd').read_bytes()
    kernel_size = struct.unpack_from('<I', data, 8)[0]
    # gzip.decompress consumes all members, including the trailing FDT, so use
    # a decompressor that retains the DTB separately.
    import zlib
    unpacker = zlib.decompressobj(16 + zlib.MAX_WBITS)
    shim_fd = unpacker.decompress(data[4096:4096 + kernel_size])
    require(unpacker.eof and shim_fd.endswith(fd), 'delivered firmware differs from the rebuilt FD')
    require(bool(unpacker.unused_data), 'appended device tree missing')
    # The DXE volume is inside a GUID-defined LZMA section. Decode that exact
    # section from the delivered FD, rather than trusting build-directory files.
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
    require(inner is not None, 'compressed DXE firmware volume missing')
    loader = (out / 'DarwinBoot.efi').read_bytes()
    require(loader in inner, 'delivered loader differs from the built diagnostic')
    require('MEM PASS PA'.encode('utf-16le') in loader, 'large-window diagnostic absent')
    require(b'FUXIPAYL' in inner, 'initrd FAT driver missing')
    report = dict(boot_sha256=sha(data), boot_bytes=len(data), baseline_sha256=sha(old.read_bytes()),
                  owned_memory_bytes=a.bytes, sparse_probe_samples=a.bytes // 0x200000 + 1,
                  firmware_sha256=sha(fd), loader_sha256=sha(loader), firmware_sources=sources,
                  delivered_loader_matches_build=True,
                  fat_roundtrip=True, baseline_kernel_ramdisk_trust_identical=True,
                  payload_hashes=file_hashes, kernel_args=entry['args'],
                  hardware_result='pending', flash_performed=False,
                  scope='Host packaging checks; no device memory qualification yet')
    (out / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
