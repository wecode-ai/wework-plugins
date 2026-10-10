"""Run from the extracted release ZIP to exercise the shipped native entry."""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class PackagedPluginTests(unittest.TestCase):
    def test_all_platforms_and_real_native_channel(self):
        spec = importlib.util.spec_from_file_location(
            "dws_packaged_verify", ROOT / ".wework-build/dws-auth/verify.py"
        )
        verify = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verify)
        verify.verify_artifacts(
            ROOT,
            (
                "darwin/arm64",
                "darwin/amd64",
                "linux/amd64",
                "linux/arm64",
                "windows/amd64",
            ),
        )
        verify.verify_native_entry(ROOT)

    def test_cloud_authentication_is_declared(self):
        manifest = json.loads(
            (ROOT / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
        )
        connector = next(c for c in manifest["connectors"] if c["slug"] == "dingtalk-access")
        self.assertEqual(connector["accountAuth"]["credentialType"], "bearer")
        self.assertNotIn("exportMode", connector["accountAuth"])
        self.assertNotIn("oauth2", connector["accountAuth"])
        local = next(c for c in manifest["connectors"] if c["slug"] == "dingtalk-local")
        self.assertIn("localAuth", local)
        self.assertNotIn("accountAuth", local)
        self.assertEqual(connector["accountAuth"]["adapter"], "scripts/account-auth.py")


if __name__ == "__main__":
    unittest.main()
