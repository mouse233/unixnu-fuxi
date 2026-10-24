#!/usr/bin/env python3
"""Offline execution of the hash-pinned 21H461 apfs_vfsop_mount; never uses a phone.

Extends analyze-apfs-ram-contract.py: instead of the device-name parser alone, this
harness runs the original APFS mount entry with modeled XNU/IOKit kernel imports.
Block devices are backed by the real SHNGROOT OS/SystemOS window bytes, so container
superblock reads see the actual on-image data. Unmodeled imports stop the run with a
report instead of guessing. Results here are ABI/integration observations of original
code, not a substitute for a complete XNU/APFS boot on hardware.
"""
import argparse
import hashlib
import importlib.util
import json
import mmap
import re
import struct
import sys
from pathlib import Path

from unicorn import UC_HOOK_CODE

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('contract', HERE / 'analyze-apfs-ram-contract.py')
contract = importlib.util.module_from_spec(spec)
sys.modules['contract'] = contract
spec.loader.exec_module(contract)
Machine = contract.Machine
APFS_TEXT_SHA = contract.APFS_TEXT_SHA
VALIDATOR = 0xfffffff005664b70       # container-superblock validator entry
MOUNT = contract.MOUNT
STOP = contract.STOP

UR_OS_OFFSET = 1048576            # SHNGROOT manifest window "OS"
UR_OS_APFS_OFFSET = 17408         # GPT partition start inside the OS window
UR_OS_APFS_BYTES = 6077546496
UR_SYS_OFFSET = 6079643648        # SHNGROOT manifest window "SystemOS"
UR_SYSTEM_BYTES = 3303014400
# NXSB follows the standard 32-byte obj_phys_t header inside block zero.
# Never skip that header. Nonzero bias is only a deliberately corrupt control.
CONTAINER_HEADER = 0
DEV_BSIZE = 512
MNT_RDONLY = 1
DEV_MD1 = 0x3000001               # major 3, minor 1 (dev_t uses a 24-bit minor)
DEV_MD2 = 0x3000002

ARENA_EXT_END = contract.ARENA + 0x1000000   # end of the inherited small arena
FAKE_PAGE = 0x60000000            # hook addresses for modeled virtual methods


class RamMedia:
    """Read-only byte backend for one RAM block device window."""

    def __init__(self, mapping, base, size):
        self.map, self.base, self.size = mapping, base, size
        self.sector_size = DEV_BSIZE  # mdevadd initializes mdSecsize = DEV_BSIZE

    def read(self, offset, size):
        if offset < 0 or size < 0 or offset + size > self.size:
            raise ValueError(f'read past md window: off={offset:#x} size={size:#x}')
        data = self.map[self.base + offset:self.base + offset + size]
        if len(data) != size:
            raise ValueError('truncated backing image')
        return data

    def ioctl(self, command, payload=b''):
        """Exact commands/widths from disk.h and mdevioctl + RAM slice patch.

        Unsupported queries return ENOTTY without writing the output buffer.
        In particular DKIOCISVIRTUAL is unsupported even for a RAM device.
        """
        if command == 0x40046418:  # _IOR('d', 24, uint32_t): GETBLOCKSIZE
            return 0, struct.pack('<I', self.sector_size)
        if command == 0x40086419:  # _IOR('d', 25, uint64_t): GETBLOCKCOUNT
            count = (self.size + self.sector_size - 1) // self.sector_size
            return 0, struct.pack('<Q', count)
        if command == 0x80046418:  # _IOW('d', 24, uint32_t): SETBLOCKSIZE
            size, = struct.unpack('<I', payload)
            if size < DEV_BSIZE:
                return 22, b''  # EINVAL
            self.sector_size = size
            return 0, b''
        if command == 0x4004641d:  # ISWRITABLE: USB RAM slice is read-only
            return 0, struct.pack('<I', 0)
        if command in (0x40086440, 0x40086441, 0x40086442, 0x40086443):
            return 0, struct.pack('<Q', 32)  # max blocks/segments read/write
        return 25, b''  # ENOTTY, including patched GETMEMDEVINFO and ISVIRTUAL


