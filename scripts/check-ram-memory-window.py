#!/usr/bin/env python3
"""Verify the RAM profile's darwinboot-memory-size window before a device boot."""
import argparse
import struct
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--adt', type=Path, required=True)
parser.add_argument('--bytes', type=lambda value: int(value, 0), required=True)
args = parser.parse_args()
if not 0x10000000 <= args.bytes <= 0x200000000 or args.bytes % 0x200000:
    raise SystemExit('requested window is outside the DarwinBoot parser bound')
adt = args.adt.read_bytes()


def node(offset, parent):
    count, children = struct.unpack_from('<II', adt, offset)
    offset += 8
    props = {}
    for _ in range(count):
        key, size = struct.unpack_from('<32sI', adt, offset)
        offset += 36
        props[key.rstrip(b'\0').decode()] = adt[offset:offset + size]
        offset += (size + 3) & ~3
    name = props['name'].rstrip(b'\0').decode()
    path = parent + '/' + name
    nodes[path] = props
    for _ in range(children):
        offset = node(offset, path)
    return offset


nodes = {}
if node(0, '') != len(adt):
    raise SystemExit('ADT node stream does not consume the file')
chosen = nodes.get('/device-tree/chosen')
if chosen is None:
    raise SystemExit('ADT has no /chosen node')
raw = chosen.get('darwinboot-memory-size')
if raw is None or len(raw) != 8:
    raise SystemExit('darwinboot-memory-size is missing or malformed')
actual = struct.unpack('<Q', raw)[0]
if actual != args.bytes:
    raise SystemExit(f'window mismatch: ADT {actual:#x} requested {args.bytes:#x}')
print(f'PASS: ADT window {actual:#x} ({actual / 2**30:.3f} GiB), 2 MiB aligned, '
      f'inside the DarwinBoot parser bound')
