import contextlib
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("ca_diagnostic", "tools/diagnose-ca.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class CADiagnosticTests(unittest.TestCase):
    def test_roles_are_fixed_not_raw_arguments(self):
        self.assertEqual(mod.role(["--ca-broker", "private"]), "ca-broker")
        self.assertEqual(mod.role(["--cam-socket", "private"]), "service-local-ca")
        self.assertEqual(mod.role(["--mux-only"]), "mux-capture")
        self.assertEqual(mod.role([]), "service-direct-ca")

    def test_proc_snapshot_groups_two_upstream_sockets_without_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            for pid in (101, 102):
                entry = proc / str(pid)
                (entry / "fd").mkdir(parents=True)
                (entry / "net").mkdir()
                os.symlink("/usr/bin/tbsstream", entry / "exe")
                os.symlink("socket:[%d]" % pid, entry / "fd" / "3")
                (entry / "status").write_text("PPid:\t100\n")
                (entry / "cmdline").write_bytes(b"tbsstream\0--ca-broker\0/private/socket\0--pass\0SECRET_SENTINEL\0")
                row = ["0:", "0100007F:1234", "04030201:4321", "01", "0:0", "0:0", "0", "1000", "0", str(pid)]
                (entry / "net" / "tcp").write_text("header\n" + " ".join(row) + "\n")
            rows, skipped = mod.snapshot(proc)
            self.assertEqual(skipped, 0)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["tcp"], [("ESTABLISHED", "peer-1")])
            self.assertEqual(rows[1]["tcp"], rows[0]["tcp"])
            output = io.StringIO()
            with patch.object(mod, "snapshot", return_value=(rows, skipped)), patch.object(mod.os, "geteuid", return_value=0), contextlib.redirect_stdout(output):
                self.assertEqual(mod.main(), 0)
            text = output.getvalue()
            self.assertIn("establecidos en motores: 2", text)
            for secret in ("SECRET_SENTINEL", "/private/socket", "04030201", "4321", "--pass"):
                self.assertNotIn(secret, text)
