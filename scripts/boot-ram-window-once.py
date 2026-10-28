#!/usr/bin/env python3
"""One hash-pinned temporary fastboot boot of a verified diagnostic candidate."""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--image', type=Path, required=True)
    p.add_argument('--expected-sha256', required=True)
    p.add_argument('--serial', default='fcde91e')
    p.add_argument('--profile', choices=['ram-window', 'usb-ram', 'usb-system'], default='ram-window')
    a = p.parse_args()
    image = a.image.resolve()
    if hashlib.sha256(image.read_bytes()).hexdigest() != a.expected_sha256:
        raise SystemExit('candidate image digest mismatch')
    report = json.loads((image.parent / 'verification.json').read_text())
    if a.profile == 'ram-window':
        verified = (report.get('fat_roundtrip') and report.get('delivered_loader_matches_build') and
                    report.get('baseline_kernel_ramdisk_trust_identical'))
    elif a.profile == 'usb-system':
        # A handoff boot additionally requires this exact image's payload/ADT/loader
        # checks, and that its ramdisk carries the FUXIPAYL tag: without the tag the
        # firmware never publishes the payload volume and the boot stops in the menu.
        payload_path = image.parent / 'payload-verification.json'
        payload = json.loads(payload_path.read_text()) if payload_path.is_file() else {}
        kmem_path = image.parent / 'kmem-tests.json'
        kmem = json.loads(kmem_path.read_text()) if kmem_path.is_file() else {}
        spi_path = image.parent / 'mount-spi-tests.json'
        spi = json.loads(spi_path.read_text()) if spi_path.is_file() else {}
        verified = (report.get('compressed_firmware_roundtrip') and
                    report.get('system_handoff') is True and report.get('kernel_handoff') is True and
                    report.get('receiver_only') is False and
                    set(report.get('delivered_modules', {})) == {'FuxiUsbRamDxe', 'UsbfnDwc3Dxe'} and
                    payload.get('boot_sha256') == a.expected_sha256 and
                    payload.get('payload_tag') == 'FUXIPAYL' and
                    kmem.get('boot_sha256') == a.expected_sha256 and
                    kmem.get('kernel_sha256') == payload.get('kernel_sha256') and
                    kmem.get('remaining_va_lower_bound', 0) >= 512 * 2**20 and
                    spi.get('boot_sha256') == a.expected_sha256 and
                    spi.get('kernel_sha256') == payload.get('kernel_sha256'))
        if payload.get('ram_volume_nodes'):
            nodes_path = image.parent / 'volume-nodes-tests.json'
            nodes = json.loads(nodes_path.read_text()) if nodes_path.is_file() else {}
            verified = (verified and nodes.get('read_only_links_verified') is True and
                        bool(nodes.get('passed')) and
                        nodes.get('boot_sha256') == a.expected_sha256 and
                        nodes.get('kernel_sha256') == payload.get('kernel_sha256'))
        if payload.get('ram_iokit_storage'):
            storage_path = image.parent / 'iokit-storage-tests.json'
            storage = json.loads(storage_path.read_text()) if storage_path.is_file() else {}
            verified = (verified and storage.get('read_only_transport_verified') is True and
                        storage.get('ownership_selection_verified') is True and bool(storage.get('passed')) and
                        storage.get('virtual_methods_checked') == 190 and
                        storage.get('boot_sha256') == a.expected_sha256 and
                        storage.get('kernel_sha256') == payload.get('kernel_sha256'))
    else:
        candidate_path = image.parent / 'candidate.json'
        candidate = json.loads(candidate_path.read_text()) if candidate_path.is_file() else {}
        if candidate.get('lun4_keymaster') is True:
            # The parked DXE diagnostic is not a USB receiver-only image.
            qualification = json.loads((image.parent / 'qualification.json').read_text())
            checks = json.loads((image.parent / 'check-exit-codes.json').read_text())
            ufs = json.loads((image.parent / 'report-image-tests.json').read_text())
            binding = json.loads((image.parent / 'committed-source-bindings.json').read_text())
            repo = Path('/home/mouse/work/UniXNU/ramdisk-test/uefi-twrp-data')
            sources = json.loads((image.parent / 'sources.json').read_text())
            commit = binding.get('commit')
            large = candidate.get('m3_bridge_large') is True
            bridge_allowed = (candidate.get('m3_scm_info') is True and candidate.get('m3_scm_bridge') is True and
                candidate.get('m3_bridge_call') is True and candidate.get('m3_bridge_hlos') is True and
                candidate.get('m3_bridge_bytes') == 0x82000 and candidate.get('m3_bridge_blob') is False and
                candidate.get('m3_smci_root') is False and candidate.get('m3_scm_invoke') is False and
                candidate.get('m3_bridge_ops') == ['enable','create','delete']) if large else (
                candidate.get('m3_scm_info') is False and candidate.get('m3_bridge_call') is False)
            if large and candidate.get('m3_smci_root') is True:
                bridge_allowed = (a.expected_sha256 == 'b0de5540f72692556694d77febca7f92439e14fdfc3c7159e414c6e55fdc704b' and
                    candidate.get('m3_scm_info') is True and candidate.get('m3_scm_bridge') is True and
                    candidate.get('m3_scm_invoke') is True and candidate.get('m3_bridge_call') is True and
                    candidate.get('m3_bridge_hlos') is True and candidate.get('m3_bridge_bytes') == 0x82000 and
                    candidate.get('m3_bridge_blob') is False and candidate.get('m3_smci_loader') is True and
                    candidate.get('m3_smci_lookup') is False and candidate.get('m3_smci_mode') == 'object' and
                    candidate.get('m3_smci_no_keys') is True and
                    candidate.get('m3_smci_ops') == ['root-register-null', 'open-loader-uid122', 'loader-release', 'environment-release'])
                expected_checks = 18
            else:
                expected_checks = 15 if large else 12
            expected_smc = {'FuxiNativeUfsDxe.efi': 1 if large else 0,
                            'modules-no-smc/FuxiNativeUfsDxe.efi': 0,
                            'modules-scm-query/FuxiNativeUfsDxe.efi': 0}
            verified = (a.serial == 'fcde91e' and report.get('receiver_only') is False and
                report.get('compressed_firmware_roundtrip') is True and
                set(report.get('delivered_modules', {})) == {'FuxiUsbRamDxe', 'UsbfnDwc3Dxe'} and
                report.get('kernel_handoff') is False and report.get('system_handoff') is False and
                candidate.get('sha256') == a.expected_sha256 and candidate.get('diagnostic_only') is True and
                candidate.get('m2_metadata_files') is True and candidate.get('diagnostic_park_before_usb') is True and
                candidate.get('partition_writes') is False and bridge_allowed and
                qualification.get('result') == 'passed' and qualification.get('checks') == expected_checks and
                len(checks) == expected_checks and all(x.get('exit_code') == 0 for x in checks) and
                ufs.get('result') == 'passed' and ufs.get('boot_sha256') == a.expected_sha256 and
                ufs.get('smc_instructions') == expected_smc and
                binding.get('result') == 'passed' and binding.get('image_sha256') == a.expected_sha256 and
                binding.get('sources_checked') == 56 and len(sources) == 56 and
                isinstance(commit, str) and len(commit) == 40)
            if verified:
                for name, digest in sources.items():
                    blob = subprocess.run(['git', '-C', str(repo), 'show', commit + ':' + name], capture_output=True)
                    if (blob.returncode or hashlib.sha256(blob.stdout).hexdigest() != digest or
                            hashlib.sha256((repo / name).read_bytes()).hexdigest() != digest):
                        raise SystemExit('source binding mismatch: ' + name)
                processes = subprocess.check_output(['ps', '-eo', 'pid=,args='], text=True)
                if any(name in line for line in processes.splitlines() for name in
                       ('test-usb-ram-full', 'usb-ram-send', 'boot-ram-window')):
                    # Ignore this script itself, but never another device process.
                    lines = [line for line in processes.splitlines() if any(name in line for name in
                             ('test-usb-ram-full', 'usb-ram-send', 'boot-ram-window')) and
                             line.split(None, 1)[0] not in (str(os.getpid()), str(os.getppid()))]
                    if lines: raise SystemExit('another device task is active')
        else:
            verified = (report.get('receiver_only') and report.get('compressed_firmware_roundtrip') and
                    set(report.get('delivered_modules', {})) == {'FuxiUsbRamDxe', 'UsbfnDwc3Dxe'} and
                    report.get('kernel_handoff') is False)
    if report.get('boot_sha256') != a.expected_sha256 or not verified:
        raise SystemExit('candidate verification is absent or belongs to a different image')
    fastboot = shutil.which('fastboot') or shutil.which('fastboot.exe') or '/opt/homebrew/bin/fastboot'
    if not Path(fastboot).is_file() and Path('/mnt/c/Users/zxc92/AppData/Local/Android/Sdk/platform-tools/fastboot.exe').is_file():
        fastboot = '/mnt/c/Users/zxc92/AppData/Local/Android/Sdk/platform-tools/fastboot.exe'
    if not Path(fastboot).is_file():
        raise SystemExit('fastboot not found')
    record = dict(checked_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  boot_sha256=a.expected_sha256, flash_performed=False, queries=[],
                  owned_memory_bytes=report.get('owned_memory_bytes'), profile=a.profile, hardware_result='pending')
    output = image.parent / 'device-test.json'
    if output.exists():
        raise SystemExit('attempt already recorded; no automatic retry')

    def save():
        output.write_text(json.dumps(record, indent=2) + '\n')

    def query(*args, optional=False):
        command = [fastboot, *args]
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        text = result.stdout + result.stderr
        record['queries'].append(dict(command=command, returncode=result.returncode, output=text))
        save()
        if result.returncode and not optional:
            raise SystemExit(text)
        return text

    devices = query('devices')
    if not any(line.split() == [a.serial, 'fastboot'] for line in devices.splitlines()):
        raise SystemExit('identified device is not in fastboot')
    if 'product: fuxi' not in query('-s', a.serial, 'getvar', 'product'):
        raise SystemExit('unexpected device product')
    if 'unlocked: yes' not in query('-s', a.serial, 'getvar', 'unlocked'):
        raise SystemExit('device is locked')
    query('-s', a.serial, 'getvar', 'current-slot')
    query('-s', a.serial, 'getvar', 'max-download-size', optional=True)
    image_arg = str(image)
    if fastboot.lower().endswith('.exe'):
        image_arg = subprocess.check_output(['wslpath', '-w', str(image)], text=True).strip()
    command = [fastboot, '-s', a.serial, 'boot', image_arg]
    record.update(command=command, status='temporary boot in progress')
    save()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as error:
        record.update(status='boot command timed out; device result unknown')
        save()
        raise SystemExit(str(error))
    text = result.stdout + result.stderr
    print(text)
    record.update(returncode=result.returncode, fastboot_output=text,
                  status='awaiting screen evidence' if result.returncode == 0 else
                         'boot command failed; device result unknown',
                  finished_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    save()
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
