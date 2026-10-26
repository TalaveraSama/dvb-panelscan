"""Validate CA selection arguments before the program can open DVB devices."""
from pathlib import Path
import subprocess
import unittest

BINARY = Path(__file__).resolve().parents[1] / "tbsstream" / "tbsstream"


@unittest.skipUnless(BINARY.is_file(), "build tbsstream before CLI validation")
class CASelectionCLI(unittest.TestCase):
    def test_invalid_or_incomplete_lock_fails_before_tuning(self):
        for arguments in (["--caid", "0B00"], ["--ecm-pid", "5169"],
                          ["--caid", "ZZZZ", "--ecm-pid", "5169"],
                          ["--caid", "0B00", "--ecm-pid", "8191"],
                          ["--caid", "0000", "--ecm-pid", "5169"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run([str(BINARY), *arguments], capture_output=True, timeout=3)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"--caid", result.stderr)
                self.assertNotIn(b"/dev/dvb", result.stderr)

    def test_invalid_delay_fails_before_tuning(self):
        for value in ("-1", "2001", "true", "500ms", ""):
            with self.subTest(value=value):
                result = subprocess.run([str(BINARY), "--descramble-delay-ms", value], capture_output=True, timeout=3)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"--descramble-delay-ms", result.stderr)
                self.assertNotIn(b"/dev/dvb", result.stderr)

    def test_invalid_protocol_fails_before_tuning(self):
        for value in ("526", "524.0", "auto", "", "-1"):
            with self.subTest(value=value):
                result = subprocess.run([str(BINARY), "--newcamd-proto", value], capture_output=True, timeout=3)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"--newcamd-proto", result.stderr)
                self.assertNotIn(b"/dev/dvb", result.stderr)


@unittest.skipUnless((BINARY.parent / "tools/ncdiag").is_file(), "build local diagnostic before testing")
class LocalDiagnosticCLI(unittest.TestCase):
    def test_synthetic_selftest_and_reject_network_or_credentials(self):
        import json
        tool = str(BINARY.parent / "tools/ncdiag")
        result = subprocess.run([tool, "--selftest", "--json"], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"ok": True, "csa": True, "network_probe": False})
        rejected = subprocess.run([tool, "--host", "SECRET_SENTINEL"], capture_output=True, timeout=5)
        self.assertEqual(rejected.returncode, 2)
        self.assertNotIn(b"SECRET_SENTINEL", rejected.stdout + rejected.stderr)
