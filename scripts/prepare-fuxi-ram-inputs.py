#!/usr/bin/env python3
"""Extract pinned original RAM-disk/trust inputs from a local IPSW; never mount them."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import struct
import zipfile


def der(data, offset):
    if offset + 2 > len(data):
        raise ValueError('Truncated DER')
    tag, size = data[offset:offset + 2]
    offset += 2
    if size & 128:
        count = size & 127
        if not 0 < count <= 4 or offset + count > len(data):
            raise ValueError('Invalid DER length')
        size = int.from_bytes(data[offset:offset + count], 'big')
        offset += count
    if offset + size > len(data):
        raise ValueError('DER extends past input')
    return tag, data[offset:offset + size], offset + size


def unwrap(blob):
    tag, sequence, end = der(blob, 0)
    fields, offset = [], 0
    while offset < len(sequence):
        kind, data, offset = der(sequence, offset)
        fields.append((kind, data))
    if (tag != 0x30 or end != len(blob) or
            [kind for kind, _ in fields] != [0x16, 0x16, 0x16, 4] or fields[0][1] != b'IM4P'):
        raise ValueError('Expected an original IM4P')
    return fields[-1][1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ipsw', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    pins = json.loads((Path(__file__).resolve().parents[1] / 'diagnostics/ram-root/pins.json').read_text())
    modules, inputs = [], []
    with zipfile.ZipFile(args.ipsw) as archive:
        manifest = plistlib.loads(archive.read('BuildManifest.plist'))
        if manifest['ProductBuildVersion'] != '21H461' or 'iPad7,1' not in manifest['SupportedProductTypes']:
            raise ValueError('Require the pinned iPad7,1 21H461 IPSW')
        identity = next(item for item in manifest['BuildIdentities'] if
            item['Info']['DeviceClass'] == 'j120ap' and item['Info']['RestoreBehavior'] == 'Erase')
        for component in ['RestoreRamDisk', 'StaticTrustCache', 'Cryptex1,SystemTrustCache']:
            entry = identity['Manifest'][component]
            blob = archive.read(entry['Info']['Path'])
            if hashlib.sha384(blob).digest() != entry['Digest']:
                raise ValueError('BuildManifest digest mismatch: ' + component)
            raw = unwrap(blob)
            if component == 'RestoreRamDisk':
                if len(raw) != pins['image_bytes'] or hashlib.sha256(raw).hexdigest() != pins['image_sha256']:
                    raise ValueError('RestoreRamDisk differs from inspected original')
                disk = raw
            else:
                if struct.unpack_from('<I', raw)[0] != 2 or len(raw) != 24 + 24 * struct.unpack_from('<I', raw, 20)[0]:
                    raise ValueError('Expected intact original version-2 trust module')
                modules.append(raw)
            inputs.append(dict(component=component, member=entry['Info']['Path'],
                raw_sha256=hashlib.sha256(raw).hexdigest(), original_manifest_digest_verified=True))
    bundle = struct.pack('<3I', 2, 12, 12 + len(modules[0])) + b''.join(modules)
    if hashlib.sha256(bundle).hexdigest() != '7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7':
        raise ValueError('Original trust modules changed')
    args.output.mkdir(parents=True, exist_ok=True)
    for name, blob in [('restore.dmg', disk), ('original-static-system.bundle', bundle)]:
        path = args.output / name
        if path.exists() and hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(blob).digest():
            raise ValueError('Refusing to replace different input: ' + str(path))
        if not path.exists():
            path.write_bytes(blob)
    (args.output / 'inputs.json').write_text(json.dumps(inputs, indent=2) + '\n')
    print('PASS: exact original RAM disk and trust modules extracted; nothing mounted or executed')


if __name__ == '__main__':
    main()
