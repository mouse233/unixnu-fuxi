#!/usr/bin/env python3
"""Check stage mutations, original LC_MAIN, and the compiled system-init path."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from ram_probe_policy import inspect_compiled_init, inspect_system_probe, validate_stage_policy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--launchd', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, help='v33 candidate with the missing ROOT_PROBE compiler define')
    args = parser.parse_args()
    out = args.output.resolve()
    pins = json.loads((out / 'pins.json').read_text())
    cases = []
    for user, main_entry, progress in [(False, False, False), (True, False, False),
                                        (True, True, False), (True, True, True)]:
        sample = dict(pins, start_userland_allowed=user, stop_before_first_user_svc=user and not main_entry,
                      stop_at_launchd_main=main_entry and not progress, continue_after_main=progress,
                      follow_process_creation=progress, block_process_creation=not progress,
                      follow_ptrace_deny_attach=progress)
        stage = validate_stage_policy(sample, user, main_entry, progress)
        cases.append('valid ' + stage)
        if not progress and pins.get('follow_ptrace_deny_attach', False):
            try:
                validate_stage_policy(dict(sample, follow_ptrace_deny_attach=True), user, main_entry, progress)
            except ValueError:
                cases.append(stage + ': rejected follow_ptrace_deny_attach')
            else:
                raise AssertionError('accepted deny-attach allowance outside progress mode')
        for key in ('start_userland_allowed', 'stop_before_first_user_svc', 'stop_at_launchd_main',
                    'continue_after_main', 'follow_process_creation', 'block_process_creation', 'physical_storage_allowed'):
            bad = dict(sample)
            bad[key] = not bad[key]
            try:
                validate_stage_policy(bad, user, main_entry, progress)
            except ValueError:
                cases.append(stage + ': rejected ' + key)
            else:
                raise AssertionError('accepted contradictory ' + stage + ': ' + key)
    image = args.launchd.read_bytes()
    assert hashlib.sha256(image).hexdigest() == pins['file_sha256'] == pins['system_file_sha256']
    assert struct.unpack_from('<I', image)[0] == 0xfeedfacf
    at, entries = 32, []
    for _ in range(struct.unpack_from('<I', image, 16)[0]):
        command, size = struct.unpack_from('<II', image, at)
        assert size >= 8 and at + size <= len(image)
        if command == 0x80000028:  # LC_MAIN includes LC_REQ_DYLD.
            entries.append(struct.unpack_from('<Q', image, at + 8)[0])
        at += size
    assert entries == [pins['main_entry_offset']] == [85896]
    assert image[entries[0]:entries[0]+16].hex() == pins['main_entry_bytes']
    cases.append('original LC_MAIN=0x80000028, entryoff=85896, exact entry bytes')
    if args.baseline:
        try:
            inspect_compiled_init(args.baseline.resolve())
        except ValueError as error:
            assert 'XNU_RAM_ROOT_PROBE' in str(error), str(error)
            cases.append('v33 missing compiler define reproduced: ' + str(error))
        else:
            raise AssertionError('baseline unexpectedly contains the fixed system-init path')
    evidence = inspect_system_probe(out)
    if pins.get('follow_ptrace_deny_attach'):
        header = (out / 'work/tree/build/ram-root-probe/RamRootProbePins.h').read_text()
        assert '#define RAM_ROOT_FOLLOW_DENY_ATTACH 1' in header, 'deny-attach allowance missing from compiled pins'
        cases.append('compiled follow deny-attach allowance pinned')
    report = dict(passed=cases, compiled_probe=evidence,
                  boot_sha256=hashlib.sha256((out / 'Mu-fuxi-usb-ram.img').read_bytes()).hexdigest(),
                  hardware_result='pending')
    (out / 'probe-policy-tests.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'PASS: {len(cases)} stage/entry/baseline cases; actual init object and archive bindings verified')


if __name__ == '__main__':
    main()
