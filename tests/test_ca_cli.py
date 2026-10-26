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
