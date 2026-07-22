import hashlib
import json
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "plugin.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "snapshot-v2.json"
FIXTURE_DIGEST = "d70748e05eab95327f5f426266cf433223834507efaebcb4a9cb203d0c320eff"


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
            "version": "0.4.1",
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
        self.assertTrue((ROOT / "SwitchboardModelV5Badges.js").is_file())
        self.assertTrue((ROOT / "switchboard-open").is_file())
        project_manager = ROOT / "switchboard-projects"
        self.assertTrue(project_manager.is_file())
        self.assertTrue(os.access(project_manager, os.X_OK))


class QmlScaffoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.launcher = (ROOT / "SwitchboardLauncher.qml").read_text(encoding="utf-8")
        cls.settings = (ROOT / "SwitchboardSettings.qml").read_text(encoding="utf-8")
        cls.model = (ROOT / "SwitchboardModelV5Badges.js").read_text(encoding="utf-8")

    def test_launcher_surface_reads_cache_synchronously(self):
        self.assertRegex(self.launcher, r"property\s+var\s+pluginService\s*:\s*null")
        self.assertRegex(self.launcher, r'property\s+string\s+trigger\s*:\s*"sb:"')
        self.assertRegex(self.launcher, r"signal\s+itemsChanged(?:\s*\(\s*\))?")
        read_path = self.launcher.split("function getItems(query)", 1)[1].split(
            "function executeItem", 1
        )[0]
        self.assertIn("SwitchboardModelV5.launcherItems", read_path)
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
        self.assertIn('"--create"', execute_path)
        self.assertIn('"--task"', execute_path)
        self.assertIn("function getCategories()", self.launcher)
        self.assertIn("function getContextMenuActions(item)", self.launcher)
        self.assertNotRegex(self.launcher, r"\basync\s+function\s+getItems\b")

    def test_model_module_is_contract_versioned_and_instance_scoped(self):
        expected_import = 'import "SwitchboardModelV5Badges.js" as SwitchboardModelV5'
        self.assertIn(expected_import, self.launcher)
        self.assertIn(expected_import, self.settings)
        self.assertNotIn(".pragma library", self.model)
        self.assertIn("badgeLabel:", self.model)
        self.assertIn("function _stateIcon(value)", self.model)

    def test_settings_use_verified_dms_components(self):
        self.assertIn("PluginSettings {", self.settings)
        self.assertIn('pluginId: "switchboard"', self.settings)
        self.assertEqual(self.settings.count("DankTextField {"), 2)
        self.assertIn(
            "maximumLength: SwitchboardModelV5.MAX_EXECUTABLE_LENGTH", self.settings
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
        self.assertIn("SwitchboardModelV5.parseActionResponse", self.launcher)
        self.assertIn("parseCurrentBridgeResponse(runStdout)", self.launcher)
        self.assertNotIn(
            "SwitchboardModelV5.parseBridgeResponse(runStdout)", self.launcher
        )
        self.assertIn('"--swbctl"', self.launcher)
        self.assertIn('"--timeout-ms"', self.launcher)
        self.assertIn('command.push("--refresh")', self.launcher)
        self.assertIn("refreshProcess.signal(15)", self.launcher)
        self.assertIn("lastGoodModel = parsed.model", self.launcher)
        self.assertIn("currentFailure = null", self.launcher)
        self.assertIn("SwitchboardModelV5.planRunRequest", self.launcher)
        self.assertIn("SwitchboardModelV5.stoppedRunDisposition", self.launcher)
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
        self.assertIn('Qt.resolvedUrl("switchboard-projects")', self.launcher)

    def test_projects_category_opens_local_manager_before_host_routing(self):
        execute_path = self.launcher.split("function executeItem(item)", 1)[1].split(
            "function getContextMenuActions", 1
        )[0]
        self.assertIn('item._switchboardKind === "project-add"', execute_path)
        self.assertIn('item._switchboardKind === "project-manager"', execute_path)
        self.assertLess(
            execute_path.index("startProjectManager(item)"),
            execute_path.index("!item._windowHost"),
        )
        self.assertIn("function startProjectManager(item)", self.launcher)
        self.assertIn('command.push("--add-project")', self.launcher)
        self.assertIn('command.push("--project", item._projectId)', self.launcher)
        self.assertIn("id: managerProcess", self.launcher)
        manager_process = self.launcher.split("id: managerProcess", 1)[1].split(
            "id: actionProcess", 1
        )[0]
        self.assertNotIn("Deadline", manager_process)
        self.assertIn("parseCurrentBridgeResponse(managerStdout)", self.launcher)
        self.assertIn("saveCachedModel(parsed.model, true)", self.launcher)

    def test_task_close_is_first_secondary_action_and_closed_rows_reopen(self):
        context_path = self.launcher.split("function getContextMenuActions(item)", 1)[
            1
        ].split("function startAction", 1)[0]
        self.assertLess(
            context_path.index('"text": "Close task"'),
            context_path.index('"text": "Claude history"'),
        )
        self.assertIn('["--close-task", item._taskId]', context_path)
        self.assertIn("item._canStop && item._sessionKey", context_path)
        self.assertNotIn('item._provider === "claude"', context_path)
        execute_path = self.launcher.split("function executeItem(item)", 1)[1].split(
            "function startProjectManager", 1
        )[0]
        self.assertIn('item._status === "closed"', execute_path)
        self.assertIn('["--task", item._taskId, "--reopen"]', execute_path)
        finish_path = self.launcher.split("function maybeFinishAction()", 1)[1].split(
            "function scheduleRun", 1
        )[0]
        self.assertIn('parsed.action.kind === "closed"', finish_path)
        self.assertIn("ToastService.showWarning", finish_path)
        self.assertIn("ToastService.showInfo", finish_path)
        self.assertIn("scheduleRun(true)", finish_path)

    def test_failure_retains_last_good_model(self):
        failure_path = self.launcher.split("function maybeFinishRun()", 1)[1]
        failure_path = failure_path.split("Timer {", 1)[0]
        self.assertEqual(len(re.findall(r"lastGoodModel\s*=(?!=)", failure_path)), 1)
        self.assertIn("runSettingsGeneration !== settingsGeneration", failure_path)
        self.assertIn("runExitCode === 0 && parsed.ok", failure_path)
        self.assertIn("setFailure(parsed.error.code", failure_path)

    def test_validated_last_good_model_is_persisted_and_diagnostics_are_bounded(self):
        self.assertIn('modelStateKey: "last_good_model_v5_bridge4"', self.launcher)
        self.assertIn("pluginService.loadPluginState", self.launcher)
        self.assertIn("validateFrontendModel(cached)", self.launcher)
        self.assertIn("function parseCurrentBridgeResponse(text)", self.launcher)
        self.assertIn("pluginService.savePluginState", self.launcher)
        self.assertIn("function persistentModelFingerprint(model)", self.launcher)
        self.assertIn(
            "if (!cacheLoadedFromState || runWasRefresh || cacheChanged)", self.launcher
        )
        self.assertIn(
            "saveCachedModel(parsed.model, runWasRefresh || cacheChanged)",
            self.launcher,
        )
        self.assertIn("id: cacheWriteFollowup", self.launcher)
        self.assertIn("interval: 350", self.launcher)
        self.assertIn('target: "switchboard-launcher"', self.launcher)
        diagnostics = self.launcher.split("IpcHandler {", 1)[1]
        for forbidden in ("runStdout", "sessionKey", "providerSessionId", "hostId"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, diagnostics)


class DocumentationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        cls.bridge_contract = (ROOT / "docs" / "bridge-contract.md").read_text(
            encoding="utf-8"
        )
        cls.normalized_readme = " ".join(cls.readme.split())
        cls.normalized_bridge_contract = " ".join(cls.bridge_contract.split())
        cls.view_entry_plan = (ROOT / "docs" / "view-entry-plan.md").read_text(
            encoding="utf-8"
        )
        cls.normalized_view_entry_plan = " ".join(cls.view_entry_plan.split())
        cls.docs = "\n".join(
            (ROOT / relative).read_text(encoding="utf-8")
            for relative in ("docs/architecture.md", "docs/implementation-plan.md")
        )

    def test_runtime_prerequisites_are_truthfully_documented(self):
        for phrase in (
            "Python 3.12 or newer",
            "Agent Switchboard 0.2.0",
            "one executable token",
            "not a shell command",
            "DMS 1.5.0 or newer",
            "Quickshell runtime supplied by DMS",
            "plugin-item transformer",
            "badgeLabel",
            "no third-party Python packages",
            "not no runtime dependencies",
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
            "swbctl fleet --json",
            "swbctl fleet --refresh --json",
            "swbctl prepare-open <session-key> --host <host-id> --request-id <uuid> --json",
            "swbctl prepare-task <task-id> --host <host-id> --request-id <uuid> --json",
            "swbctl prepare-task <task-id> --host <host-id> --reopen --request-id <uuid> --json",
            "swbctl prepare-task <task-id> --host <host-id> --create --project <project-id> --title <text> --checkout <checkout-id> --provider <provider> --request-id <uuid> --json",
            "swbctl prepare-history --project <project-id> --host <host-id> --checkout <checkout-id> --request-id <uuid> --json",
            "swbctl stop-session <session-key> --host <host-id> --json",
            "swbctl task close <task-id> --host <host-id> --json",
            "swbctl select-surface <surface-id> --host <host-id> --client <tmux-client-id>",
            "swbctl attach-surface <surface-id> --host <host-id>",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, self.docs)
        self.assertIn("Fleet v1", self.docs)
        self.assertIn("Snapshot v2", self.docs)
        self.assertIn("PresentationPlan v2", self.docs)
        self.assertIn("user-configured local `swbctl`", self.docs)
        self.assertIn("must not import internal Agent Switchboard", self.docs)
        self.assertIn("read its database", self.docs)

    def test_cache_semantics_are_documented(self):
        for phrase in (
            "`Qt.callLater`",
            "last-good model",
            "unavailable hosts",
            "host-qualified open and closed tasks",
            "does not connect launcher `itemsChanged()`",
            "reopened or its query changes",
            "kills and reaps descendants",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.docs)

    def test_non_goals_are_explicit(self):
        for non_goal in (
            "SSH",
            "infer provider liveness",
            "arbitrary working",
            "edit projects",
            "tmux locator",
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

    def test_view_entry_clean_break_is_documented(self):
        for phrase in (
            "Status: Phase 6A.1 contract repair complete; implementation pending",
            "NavigatorState v1",
            "PresentationDirective v1",
            "Views",
            "Projects",
            "Recovery",
            "There is no compatibility mode",
            "last_good_switchboard_entry_model_v1",
            "Views focus as-is",
            "Projects navigate",
            "cutover_staged",
            "cold DMS restart",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.normalized_view_entry_plan)


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

    def test_ci_uses_supported_actions_and_installs_test_dependencies(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("actions/checkout@v6", workflow)
        self.assertIn("actions/setup-python@v6", workflow)
        self.assertIn("actions/setup-node@v6", workflow)
        self.assertIn('node-version: "24"', workflow)
        install = workflow.index("sudo apt-get install --yes ripgrep")
        check = workflow.index("./scripts/check")
        self.assertLess(install, check)


class FixtureContractTests(unittest.TestCase):
    def test_fixture_digest_and_v2_envelope(self):
        payload = FIXTURE_PATH.read_bytes()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), FIXTURE_DIGEST)
        snapshot = json.loads(payload)
        self.assertEqual(snapshot["schemaVersion"], 2)
        self.assertEqual(snapshot["protocolVersion"], 2)

    def test_fixture_provenance_is_recorded(self):
        provenance = (ROOT / "tests" / "fixtures" / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "byebyebryan/agent-switchboard",
            provenance,
        )
        self.assertIn("tests/fixtures/protocol/v2/snapshot.json", provenance)
        self.assertIn("803f0f8", provenance)
        self.assertNotIn("/home/bryan", provenance)
        self.assertIn("synthetic test data", provenance)
        self.assertIn("not a capture of a live machine", provenance)
        self.assertIn(FIXTURE_DIGEST, provenance)


if __name__ == "__main__":
    unittest.main()
