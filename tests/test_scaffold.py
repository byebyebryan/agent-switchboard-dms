import hashlib
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "plugin.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "snapshot-v1.json"
FIXTURE_DIGEST = "fd3146e6f62eff8fe607227a7b22453f3ffbdcc1de28754da23ecc8c72dd10cb"


class ManifestContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_required_manifest_contract(self):
        expected = {
            "$schema": "https://danklinux.com/schemas/plugin.json",
            "id": "switchboard",
            "name": "Switchboard",
            "description": "Agent Switchboard launcher integration for DMS.",
            "version": "0.1.0",
            "author": "Bryan Bai",
            "type": "launcher",
            "component": "./SwitchboardLauncher.qml",
            "settings": "./SwitchboardSettings.qml",
            "trigger": "sb:",
            "requires_dms": ">=1.5.0",
        }
        for key, value in expected.items():
            with self.subTest(key=key):
                self.assertEqual(self.manifest.get(key), value)

        self.assertTrue(self.manifest.get("capabilities"))
        self.assertIn("launcher", self.manifest["capabilities"])
        self.assertEqual(
            set(self.manifest.get("permissions", [])),
            {"settings_read", "settings_write", "process"},
        )

    def test_referenced_qml_files_exist(self):
        for key in ("component", "settings"):
            with self.subTest(key=key):
                self.assertTrue((ROOT / self.manifest[key]).is_file())


class QmlScaffoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = (ROOT / "SwitchboardLauncher.qml").read_text(encoding="utf-8")
        cls.settings = (ROOT / "SwitchboardSettings.qml").read_text(encoding="utf-8")

    def test_launcher_surface_is_synchronous_and_inert(self):
        self.assertRegex(self.launcher, r"property\s+var\s+pluginService\s*:\s*null")
        self.assertRegex(self.launcher, r'property\s+string\s+trigger\s*:\s*"sb:"')
        self.assertRegex(self.launcher, r"signal\s+itemsChanged\s*\(\s*\)")
        self.assertRegex(
            self.launcher,
            r"function\s+getItems\s*\(\s*query\s*\)\s*\{\s*return\s*\[\s*\]",
        )
        self.assertRegex(self.launcher, r"function\s+executeItem\s*\(\s*item\s*\)")
        self.assertNotRegex(self.launcher, r"\basync\s+function\s+getItems\b")

    def test_settings_surface_is_inert_visual_focus_root(self):
        self.assertRegex(self.settings, r"\Aimport\s+QtQuick\s+FocusScope\s*\{")
        self.assertNotRegex(self.settings, r"\bQtObject\s*\{")
        self.assertRegex(self.settings, r"property\s+var\s+pluginService\s*:\s*null")
        self.assertRegex(self.settings, r"implicitHeight\s*:\s*0\b")

    def test_qml_contains_no_process_or_shell_behavior(self):
        qml = self.launcher + "\n" + self.settings
        forbidden = (
            "swbctl",
            "Process",
            "QProcess",
            "child_process",
            "exec(",
            "spawn(",
            "/bin/sh",
            "/home/",
        )
        for marker in forbidden:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, qml)


class DocumentationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = "\n".join(
            (ROOT / relative).read_text(encoding="utf-8")
            for relative in ("docs/architecture.md", "docs/implementation-plan.md")
        )

    def test_public_command_boundary_is_documented(self):
        commands = (
            "swbctl snapshot --json",
            "swbctl snapshot --reconcile full --json",
            "swbctl list --json",
            "swbctl list --refresh --json",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, self.docs)
        self.assertIn("Snapshot v1 JSON", self.docs)
        self.assertIn("user-configured `swbctl`", self.docs)
        self.assertIn("must not import internal Agent Switchboard", self.docs)
        self.assertIn("read its database", self.docs)

    def test_future_cache_semantics_are_documented(self):
        for phrase in (
            "asynchronous refresh",
            "last-good snapshot",
            "honest unknown states",
            "empty capabilities list as neutral",
            "Selection remains unavailable",
            "DMS does not currently consume `itemsChanged()`",
            "`requestLauncherUpdate`",
            "reopen or query change",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.docs)

    def test_non_goals_are_explicit(self):
        for non_goal in (
            "Claude",
            "SSH",
            "hooks/liveness",
            "project actions",
            "tmux creation",
            "niri",
            "Ghostty",
            "chezmoi cutover",
            "rich widget",
        ):
            with self.subTest(non_goal=non_goal):
                self.assertIn(non_goal, self.docs)


class FixtureContractTests(unittest.TestCase):
    def test_fixture_digest_and_v1_envelope(self):
        payload = FIXTURE_PATH.read_bytes()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), FIXTURE_DIGEST)
        snapshot = json.loads(payload)
        self.assertEqual(snapshot["schemaVersion"], 1)
        self.assertEqual(snapshot["protocolVersion"], 1)

    def test_fixture_provenance_is_recorded(self):
        provenance = (ROOT / "tests" / "fixtures" / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "byebyebryan/agent-switchboard",
            provenance,
        )
        self.assertIn("tests/fixtures/protocol/v1/snapshot.json", provenance)
        self.assertNotIn("/home/bryan", provenance)
        self.assertIn("synthetic test data", provenance)
        self.assertIn("not a capture of a live machine", provenance)
        self.assertIn("898fa1080712235993781c27c56d312e8e3cef9e", provenance)
        self.assertIn("b3b54b4dc1eea5a5b0bd78792fa6c7f626701a8f", provenance)
        self.assertIn(FIXTURE_DIGEST, provenance)


if __name__ == "__main__":
    unittest.main()
