#!/usr/bin/env python3
"""Audit the full OS/SystemOS windows for a RAM-root experiment.

Reads the SHNGROOT userdata image, verifies its windows, inspects the GPT and
APFS container headers, optionally mounts each window read-only to collect the
volume UUID/role/sealed state, and reports a memory budget. It never writes to
a device or to the source image.
"""
import argparse
import hashlib
import json
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HEADER = 4096
ENTRY = 128
APFS_PARTITION = uuid.UUID('7c3457ef-0000-11aa-aa11-00306543ecac')
MIB = 1024 ** 2
GIB = 1024 ** 3
DARWIN_WINDOW_LIMIT = 0x400000000  # Parser bound, not device-qualified RAM.
CURRENT_XNU_WINDOW = 0x130000000


def sha256_file(path, offset=0, size=None):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        f.seek(offset)
        left = size
        while left is None or left:
            chunk = f.read(8 * MIB if left is None else min(8 * MIB, left))
            if not chunk:
                break
            digest.update(chunk)
            if left is not None:
                left -= len(chunk)
    return digest.hexdigest()


def parse_manifest(path):
    with open(path, 'rb') as f:
        header = f.read(HEADER)
    if len(header) != HEADER or header[:8] != b'SHNGROOT':
        raise SystemExit('not a SHNGROOT image: ' + str(path))
    version, count = struct.unpack_from('<II', header, 8)
    image_bytes = struct.unpack_from('<Q', header, 16)[0]
    if version != 1 or not 1 <= count <= 64:
        raise SystemExit(f'unsupported manifest version={version} count={count}')
    if hashlib.sha256(header[:4064]).digest() != header[4064:]:
        raise SystemExit('manifest checksum mismatch')
    windows = []
    for index in range(count):
        at = 24 + index * ENTRY
        name, offset, size, block_size, flags, digest = struct.unpack_from('<32sQQII32s', header, at)
        windows.append(dict(name=name.rstrip(b'\0').decode(), offset=offset, size=size,
                            block_size=block_size, flags=flags, sha256=digest.hex()))
    if path.stat().st_size < image_bytes:
        raise SystemExit(f'image is shorter than its manifest: {path.stat().st_size} < {image_bytes}')
    end = 0
    for window in windows:
        if window['offset'] < end or window['offset'] + window['size'] > image_bytes:
            raise SystemExit('window overlaps or exceeds the image: ' + window['name'])
        end = window['offset'] + window['size']
    return dict(version=version, count=count, image_bytes=image_bytes, windows=windows)


def parse_gpt(head):
    if head[510:512] != b'\x55\xaa' or head[512:520] != b'EFI PART':
        return None
    entries_lba, count, entry_size = struct.unpack_from('<QII', head, 512 + 72)
    if not 1 <= count <= 256 or entry_size not in (128, 256) or entries_lba > 64:
        raise SystemExit('invalid GPT header')
    start = entries_lba * 512
    entries = head[start:start + count * entry_size]
    partitions = []
    for index in range(count):
        entry = entries[index * entry_size:(index + 1) * entry_size]
        if entry[:16] == bytes(16):
            continue
        first, last = struct.unpack_from('<QQ', entry, 32)
        partitions.append(dict(index=index, type=str(uuid.UUID(bytes_le=entry[:16])).lower(),
                               first_lba=first, last_lba=last,
                               name=entry[56:128].decode('utf-16-le').rstrip('\0')))
    return partitions


def parse_apfs(head, window_size):
    found = []
    pos = head.find(b'NXSB')
    while pos >= 0:
        base = pos - 0x20
        if base >= 0 and base + 0x58 <= len(head) and base % 4096 == 0:
            block_size, block_count = struct.unpack_from('<IQ', head, base + 0x24)
            container = uuid.UUID(bytes=head[base + 0x48:base + 0x58])
            if block_size in (4096, 16384) and 0 < block_count <= window_size // block_size:
                found.append(dict(offset=base, block_size=block_size,
                                  block_count=block_count, container_uuid=str(container)))
        pos = head.find(b'NXSB', pos + 1)
    return found


def slice_window(source, window, directory):
    target = Path(directory) / (window['name'] + '-window.img')
    if target.exists() and target.stat().st_size == window['size']:
        return target
    with open(source, 'rb') as src, open(target, 'wb') as out:
        src.seek(window['offset'])
        left = window['size']
        while left:
            chunk = src.read(min(8 * MIB, left))
            if not chunk:
                raise SystemExit('short read while slicing ' + window['name'])
            out.write(chunk)
            left -= len(chunk)
    return target


def diskutil_info(mount):
    raw = subprocess.check_output(['diskutil', 'info', '-plist', str(mount)])
    info = plistlib.loads(raw)
    apfs_raw = subprocess.check_output(['diskutil', 'apfs', 'list', '-plist'])
    apfs = plistlib.loads(apfs_raw)
    roles, in_use, container_uuid = [], None, ''
    for container in apfs.get('Containers', []):
        for volume in container.get('Volumes', []):
            if volume.get('APFSVolumeUUID') == info.get('VolumeUUID'):
                roles = volume.get('Roles', [])
                in_use = volume.get('CapacityInUse')
                container_uuid = container.get('APFSContainerUUID', '')
    return dict(name=info.get('VolumeName'), uuid=info.get('VolumeUUID'),
                filesystem=info.get('FilesystemName'), filesystem_type=info.get('FilesystemType'),
                sealed=info.get('Sealed'), read_only=not info.get('WritableMedia', True),
                block_size=info.get('VolumeAllocationBlockSize'),
                container_uuid=container_uuid, roles=roles, capacity_in_use=in_use)


