#!/usr/bin/env python3
"""Verify RAM transfer windows, mount private copies read-only, audit and detach."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile

DYLD_SHA = 'f8df3c58fae6e409db740df22c98aa699aee110b73523541a63f1bd5e23cf705'


def detach_image(image):
    info = plistlib.loads(subprocess.check_output(['hdiutil', 'info', '-plist']))
    matches = [entry for entry in info['images']
               if Path(entry.get('image-path', '/')).resolve() == image.resolve()]
    for entry in matches:
        devices = [e['dev-entry'] for e in entry['system-entities']
                   if re.fullmatch(r'/dev/disk\d+', e.get('dev-entry', ''))]
        if not devices:
            raise RuntimeError('cannot identify backing device for ' + str(image))
        subprocess.run(['hdiutil', 'detach', devices[0]], check=True, capture_output=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--research-image', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--format-header', type=Path, required=True)
    args = p.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with args.research_image.open('rb') as source:
        header = source.read(4096)
    if len(header) != 4096 or header[:8] != b'SHNGROOT' or hashlib.sha256(header[:4064]).digest() != header[4064:]:
        p.error('invalid source manifest/checksum')
    version, count = struct.unpack_from('<II', header, 8)
    if version != 1 or not 1 <= count <= 31:
        p.error('unsupported source manifest')
    entries = [struct.unpack_from('<32sQQII32s', header, 24 + i*128) for i in range(count)]
    work = Path(tempfile.mkdtemp(prefix='unixnu-ram-cache-'))
    images = []
    evidence = dict(source=str(args.research_image.resolve()), manifest_sha256=hashlib.sha256(header).hexdigest(),
                    windows=[], partition_writes=False, runtime_mapping_verified=False, cleanup_complete=False)
    try:
        for name, expected_bytes in [('OS', 6077580800), ('SystemOS', 3303014400)]:
            selected = [e for e in entries if e[0].rstrip(b'\0').decode() == name]
            if len(selected) != 1:
                raise ValueError('missing or duplicated ' + name)
            _, offset, size, _, _, expected = selected[0]
            if size != expected_bytes or offset + size > args.research_image.stat().st_size:
                raise ValueError('invalid source window extent')
            image = work/(name+'.dmg')
            digest = hashlib.sha256()
            with args.research_image.open('rb') as source, image.open('wb') as target:
                source.seek(offset)
                left = size
                while left:
                    data = source.read(min(left, 8 << 20))
                    if not data:
                        raise ValueError('truncated source window')
                    digest.update(data)
                    if data == bytes(len(data)):
                        target.seek(len(data), 1)
                    else:
                        target.write(data)
                    left -= len(data)
                target.truncate(size)
            if digest.digest() != expected:
                raise ValueError('source SHA256 mismatch: ' + name)
            mount = work/('mnt-'+name)
            mount.mkdir()
            images.append(image)  # Also clean up a partial attach failure, by this private image path.
            attached = subprocess.check_output(['hdiutil', 'attach', '-readonly', '-nobrowse',
                                                '-mountpoint', str(mount), '-plist', str(image)])
            evidence['windows'].append(dict(name=name, offset=offset, size=size, sha256=digest.hexdigest(),
                                            mount_readonly=True, attach=plistlib.loads(attached)))
            if name == 'OS':
                dyld = (mount/'usr/lib/dyld').read_bytes()
                if hashlib.sha256(dyld).hexdigest() != DYLD_SHA:
                    raise ValueError('OS dyld differs from the original instruction-model input')
                (out/'dyld.original').write_bytes(dyld)
                evidence['os_dyld_sha256'] = DYLD_SHA
            else:
                cache = mount/'System/Library/Caches/com.apple.dyld'
                evidence['cache_files'] = [dict(name=f.name, bytes=f.stat().st_size)
                                           for f in sorted(cache.glob('dyld_shared_cache_arm64*'))]
                subprocess.run([sys.executable, str(Path(__file__).with_name('check-ram-cache-files.py')),
                                '--directory', str(cache), '--format-header', str(args.format_header.resolve()),
                                '--output', str(out)], check=True)
    finally:
        # Never detach another session's mounts, and never unlink an attached image.
        for image in reversed(images):
            detach_image(image)
        evidence['cleanup_complete'] = True
        (out/'cache-volume-audit.json').write_text(json.dumps(evidence, indent=2)+'\n')
        shutil.rmtree(work)
    print('PASS: source manifest and both RAM-window hashes, original OS dyld, readonly cache audit and cleanup')


if __name__ == '__main__':
    main()
