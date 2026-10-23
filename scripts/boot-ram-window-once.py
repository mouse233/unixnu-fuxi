#!/usr/bin/env python3
"""One hash-pinned temporary fastboot boot of a verified diagnostic candidate."""
import argparse
import datetime
import hashlib
import json
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
        verified = (report.get('compressed_firmware_roundtrip') and
                    report.get('system_handoff') is True and report.get('kernel_handoff') is True and
                    report.get('receiver_only') is False and
                    set(report.get('delivered_modules', {})) == {'FuxiUsbRamDxe', 'UsbfnDwc3Dxe'} and
                    payload.get('boot_sha256') == a.expected_sha256 and
                    payload.get('payload_tag') == 'FUXIPAYL' and
                    kmem.get('boot_sha256') == a.expected_sha256 and
                    kmem.get('kernel_sha256') == payload.get('kernel_sha256') and
                    kmem.get('remaining_va_lower_bound', 0) >= 512 * 2**20)
    else:
        verified = (report.get('receiver_only') and report.get('compressed_firmware_roundtrip') and
                    set(report.get('delivered_modules', {})) == {'FuxiUsbRamDxe', 'UsbfnDwc3Dxe'} and
                    report.get('kernel_handoff') is False)
    if report.get('boot_sha256') != a.expected_sha256 or not verified:
        raise SystemExit('candidate verification is absent or belongs to a different image')
    fastboot = '/opt/homebrew/bin/fastboot'
    if not Path(fastboot).is_file():
        raise SystemExit('fastboot not found')
    record = dict(checked_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  boot_sha256=a.expected_sha256, flash_performed=False, queries=[],
                  owned_memory_bytes=report.get('owned_memory_bytes'), profile=a.profile, hardware_result='pending')
    output = image.parent / 'device-test.json'

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
    command = [fastboot, '-s', a.serial, 'boot', str(image)]
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