def mount_window(image):
    mountpoint = Path(tempfile.mkdtemp(prefix='fuxi-full-os-mnt-'))
    output = subprocess.run(['hdiutil', 'attach', '-readonly', '-nobrowse',
                             '-mountpoint', str(mountpoint), str(image)],
                            capture_output=True, text=True)
    if output.returncode:
        raise SystemExit(f'hdiutil attach failed for {image}: {output.stdout}{output.stderr}')
    try:
        return diskutil_info(mountpoint)
    finally:
        subprocess.run(['hdiutil', 'detach', str(mountpoint)], capture_output=True, text=True)
        shutil.rmtree(mountpoint, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True, help='SHNGROOT userdata image')
    parser.add_argument('--report', type=Path, help='Write the JSON report here')
    parser.add_argument('--mount', action='store_true', help='Slice and mount each window read-only')
    parser.add_argument('--slice-dir', type=Path, help='Reuse or write sliced windows here')
    parser.add_argument('--no-hash', action='store_true', help='Skip the full window SHA256')
    parser.add_argument('--kernel', type=Path, help='Prelinked kernel for the memory budget')
    parser.add_argument('--data-mib', type=int, default=1024, help='RAM Data seed input budget')
    parser.add_argument('--overhead-percent', type=float, default=5.0, help='Runtime/page-table overhead')
    args = parser.parse_args()

    manifest = parse_manifest(args.image)
    slices = args.slice_dir or Path(tempfile.mkdtemp(prefix='fuxi-full-os-'))
    slices.mkdir(parents=True, exist_ok=True)
    result = dict(image=str(args.image.resolve()), image_bytes=manifest['image_bytes'],
                  manifest_version=manifest['version'], windows=[])

    for window in manifest['windows']:
        entry = dict(name=window['name'], offset=window['offset'], size=window['size'],
                     block_size=window['block_size'], flags=window['flags'],
                     sha256=window['sha256'], sha256_verified=None)
        if not args.no_hash:
            entry['sha256_verified'] = sha256_file(args.image, window['offset'], window['size']) == window['sha256']
            if not entry['sha256_verified']:
                raise SystemExit('window SHA256 mismatch: ' + window['name'])
        with open(args.image, 'rb') as f:
            f.seek(window['offset'])
            head = f.read(8 * MIB)
        gpt = parse_gpt(head)
        entry['gpt'] = gpt
        if gpt:
            apfs_parts = [p for p in gpt if p['type'] == str(APFS_PARTITION)]
            if len(apfs_parts) != 1:
                raise SystemExit('expected one APFS partition in ' + window['name'])
            part = apfs_parts[0]
            entry['apfs_partition'] = part
            container_offset = part['first_lba'] * 512
            if container_offset >= len(head):
                with open(args.image, 'rb') as f:
                    f.seek(window['offset'] + container_offset)
                    container_head = f.read(4096)
            else:
                container_head = head[container_offset:container_offset + 4096]
            containers = parse_apfs(container_head, part['last_lba'] * 512 - container_offset + 512)
        else:
            containers = parse_apfs(head, window['size'])
        if len(containers) != 1:
            raise SystemExit(f'expected one APFS container in {window["name"]}: {containers}')
        entry['apfs_container'] = containers[0]
        if containers[0]['block_size'] != 4096:
            raise SystemExit('expected 4 KiB APFS blocks in ' + window['name'])
        if args.mount:
            image = slice_window(args.image, window, slices)
            entry['volume'] = mount_window(image)
        result['windows'].append(entry)

    total_windows = sum(w['size'] for w in manifest['windows'])
    kernel = args.kernel.stat().st_size if args.kernel else 0
    data = args.data_mib * MIB
    overhead = int((total_windows + data + kernel) * args.overhead_percent / 100)
    budget = total_windows + data + kernel + overhead
    result['budget'] = dict(window_bytes=total_windows, kernel_bytes=kernel, data_bytes=data,
                            overhead_bytes=overhead, total_bytes=budget,
                            total_gib=round(budget / GIB, 3),
                            current_xnu_window_bytes=CURRENT_XNU_WINDOW,
                            fits_current_window=budget <= CURRENT_XNU_WINDOW,
                            darwin_window_limit_bytes=DARWIN_WINDOW_LIMIT,
                            fits_single_darwin_window=budget <= DARWIN_WINDOW_LIMIT)

    text = json.dumps(result, indent=2) + '\n'
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text)
    print(text, end='')
    if args.mount and not args.slice_dir:
        shutil.rmtree(slices, ignore_errors=True)


if __name__ == '__main__':
    main()
