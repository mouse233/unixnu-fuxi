#!/usr/bin/env python3
"""Send verified objects to the fuxi UEFI USB Bulk RAM receiver.

Uses libusb via ctypes (brew install libusb); no Python package is required.
This protocol has no partition-write command. Sending an OS retains it in RAM;
the first receiver diagnostic does not yet hand that RAM to XNU.
"""
import argparse
import ctypes as C
import ctypes.util
import hashlib
import io
import json
import struct
import sys
import time
from pathlib import Path

MAGIC = b'UNXRAM01'
HEADER = struct.Struct('<8sHHIQII32s')
CHUNK = 65536
HELLO, BEGIN, DATA, END, ABORT = range(1, 6)
KINDS = {'test': 0, 'os': 1, 'systemos': 2}


class UsbTransport:
    def __init__(self, vid=0x1209, pid=1, timeout=120000):
        path = ctypes.util.find_library('usb-1.0')
        if not path:
            for candidate in ('/opt/homebrew/lib/libusb-1.0.dylib',
                              '/usr/local/lib/libusb-1.0.dylib',
                              '/usr/lib/libusb-1.0.so.0',
                              'libusb-1.0.so.0'):
                try:
                    C.CDLL(candidate)
                except OSError:
                    continue
                path = candidate
                break
        if not path:
            raise RuntimeError('libusb not found; install it with brew install libusb')
        self.lib = C.CDLL(path)
        self.ctx, self.handle = C.c_void_p(), None
        self.claimed = False
        self.timeout = timeout
        signatures = {
            'libusb_init': ([C.POINTER(C.c_void_p)], C.c_int),
            'libusb_open_device_with_vid_pid': ([C.c_void_p, C.c_uint16, C.c_uint16], C.c_void_p),
            'libusb_get_string_descriptor_ascii': ([C.c_void_p, C.c_uint8, C.c_void_p, C.c_int], C.c_int),
            'libusb_set_configuration': ([C.c_void_p, C.c_int], C.c_int),
            'libusb_claim_interface': ([C.c_void_p, C.c_int], C.c_int),
            'libusb_release_interface': ([C.c_void_p, C.c_int], C.c_int),
            'libusb_bulk_transfer': ([C.c_void_p, C.c_uint8, C.c_void_p, C.c_int, C.POINTER(C.c_int), C.c_uint], C.c_int),
            'libusb_close': ([C.c_void_p], None),
            'libusb_exit': ([C.c_void_p], None),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.lib, name)
            fn.argtypes, fn.restype = args, result
        self.check(self.lib.libusb_init(C.byref(self.ctx)), 'initialize libusb')
        try:
            self.handle = self.lib.libusb_open_device_with_vid_pid(self.ctx, vid, pid)
            if not self.handle:
                raise RuntimeError(f'USB RAM receiver {vid:04x}:{pid:04x} not found or inaccessible')
            name = C.create_string_buffer(256)
            size = self.lib.libusb_get_string_descriptor_ascii(self.handle, 2, name, 256)
            if size < 0 or name.raw[:size] != b'fuxi RAM Bulk':
                raise RuntimeError('USB product does not identify the fuxi RAM receiver')
            self.check(self.lib.libusb_set_configuration(self.handle, 1), 'start a fresh receive session')
            self.check(self.lib.libusb_claim_interface(self.handle, 0), 'claim USB interface')
            self.claimed = True
        except BaseException:
            self.close()
            raise

    @staticmethod
    def check(result, action):
        if result < 0:
            raise RuntimeError(f'{action}: libusb error {result}')

    def transfer(self, endpoint, data_or_size):
        incoming = bool(endpoint & 0x80)
        buffer = C.create_string_buffer(data_or_size if incoming else bytes(data_or_size),
                                        data_or_size if incoming else len(data_or_size))
        length = data_or_size if incoming else len(data_or_size)
        actual = C.c_int()
        self.check(self.lib.libusb_bulk_transfer(self.handle, endpoint, buffer, length,
                                                C.byref(actual), self.timeout), 'bulk transfer')
        if actual.value != length:
            raise RuntimeError(f'short USB transfer: {actual.value}/{length}; session must be restarted')
        return buffer.raw[:actual.value]

    def exchange(self, header, payload):
        self.transfer(1, header)
        if payload:
            self.transfer(1, payload)
        return self.transfer(0x81, HEADER.size)

    def close(self):
        if self.handle:
            if self.claimed:
                self.lib.libusb_release_interface(self.handle, 0)
            self.lib.libusb_close(self.handle)
            self.handle = None
        if self.ctx:
            self.lib.libusb_exit(self.ctx)
            self.ctx = C.c_void_p()