class MountEmulator(Machine):
    def __init__(self, kernel, base, image, media_for=None, container_header=CONTAINER_HEADER):
        super().__init__(kernel, base)
        self.container_header = container_header
        self.image_path = image
        self.devices = {}          # path -> (dev_t, RamMedia)
        self.opened = []           # (path, error) seen through vnode_open
        self.ioctls = []           # (path, selector, result)
        self.media_for = media_for or (lambda dev: None)
        self.objects = {}          # fake-object address -> dict
        self.method_hooks = {}     # hook address -> (name, closure)
        self.next_method = FAKE_PAGE + 0x1000
        self.log_tail = []
        self.uptime_us = 1000
        self.mnt_flags = MNT_RDONLY
        self.big_arena = 0x40000000
        self.big_end = self.big_arena + 0x10000000
        self.big_mapped = False
        self.image_handle = image.open('rb')
        super_map = mmap.mmap(self.image_handle.fileno(), 0, access=mmap.ACCESS_READ)
        self.devices['/dev/md1'] = (DEV_MD1, RamMedia(super_map, UR_OS_OFFSET + UR_OS_APFS_OFFSET + self.container_header, UR_OS_APFS_BYTES))
        self.devices['/dev/md2'] = (DEV_MD2, RamMedia(super_map, UR_SYS_OFFSET + self.container_header, UR_SYSTEM_BYTES))
        from collections import deque
        self.recent = deque(maxlen=400)      # keep a deeper tail for failure analysis
        self.validator_dumps = []
        self.uc.hook_add(UC_HOOK_CODE, self.dump_validator, begin=VALIDATOR, end=VALIDATOR)
        self.install_core()

    def dump_validator(self, uc, address, size, data):
        # One buffer snapshot per container-superblock validation entry: x0 is
        # the candidate block bytes, x1 the block size. Correlated with the
        # getblk data below, this pins down where the driver expects the NXSB
        # inside its first container read.
        if len(self.validator_dumps) >= 4:
            return
        x0, x1 = self.x(0), self.x(1)
        try:
            payload = bytes(uc.mem_read(x0, 0x140)).hex()
        except Exception as error:
            payload = f'unreadable: {error!r}'
        self.validator_dumps.append(dict(x0=hex(x0), x1=hex(x1), bytes=payload))

    # -- fake-object plumbing ------------------------------------------------
    def alloc(self, size):
        rounded = (size + 15) & ~15
        if self.heap + rounded > ARENA_EXT_END and not self.big_mapped:
            self.map(self.big_arena, self.big_end - self.big_arena)
            self.big_mapped = True
            self.heap = self.big_arena
        if self.heap + rounded > self.big_end:
            raise ValueError('mount emulation arena exhausted')
        address = self.heap
        self.heap += rounded
        self.put(address, bytes(size))
        return address

    def method(self, name, closure):
        address = self.next_method
        self.next_method += 4
        self.map(address & ~4095, 4096)
        self.hooks[address] = lambda: closure(self)
        self.method_hooks[address] = name
        return address

    def obj(self, kind, vtable, fields=None, size=0x200):
        address = self.alloc(size)
        table = self.alloc(0x400)
        for offset, slot in vtable.items():
            # keys are byte offsets into the vtable, matching ldr [x8, #imm]
            self.put(table + offset, struct.pack('<Q', slot))
        self.put(address, struct.pack('<Q', table))
        entry = dict(kind=kind, table=table, refcount=1, fields=fields or {})
        self.objects[address] = entry
        return address

    def kind(self, address):
        entry = self.objects.get(address)
        return entry['kind'] if entry else f'?{address:#x}'

    # -- modeled imports -----------------------------------------------------
    def install_core(self):
        def noop(*names):
            for name in names:
                if any(name in aliases for aliases in self.external.values()):
                    self.install(name, lambda: self.ret(0))
        noop('_kernel_debug', '_kdebug_vnode', '_vfs_setflags', '_vfs_clearflags',
             '_vfs_setowner', '_vfs_init_io_attributes', '_vnode_put', '_vnode_close',
             '_vnode_setmountedon', '_proc_selfpid', '_IOLockLock', '_IOLockUnlock',
             '_lck_rw_init', '_lck_rw_destroy', '_IOLockSleep',
             '_kauth_cred_setuidgid')
        def from_path():
            if self.x(4):
                self.put(self.x(4), bytes(8))
            self.ret(0)
        self.install('__ZN15IORegistryEntry8fromPathEPKcPK15IORegistryPlanePcPiPS_', from_path)
        # XNU lock APIs: allocations return distinct fake handles; ops are no-ops.
        allocators = ('_lck_grp_attr_alloc_init', '_lck_grp_alloc_init', '_lck_mtx_alloc_init',
                      '_lck_rw_alloc_init', '_lck_attr_alloc_init', '_lck_mtx_attr_alloc_init')
        noops = ('_lck_mtx_init', '_lck_mtx_destroy', '_lck_mtx_lock', '_lck_mtx_unlock', '_lck_mtx_assert', '_lck_rw_lock',
                 '_lck_rw_unlock', '_lck_rw_lock_shared', '_lck_rw_unlock_shared',
                 '_lck_rw_lock_exclusive', '_lck_rw_unlock_exclusive', '_lck_grp_free',
                 '_lck_mtx_free', '_lck_rw_free', '_lck_grp_attr_free', '_lck_attr_free',
                 '_lck_mtx_attr_free', '_lck_sleep', '_thread_block')
        for name in allocators + noops:
            if any(name in aliases for aliases in self.external.values()):
                self.install(name, lambda: self.ret(self.alloc(0x100)))
        self.install('_vprintf', self.do_vprintf_safe)

        def sysctlbyname():
            name = self.string(self.x(0)).decode()
            old, oldlen = self.x(1), self.x(2)
            values = {'hw.ncpu': 8, 'hw.activecpu': 8, 'hw.physicalcpu': 8,
                      'hw.logicalcpu': 8, 'kern.bootargs': 0}
            quads = {'hw.memsize': 8 * 1024 * 1024 * 1024}
            if name in quads and old:
                if oldlen:
                    self.put(oldlen, struct.pack('<Q', 8))
                self.put(old, struct.pack('<Q', quads[name]))
                self.log_tail.append(f'sysctl {name}={quads[name]}')
                self.ret(0)
            elif name in values and old:
                if oldlen:
                    self.put(oldlen, struct.pack('<Q', 4))
                self.put(old, struct.pack('<i', values[name]))
                self.log_tail.append(f'sysctl {name}={values[name]}')
                self.ret(0)
            else:
                self.log_tail.append(f'sysctl {name}=ENOENT')
                self.ret(2)
        self.install('_kernel_sysctlbyname', sysctlbyname)

        def parse_boot_argn():
            # No special boot-args in this harness; every lookup misses.
            self.log_tail.append('boot-arg? ' + self.string(self.x(0)).decode(errors='replace'))
            self.ret(0)
        self.install('_PE_parse_boot_argn', parse_boot_argn)

        def microuptime():
            self.put(self.x(0), struct.pack('<Q', self.uptime_us))
            self.uptime_us += 100
            self.ret()
        self.install('_microuptime', microuptime)
        for extra in ('_nanouptime', '_clock_absolutetime_interval_to_microtime',
                      '_clock_absolutetime_interval_to_nanoseconds', '_absolutetime_to_microtime'):
            if any(extra in aliases for aliases in self.external.values()):
                self.install(extra, lambda: self.ret(0))

        self.install('_vfs_isupdate', lambda: self.ret(0))
        self.install('_vfs_typenum', lambda: self.ret(0))
        self.install('_vfs_context_current', lambda: self.ret(self.ctx_ptr))
        self.install('_vfs_context_kernel', lambda: self.ret(self.ctx_ptr))
        self.install('_vfs_context_create', lambda: self.ret(self.ctx_ptr))
        # chained-fixup binding names can arrive suffix-truncated
        self.install('_vfs_context_rele', lambda: self.ret(0))
        self.install('_vfs_context_suser', lambda: self.ret(0))
        self.install('_current_task', lambda: self.ret(self.task_ptr))
        self.install('_copyin', self.do_copyin)
        def do_strlen():
            text = self.string(self.x(0))
            self.log_tail.append(f'strlen({self.x(0):#x}) = {len(text)} {text[:40]!r}')
            self.ret(len(text))
        self.install('_strlen', do_strlen)
        self.install('_strlcpy', self.strlcpy)
        self.install('_strncmp', self.strncmp)
        self.install('_strcmp', self.do_strcmp)
        self.install('_memcmp', self.do_memcmp)
        self.install('_bzero', self.do_bzero)
        self.install('_uuid_parse', self.do_uuid_parse)
        self.install('_vfs_flags', lambda: self.ret(self.mnt_flags))
        self.install('_vfs_statfs', self.do_vfs_statfs)
        self.install('_vfs_iterate', self.do_vfs_iterate)
        self.install('_vnode_specrdev', self.do_specrdev)
        self.install('_vnode_vtype', lambda: self.ret(3))  # VBLK
        def do_memcpy():
            dst, src, n = self.x(0), self.x(1), self.x(2)
            self.put(dst, bytes(self.uc.mem_read(src, n)))
            self.ret(dst)
        self.install('_memcpy', do_memcpy)
        self.install('_memmove', do_memcpy)
        def do_zalloc():
            # zalloc(zone, ...) callers here request <= one page
            self.ret(self.alloc(0x1000))
        self.install('_zalloc', do_zalloc)
        self.try_install('_zfree', lambda: self.ret(0))
        # APFS reference locks: kernel-side shims; model as always-acquired
        self.try_install('_kern_apfs_reflock_alloc_init', lambda: self.ret(self.alloc(0x40)))
        for zero in ('_kern_apfs_reflock_free', '_kern_apfs_reflock_unlock',
                     '_kern_apfs_reflock_try_put_ref'):
            self.try_install(zero, lambda: self.ret(0))
        def stop_reflock_wait():
            # Returning immediately from this stateful wait caused an infinite
            # retry loop. Stop at this model boundary; do not infer an APFS hang.
            self.failure = dict(model_boundary='kern_apfs_reflock_wait_for_unlock',
                                reason='reference-lock state and scheduling are not modeled')
            self.uc.emu_stop()
        self.try_install('_kern_apfs_reflock_wait_for_unlock', stop_reflock_wait)
        for one in ('_kern_apfs_reflock_read_ref', '_kern_apfs_reflock_try_get_ref',
                    '_kern_apfs_reflock_try_lock'):
            self.try_install(one, lambda: self.ret(1))
        def do_panic():
            text = self.string(self.x(0)).decode('latin1')
            self.log_tail.append('PANIC: ' + text)
            self.failure = dict(panic=text)
            self.uc.emu_stop()
        self.try_install('_panic', do_panic)
        def kernel_printf():
            try:
                self.log_tail.append('kprintf: ' + self.string(self.x(0)).decode('latin1'))
            except Exception:
                pass
            self.ret(0)
        for name in ('_printf', '_IOLog', '_snprintf', '_kernel_debug', '_ktriage_record_string'):
            self.try_install(name, kernel_printf)
        for zero in ('_msleep', '_tsleep', '_assert_wait', '_thread_block',
                     '_thread_block_parameter', '_wakeup', '_clock_interval_to_deadline',
                     '_clock_absolutetime_interval_to_deadline', '_absolutetime_to_nanoseconds',
                     '_nanoseconds_to_absolutetime', '_IOSleep', '_thread_deallocate',
                     '_thread_policy_set', '_thread_set_thread_name'):
            self.try_install(zero, lambda: self.ret(0))
        def uptime():
            self.uptime_us += 1000
            self.ret(self.uptime_us)
        for name in ('_mach_absolute_time', '_mach_continuous_time', '_microuptime', '_microtime', '_nanotime'):
            self.try_install(name, uptime)
        self.try_install('__bzero', lambda: (self.put(self.x(0), bytes(self.x(1))), self.ret(0))[-1])
        self.try_install('_memset_s', lambda: (self.put(self.x(0), bytes(self.x(1))), self.ret(0))[-1])
        def do_bcmp():
            a, b, n = self.x(0), self.x(1), self.x(2)
            self.ret(0 if bytes(self.uc.mem_read(a, n)) == bytes(self.uc.mem_read(b, n)) else 1)
        self.try_install('_bcmp', do_bcmp)
        self.try_install('_strnlen', lambda: self.ret(len(self.string(self.x(0), self.x(1)))))
        self.try_install('_uuid_is_null', lambda: self.ret(1 if bytes(self.uc.mem_read(self.x(0), 16)) == bytes(16) else 0))
        self.try_install('_uuid_clear', lambda: (self.put(self.x(0), bytes(16)), self.ret(0))[-1])
        self.try_install('_uuid_copy', lambda: (self.put(self.x(0), bytes(self.uc.mem_read(self.x(1), 16))), self.ret(0))[-1])
        for zero in ('_fs_buffer_cache_gc_register', '_fs_buffer_cache_gc_unregister',
                     '_sysctl_register_oid', '_sysctl_unregister_oid',
                     '_ktriage_register_subsystem_strings', '_ktriage_unregister_subsystem_strings',
                     '_kernel_thread_start', '_bdevsw_add', '_bdevsw_remove'):
            self.try_install(zero, lambda: self.ret(0))
        def do_uuid_compare():
            a = bytes(self.uc.mem_read(self.x(0), 16))
            b = bytes(self.uc.mem_read(self.x(1), 16))
            self.ret((a > b) - (a < b))
        self.try_install('_uuid_compare', do_uuid_compare)
        self.install_buffer()
        self.install('_vnode_open', self.do_vnode_open)
        self.install('_vnode_mount', lambda: self.ret(self.mount_ptr))
        self.install('_VNOP_IOCTL', self.do_vnop_ioctl)
        self.install('_proc_selfname', self.do_proc_selfname)
        self.install('_IOMallocTypeImpl', lambda: self.ret(self.alloc(0x1000)))
        self.install('_IOMallocTypeVarImpl', self.do_iomallocvar)
        self.install('_IOFreeTypeImpl', lambda: self.ret())
        self.install('_IOMallocData', self.do_iomallocdata)
        self.install('_IOMallocZeroData', self.do_iomalloczero)
        self.install('_IOFreeData', lambda: self.ret())
        self.install('_IOFreeTypeVarImpl', lambda: self.ret())
        self.install('__ZN12OSDictionary12withCapacityEj', self.do_dict_withcapacity)
        self.install('__ZN8OSNumber10withNumberEyj', self.do_number_withnumber)
        self.install('__ZN15OSMetaClassBase12safeMetaCastEPKS_PK11OSMetaClass', self.do_safemetacast)
        self.install('__ZNK15OSMetaClassBase8metaCastEPKc', self.do_metacast)
        self.install('__ZN9IOService15serviceMatchingEPKcP12OSDictionary', self.do_service_matching)
        self.install('__ZN9IOService19copyMatchingServiceEP12OSDictionary', self.do_copy_matching)
        self.install('__ZN9IOService9waitQuietEy', lambda: self.ret(0))
        # shared fakes
        self.ctx_ptr = self.alloc(0x100)
        self.task_ptr = self.alloc(0x100)
        # struct mount is large; f_mntfromname lives at +0x460 and must stay
        # inside this allocation so later allocs cannot reclaim it.
        self.mount_ptr = self.obj('mount', {0x28: self.release_slot()}, size=0x800)
        self.statfs_ptr = self.alloc(0x400)
        for symbol, value in (('_desiredvnodes', 1536),):
            for target, aliases in self.external.items():
                if symbol in aliases:
                    self.put(target, struct.pack('<i', value))
                    break

    def release_slot(self):
        return self.method('release', lambda emu: emu.ret())

    def do_copyin(self):
        src, dst, size = self.x(0), self.x(1), self.x(2)
        self.put(dst, bytes(self.uc.mem_read(src, size)))
        self.ret(0)

    def strlcpy(self):
        dst, src, size = self.x(0), self.x(1), self.x(2)
        text = self.string(src)
        if size:
            self.put(dst, text[:size - 1] + b'\0')
        self.log_tail.append(f'strlcpy({dst:#x} <- {src:#x}, {size:#x}) = {text[:48]!r}')
        self.ret(len(text))

    def do_strcmp(self):
        a, b = self.string(self.x(0)), self.string(self.x(1))
        self.ret((a > b) - (a < b))

    def do_memcmp(self):
        a = bytes(self.uc.mem_read(self.x(0), self.x(2)))
        b = bytes(self.uc.mem_read(self.x(1), self.x(2)))
        self.ret((a > b) - (a < b))

    def do_bzero(self):
        self.put(self.x(0), bytes(self.x(1)))
        self.ret()

    def do_uuid_parse(self):
        text = self.string(self.x(0))
        hexed = bytes.fromhex(text.decode().replace('-', ''))
        self.put(self.x(1), hexed)
        self.ret(0)

    def do_vprintf(self):
        # Darwin arm64 va_list is a pointer to 8-byte stack argument slots.
        # The original APFS wrapper passes x29+0x10 directly as x1; the generic
        # AAPCS64 register-save-area struct is not the Darwin ABI.
        fmt = self.string(self.x(0)).decode(errors='replace')
        va = self.x(1)

        def next_gp():
            nonlocal va
            value, = struct.unpack('<Q', bytes(self.uc.mem_read(va, 8)))
            va += 8
            return value

        out, i = [], 0
        while i < len(fmt):
            ch = fmt[i]
            if ch == '%' and i + 1 < len(fmt):
                body, j = '', i + 1
                while j < len(fmt) and fmt[j] not in 'diuxXscp%':
                    body += fmt[j]; j += 1
                if j < len(fmt):
                    conv = fmt[j]
                    if conv == '%':
                        out.append('%')
                    elif conv == 's':
                        pointer = next_gp()
                        out.append(self.string(pointer).decode(errors='replace') if pointer else '(null)')
                    elif conv == 'c':
                        out.append(chr(next_gp() & 0xff))
                    else:
                        value = next_gp()
                        if conv in 'di':
                            bits = 64 if 'l' in body or 'z' in body else 32
                            value &= (1 << bits) - 1
                            out.append(str(value - (1 << bits) if value >> (bits - 1) else value))
                        elif conv == 'u':
                            out.append(str(value & ((1 << 64) - 1)))
                        elif conv in 'xX':
                            out.append(hex(value & ((1 << (64 if 'll' in body else 32)) - 1))[2:])
                        else:
                            out.append('?')
                i = j + 1
            else:
                out.append(ch); i += 1
        self.log_tail.append(''.join(out)[:400])
        self.ret(0)

    def do_vprintf_safe(self):
        try:
            self.do_vprintf()
        except Exception:
            self.log_tail.append('(log line not rendered: ' + self.string(self.x(0)).decode(errors='replace')[:200] + ')')
            self.ret(0)

    @staticmethod
    def signed(value):
        value &= (1 << 64) - 1
        return value - (1 << 64) if value >> 63 else value

    def do_proc_selfname(self):
        self.put(self.x(0), b'kernel\0')
        self.ret(self.x(0))

    def do_iomallocvar(self):
        self.ret(self.alloc(max(self.x(1), 0x100)))   # (metaClass, size)

    def do_iomallocdata(self):
        self.ret(self.alloc(max(self.x(0), 1)))

    def do_iomalloczero(self):
        self.ret(self.alloc(max(self.x(0), 1)))


    def try_install(self, name, callback):
        # optional imports: absent on some builds, so no assert
        for address, names in self.external.items():
            if name in names:
                self.hooks[address] = callback

    # struct buf offsets from 21H461 bsd/sys/buf_internal.h (arm64)
    BUF_FLAGS = 0x4c
    BUF_ERROR = 0x54
    BUF_BUFSIZE = 0x58
    BUF_BCOUNT = 0x5c
    BUF_DEV = 0x64
    BUF_DATAP = 0x68
    BUF_LBLKNO = 0x70
    BUF_BLKNO = 0x78
    BUF_IODONE = 0x80
    BUF_VP = 0x88
    BUF_ATTR = 0xf0
    B_READ = 0x1
    B_ASYNC = 0x2
    B_DONE = 0x20000
    B_ERROR = 0x80000

    def install_buffer(self):
        self.reads = []            # (path, lba, size) served from RamMedia

        def media_of(vp):
            entry = self.objects.get(vp) or {}
            fields = entry.get('fields', {})
            path = fields.get('resolved') or fields.get('path')
            return path, self.devices[path][1]

        def getblk():
            vp, lblkno, size = self.x(0), self.x(1), self.x(2)
            path, media = media_of(vp)
            bp = self.alloc(0x200)
            data = self.alloc(max(size, 0x1000))
            # For VBLK, buf_getblk stores blkno unchanged in b_blkno. The md
            # strategy multiplies it by mdSecsize, never by the transfer size.
            offset = lblkno * media.sector_size
            chunk = media.read(offset, size)
            self.put(data, bytes(chunk))
            self.qword(bp + self.BUF_DATAP, data)
            self.put(bp + self.BUF_FLAGS, struct.pack('<I', self.B_READ | self.B_DONE))
            for field, value in ((self.BUF_BUFSIZE, size), (self.BUF_BCOUNT, size),
                                 (self.BUF_ERROR, 0), (self.BUF_LBLKNO, lblkno),
                                 (self.BUF_BLKNO, lblkno)):
                if field in (self.BUF_LBLKNO, self.BUF_BLKNO):
                    self.qword(bp + field, value)
                else:
                    self.put(bp + field, struct.pack('<i', value))
            self.qword(bp + self.BUF_VP, vp)
            self.put(bp + self.BUF_DEV, struct.pack('<I', self.devices[path][0]))
            self.reads.append(dict(path=path, block_number=lblkno, size=size,
                                   byte_offset=offset, sector_size=media.sector_size,
                                   data_head=bytes(chunk[:0x140]).hex() if len(self.reads) < 4 else None))
            self.ret(bp)

        self.try_install('_buf_getblk', getblk)
        self.try_install('_buf_error', lambda: self.ret(struct.unpack('<i', bytes(self.uc.mem_read(self.x(0) + self.BUF_ERROR, 4)))[0]))
        self.try_install('_buf_valid', lambda: self.ret(1))
        self.try_install('_buf_map', lambda: (self.qword(self.x(1), struct.unpack('<Q', bytes(self.uc.mem_read(self.x(0) + self.BUF_DATAP, 8)))[0]), self.ret(0))[-1])
        self.try_install('_buf_brelse', lambda: self.ret(0))
        self.try_install('_buf_iowait', lambda: self.ret(0))
        self.try_install('_buf_setflags', lambda: self.ret(0))
        self.try_install('_buf_setcallback', lambda: self.ret(0))
        self.try_install('_buf_clearerror', lambda: self.ret(0))
        self.try_install('_buf_attr', lambda: self.ret(self.x(0) + self.BUF_ATTR))
        self.try_install('_buf_blkno', lambda: self.ret(struct.unpack('<q', bytes(self.uc.mem_read(self.x(0) + self.BUF_BLKNO, 8)))[0]))
        self.try_install('_buf_count', lambda: self.ret(struct.unpack('<i', bytes(self.uc.mem_read(self.x(0) + self.BUF_BCOUNT, 4)))[0]))
        self.try_install('_buf_size', lambda: self.ret(struct.unpack('<i', bytes(self.uc.mem_read(self.x(0) + self.BUF_BUFSIZE, 4)))[0]))
        self.try_install('_buf_resid', lambda: self.ret(struct.unpack('<i', bytes(self.uc.mem_read(self.x(0) + 0x60, 4)))[0]))
        self.try_install('_buf_biowait', lambda: self.ret(0))
        self.try_install('_buf_biodone', lambda: self.ret(0))
        self.try_install('_buf_dataptr', lambda: self.ret(struct.unpack('<Q', bytes(self.uc.mem_read(self.x(0) + self.BUF_DATAP, 8)))[0]))
        self.try_install('_buf_device', lambda: self.ret(struct.unpack('<I', bytes(self.uc.mem_read(self.x(0) + self.BUF_DEV, 4)))[0]))
        self.try_install('_buf_flags', lambda: self.ret(struct.unpack('<I', bytes(self.uc.mem_read(self.x(0) + self.BUF_FLAGS, 4)))[0]))
        self.try_install('_buf_lblkno', lambda: self.ret(struct.unpack('<q', bytes(self.uc.mem_read(self.x(0) + self.BUF_LBLKNO, 8)))[0]))
        self.try_install('_buf_vnode', lambda: self.ret(struct.unpack('<Q', bytes(self.uc.mem_read(self.x(0) + self.BUF_VP, 8)))[0]))
        self.try_install('_buf_map_range', lambda: (self.qword(self.x(1), struct.unpack('<Q', bytes(self.uc.mem_read(self.x(0) + self.BUF_DATAP, 8)))[0]), self.ret(0))[-1])
        for noop in ('_buf_bawrite', '_buf_bwrite', '_buf_flushdirtyblks', '_buf_invalidateblks',
                     '_buf_markinvalid', '_buf_markstatic', '_buf_reset', '_buf_setblkno',
                     '_buf_setfilter', '_buf_setfsprivate', '_buf_setlblkno', '_buf_setresid',
                     '_buf_unmap_range', '_buf_strategy'):
            self.try_install(noop, lambda: self.ret(0))
        self.try_install('_buf_seterror', lambda: (self.put(self.x(0) + self.BUF_ERROR, struct.pack('<i', self.x(1))), self.ret(0))[-1])
        # bufattr setters only flip bits in b_attr; the RAM windows carry no
        # content-protection state, so accepting them is faithful.
        self.try_install('_bufattr_setcpoff', lambda: self.ret(0))
        self.try_install('_bufattr_setcpx', lambda: self.ret(0))
        self.try_install('_bufattr_alloc', lambda: self.ret(self.alloc(0x40)))
        self.try_install('_bufattr_free', lambda: self.ret(0))
        for mark in ('_bufattr_markexpeditedmeta', '_bufattr_markgreedymode',
                     '_bufattr_markioscheduled', '_bufattr_markisochronous',
                     '_bufattr_markmeta', '_bufattr_rawencrypted', '_bufattr_willverify'):
            self.try_install(mark, lambda: self.ret(0))

    def do_vfs_statfs(self):
        # XNU vfs_statfs(mp) returns &mp->mnt_vfsstat; the APFS wrapper reads
        # f_mntfromname at returned+0x460 (see parser call at 0x56d1988).
        self.ret(self.mount_ptr)

    def do_vfs_iterate(self):
        # No other mounted apfs filesystems; callback never fires.
        self.ret(0)

    def do_specrdev(self):
        vnode = self.x(0)
        entry = self.objects.get(vnode)
        self.ret(entry['fields'].get('dev', 0) if entry else 0)

    def do_vnode_open(self):
        path = self.string(self.x(0)).decode()
        error, vnode = 0, 0
        resolved = self.resolve_device(path)
        if resolved:
            dev, _ = self.devices[resolved]
            vnode = self.obj('vnode:' + path, {0x28: self.release_slot()},
                             {'dev': dev, 'path': path, 'resolved': resolved})
        else:
            error, self.failure = 2, dict(vnode_open_missing=path)
        self.opened.append(dict(path=path, error=error, pointer=hex(self.x(0))))
        self.put(self.x(4), struct.pack('<Q', vnode))
        self.ret(error)

    def do_vnop_ioctl(self):
        vnode, command = self.x(0), self.x(1) & 0xffffffff
        entry = self.objects.get(vnode)
        result, output = 6, b''  # ENXIO for an invalid device
        if entry and 'path' in entry['fields']:
            path = entry['fields']['resolved']
            _, media = self.devices[path]
            data = self.x(2)
            payload = bytes(self.uc.mem_read(data, 4)) if command == 0x80046418 else b''
            result, output = media.ioctl(command, payload)
            if output:
                self.put(data, output)
            self.ioctls.append(dict(path=path, command=hex(command), result=result,
                                    output=output.hex(), output_bytes=len(output)))
        self.ret(result)

    def do_dict_withcapacity(self):
        address = self.obj('OSDictionary', {
            0x28: self.release_slot(),
            0x100: self.method('setObject', lambda emu: emu.ret(1)),
        }, {'entries': {}})
        self.ret(address)

    def do_number_withnumber(self):
        address = self.obj('OSNumber', {0x28: self.release_slot()}, {'value': self.x(0)})
        self.ret(address)

    def do_safemetacast(self):
        obj, meta = self.x(0), self.x(1)
        self.ret(obj if obj in self.objects and meta in self.wanted_classes else 0)

    def do_metacast(self):
        obj, name = self.x(0), self.string(self.x(1)).decode()
        self.ret(obj if self.kind(obj) == name else 0)

    def do_service_matching(self):
        name = self.string(self.x(0)).decode()
        address = self.obj('OSDictionary', {
            0x28: self.release_slot(),
            0x100: self.method('setObject', lambda emu: emu.ret(1)),
            # the ctor merges the BSD major/minor dictionary into the matching
            # dict through vtable slot 0xf0 before copyMatchingService
            0xf0: self.method('merge', lambda emu: emu.ret(1)),
        }, {'match': name})
        self.ret(address)

    def do_copy_matching(self):
        entry = self.objects.get(self.x(0)) or {}
        name = entry.get('fields', {}).get('match', '')
        media = self.media_for(name)
        self.log_tail.append(f'copyMatchingService(class={name}) -> {"media" if media else "NULL"}')
        self.ret(media or 0)

    # the meta classes APFS compares against live in its own __DATA; treat the
    # pointer values the code passes as opaque identities and answer by object kind.
    wanted_classes = set()

    # -- entry ---------------------------------------------------------------
    def trace_range(self, lo, hi):
        ranges = self.range_trace_ranges
        def hook(uc, address, size, data):
            for lo2, hi2 in ranges:
                if lo2 <= address < hi2:
                    self.range_trace.append(address)
                    return
        self.uc.hook_add(UC_HOOK_CODE, hook)
        self.range_trace = []
    range_trace_ranges = ()

    def watch_writes(self, address, size=64):
        from unicorn import UC_HOOK_MEM_WRITE
        from unicorn import arm64_const as arm
        def hook(uc, access, at, value, sz, data):
            if address <= at < address + size:
                self.write_watch.append((hex(at), hex(value),
                                         hex(uc.reg_read(arm.UC_ARM64_REG_PC))))
        self.uc.hook_add(UC_HOOK_MEM_WRITE, hook)
        self.write_watch = []

    def resolve_device(self, path):
        # Hypothetical comparison only: the delivered XNU publishes no mdXsY
        # aliases. Resolving one here does not establish a real devfs contract.
        if path in self.devices:
            return path
        stripped = re.sub(r's\d+$', '', path)
        return stripped if stripped in self.devices else None

    def mount(self, device, args):
        self.wanted_classes = {a for a in self.objects}
        block = self.alloc(0x200)
        payload = struct.pack('<IIH', self.mnt_flags, 0, 1) + bytes(0x144 - 10)
        payload = payload[:0x13c] + struct.pack('<II', 99, 99)
        self.put(block, payload[:0x144])
        # mount_common records the pre-lookup device path in f_mntfromname
        # (mp+0x460) before VFS_MOUNT; the APFS wrapper parses that field,
        # never the mount args themselves.
        self.put(self.mount_ptr + 0x460, device.encode() + b'\0')
        resolved = self.resolve_device(device)
        assert resolved, 'unknown device ' + device
        dev, _ = self.devices[resolved]
        devvp = self.obj('vnode:' + device, {0x28: self.release_slot()},
                         {'dev': dev, 'path': device, 'resolved': resolved})
        result = self.run(MOUNT, [self.mount_ptr, devvp, block, self.ctx_ptr])
        return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel', type=Path, required=True)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--image', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--device', default='/dev/md1')
    p.add_argument('--trace', default=None, help='lo:hi hex range to record PCs for')
    p.add_argument('--watch', default=None, help='hex address to watch writes to')
    p.add_argument('--with-media', action='store_true',
                   help='model a registered IOMedia service for the container device')
    p.add_argument('--container-header', type=lambda s: int(s, 0), default=CONTAINER_HEADER,
                   help='deliberate corrupting bias for negative controls; default 0 preserves obj_phys_t')
    p.add_argument('--verbose-report', action='store_true', help='also print the complete JSON report')
    a = p.parse_args()
    if a.container_header not in (0, 0x20):
        p.error('--container-header must be 0 or 0x20 (corrupt negative control)')
    def media_for(name):
        if not a.with_media or name != 'IOMedia':
            return None
        address = emu.obj('IOMedia', {0x28: emu.release_slot(), 0x88: emu.method('getProvider', lambda e: e.ret(0))})
        return address
    emu = MountEmulator(a.kernel.resolve(), a.base.resolve(), a.image.resolve(), media_for,
                        container_header=a.container_header)
    if a.watch:
        emu.watch_writes(int(a.watch, 16))
    if a.trace:
        emu.range_trace_ranges = [tuple(int(part, 16) for part in item.split(':'))
                                  for item in a.trace.split(',')]
        emu.trace_range(*emu.range_trace_ranges[0])
    result = emu.mount(a.device, None)
    report = dict(
        reads=getattr(emu, 'reads', []),
        container_header=a.container_header,
        validator_dumps=emu.validator_dumps,
        range_trace=[hex(pc) for pc in getattr(emu, 'range_trace', [])[-4000:]],
        kernel_sha256=hashlib.sha256(emu.data).hexdigest(),
        original_apfs_text_sha256=APFS_TEXT_SHA,
        device=a.device,
        with_media=a.with_media,
        hypothetical_volume_alias=bool(re.search(r's\d+$', a.device)),
        assumptions=['single-threaded approximate lock/allocator/IOKit models',
                     'hw.ncpu=8, hw.memsize=8 GiB, desiredvnodes=1536',
                     'buffer cache modeled as immediately valid read-only data'],
        completed=result['failure'] is None,
        return_value=(result['return_value'] & 0xffffffff) if result['failure'] is None else None,
        stopped_x0=hex(result['return_value']),
        analyzer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        contract_sha256=hashlib.sha256((HERE / 'analyze-apfs-ram-contract.py').read_bytes()).hexdigest(),
        failure=result['failure'],
        opened=emu.opened,
        ioctls=emu.ioctls,
        media_queries=emu.log_tail,
        log_tail=[line[:200] for line in emu.log_tail],
        events=[dict(e) for e in result['events']],
        recent=result['recent'][-24:],
        write_watch=getattr(emu, 'write_watch', []),
        scope='Original apfs_vfsop_mount executed with modeled kernel imports and real window bytes. No phone.',
    )
    source_root = a.kernel.resolve().parent.parent / 'tree/.cache/xnu-work'
    report['xnu_source_sha256'] = {
        name: hashlib.sha256((source_root / name).read_bytes()).hexdigest()
        for name in ('bsd/dev/memdev.c', 'bsd/sys/disk.h', 'bsd/vfs/vfs_bio.c')
        if (source_root / name).exists()
    }
    a.report.parent.mkdir(parents=True, exist_ok=True)
    pending = a.report.with_suffix(a.report.suffix + '.tmp')
    pending.write_text(json.dumps(report, indent=2) + '\n')
    pending.replace(a.report)
    print(json.dumps(report if a.verbose_report else {
        key: report[key] for key in ('device', 'container_header', 'completed', 'return_value', 'failure')
    }, indent=2))
    return 0 if report['completed'] else 2


if __name__ == '__main__':
    sys.exit(main())
