#!/usr/bin/env python3
"""Read-only 21H461 launch-constraint audit; execute original AMFI leaf routines.

Kernel APIs are explicit host models, not measurements of a phone. No input
image, trust entry, policy decision or device is modified.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE, UC_HOOK_MEM_INVALID
from unicorn import arm64_const as arm

AMFI = 'com.apple.driver.AppleMobileFileIntegrity'
AMFI_TEXT = 'a1088aaabc259418ed503d08df29b6146470b8c962696c688122a1f60a141db6'
LAUNCHD_SHA = '8cec9a3c44a443b08e977ccc5d577850316ad428c575dc01f09b018dbd5b6a25'
BUNDLE_SHA = '7416ca4f8377146626bb284e2133c76bee6cb4e3ee0af5d648b186235a7323a7'
SELECT_SELF = 0xfffffff004aaccd0
PARSE_DER = 0xfffffff004aace68
ON_SYSTEM = 0xfffffff004ab8d9c
ON_AUTH_APFS = 0xfffffff004ab8cf0
ARENA, STACK, STOP, GET_VNODE = 0x10000000, 0x20000000, 0x30000000, 0x30001000
VNODE, MOUNT, CONTEXT, VTABLE = ARENA + 0x1000, ARENA + 0x2000, ARENA + 0x3000, ARENA + 0x4000


def sha(data):
    return hashlib.sha256(data).hexdigest()


def signature(data):
    assert sha(data) == LAUNCHD_SHA
    at, signatures = 32, []
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        command, size = struct.unpack_from('<II', data, at)
        if command == 0x1d:
            start, length = struct.unpack_from('<II', data, at + 8)
            signatures.append(data[start:start + length])
        at += size
    assert len(signatures) == 1
    blob = signatures[0]
    magic, length, count = struct.unpack_from('>III', blob)
    assert magic == 0xfade0cc0 and length <= len(blob)
    slots = []
    cdhash = None
    for i in range(count):
        slot, offset = struct.unpack_from('>II', blob, 12 + i * 8)
        magic, size = struct.unpack_from('>II', blob, offset)
        assert offset + size <= length
        slots.append(dict(slot=slot, magic=hex(magic), bytes=size))
        if slot == 0:
            directory = blob[offset:offset + size]
            assert magic == 0xfade0c02 and directory[37] == 2  # SHA256 CodeDirectory
            cdhash = hashlib.sha256(directory).digest()[:20]
    assert cdhash is not None
    return dict(sha256=sha(data), cdhash=cdhash.hex(), signature_slots=slots,
                embedded_launch_constraint_slots=[x for x in slots if x['slot'] in (8, 9, 10)])


def trust_matches(data, cdhash):
    version, count = struct.unpack_from('<I', data)[0], struct.unpack_from('<I', data, 20)[0]
    entry_size = {1: 22, 2: 24}[version]
    assert len(data) == 24 + count * entry_size
    entries = []
    for i in range(count):
        entry = data[24 + i * entry_size:24 + (i + 1) * entry_size]
        if entry[:20].hex() == cdhash:
            entries.append(dict(index=i, hash_type=entry[20], flags=entry[21],
                                constraint_category=entry[22] if version == 2 else None,
                                raw=entry.hex()))
    return dict(sha256=sha(data), version=version, count=count, matches=entries)


def der_node(data, at=0):
    start = at
    tag, size = data[at:at + 2]
    at += 2
    if size & 128:
        n = size & 127
        assert 0 < n <= 4
        size = int.from_bytes(data[at:at + n], 'big')
        at += n
    end = at + size
    assert end <= len(data)
    if tag & 0x20:
        value, pos = [], at
        while pos < end:
            child, pos = der_node(data, pos)
            value.append(child)
        assert pos == end
    elif tag == 0xc:
        value = data[at:end].decode()
    elif tag == 1:
        assert size == 1
        value = data[at] != 0
    elif tag == 2:
        value = int.from_bytes(data[at:end], 'big', signed=True)
    else:
        value = data[at:end].hex()
    return dict(tag=hex(tag), bytes=end-start, value=value), end


class OriginalAMFI:
    def __init__(self, kernel, base):
        sys.path.insert(0, str(base / 'kits/NativePrelink'))
        import native_prelink as np
        self.np = np
        self.data = kernel.read_bytes()
        self.image = np.macho(self.data)
        self.plan = json.loads(kernel.with_suffix('.json').read_text())
        assert sha(self.data) == self.plan['candidate_sha256']
        assert self.plan['original_executable_sha256'][AMFI] == AMFI_TEXT
        self.names = np.exports(self.data, self.image)
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        self.pages, self.hooks, self.external, self.events = set(), {}, {}, []
        for extent in self.plan['extents']:
            if extent['module'] == AMFI:
                self.map(extent['va'], extent['size'])
                raw = self.read_image(extent['va'], extent['size'])
                self.uc.mem_write(extent['va'], raw)
                if extent['segment'] == '__TEXT_EXEC':
                    assert sha(raw) == AMFI_TEXT
        for binding in self.plan['bindings']:
            if binding['module'] == AMFI and binding['kind'] == 'kernel':
                address = binding['new_target']
                self.map(address, 16)
                self.external[address] = self.names.get(address, [])
        self.map(ARENA, 0x10000)
        self.map(STACK, 0x10000)
        self.map(STOP, 0x2000)
        self.uc.reg_write(arm.UC_ARM64_REG_CPACR_EL1, 3 << 20)
        self.uc.mem_write(ARENA, struct.pack('<Q', VTABLE))
        self.uc.mem_write(VTABLE + 0x30, struct.pack('<Q', GET_VNODE))
        self.hooks[GET_VNODE] = self.get_vnode
        self.hooks[PARSE_DER] = self.capture_der
        self.install('_vnode_mount', self.vnode_mount)
        self.install('_vfs_flags', self.vfs_flags)
        self.install('_vfs_context_create', self.context_create)
        self.install('_vfs_context_rele', self.context_release)
        self.install('_VNOP_IOCTL', self.ioctl)
        self.uc.hook_add(UC_HOOK_CODE, self.on_code)
        self.uc.hook_add(UC_HOOK_MEM_INVALID, self.on_invalid)

    def map(self, address, length):
        for page in range(address & ~4095, (address + length + 4095) & ~4095, 4096):
            if page not in self.pages:
                self.uc.mem_map(page, 4096)
                self.pages.add(page)

    def read_image(self, address, length):
        at = self.np.backed_offset(self.image['segments'], address, length, len(self.data))
        return self.data[at:at + length]

    def x(self, n, value=None):
        reg = getattr(arm, f'UC_ARM64_REG_X{n}')
        if value is None:
            return self.uc.reg_read(reg)
        self.uc.reg_write(reg, value & ((1 << 64) - 1))

    def ret(self, value=0):
        self.x(0, value)
        self.uc.reg_write(arm.UC_ARM64_REG_PC, self.x(30))

    def install(self, name, callback):
        addresses = [a for a, names in self.external.items() if name in names]
        assert addresses, 'missing AMFI import: ' + name
        for address in addresses:
            self.hooks[address] = callback

    def get_vnode(self):
        # Model only the context's vnode getter, preserving the original policy.
        self.uc.mem_write(self.x(8), struct.pack('<QQQ', 0, VNODE, 0))
        self.ret()

    def vnode_mount(self):
        assert self.x(0) == VNODE
        self.ret(MOUNT)

    def vfs_flags(self):
        assert self.x(0) == MOUNT
        self.events.append(dict(api='vfs_flags', value=hex(self.flags)))
        self.ret(self.flags)

    def context_create(self):
        assert self.x(0) == 0
        self.ret(CONTEXT)

    def context_release(self):
        assert self.x(0) == CONTEXT
        self.ret()

    def ioctl(self):
        assert self.x(0) == VNODE and self.x(4) == CONTEXT and self.x(3) == 0
        assert self.x(1) == 0x80084118
        self.events.append(dict(api='VNOP_IOCTL', command=hex(self.x(1)), result=self.ioctl_result))
        self.ret(self.ioctl_result)

    def capture_der(self):
        self.der_address, self.der_bytes = self.x(0), self.x(1)
        self.uc.emu_stop()  # Stop at deserialization; extract, never substitute a verdict.

    def on_code(self, uc, address, size, _):
        if address == STOP:
            uc.emu_stop()
        elif address in self.hooks:
            try:
                self.hooks[address]()
            except Exception as error:
                self.failure = repr(error)
                uc.emu_stop()
        elif address in self.external:
            self.failure = 'unmodeled kernel API: ' + repr(self.external[address])
            uc.emu_stop()

    def on_invalid(self, uc, access, address, size, value, _):
        self.failure = dict(unmapped=hex(address), pc=hex(uc.reg_read(arm.UC_ARM64_REG_PC)))
        return False

    def run(self, pc, arguments):
        self.failure, self.events, self.der_address = None, [], None
        self.uc.reg_write(arm.UC_ARM64_REG_SP, STACK + 0xf000)
        self.x(30, STOP)
        for n, value in enumerate(arguments):
            self.x(n, value)
        self.uc.emu_start(pc, STOP + 4, count=10000)
        assert self.failure is None, self.failure
        if self.der_address is None:
            assert self.uc.reg_read(arm.UC_ARM64_REG_PC) == STOP, 'instruction limit hit'
        return self.x(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('kernel', 'base', 'launchd', 'trust-bundle', 'restore-trust', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    executable = signature(args.launchd.read_bytes())
    assert not executable['embedded_launch_constraint_slots']
    bundle = args.trust_bundle.read_bytes()
    assert sha(bundle) == BUNDLE_SHA
    count = struct.unpack_from('<I', bundle)[0]
    assert count == 2
    offsets = [*struct.unpack_from('<2I', bundle, 4), len(bundle)]
    modules = [trust_matches(bundle[offsets[i]:offsets[i+1]], executable['cdhash']) for i in range(count)]
    assert modules[0]['matches'][0]['constraint_category'] == 2 and not modules[1]['matches']
    restore = trust_matches(args.restore_trust.read_bytes(), executable['cdhash'])
    assert restore['sha256'] == '54e80085f2de19d76f870aa63366ef520a905913dc0208381010864f7d1fbb09'
    machine = OriginalAMFI(args.kernel.resolve(), args.base.resolve())
    candidate = args.kernel.resolve().parents[2]
    payload = json.loads((candidate / 'payload-verification.json').read_text())
    boot_digest = sha((candidate / 'Mu-fuxi-usb-ram.img').read_bytes())
    assert payload['boot_sha256'] == boot_digest
    assert payload['kernel_sha256'] == sha(machine.data) and payload['trust_cache_sha256'] == sha(bundle)
    machine.run(SELECT_SELF, [2, ARENA + 0x8000])
    assert machine.der_address == 0xfffffff0042e506a and machine.der_bytes == 75
    der_address = machine.der_address
    der = machine.read_image(machine.der_address, machine.der_bytes)
    decoded, end = der_node(der)
    assert end == len(der)
    root = decoded['value']
    assert root[0]['value'] == 1
    dictionary = root[1]['value'][0]['value']
    assert dictionary[0]['value'] == '$or'
    leaves = dictionary[1]['value']
    predicates = {node['value'][0]['value']: node['value'][1]['value'] for node in leaves}
    assert predicates == {'on-authorized-authapfs-volume': True, 'on-system-volume': True}
    header = args.kernel.parent.parent / 'tree/.cache/xnu-work/bsd/sys/mount.h'
    rootfs = int(re.search(r'#define MNT_ROOTFS\s+(0x[0-9a-fA-F]+)', header.read_text()).group(1), 16)
    assert rootfs == 0x4000
    results = []
    for flags in (0, 1, 0x100001, rootfs, rootfs | 1):
        machine.flags = flags
        result = machine.run(ON_SYSTEM, [ARENA])
        assert result == bool(flags & rootfs)
        results.append(dict(routine='on-system-volume', flags=hex(flags), result=result, events=machine.events))
    for error in (0, 1, 22, 25):
        machine.ioctl_result = error
        result = machine.run(ON_AUTH_APFS, [ARENA])
        assert result == (error == 0)
        results.append(dict(routine='on-authorized-authapfs-volume', ioctl_errno=error,
                            result=result, events=machine.events))
    report = dict(complete=True, analyzer_sha256=sha(Path(__file__).read_bytes()),
                  boot_sha256=boot_digest, kernel_sha256=sha(machine.data), original_amfi_text_sha256=AMFI_TEXT,
                  launchd=executable, trust_bundle_sha256=sha(bundle), trust_modules=modules,
                  restore_module=restore, category=2, category_der_va=hex(der_address),
                  category_der_sha256=sha(der), category_der_decoded=decoded, category_predicates=predicates,
                  original_leaf_routines=dict(on_system_volume=hex(ON_SYSTEM), on_authorized_authapfs_volume=hex(ON_AUTH_APFS)),
                  original_code_leaf_tests=results, rootfs_flag=hex(rootfs),
                  limitations=['Kernel API returns are modeled; no new phone run',
                               'Only selection and leaf routines execute; full MACF/AMFI callback is not emulated',
                               'On-device authorization ioctl failure is inferred from category-2 no-match, not directly measured'],
                  inputs_modified=False, hardware_result='v34 IMG_6980: category-2 self no-match before EL0')
    (out / 'category2.der').write_bytes(der)
    (out / 'launch-constraints.json').write_text(json.dumps(report, indent=2) + '\n')
    print('PASS: original TC category 2; original DER OR policy; 9 original AMFI leaf cases with modeled kernel APIs')


if __name__ == '__main__':
    main()
