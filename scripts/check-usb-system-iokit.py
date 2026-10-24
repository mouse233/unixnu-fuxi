#!/usr/bin/env python3
"""Exercise the actual RAM-only transport with host IOKit dependencies.

Also bind its compiled 190-slot ABI, sources and archive to the delivered kernel.
No host test substitutes for a phone photo of the original storage/APFS stack.
"""
import argparse
import hashlib
import json
import runpy
import subprocess
import tempfile
from pathlib import Path

sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--base', type=Path, required=True)
    a = p.parse_args()
    out, base = a.output.resolve(), a.base.resolve()
    driver = base / 'drivers/NativeRamSystemBlock'
    with tempfile.TemporaryDirectory(prefix='ram-system-iokit-') as temp:
        scratch = Path(temp)
        for relative in ('IOKit/IOLib.h','IOKit/IOBSD.h','IOKit/storage/IOBlockStorageDevice.h',
                         'libkern/c++/OSIterator.h','libkern/c++/OSNumber.h','libkern/c++/OSString.h',
                         'machine/machine_routines.h','pexpert/pexpert.h'):
            path = scratch / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('#include "IOKitShim.h"\n')
        command = ['clang++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                   '-I',str(scratch),'-I',str(driver/'Test'),str(driver/'Test/transport.cpp'),'-o',str(scratch/'test')]
        subprocess.run(command,check=True)
        result = subprocess.check_output([str(scratch/'test')],text=True).strip()
    print(result)
    tree = out / 'work/tree'
    directory = tree / 'build/native-ram-system-block'
    bindings = json.loads((directory/'bindings.json').read_text())
    assert bindings['virtual_methods_checked']==190 and bindings['unresolved_original_imports']==0
    assert bindings['compiled_object_sha256']==sha(directory/'NativeRamSystemBlock.o')
    assert bindings['original_code_modified'] is False
    command = json.loads((directory/'compile-command.json').read_text())
    assert str(tree/'drivers/NativeRamSystemBlock/NativeRamSystemBlock.cpp') in command
    assert '-Werror' in command
    sources = json.loads((directory/'source-bindings.json').read_text())
    assert sources['object_sha256']==sha(directory/'NativeRamSystemBlock.o')
    for name,digest in sources['source_sha256'].items(): assert sha(Path(name))==digest
    source = (driver/'NativeRamSystemBlock.cpp').read_text()
    assert 'native_ufs' not in source and 'PersistOverlay' not in source
    assert '*v=true; return kIOReturnSuccess;' in source
    probe = (tree/'kits/sheng/RamSystemProbe.c').read_text()
    assert probe.index('memcmp(digest,UrDigest(id),32)') < probe.index('fuxi_ram_system_publish(id,')
    root = tree / '.cache/xnu-work'
    vfs = (root/'bsd/vfs/vfs_syscalls.c').read_text()
    assert 'fuxi_ram_system_volume(id, volume_device, sizeof(volume_device))' in vfs
    assert 'MNT_RDONLY | MNT_DONTBROWSE, KERNEL_MOUNT_RAM_SYSTEM' in vfs
    spi = runpy.run_path(str(Path(__file__).with_name('check-usb-system-mount-spi.py')),run_name='spi')
    spi['check_mount_patch'](root,base,base/'patches/xnu/zzz-ram-system-iokit.patch')
    config = (out/'config').read_text()
    for line in ('CONFIG_XNU_NATIVE_RAM_SYSTEM_BLOCK=y','CONFIG_XNU_NATIVE_RAM_BLOCK=n','CONFIG_XNU_NATIVE_UFS=n'):
        assert line in config
    kernel = out/'work/kernel/kernel.macho'
    data = kernel.read_bytes()
    for literal in (b'NativeRamSystemBlock\0',b'RAM_SYSTEM IOKit volume timeout',b'Verified RAM SystemObjects\0'):
        assert literal in data
    objdir = tree/'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837'
    # The link invalidation stamp includes every enabled archive's exact digest.
    stamp = (objdir/'.native-aes-link-input').read_text()
    assert '1:'+sha(directory/'libNativeRamSystemBlock.a') in stamp
    payload = json.loads((out/'payload-verification.json').read_text())
    assert sha(kernel)==payload['kernel_sha256'] and payload['ram_iokit_storage'] is True
    plan = json.loads(kernel.with_suffix('.json').read_text())
    audit = json.loads((tree/'build/storage-abi/interface-audit.json').read_text())
    parent_plan = json.loads(Path(audit['candidate_path']).with_suffix('.json').read_text())
    for module in ('com.apple.iokit.IOStorageFamily','com.apple.filesystems.apfs'):
        assert plan['original_executable_sha256'][module]==parent_plan['original_executable_sha256'][module]
    sources = [driver/'NativeRamSystemBlock.cpp',driver/'ReadOnlyWindow.h',driver/'Test/transport.cpp',
               driver/'Test/IOKitShim.h',tree/'kits/sheng/RamSystemProbe.c',
               base/'patches/xnu/zzz-ram-system-iokit.patch']
    report = dict(passed=[result],read_only_transport_verified=True,ownership_selection_verified=True,
                  sanitizers=['address','undefined'],source_sha256={str(x):sha(x) for x in sources},
                  archive_sha256=sha(directory/'libNativeRamSystemBlock.a'),bindings_sha256=sha(directory/'bindings.json'),
                  virtual_methods_checked=190,original_apfs_storage_code_unchanged=True,
                  boot_sha256=payload['boot_sha256'],kernel_sha256=payload['kernel_sha256'],hardware_result='pending')
    (out/'iokit-storage-tests.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__': main()
