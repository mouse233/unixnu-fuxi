#!/usr/bin/env python3
"""Replace the ramdisk of a v3 fuxi boot image with the real UniXNU payload.

The 4 MiB diagnostic image had a byte-exact FAT16 identity that
tools/make_fuxi_payload.py emitted. The real payload is the volume the unixnu
driver makes with mformat, whose geometry mtools picks itself, so the firmware
identifies it structurally instead: the OEM name carries the FUXIPAYL tag and
the BPB must describe exactly the payload length. This tool stamps that tag and
checks the same conditions the driver checks, so a payload the driver would
reject fails here first.

Never touches a phone; every step works on regular files.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

TAG = b"FUXIPAYL"


def le16(data, at):
    return data[at] | (data[at + 1] << 8)


def le32(data, at):
    return le16(data, at) | (le16(data, at + 2) << 16)


def check_payload(data, size_mib):
    """The structural identity of PayloadBounds.h, on the same bytes."""
    expected = size_mib * 1024 * 1024
    if len(data) != expected:
        raise ValueError(f"payload is {len(data)} bytes; expected {expected}")
    if data[0] != 0xEB or data[2] != 0x90:
        raise ValueError("not a FAT boot sector")
    per_cluster = data[13]
    if le16(data, 11) != 512 or per_cluster == 0 or per_cluster > 128 or (per_cluster & (per_cluster - 1)):
        raise ValueError(f"sector size/cluster geometry rejected: {le16(data, 11)}/{per_cluster}")
    if le16(data, 14) == 0 or le16(data, 14) > 64:
        raise ValueError(f"reserved sector count rejected: {le16(data, 14)}")
    if not 1 <= le16(data, 16) <= 2:
        raise ValueError(f"FAT count rejected: {le16(data, 16)}")
    if data[21] != 0xF8 or data[510] != 0x55 or data[511] != 0xAA:
        raise ValueError("media descriptor or boot signature rejected")
    total = le16(data, 19) or le32(data, 32)
    if total * 512 != expected:
        raise ValueError(f"BPB describes {total * 512} bytes, not {expected}")


def package(boot_path, payload_path, size_mib, output):
    boot = boot_path.read_bytes()
    payload = bytearray(payload_path.read_bytes())
    if boot[:8] != b"ANDROID!" or struct.unpack_from("<I", boot, 40)[0] != 3:
        raise ValueError("expected Android boot header v3")
    kernel_size, _, version, header_size = struct.unpack_from("<IIII", boot, 8)
    if header_size != 1580 or version != 0x220001A7:
        raise ValueError("unexpected header/OS metadata; recheck the stock boot header")
    check_payload(payload, size_mib)
    payload[3:11] = TAG
    offset = 4096 + ((kernel_size + 4095) // 4096) * 4096
    ramdisk_size = struct.unpack_from("<I", boot, 12)[0]
    if len(boot) != offset + ((ramdisk_size + 4095) // 4096) * 4096:
        raise ValueError("unexpected trailing data; not discarding an unknown footer")
    if boot[offset:offset + ramdisk_size] != b"dummy":
        raise ValueError("expected a newly built dummy-ramdisk firmware image")
    packed = bytearray(boot[:offset])
    struct.pack_into("<I", packed, 12, len(payload))
    packed.extend(payload)
    packed.extend(b"\0" * (-len(packed) % 4096))
    assert packed[:12] == boot[:12] and packed[16:offset] == boot[16:offset]
    # Re-read the result through the same checks the firmware applies.
    staged = packed[offset:offset + len(payload)]
    if staged[3:11] != TAG or staged[:4] != payload[:4]:
        raise ValueError("stamped payload did not round-trip")
    check_payload(bytearray(staged), size_mib)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(packed)
    digest = lambda data: hashlib.sha256(data).hexdigest()
    manifest = {
        "bytes": len(packed), "sha256": digest(packed), "input_boot_sha256": digest(boot),
        "payload_sha256": digest(bytes(payload)), "payload_bytes": len(payload),
        "payload_size_mib": size_mib, "payload_tag": TAG.decode(),
        "header_version": 3, "os_version_raw": hex(version),
        "kernel_sha256": digest(boot[4096:4096 + kernel_size]),
        "ramdisk_bytes": len(payload), "ramdisk_offset": offset,
        "preserved": "kernel (shim, FD, appended DTB) and all other header fields",
        "device_test": "not yet boot-tested",
    }
    output.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--size-mib", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package(args.boot, args.payload, args.size_mib, args.output)