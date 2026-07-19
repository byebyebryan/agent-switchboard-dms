import hashlib
import json
import unittest
from pathlib import Path

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
        self.assertTrue((ROOT / "SwitchboardModel.js").is_file())
        self.assertTrue((ROOT / "switchboard-open").is_file())


class QmlScaffoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = (ROOT / "SwitchboardLauncher.qml").read_text(encoding="utf-8")
        cls.settings = (ROOT / "SwitchboardSettings.qml").read_text(encoding="utf-8")
        cls.model = (ROOT / "SwitchboardModel.js").read_text(encoding="utf-8")

    def test_launcher_surface_reads_cache_synchronously(self):
        self.assertRegex(self.launcher, r"property\s+var\s+pluginService\s*:\s*null")
        self.assertRegex(self.launcher, r'property\s+string\s+trigger\s*:\s*"sb:"')
        self.assertRegex(self.launcher, r"signal\s+itemsChanged(?:\s*\(\s*\))?")
        read_path = self.launcher.split("function getItems(query)", 1)[1].split(
            "function executeItem", 1
        )[0]
        self.assertIn("SwitchboardModel.launcherItems", read_path)
        self.assertIn("Qt.callLater(root.scheduleForRead)", read_path)
        self.assertNotIn("refreshProcess.running = true", read_path)
        self.assertNotIn("swbctlExecutable", read_path)
        self.assertRegex(self.launcher, r"function\s+executeItem\s*\(\s*item\s*\)")
        execute_path = self.launcher.split("function executeItem(item)", 1)[1].split(
            "function scheduleRun", 1
        )[0]
        self.assertIn("actionProcess.command", execute_path)
        self.assertIn("openerExecutable", execute_path)
        self.assertIn('"--window-host"', execute_path)
        self.assertIn("item._sessionKey", execute_path)
        self.assertIn("item._provider", execute_path)
        self.assertIn('"--provider"', execute_path)
        self.assertNotRegex(self.launcher, r"\basync\s+function\s+getItems\b")

    def test_settings_use_verified_dms_components(self):
        self.assertIn("PluginSettings {", self.settings)
        self.assertIn('pluginId: "switchboard"', self.settings)
        self.assertEqual(self.settings.count("DankTextField {"), 2)
        self.assertIn(
            "maximumLength: SwitchboardModel.MAX_EXECUTABLE_LENGTH", self.settings
        )
        self.assertEqual(self.settings.count("SliderSetting {"), 2)
        self.assertIn('loadValue("swbctl", "swbctl")', self.settings)
        self.assertIn('saveValue("swbctl", boundedValue)', self.settings)
        self.assertIn('loadValue("terminal", "ghostty")', self.settings)
        self.assertIn('saveValue("terminal", boundedValue)', self.settings)
        for key in ("timeout_ms", "refresh_seconds"):
            with self.subTest(key=key):
                self.assertIn(f'settingKey: "{key}"', self.settings)

    def test_process_is_async_fixed_argv_and_shell_free(self):
        self.assertIn("Process {", self.launcher)
        self.assertIn("StdioCollector {", self.launcher)
        self.assertIn("refreshProcess.command = command", self.launcher)
        self.assertIn("actionProcess.command", self.launcher)
        self.assertIn("SwitchboardModel.parseActionResponse", self.launcher)
        self.assertIn('"--swbctl"', self.launcher)
        self.assertIn('"--timeout-ms"', self.launcher)
        self.assertIn('command.push("--refresh")', self.launcher)
        self.assertIn("refreshProcess.signal(15)", self.launcher)
        self.assertIn("lastGoodModel = parsed.model", self.launcher)
        self.assertIn("currentFailure = null", self.launcher)
        self.assertIn("SwitchboardModel.planRunRequest", self.launcher)
        self.assertIn("SwitchboardModel.stoppedRunDisposition", self.launcher)
        self.assertIn("onRunningChanged", self.launcher)
        self.assertIn(
            "root.finishStoppedRunIfNeeded(root.runGeneration, true)", self.launcher
        )
        self.assertIn("refresh && !state.runWasRefresh", self.model)
        self.assertIn(
            "state.settingsGeneration !== state.runSettingsGeneration", self.model
        )

        qml = self.launcher + "\n" + self.settings
        forbidden = (
            "QProcess",
            "child_process",
            "execDetached",
            "sh -c",
            "/bin/sh",
            "/home/",
            "ssh",
            "niri msg",
            "tmux attach",
            "systemd-run",
        )
        for marker in forbidden:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, qml)

        self.assertIn('property string terminalExecutable: "ghostty"', self.launcher)
        self.assertIn('Qt.resolvedUrl("switchboard-open")', self.launcher)

    def test_failure_retains_last_good_model(self):
        failure_path = self.launcher.split("function maybeFinishRun()", 1)[1]
        failure_path = failure_path.split("Timer {", 1)[0]
        self.assertEqual(failure_path.count("lastGoodModel ="), 1)
        self.assertIn("runSettingsGeneration !== settingsGeneration", failure_path)
        self.assertIn("SwitchboardModel.shouldAcceptRunResult", failure_path)
        self.assertIn("setFailure(parsed.error.code", failure_path)


class DocumentationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        cls.bridge_contract = (ROOT / "docs" / "bridge-contract.md").read_text(
            encoding="utf-8"
        )
        cls.normalized_readme = " ".join(cls.readme.split())
        cls.normalized_bridge_contract = " ".join(cls.bridge_contract.split())
        cls.docs = "\n".join(
            (ROOT / relative).read_text(encoding="utf-8")
            for relative in ("docs/architecture.md", "docs/implementation-plan.md")
        )

    def test_runtime_prerequisites_are_truthfully_documented(self):
        for phrase in (
            "Python 3.12 or newer",
            "Agent Switchboard 0.1.0",
            "one executable token",
            "not a shell command",
            "DMS 1.5.0 or newer",
            "Quickshell runtime supplied by DMS",
            "no third-party Python packages",
            "does not mean the integration has no runtime dependencies",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.normalized_readme)

        self.assertIn(
            "uses only the Python standard library",
            self.normalized_bridge_contract,
        )
        self.assertIn("not no runtime dependencies", self.normalized_bridge_contract)

    def test_public_command_boundary_is_documented(self):
        commands = (
            "swbctl snapshot --json",
            "swbctl snapshot --reconcile full --json",
            "swbctl list --json",
            "swbctl list --refresh --json",
            "swbctl prepare-open <session-key> --request-id <uuid> --json",
            "swbctl prepare-new --project <project-id> --location <location-id> --provider <provider> --request-id <uuid> --json",
            "swbctl prepare-history --project <project-id> --location <location-id> --request-id <uuid> --json",
            "swbctl stop-session <session-key> --json",
            "swbctl select-surface <surface-id> --client <tmux-client-id>",
            "swbctl attach-surface <surface-id>",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, self.docs)
        self.assertIn("Snapshot v1 JSON", self.docs)
        self.assertIn("PresentationPlan v1 JSON", self.docs)
        self.assertIn("user-configured `swbctl`", self.docs)
        self.assertIn("must not import internal Agent Switchboard", self.docs)
        self.assertIn("read its database", self.docs)

    def test_cache_semantics_are_documented(self):
        for phrase in (
            "`Qt.callLater`",
            "last-good snapshot",
            "Missing observations and stale data",
            "neutral Codex or Claude capability",
            "Launch targets contain stable IDs",
            "does not connect that signal",
            "reopened or the query changes",
            "`Process.signal(15)`",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.docs)

    def test_non_goals_are_explicit(self):
        for non_goal in (
            "SSH",
            "provider hooks or liveness inference",
            "arbitrary working-directory launch",
            "project-catalog editing",
            "direct tmux locator",
            "non-niri/non-Ghostty adapters",
            "chezmoi cutover",
            "rich widget",
        ):
            with self.subTest(non_goal=non_goal):
                self.assertIn(non_goal, self.docs)

    def test_live_integration_boundary_is_documented(self):
        live = (ROOT / "docs" / "live-integration.md").read_text(encoding="utf-8")
        for command in (
            "dms ipc call plugin-scan scan",
            "dms ipc call plugins enable switchboard",
            "dms ipc call plugins reload switchboard",
            "dms ipc call plugins disable switchboard",
        ):
            with self.subTest(command=command):
                self.assertIn(command, live)
        self.assertIn("no launcher-result query IPC", live)
        self.assertIn("Quickshell 0.3.0", live)
        self.assertIn("Qt 6.11.1", live)
        self.assertIn("dms ipc call launcher openQuery 'sb:switchboard'", live)
        self.assertIn("journalctl --user -u dms.service", live)
        self.assertIn("Phase 3A local action evidence", live)
        self.assertIn("tmux server PID was unchanged", live)
        self.assertIn("`agentSessions` plugin path remained", live)
        self.assertIn("all five Agent Switchboard handlers", live)
        self.assertIn("reported healthy on Codex 0.144.4", live)
        self.assertIn("did not leave a retained `SessionStart` event", live)
        self.assertIn("Phase 3A live DMS acceptance", live)
        self.assertNotIn("dms logs", live)


class DevelopmentWorkflowTests(unittest.TestCase):
    def test_workflow_has_no_machine_specific_switchboard_path(self):
        dev_script = (ROOT / "scripts" / "dev-plugin").read_text(encoding="utf-8")
        live_script = (ROOT / "scripts" / "live-integration").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("/home/", dev_script)
        self.assertNotIn("agent-switchboard/.venv", live_script)
        self.assertIn("--swbctl", live_script)
        self.assertIn("stat -c '%u'", dev_script)
        self.assertIn("refusing to remove", dev_script)


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