class Client:
    def __init__(self, transport):
        self.transport = transport

    def command(self, op, obj=0, offset=0, payload=b''):
        digest = hashlib.sha256(payload).digest() if payload else bytes(32)
        frame = HEADER.pack(MAGIC, 1, op, obj, offset, len(payload), 0, digest)
        reply = self.transport.exchange(frame, payload)
        if len(reply) != HEADER.size:
            raise RuntimeError('short reply header')
        magic, version, operation, object_id, accepted, length, error, whole = HEADER.unpack(reply)
        if (magic, version, operation, object_id, length) != (MAGIC, 1, op | 0x8000, obj, 0):
            raise RuntimeError('unexpected reply identity or framing')
        if error:
            raise RuntimeError(f'receiver refused operation {op}: status={error}, offset={accepted}')
        return accepted, whole

    def send(self, stream, size, digest, obj, progress=None):
        maximum, _ = self.command(HELLO)
        if maximum != CHUNK:
            raise RuntimeError(f'unsupported receiver chunk size {maximum}')
        accepted, _ = self.command(BEGIN, obj, payload=struct.pack('<Q', size) + digest)
        if accepted != 0:
            raise RuntimeError('BEGIN returned a nonzero offset')
        offset = 0
        while offset < size:
            block = stream.read(min(CHUNK, size - offset))
            if not block:
                raise RuntimeError('source became shorter during transfer')
            accepted, _ = self.command(DATA, obj, offset, block)
            offset += len(block)
            if accepted != offset:
                raise RuntimeError('receiver offset diverged')
            if progress:
                progress(offset, size)
        accepted, actual = self.command(END, obj, offset)
        if accepted != size or actual != digest:
            raise RuntimeError('receiver RAM readback digest mismatch')
        return dict(object_id=obj, bytes=size, sha256=digest.hex(), ram_readback_verified=True)


def source(args):
    if args.self_test:
        data = bytes(range(256)) * 4096
        return io.BytesIO(data), len(data), hashlib.sha256(data).digest(), 0
    stream = args.file.open('rb') if args.file else args.research_image.open('rb')
    offset = 0
    if args.research_image:
        header = stream.read(4096)
        if (len(header) != 4096 or header[:8] != b'SHNGROOT' or
                hashlib.sha256(header[:4064]).digest() != header[4064:]):
            raise ValueError('invalid SHNGROOT manifest/checksum')
        version, count = struct.unpack_from('<II', header, 8)
        if version != 1 or not 1 <= count <= 31:
            raise ValueError('unsupported SHNGROOT manifest')
        selected = 'OS' if args.kind == 'os' else 'SystemOS'
        entries = [struct.unpack_from('<32sQQII32s', header, 24 + i * 128) for i in range(count)]
        matches = [e for e in entries if e[0].rstrip(b'\0').decode() == selected]
        if len(matches) != 1:
            raise ValueError('requested image window is missing or duplicated')
        _, offset, size, _, _, expected = matches[0]
        if offset + size > args.research_image.stat().st_size:
            raise ValueError('image window extends beyond source')
    else:
        size, expected = args.file.stat().st_size, None
    stream.seek(offset)
    digest = hashlib.sha256()
    left = size
    while left:
        block = stream.read(min(8 * 2**20, left))
        if not block:
            raise ValueError('truncated source')
        digest.update(block)
        left -= len(block)
    actual = digest.digest()
    if expected and actual != expected:
        raise ValueError('source image window digest mismatch')
    stream.seek(offset)
    return stream, size, actual, KINDS[args.kind]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--self-test', action='store_true')
    group.add_argument('--file', type=Path)
    group.add_argument('--research-image', type=Path)
    p.add_argument('--kind', choices=[*KINDS, 'both'], default='test')
    p.add_argument('--vid', type=lambda s: int(s, 0), default=0x1209)
    p.add_argument('--pid', type=lambda s: int(s, 0), default=1)
    p.add_argument('--report', type=Path)
    p.add_argument('--wait-seconds', type=float, default=0, help='wait only for device discovery; never retry a transfer')
    args = p.parse_args()
    if not 0 <= args.wait_seconds <= 60:
        p.error('--wait-seconds must be between 0 and 60')
    if args.research_image and args.kind == 'test':
        p.error('--research-image requires --kind os or systemos')
    if args.kind == 'both' and not args.research_image:
        p.error('--kind both requires --research-image')
    kinds = ['os', 'systemos'] if args.kind == 'both' else [args.kind]
    sources = [source(argparse.Namespace(**{**vars(args), 'kind': kind})) for kind in kinds]
    transport = None
    try:
        deadline = time.monotonic() + args.wait_seconds
        while True:
            try:
                transport = UsbTransport(args.vid, args.pid)
                break
            except RuntimeError as error:
                if ('not found or inaccessible' not in str(error) or
                        time.monotonic() >= deadline):
                    raise
                time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        def progress(done, total):
            if done == total or done % (16 * 2**20) == 0:
                print(f'{done}/{total} bytes', file=sys.stderr)
        client = Client(transport)
        objects = [client.send(stream, size, digest, obj, progress) for stream, size, digest, obj in sources]
        report = dict(objects=objects, partition_writes=False, kernel_handoff=False)
        if args.report:
            args.report.write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
    finally:
        for stream, _, _, _ in sources:
            stream.close()
        if transport:
            transport.close()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        raise SystemExit(str(error))
