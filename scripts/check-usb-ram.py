#!/usr/bin/env python3
"""Exercise the production C receiver and Python sender without USB hardware."""
import argparse
import ctypes
import hashlib
import importlib.util
import io
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--uefi', type=Path, required=True)
p.add_argument('--report', type=Path)
a = p.parse_args()
spec = importlib.util.spec_from_file_location('usb_sender', Path(__file__).with_name('usb-ram-send.py'))
sender = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sender)
source = a.uefi.resolve() / 'Platforms/Xiaomi/fuxiPkg/Drivers/FuxiUsbRamDxe'
adapter = r'''
#if defined(__linux__)
#define _GNU_SOURCE
#endif
#include "RamBulk.h"
#if defined(__APPLE__)
#include <CommonCrypto/CommonDigest.h>
#else
#include <openssl/sha.h>
#define CC_SHA256_CTX SHA256_CTX
#define CC_LONG unsigned int
#define CC_SHA256_Init SHA256_Init
#define CC_SHA256_Update SHA256_Update
#define CC_SHA256_Final SHA256_Final
#endif
#include <sys/mman.h>
#include <stdlib.h>
static int fail_alloc;
static void *alloc(void *ctx,RB64 n) {
  (void)ctx;if(fail_alloc)return 0;
  void *p=mmap(0,(size_t)n,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);
  return p==MAP_FAILED?0:p;
}
static void release(void *ctx,void *p,RB64 n) {(void)ctx;munmap(p,(size_t)n);}
static int hash(const void *p,RB64 n,RB8 digest[32]) {
  CC_SHA256_CTX c;CC_SHA256_Init(&c);
  while(n) {CC_LONG k=(CC_LONG)(n>65536?65536:n);CC_SHA256_Update(&c,p,k);p=(const RB8 *)p+k;n-=k;}
  return CC_SHA256_Final(digest,&c);
}
void *session_new(void) {
  RB_SESSION *s=calloc(1,sizeof(*s));s->Allocate=alloc;s->Release=release;s->Hash=hash;return s;
}
void session_free(RB_SESSION *s) {RbClear(s);free(s);}
void session_call(RB_SESSION *s,const RB8 *h,const RB8 *p,RB32 n,RB8 *out) {RbHandle(s,h,p,n,out);}
void session_corrupt(RB_SESSION *s,unsigned id,RB64 at) {s->Object[id].Data[at]^=1;}
void fixture_offset(RB_SESSION *s,unsigned id,RB64 at) {s->Object[id].Received=at;}
RB64 session_allocated(RB_SESSION *s) {return s->Allocated;}
void fixture_alloc_fail(int value) {fail_alloc=value;}
'''
cases = []
with tempfile.TemporaryDirectory(prefix='ram-bulk-test-') as directory:
    work = Path(directory)
    (work / 'host.c').write_text(adapter)
    darwin = sys.platform == 'darwin'
    lib = work / ('receiver.dylib' if darwin else 'receiver.so')
    shared_flags = ['-dynamiclib'] if darwin else ['-shared', '-fPIC']
    crypto_flags = [] if darwin else ['-lcrypto']
    subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-deprecated-declarations',
                    *shared_flags, '-I', str(source), str(source / 'RamBulk.c'), str(work / 'host.c'),
                    '-o', str(lib), *crypto_flags], check=True)
    c = ctypes.CDLL(str(lib))
    c.session_new.restype = ctypes.c_void_p
    c.session_free.argtypes = [ctypes.c_void_p]
    c.session_call.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    c.session_corrupt.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint64]
    c.fixture_offset.argtypes = c.session_corrupt.argtypes
    c.session_allocated.argtypes, c.session_allocated.restype = [ctypes.c_void_p], ctypes.c_uint64
    c.fixture_alloc_fail.argtypes = [ctypes.c_int]

    class ProductionReceiver:
        def __init__(self):
            self.session = c.session_new()

        def exchange(self, header, payload):
            out = ctypes.create_string_buffer(64)
            c.session_call(self.session, ctypes.create_string_buffer(header),
                           ctypes.create_string_buffer(payload) if payload else None, len(payload), out)
            return out.raw

        def close(self):
            c.session_free(self.session)

    receiver = ProductionReceiver()
    client = sender.Client(receiver)
    data = bytes(range(256)) * 4096
    digest = hashlib.sha256(data).digest()

    def refused(fn, status, name):
        try:
            fn()
        except RuntimeError as error:
            assert f'status={status},' in str(error), str(error)
        else:
            raise AssertionError('unexpected acceptance: ' + name)
        cases.append(name)

    try:
        result = client.send(io.BytesIO(data), len(data), digest, 0)
        assert result['ram_readback_verified'] and result['bytes'] == 2**20
        cases.append('actual Python sender to production C receiver, 1 MiB RAM readback')
        refused(lambda: client.command(sender.DATA, 0, len(data), b'x'), 3, 'write after commit')
        refused(lambda: client.command(sender.BEGIN, 0, payload=struct.pack('<Q', 1) + digest), 3, 'duplicate BEGIN')
        client.command(sender.ABORT)
        assert c.session_allocated(receiver.session) == 0
        cases.append('ABORT releases owned RAM')
        for object_id, content in [(1, b'OS window'), (2, b'SystemOS window')]:
            client.send(io.BytesIO(content), len(content), hashlib.sha256(content).digest(), object_id)
        assert c.session_allocated(receiver.session) == len(b'OS windowSystemOS window')
        cases.append('OS and SystemOS retained together in one USB session')
        client.command(sender.ABORT, 1)
        client.command(sender.ABORT, 2)
        refused(lambda: client.command(sender.BEGIN, 3, payload=struct.pack('<Q', 1) + digest), 2, 'unknown object')
        refused(lambda: client.command(sender.BEGIN, 0, payload=struct.pack('<Q', 2**20 + 1) + digest), 4, 'test allocation limit')
        refused(lambda: client.command(sender.BEGIN, 1, payload=struct.pack('<Q', 0xffffffffffffffff) + digest), 4, '64-bit size overflow')
        c.fixture_alloc_fail(1)
        refused(lambda: client.command(sender.BEGIN, 0, payload=struct.pack('<Q', 1) + digest), 7, 'allocation failure')
        c.fixture_alloc_fail(0)
        client.command(sender.BEGIN, 0, payload=struct.pack('<Q', 3) + hashlib.sha256(b'abc').digest())
        refused(lambda: client.command(sender.DATA, 0, 1, b'abc'), 5, 'out-of-order data')
        refused(lambda: client.command(sender.DATA, 0, 0, b'abcd'), 4, 'object overrun')
        refused(lambda: client.command(sender.END, 0, 0), 3, 'incomplete commit')
        bad = sender.HEADER.pack(sender.MAGIC, 1, sender.DATA, 0, 0, 3, 0, bytes(32))
        reply = sender.HEADER.unpack(receiver.exchange(bad, b'abc'))
        assert reply[6] == 6
        cases.append('bad chunk digest rejected')
        client.command(sender.DATA, 0, 0, b'abc')
        refused(lambda: client.command(sender.DATA, 0, 0, b'abc'), 5, 'duplicate data')
        c.session_corrupt(receiver.session, 0, 1)
        refused(lambda: client.command(sender.END, 0, 3), 6, 'actual RAM corruption detected at commit')
        refused(lambda: client.command(sender.DATA, 0, 3, b'x'), 3, 'poisoned object cannot continue')
        client.command(sender.ABORT)
        # Reserve sparse virtual memory, then exercise a genuine copy across
        # 4 GiB. No 4 GiB byte stream or physical allocation is fabricated.
        client.command(sender.BEGIN, 1, payload=struct.pack('<Q', 5 << 30) + digest)
        c.fixture_offset(receiver.session, 1, 0xfffffffe)
        accepted, _ = client.command(sender.DATA, 1, 0xfffffffe, b'abcd')
        assert accepted == 0x100000002
        cases.append('64-bit offset copy and ACK cross 4 GiB')
        refused(lambda: client.command(sender.BEGIN, 2, payload=struct.pack('<Q', 6 << 30) + digest), 4, 'SystemOS object limit')
        client.command(sender.ABORT, 1)
        client.command(sender.BEGIN, 1, payload=struct.pack('<Q', 8 << 30) + digest)
        refused(lambda: client.command(sender.BEGIN, 2, payload=struct.pack('<Q', 3 << 30) + digest), 4, 'combined RAM budget')
        client.command(sender.ABORT, 1)
        for field, value in [(0, b'BADMAGIC'), (1, 2), (5, sender.CHUNK + 1), (6, 1)]:
            fields = [sender.MAGIC, 1, sender.HELLO, 0, 0, 0, 0, bytes(32)]
            fields[field] = value
            reply = sender.HEADER.unpack(receiver.exchange(sender.HEADER.pack(*fields), b''))
            assert reply[6] == 1
        cases.append('bad magic/version/chunk size/reserved status rejected')
        refused(lambda: client.command(0x100), 1, 'unknown opcode')
    finally:
        receiver.close()
report = dict(cases=len(cases), passed=cases, engine='production RamBulk.c',
              host='production usb-ram-send.py Client', hardware_usb_tested=False)
report['source_sha256'] = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                         for name in ['RamBulk.c', 'RamBulk.h']}
report['sender_sha256'] = hashlib.sha256(Path(__file__).with_name('usb-ram-send.py').read_bytes()).hexdigest()
if a.report:
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
