"""Host-only tests: no case is allowed to issue a phone command."""
import hashlib
import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


RUNNER = Path(__file__).resolve().parents[3] / 'scripts/boot-ram-window-once.py'


class UsbSystemBootGate(unittest.TestCase):
    def test_bound_kmem_evidence_required_before_device_query(self):
        main = runpy.run_path(str(RUNNER), run_name='boot_fixture')['main']
        for scenario in ('valid', 'missing', 'stale-image', 'stale-kernel', 'exhausted'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                image = root / 'boot.img'
                image.write_bytes(b'host-only fixture')
                sha = hashlib.sha256(image.read_bytes()).hexdigest()
                report = dict(boot_sha256=sha, compressed_firmware_roundtrip=True,
                              system_handoff=True, kernel_handoff=True, receiver_only=False,
                              delivered_modules={'FuxiUsbRamDxe': 'x', 'UsbfnDwc3Dxe': 'y'})
                payload = dict(boot_sha256=sha, kernel_sha256='kernel', payload_tag='FUXIPAYL')
                kmem = dict(boot_sha256=sha, kernel_sha256='kernel', remaining_va_lower_bound=512 << 20)
                if scenario == 'stale-image':
                    kmem['boot_sha256'] = 'old'
                if scenario == 'stale-kernel':
                    kmem['kernel_sha256'] = 'old'
                if scenario == 'exhausted':
                    kmem['remaining_va_lower_bound'] -= 4096
                for name, data in [('verification.json', report), ('payload-verification.json', payload)]:
                    (root / name).write_text(json.dumps(data))
                if scenario != 'missing':
                    (root / 'kmem-tests.json').write_text(json.dumps(kmem))
                argv = [str(RUNNER), '--image', str(image), '--expected-sha256', sha,
                        '--profile', 'usb-system']
                with patch.object(sys, 'argv', argv), patch('subprocess.run') as command:
                    command.side_effect = RuntimeError('device query blocked by test')
                    if scenario == 'valid':
                        with self.assertRaisesRegex(RuntimeError, 'blocked by test'):
                            main()
                        self.assertEqual(command.call_args.args[0], ['/opt/homebrew/bin/fastboot', 'devices'])
                    else:
                        with self.assertRaisesRegex(SystemExit, 'verification is absent'):
                            main()
                        command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
