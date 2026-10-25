#!/usr/bin/env python3
"""Inspect original dyld cache mappings and verify complete signature blocks.

A signing block is a file-integrity unit, not a hardware translation granule.
No file, CodeDirectory, cache layout or page table is converted by this audit.
"""
import hashlib
import json
from pathlib import Path
import struct
import subprocess

import argparse
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--directory', type=Path, required=True)
parser.add_argument('--format-header', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=True)
SOURCE = args.format_header.resolve().parent
# Derive offsets from the actual pinned Apple format, rather than guessing.
cpp = out / 'cache-offsets.cpp'
cpp.write_text('''#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <uuid/uuid.h>
#include "dyld_cache_format.h"
int main() {
 printf("%zu %zu %zu %zu %zu %zu\\n", offsetof(dyld_cache_header, codeSignatureOffset), offsetof(dyld_cache_header, mappingWithSlideOffset), offsetof(dyld_cache_header, imagesCount), sizeof(dyld_cache_mapping_and_slide_info), offsetof(dyld_cache_header, sharedRegionStart), offsetof(dyld_cache_header, sharedRegionSize));
}
''')
subprocess.run(['xcrun', 'clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-I',
                SOURCE, cpp, '-o', out / 'cache-offsets'], check=True)
cs_offset, mapping_offset, images_offset, mapping_size, region_start_offset, region_size_offset = map(
    int, subprocess.check_output([out / 'cache-offsets'], text=True).split())
cache_dir = args.directory.resolve()
files = sorted(path for path in cache_dir.glob('dyld_shared_cache_arm64*') if path.suffix != '.symbols')
if not files:
    raise SystemExit('Mount the manifest-selected SystemOS volume read-only first')
results = []
first_block = None
for path in files:
    with path.open('rb') as stream:
        header = stream.read(512)
        if header[:16].rstrip(b'\0') != b'dyld_v1   arm64':
            raise SystemExit(f'{path.name}: unexpected shared-cache architecture')
        sigoff, sigsize = struct.unpack_from('<QQ', header, cs_offset)
        mapoff, mapcount = struct.unpack_from('<II', header, mapping_offset)
        stream.seek(mapoff)
        mappings = []
        for _ in range(mapcount):
            va, size, fileoff, slideoff, slidesize, flags, maxprot, initprot = struct.unpack('<6Q2I', stream.read(mapping_size))
            if va % 4096 or size % 4096 or fileoff % 4096:
                raise SystemExit('A cache mapping cannot be represented directly by native 4K pages')
            mappings.append({'address': hex(va), 'size': size, 'fileoff': fileoff,
                             'slide_offset': slideoff, 'slide_size': slidesize})
        for mapping in mappings:
            if mapping['slide_size']:
                stream.seek(mapping['slide_offset'])
                version, page_size = struct.unpack('<II', stream.read(8))
                mapping['slide_version'] = version
                mapping['slide_page_bytes'] = page_size
        stream.seek(sigoff)
        signature = stream.read(sigsize)
        magic, length, count = struct.unpack_from('>3I', signature)
        if magic != 0xfade0cc0 or length > len(signature):
            raise SystemExit('Invalid cache signature container')
        directories = []
        for i in range(count):
            slot, at = struct.unpack_from('>II', signature, 12 + i * 8)
            magic, length = struct.unpack_from('>II', signature, at)
            if magic != 0xfade0c02:
                continue
            cd = signature[at:at + length]
            version, flags, hashoff, identoff, special, slots, limit = struct.unpack_from('>7I', cd, 8)
            hashsize, hashtype, platform, shift = struct.unpack_from('4B', cd, 36)
            if version >= 0x20300:
                limit = struct.unpack_from('>Q', cd, 56)[0] or limit
            if hashtype != 2 or hashsize != 32 or shift not in (12, 14):
                raise SystemExit('Unexpected original cache hash algorithm or block size')
            blocksize = 1 << shift
            if slots != (limit + blocksize - 1) // blocksize:
                raise SystemExit('Unexpected cache slot count')
            stream.seek(0)
            for page in range(slots):
                block = stream.read(min(blocksize, limit - page * blocksize))
                digest = hashlib.sha256()
                # Hash the unchanged complete signing block through 4K pieces.
                for start in range(0, len(block), 4096):
                    digest.update(block[start:start + 4096])
                expected = cd[hashoff + page * 32:hashoff + (page + 1) * 32]
                if digest.digest() != expected:
                    raise SystemExit(f'{path.name}: original block {page} signature mismatch')
                if first_block is None and len(block) == 16384:
                    first_block = (block, expected)
            directories.append({'cdhash': hashlib.sha256(cd).hexdigest()[:40],
                                'hash_block_bytes': blocksize, 'original_code_slots_verified': slots})
        if not directories:
            raise SystemExit('Missing original cache CodeDirectory')
        results.append({'file': path.name, 'mappings': mappings, 'code_directories': directories})
if first_block is None:
    raise SystemExit('Expected an original 16K signature block for negative checks')
block, expected = first_block
for quarter in range(4):
    corrupt = bytearray(block)
    corrupt[quarter * 4096] ^= 1
    digest = hashlib.sha256()
    for start in range(0, len(corrupt), 4096):
        digest.update(corrupt[start:start + 4096])
    if digest.digest() == expected:
        raise SystemExit('Corruption in a native 4K piece was not detected')
report = {'cache_files': results, 'files_checked': len(results),
          'all_mappings_directly_native_4k_aligned': True,
          'original_complete_signature_blocks_verified': True, 'corruption_in_each_4k_quarter_rejected': True,
          'code_directory_or_image_rewritten': False, 'kernel_large_hash_support_implemented': False,
          'native_4k_userspace_execution_observed': False,
          'format_header_sha256': hashlib.sha256(args.format_header.read_bytes()).hexdigest(),
          'format_reference': 'https://github.com/apple-oss-distributions/dyld/blob/dyld-1122.1/cache-builder/dyld_cache_format.h'}
(out / 'dyld-cache-audit.json').write_text(json.dumps(report, indent=2) + '\n')
print(f'PASS: {len(results)} unchanged ARM64 cache files, native 4K mapping alignment, original complete signature hashes')
print('PASS: corrupted bytes in each of four native 4K pieces invalidate the original 16K signature block')
print('Runtime mapping is unverified; this audit validates unchanged file mappings and signature hashes only')
