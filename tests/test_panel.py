import importlib.util
import os
import tempfile
import unittest
from unittest.mock import patch

class PanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, DVB_PANEL_DATA_DIR=self.tmp.name)
        self.env.start()
        spec = importlib.util.spec_from_file_location("panel_server", "panel/server.py")
        self.panel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.panel)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_private_atomic_configuration(self):
        self.panel._save("newcamd")
        path = os.path.join(self.tmp.name, "newcamd.json")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertFalse(os.path.exists(path + ".tmp"))
        self.assertEqual(self.panel.STATE["newcamd"]["pass"], "")

    def test_no_hardware(self):
        self.assertFalse(self.panel.stream_status()["running"])

if __name__ == "__main__":
    unittest.main()
