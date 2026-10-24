#!/usr/bin/env python3
"""Compare the offline md model with the candidate's actual C ioctl handler.

No phone and no APFS mount. The C oracle is extracted from the built XNU tree
and runs under ASan/UBSan. Python checks the real model's output-buffer writes,
sector arithmetic, Darwin variadic decoding, and standard APFS object headers.
"""
import argparse
import hashlib
import importlib.util
import json
import re
import struct
import subprocess
import tempfile
from pathlib import Path


def braced(text, marker):
    start = text.index(marker)
    opening = text.index('{', start)
    end, depth = opening + 1, 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def oracle_source(root):
    source = (root / 'bsd/dev/memdev.c').read_text()
    disk = (root / 'bsd/sys/disk.h').read_text()
    names = re.findall(r'case (DKIOC\w+):', braced(source, 'static int\nmdevioctl('))
    defines = '\n'.join(re.search(r'^#define ' + name + r'\s+[^\n]+', disk, re.M)[0]
                        for name in names)
    flags = '\n'.join(line for line in source.splitlines() if line.startswith('#define md'))
    return r'''
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
#include <sys/ioctl.h>
#ifndef __unused
#define __unused __attribute__((unused))
#endif
#define TRUE 1
#define FALSE 0
#define DEV_BSIZE 512
#define NB_MAX_MDEVICES 3
#ifndef minor
#define minor(d) ((d) & 0xffffff)
#endif
struct proc { int unused; };
static int proc_suser(struct proc *p) { (void)p; return 0; }
typedef struct { int mi_mdev,mi_phys; uint32_t mi_base; uint64_t mi_size; } dk_memdev_info_t;
''' + defines + '\n' + flags + '\n' + braced(source, 'struct mdev {') + r''';
static struct mdev mdev[NB_MAX_MDEVICES];
''' + braced(source, 'static uint64_t mdevbytes(') + '\n' + braced(source, 'static int\nmdevioctl(') + r'''
int main(int argc, char **argv) {
    if (argc != 5) return 1;
    uint32_t cmd=(uint32_t)strtoul(argv[1],NULL,0);
    mdev[1].mdByteSize=strtoull(argv[2],NULL,0);
    mdev[1].mdSecsize=(int)strtoul(argv[3],NULL,0);
    mdev[1].mdFlags=mdRO|mdInited;
    union { uint64_t aligned; unsigned char b[64]; } output;
    memset(&output,0xa5,sizeof(output));
    if (cmd==DKIOCSETBLOCKSIZE) {
        uint32_t input=(uint32_t)strtoul(argv[4],NULL,0);
        memcpy(output.b+8,&input,4);
    }
    int error=mdevioctl(1,cmd,(caddr_t)(output.b+8),0,NULL,0);
    printf("%d %d ",error,mdev[1].mdSecsize);
    for (unsigned i=0;i<sizeof(output);i++) printf("%02x",output.b[i]);
    puts("");
    return 0;
}
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path, help='candidate containing work/tree/.cache/xnu-work')
    p.add_argument('--image', required=True, type=Path)
    p.add_argument('--report', required=True, type=Path)
    a = p.parse_args()
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location('apfs_model', here / 'analyze-apfs-ram-mount.py')
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    root = a.output.resolve() / 'work/tree/.cache/xnu-work'
    cases = []
    with tempfile.TemporaryDirectory(prefix='apfs-md-oracle-') as tmp:
        folder = Path(tmp)
        (folder / 'oracle.c').write_text(oracle_source(root))
        subprocess.run(['clang', '-std=c11', '-Wall', '-Wextra', '-Werror',
                        '-fsanitize=address,undefined', str(folder / 'oracle.c'),
                        '-o', str(folder / 'oracle')], check=True)
        commands = [0x40046418, 0x40086419, 0x4004641d, 0x40046448,
                    0x4004644c, 0x4004644f, 0x40046456, 0x4018645a,
                    0x40086440, 0x40086441, 0x40086442, 0x40086443,
                    0x40086418, 0x40046518, 0x80046418]
        for size in (model.UR_OS_APFS_BYTES, model.UR_SYSTEM_BYTES):
            for sector in (512, 4096):
                for command in commands:
                    for input_size in ((511, 512, 4096) if command == 0x80046418 else (0,)):
                        raw = subprocess.check_output([str(folder / 'oracle'), hex(command),
                                                       str(size), str(sector), str(input_size)], text=True)
                        rc, after_sector, actual = raw.split()
                        media = model.RamMedia(b'', 0, size)
                        media.sector_size = sector
                        memory = bytearray(b'\xa5' * 64)
                        if command == 0x80046418:
                            memory[8:12] = struct.pack('<I', input_size)
                        # Exercise the actual VNOP wrapper, including all bytes
                        # around the caller's output. Do not only test a table.
                        emu = model.MountEmulator.__new__(model.MountEmulator)
                        emu.objects = {1: {'fields': {'path': '/dev/md1', 'resolved': '/dev/md1'}}}
                        emu.devices = {'/dev/md1': (model.DEV_MD1, media)}
                        emu.ioctls = []
                        emu.x = lambda i: (1, command, 8)[i]
                        emu.uc = type('Memory', (), {'mem_read': lambda _, at, n: memory[at:at+n]})()
                        emu.put = lambda at, data: memory.__setitem__(slice(at, at+len(data)), data)
                        emu.ret = lambda value: setattr(emu, 'result', value)
                        emu.do_vnop_ioctl()
                        assert (emu.result, media.sector_size, memory.hex()) == (int(rc), int(after_sector), actual), raw
                        cases.append(dict(command=hex(command), bytes=size, sector=sector,
                                          input=input_size, result=emu.result))
    # Exercise the actual getblk hook on byte-patterned media. Changing the
    # transfer length must not change the start of a given device block.
    emu = model.MountEmulator.__new__(model.MountEmulator)
    media = model.RamMedia(bytes(i % 251 for i in range(16384)), 0, 16384)
    emu.objects = {1: {'fields': {'path': '/dev/md1'}}}
    emu.devices = {'/dev/md1': (model.DEV_MD1, media)}
    hooks = {}
    emu.try_install = lambda name, callback: hooks.__setitem__(name, callback)
    emu.install_buffer()
    memory = bytearray(32768)
    def alloc(n):
        at = emu.heap
        emu.heap += n
        return at
    emu.alloc = alloc
    emu.put = lambda at, data: memory.__setitem__(slice(at, at+len(data)), data)
    emu.qword = lambda at, value: emu.put(at, struct.pack('<Q', value))
    emu.ret = lambda value: setattr(emu, 'result', value)
    addressing = []
    for sector, number, length in [(512, 8, 512), (512, 8, 4096), (4096, 1, 4096)]:
        emu.heap = 1024
        media.sector_size = sector
        emu.x = lambda i: (1, number, length)[i]
        hooks['_buf_getblk']()
        pointer, = struct.unpack_from('<Q', memory, emu.result + emu.BUF_DATAP)
        assert memory[pointer:pointer+length] == media.read(4096, length)
        assert emu.reads[-1]['byte_offset'] == 4096
        addressing.append(emu.reads[-1])
    for offset, n in [(-1, 512), (16384, 1)]:
        try:
            media.read(offset, n)
        except ValueError:
            pass
        else:
            raise AssertionError('out-of-range read accepted')
    # Darwin va_list: a pointer to stack slots, with no register-save structure.
    emu = model.MountEmulator.__new__(model.MountEmulator)
    emu.x = lambda i: (0x100, 0x200)[i]
    strings = {0x100: b'%s:%d: size=%u count=%llu %%\n', 0x300: b'dev_init'}
    emu.string = lambda at: strings[at]
    stack = struct.pack('<QQQQ', 0x300, 303, 512, model.UR_OS_APFS_BYTES // 512)
    emu.uc = type('Memory', (), {'mem_read': lambda _, at, n: stack[at-0x200:at-0x200+n]})()
    emu.log_tail = []
    emu.ret = lambda value: None
    emu.do_vprintf()
    assert emu.log_tail == ['dev_init:303: size=512 count=11870208 %\n']
    headers = []
    assert model.CONTAINER_HEADER == 0
    with a.image.open('rb') as f:
        for name, at, length in [('OS', model.UR_OS_OFFSET + model.UR_OS_APFS_OFFSET, model.UR_OS_APFS_BYTES),
                                 ('SystemOS', model.UR_SYS_OFFSET, model.UR_SYSTEM_BYTES)]:
            f.seek(at)
            data = f.read(4096)
            assert data[32:36] == b'NXSB' and data[64:68] != b'NXSB'
            oid, xid = struct.unpack_from('<QQ', data, 8)
            block_size, blocks = struct.unpack_from('<IQ', data, 36)
            assert oid == 1 and xid > 0 and block_size * blocks == length
            headers.append(dict(name=name, offset=at, oid=oid, xid=xid,
                                bytes=length, block_sha256=hashlib.sha256(data).hexdigest()))
    report = dict(passed=True, ioctl_cases=cases, addressing=addressing,
                  darwin_log=emu.log_tail, container_headers=headers,
                  analyzer_sha256=sha(here / 'analyze-apfs-ram-mount.py'),
                  checker_sha256=sha(Path(__file__)),
                  sources={name: sha(root / name) for name in ('bsd/dev/memdev.c', 'bsd/sys/disk.h', 'bsd/vfs/vfs_bio.c')},
                  scope='C ioctl equivalence, output canaries, block units and header/log ABI; no APFS mount or phone')
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2) + '\n')
    print(f'PASS: {len(cases)} C/model ioctl comparisons; block units, Darwin logs and both object headers')


if __name__ == '__main__':
    main()
