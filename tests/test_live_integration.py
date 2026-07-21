# ruff: noqa: E501
import contextlib
import os
import select
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLEANUP = ROOT / "scripts" / "live-cleanup.sh"
LIVE_SCRIPT = ROOT / "scripts" / "live-integration"
LIVE_SHELL_SCRIPT = ROOT / "scripts" / "live-shell-integration"
LIVE_PROCESS_SAMPLER = ROOT / "scripts" / "live-process-sampler"
LIVE_QML = ROOT / "tests" / "live" / "Shell.qml"


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class LiveIntegrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = LIVE_SCRIPT.read_text(encoding="utf-8")
        cls.qml = LIVE_QML.read_text(encoding="utf-8")

    def test_ipc_summary_contains_no_raw_identity_or_items(self):
        summary = self.qml.split("function summary()", 1)[1].split("QtObject {", 1)[0]
        for forbidden in (
            "items",
            "sessionKey",
            "providerSessionId",
            "cwd",
            "hostId",
            "displayName",
            "capturedIdentity",
            "capturedItemKey",
            "modelGeneratedAt",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, summary)

        status = self.qml.split("function status()", 1)[1].split(
            "function captureBaseline", 1
        )[0]
        self.assertIn("return root.summary()", status)
        self.assertNotIn("JSON.stringify", status)
        self.assertNotIn("launcher.getItems", status)
        self.assertIn("printf 'LIVE_INTEGRATION_OK\\n'", self.script)
        self.assertNotIn("LIVE_INTEGRATION_OK dms_root", self.script)

    def test_exact_query_refresh_and_retention_assertions_are_mandatory(self):
        self.assertIn("launcher.getItems(root.capturedIdentity)", self.qml)
        self.assertIn("items[index]._sessionKey === root.capturedItemKey", self.qml)
        self.assertIn("root.queryMatchedExact = matches === 1", self.qml)
        self.assertIn('assert s["entryCount"] > 0', self.script)
        self.assertNotIn('if [ "$session_id" ]', self.script)
        self.assertIn('s["refreshGeneratedAtAdvanced"]', self.script)
        self.assertIn('s["cacheReloaded"]', self.script)
        self.assertIn('s["validatorRejectedInvalid"]', self.script)
        self.assertIn("launcher.loadCachedModel()", self.qml)
        self.assertGreaterEqual(self.script.count('s["retainedModelMatches"]'), 3)
        self.assertGreaterEqual(self.script.count("expected > 0"), 2)
        self.assertGreaterEqual(self.script.count('s["runGeneration"] > minimum'), 3)
        self.assertGreaterEqual(self.script.count('tail -n 80 "$log_file"'), 2)

    def test_project_category_and_local_rows_are_part_of_installed_harness(self):
        self.assertIn("function captureProjectSurface", self.qml)
        self.assertIn('category.id === "projects"', self.qml)
        self.assertIn('launcher.setCategory("projects")', self.qml)
        self.assertIn('items[itemIndex]._switchboardKind === "project-add"', self.qml)
        self.assertIn("projectRows === localProjects", self.qml)
        self.assertIn("projects=$(ipc captureProjectSurface)", self.script)
        self.assertIn('s["projectActionsAvailable"]', self.script)
        self.assertIn('"$repo_root/switchboard-projects"', self.script)

    def test_private_state_and_process_group_are_required(self):
        self.assertIn('cp -a -- "$state_source"', self.script)
        self.assertIn('XDG_STATE_HOME="$temporary/state"', self.script)
        self.assertIn('XDG_CONFIG_HOME="$temporary/config"', self.script)
        self.assertIn("setsid env", self.script)
        deferred = self.script.index("live_defer_traps")
        launched = self.script.index("setsid env")
        published = self.script.index('live_cleanup_init "$harness_pid" "$harness_pid"')
        activated = self.script.index("live_activate_traps", published)
        self.assertLess(deferred, launched)
        self.assertLess(launched, published)
        self.assertLess(published, activated)
        self.assertIn('live_cleanup_init "$harness_pid" "$harness_pid"', self.script)
        self.assertNotIn("SWITCHBOARD_LIVE_KEEP", self.script)


class LiveIntegrationInitializationTests(unittest.TestCase):
    def run_fault(self, fault: str):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tmp = root / "tmp"
            bin_dir = root / "bin"
            dms_root = root / "dms"
            state = root / "state"
            tmp.mkdir()
            bin_dir.mkdir()
            state.mkdir()
            (state / "registry.db").write_text("fixture\n", encoding="utf-8")
            (dms_root / "shell.qml").parent.mkdir()
            (dms_root / "shell.qml").write_text("fixture\n", encoding="utf-8")
            for directory in ("Common", "Modules", "Services", "Widgets"):
                (dms_root / directory).mkdir()
            swbctl = root / "swbctl"
            swbctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            swbctl.chmod(0o755)
            (bin_dir / "qs").write_text(
                "#!/bin/sh\nwhile :; do sleep 60; done\n", encoding="utf-8"
            )
            (bin_dir / "qs").chmod(0o755)
            if fault in {"mktemp", "signal"}:
                action = (
                    'kill -TERM "$PPID"; exec /usr/bin/mktemp "$@"'
                    if fault == "signal"
                    else "exit 43"
                )
                (bin_dir / "mktemp").write_text(
                    f"#!/bin/sh\n{action}\n", encoding="utf-8"
                )
                (bin_dir / "mktemp").chmod(0o755)
            else:
                (bin_dir / "chmod").write_text(
                    '#!/bin/sh\ncase "$*" in *switchboard-live.*) exit 43;; esac\nexec /usr/bin/chmod "$@"\n',
                    encoding="utf-8",
                )
                (bin_dir / "chmod").chmod(0o755)
            result = subprocess.run(
                [
                    str(LIVE_SCRIPT),
                    "--swbctl",
                    str(swbctl),
                    "--dms-root",
                    str(dms_root),
                    "--state-source",
                    str(state),
                ],
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "PATH": f"{bin_dir}:/usr/bin:/bin",
                    "TMPDIR": str(tmp),
                },
                timeout=10,
                check=False,
            )
            if fault == "signal":
                self.assertEqual(result.returncode, 143)
            else:
                self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(tmp.glob("switchboard-live.*")), [])

    def test_pre_temp_signal_and_initialization_failures_leave_nothing(self):
        for fault in ("signal", "mktemp", "chmod"):
            with self.subTest(fault=fault):
                self.run_fault(fault)


class LiveShellIntegrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = LIVE_SHELL_SCRIPT.read_text(encoding="utf-8")
        cls.docs = (ROOT / "docs" / "live-integration.md").read_text(encoding="utf-8")
        cls.sampler = LIVE_PROCESS_SAMPLER.read_text(encoding="utf-8")

    def test_dms_query_arity_and_verified_journal_source(self):
        exact_query = "dms ipc call launcher openQuery 'sb:switchboard'"
        self.assertIn(exact_query, self.script)
        self.assertIn(exact_query, self.docs)
        self.assertNotIn("openQuery switchboard", self.script)
        self.assertNotIn("openQuery switchboard", self.docs)
        self.assertNotIn("dms logs", self.script)
        self.assertNotIn("dms logs", self.docs)
        for contract in (
            "journalctl --user -u dms.service -n 0 --show-cursor --no-pager",
            'journalctl --user -u dms.service --after-cursor "$journal_cursor"',
            '[ -s "$journal_log" ]',
            "dms\\[[0-9]+\\]:",
            "error loading plugin:[[:space:]]*switchboard",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, self.script)

    def test_rollback_precedes_mutation_and_restores_atomically(self):
        trap_index = self.script.index("trap 'live_shell_finish")
        mutation_index = self.script.index("mutation_started=1")
        install_index = self.script.index(
            '"$repo_root/scripts/dev-plugin" --plugin-dir "$plugin_dir" install'
        )
        self.assertLess(trap_index, mutation_index)
        self.assertLess(mutation_index, install_index)
        self.assertIn("trap '' HUP INT TERM", self.script)
        self.assertIn("cp --preserve=mode,ownership,timestamps", self.script)
        self.assertIn(
            'mktemp "$config_dir/plugin_settings.json.switchboard-restore.XXXXXX"',
            self.script,
        )
        self.assertIn('mv -f -- "$restore_tmp" "$plugin_settings"', self.script)
        self.assertIn("dms ipc call plugins disable switchboard", self.script)
        self.assertIn("dms ipc call plugin-scan scan", self.script)
        self.assertIn("dms restart", self.script)
        self.assertLess(
            self.script.index("trap 'live_shell_finish"),
            self.script.index("backup=$(mktemp -d"),
        )
        self.assertIn('[ -z "$settings_tmp" ] || rm -f', self.script)

    def test_process_plugin_log_and_restoration_assertions_are_exact(self):
        for contract in (
            '[ "$baseline_plugin_count" -gt 0 ]',
            'cmp -s "$expected_plugins" "$during_plugins"',
            "BRIDGE_RETAINED",
            "FLEET_RETAINED",
            "BRIDGE_REFRESH",
            "FLEET_REFRESH",
            'cmp -s "$baseline_plugins" "$final_plugins"',
            "stat -c '%a:%u:%g'",
            'has("switchboard")',
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, self.script)
        for contract in (
            'bridge_tail = ["--swbctl", swbctl, "--timeout-ms", "10000"]',
            'fleet_tail = ["fleet", "--json"]',
            'fleet_tail = ["fleet", "--refresh", "--json"]',
        ):
            with self.subTest(sampler_contract=contract):
                self.assertIn(contract, self.sampler)

    def test_preflight_and_sampler_ownership_are_locked(self):
        self.assertIn("symlinked path component", self.script)
        self.assertIn("stat -c '%u'", self.script)
        self.assertIn("baseline plugin list already contains switchboard", self.script)
        self.assertIn("sampler_active=0", self.script)
        self.assertIn('setsid "$repo_root/scripts/live-process-sampler"', self.script)
        self.assertIn("shell_defer_traps", self.script)
        self.assertIn("stop_sampler", self.script)
        self.assertNotIn('kill "$sampler_pid"', self.script)


class LiveShellSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.tmp = self.root / "tmp"
        self.bin = self.root / "bin"
        self.config_home = self.root / "config"
        self.config = self.config_home / "DankMaterialShell"
        self.plugins = self.config / "plugins"
        self.state = self.root / "fake-dms-state"
        self.tmp.mkdir()
        self.bin.mkdir()
        self.plugins.mkdir(parents=True)
        self.plugin_settings = self.config / "plugin_settings.json"
        self.shell_settings = self.config / "settings.json"
        self.plugin_settings.write_text("{}\n", encoding="utf-8")
        self.shell_settings.write_text('{"theme":"test"}\n', encoding="utf-8")
        self.plugin_settings.chmod(0o640)
        self.original = self.plugin_settings.read_bytes()
        self.original_mode = self.plugin_settings.stat().st_mode & 0o777
        self.swbctl = self.root / "swbctl"
        self.write_executable(self.swbctl, "#!/bin/sh\nexit 0\n")
        self.write_executable(
            self.bin / "dms",
            r"""#!/bin/sh
set -eu
command=$*
case $command in
  "ipc call plugin-scan list")
    printf 'basePlugin\tloaded\tlauncher\tBase\n'
    if [ "${FAKE_BASELINE_SWITCHBOARD:-0}" = 1 ]; then
      printf 'switchboard\tloaded\tlauncher\tSwitchboard\n'
    elif [ -L "$FAKE_PLUGIN_DIR/switchboard" ] && [ -f "$FAKE_DMS_STATE/enabled" ]; then
      printf 'switchboard\tloaded\tlauncher\tSwitchboard\n'
    fi
    ;;
  "ipc call plugin-scan status switchboard")
    if [ -f "$FAKE_DMS_STATE/enabled" ]; then printf 'loaded\tlauncher\t\n'; else printf 'unloaded\tlauncher\t\n'; fi
    ;;
  "ipc call plugins enable switchboard") mkdir -p "$FAKE_DMS_STATE"; : >"$FAKE_DMS_STATE/enabled" ;;
  "ipc call plugins disable switchboard") rm -f "$FAKE_DMS_STATE/enabled" ;;
  "ipc call plugins reload switchboard"|"ipc call plugin-scan scan"|"ipc call launcher close"|"ipc call launcher openQuery sb:switchboard") : ;;
  restart)
    if [ "${FAKE_SIGNAL_ON_RESTART:-0}" = 1 ] && [ ! -e "$FAKE_DMS_STATE/signalled" ]; then
      : >"$FAKE_DMS_STATE/signalled"
      kill -TERM "$PPID"
    fi
    ;;
  *) printf 'unexpected fake dms command: %s\n' "$command" >&2; exit 2 ;;
esac
""",
        )
        self.write_executable(
            self.bin / "journalctl",
            r"""#!/bin/sh
case " $* " in
  *" --show-cursor "*) printf '%s\n' '-- cursor: fake-cursor' ;;
  *" --after-cursor "*) printf '%s\n' '2026 fake dms[123]: plugin lifecycle ok' ;;
  *) exit 2 ;;
esac
""",
        )
        self.write_executable(
            self.bin / "setsid",
            r"""#!/bin/sh
printf '%s\n' "$$" >"$FAKE_SAMPLER_PID_FILE"
case ${FAKE_SAMPLER_MODE:-fail} in
  fail) exec /usr/bin/setsid /bin/sh -c 'exit 7' ;;
  stubborn) exec /usr/bin/setsid /bin/sh -c 'trap "" TERM; while :; do sleep 60; done' ;;
  *) exec /usr/bin/setsid "$@" ;;
esac
""",
        )
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(self.root),
            "XDG_CONFIG_HOME": str(self.config_home),
            "TMPDIR": str(self.tmp),
            "FAKE_PLUGIN_DIR": str(self.plugins),
            "FAKE_DMS_STATE": str(self.state),
            "FAKE_SAMPLER_PID_FILE": str(self.root / "sampler.pid"),
        }

    def tearDown(self):
        self.temporary.cleanup()

    def write_executable(self, path: Path, content: str):
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def wrapper(self, name: str, content: str):
        self.write_executable(self.bin / name, content)

    def run_script(self, **extra_env):
        return subprocess.run(
            [
                str(LIVE_SHELL_SCRIPT),
                "--swbctl",
                str(self.swbctl),
                "--confirm-disruptive",
            ],
            capture_output=True,
            text=True,
            env={**self.env, **extra_env},
            timeout=20,
            check=False,
        )

    def assert_restored(self):
        self.assertEqual(self.plugin_settings.read_bytes(), self.original)
        self.assertEqual(
            self.plugin_settings.stat().st_mode & 0o777, self.original_mode
        )
        self.assertFalse((self.plugins / "switchboard").exists())
        self.assertFalse((self.plugins / "switchboard").is_symlink())
        self.assertEqual(list(self.tmp.glob("switchboard-shell-*")), [])
        self.assertEqual(
            list(self.config.glob("plugin_settings.json.switchboard-*")), []
        )

    def test_first_and_second_mktemp_and_copy_hash_stat_failures_cleanup(self):
        cases = (
            ("mktemp", "1", "fail"),
            ("mktemp", "2", "fail"),
            ("mktemp", "1", "signal"),
            ("mktemp", "2", "signal"),
            ("cp", "", "fail"),
            ("cp", "", "signal"),
            ("sha256sum", "", "fail"),
            ("sha256sum", "", "signal"),
            ("stat", "", "fail"),
            ("stat", "", "signal"),
        )
        for command, ordinal, mode in cases:
            with self.subTest(command=command, ordinal=ordinal, mode=mode):
                counter = self.root / "counter"
                counter.unlink(missing_ok=True)
                if command == "mktemp":
                    action = (
                        'kill -TERM "$PPID"; exec /usr/bin/mktemp "$@"'
                        if mode == "signal"
                        else "exit 44"
                    )
                    self.wrapper(
                        "mktemp",
                        f'''#!/bin/sh
n=$(cat "{counter}" 2>/dev/null || printf 0); n=$((n + 1)); printf '%s\n' "$n" >"{counter}"
[ "$n" = {ordinal} ] && {{ {action}; }}
exec /usr/bin/mktemp "$@"
''',
                    )
                elif command == "stat":
                    action = (
                        'kill -TERM "$PPID"; exec /usr/bin/stat "$@"'
                        if mode == "signal"
                        else "exit 44"
                    )
                    self.wrapper(
                        "stat",
                        f'#!/bin/sh\n[ "${{2:-}}" = %a:%u:%g ] && {{ {action}; }}\nexec /usr/bin/stat "$@"\n',
                    )
                else:
                    real = f"/usr/bin/{command}"
                    action = (
                        f'kill -TERM "$PPID"; exec {real} "$@"'
                        if mode == "signal"
                        else "exit 44"
                    )
                    self.wrapper(command, f"#!/bin/sh\n{action}\n")
                result = self.run_script()
                if mode == "signal":
                    self.assertEqual(result.returncode, 143, result)
                else:
                    self.assertNotEqual(result.returncode, 0, result)
                self.assert_restored()
                (self.bin / command).unlink(missing_ok=True)

    def test_symlink_foreign_owner_and_baseline_switchboard_are_rejected(self):
        linked = self.root / "linked-config"
        self.config_home.rename(linked)
        self.config_home.symlink_to(linked, target_is_directory=True)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.config_home.unlink()
        linked.rename(self.config_home)
        self.assert_restored()

        self.wrapper(
            "stat",
            '#!/bin/sh\n[ "${2:-}" = %u ] && { printf 99999; exit 0; }\nexec /usr/bin/stat "$@"\n',
        )
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        (self.bin / "stat").unlink()
        self.assert_restored()

        result = self.run_script(FAKE_BASELINE_SWITCHBOARD="1")
        self.assertNotEqual(result.returncode, 0)
        self.assert_restored()

    def test_settings_failures_and_signal_restore_exact_file(self):
        cases = tuple(
            (command, mode)
            for command in ("jq", "chmod", "chown", "mv")
            for mode in ("fail", "signal")
        )
        for command, mode in cases:
            with self.subTest(command=command, mode=mode):
                real = f"/usr/bin/{command}"
                match = ".switchboard =" if command == "jq" else "switchboard-test"
                action = (
                    'kill -TERM "$PPID"; exec ' + real + ' "$@"'
                    if mode == "signal"
                    else "exit 45"
                )
                self.wrapper(
                    command,
                    f'''#!/bin/sh
case "$*" in *"{match}"*) {action} ;; esac
exec {real} "$@"
''',
                )
                try:
                    result = self.run_script()
                    if mode == "signal":
                        self.assertEqual(
                            result.returncode, 143, (result.stdout, result.stderr)
                        )
                    else:
                        self.assertNotEqual(result.returncode, 0)
                    self.assert_restored()
                finally:
                    (self.bin / command).unlink(missing_ok=True)

    def test_sampler_failure_and_signal_have_no_survivors(self):
        failed = self.run_script(FAKE_SAMPLER_MODE="fail")
        self.assertNotEqual(failed.returncode, 0)
        self.assert_restored()

        signalled = self.run_script(
            FAKE_SAMPLER_MODE="stubborn", FAKE_SIGNAL_ON_RESTART="1"
        )
        self.assertEqual(
            signalled.returncode, 143, (signalled.stdout, signalled.stderr)
        )
        pgid = int((self.root / "sampler.pid").read_text(encoding="utf-8"))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and LiveCleanupTests.group_members(
            self, pgid
        ):
            time.sleep(0.02)
        self.assertEqual(LiveCleanupTests.group_members(self, pgid), [])
        self.assert_restored()


class LiveCleanupTests(unittest.TestCase):
    def group_members(self, pgid: int) -> list[int]:
        members = []
        for stat_path in Path("/proc").glob("[0-9]*/stat"):
            try:
                fields = stat_path.read_text(encoding="utf-8").rsplit(")", 1)[1].split()
                if int(fields[2]) == pgid:
                    members.append(int(stat_path.parent.name))
            except (FileNotFoundError, IndexError, ValueError):
                continue
        return members

    def wait_for_exit(self, pid: int, timeout: float = 3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not process_exists(pid):
                return
            time.sleep(0.02)
        self.fail(f"process {pid} survived cleanup")

    def wait_for_group_exit(self, pgid: int, timeout: float = 3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.group_members(pgid):
                return
            time.sleep(0.02)
        self.fail(f"process group {pgid} survived cleanup: {self.group_members(pgid)}")

    def test_term_exits_143_and_kills_stubborn_process_group(self):
        with tempfile.TemporaryDirectory() as parent:
            private = Path(parent) / "private"
            private.mkdir()
            fixture = r"""
set -eu
. "$1"
private=$2
setsid sh -c 'trap "" TERM; while :; do sleep 60; done' &
child=$!
live_cleanup_init "$child" "$child" "$private"
live_install_traps
printf '%s\n' "$child"
while :; do sleep 0.05; done
"""
            process = subprocess.Popen(
                ["sh", "-c", fixture, "fixture", str(CLEANUP), str(private)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert process.stdout is not None
            readable, _, _ = select.select([process.stdout], [], [], 2.0)
            self.assertTrue(readable, "fixture did not report its child")
            child = int(process.stdout.readline().strip())
            try:
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=5.0)
                self.assertEqual(process.returncode, 143, (stdout, stderr))
                self.assertFalse(private.exists())
                self.wait_for_exit(child)
                self.wait_for_group_exit(child)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                if process_exists(child):
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(child, signal.SIGKILL)

    def test_cleanup_is_idempotent(self):
        with tempfile.TemporaryDirectory() as parent:
            private = Path(parent) / "private"
            private.mkdir()
            fixture = r"""
set -eu
. "$1"
private=$2
setsid sh -c 'while :; do sleep 60; done' &
child=$!
live_cleanup_init "$child" "$child" "$private"
live_cleanup
live_cleanup
test ! -e "$private"
if /bin/kill -0 -- "-$child" 2>/dev/null; then exit 1; fi
"""
            result = subprocess.run(
                ["sh", "-c", fixture, "fixture", str(CLEANUP), str(private)],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))

    def test_deferred_signals_close_post_fork_pre_registration_window(self):
        cases = (
            (signal.SIGHUP, "HUP", 129),
            (signal.SIGINT, "INT", 130),
            (signal.SIGTERM, "TERM", 143),
        )
        for _signal, signal_name, exit_code in cases:
            with (
                self.subTest(signal=signal_name),
                tempfile.TemporaryDirectory() as parent,
            ):
                private = Path(parent) / "private"
                private.mkdir()
                fixture = r"""
set -eu
. "$1"
private=$2
signal_name=$3
live_cleanup_init "" "" "$private"
live_defer_traps
setsid sh -c 'while :; do sleep 60; done' &
child=$!
printf '%s\n' "$child"
kill -"$signal_name" "$$"
live_cleanup_init "$child" "$child" "$private"
live_activate_traps
exit 99
"""
                process = subprocess.Popen(
                    [
                        "sh",
                        "-c",
                        fixture,
                        "fixture",
                        str(CLEANUP),
                        str(private),
                        signal_name,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                assert process.stdout is not None
                child = int(process.stdout.readline().strip())
                try:
                    stdout, stderr = process.communicate(timeout=5.0)
                    self.assertEqual(process.returncode, exit_code, (stdout, stderr))
                    self.assertFalse(private.exists())
                    self.wait_for_exit(child)
                    self.wait_for_group_exit(child)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                    if self.group_members(child):
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(child, signal.SIGKILL)

    def test_cleanup_waits_for_setsid_launch_race(self):
        with tempfile.TemporaryDirectory() as parent:
            private = Path(parent) / "private"
            private.mkdir()
            fixture = r"""
set -eu
. "$1"
private=$2
sh -c 'sleep 0.2; exec setsid sh -c '\''while :; do sleep 60; done'\''' &
child=$!
live_cleanup_init "$child" "$child" "$private"
live_cleanup
test ! -e "$private"
if /bin/kill -0 -- "-$child" 2>/dev/null; then exit 1; fi
"""
            result = subprocess.run(
                ["sh", "-c", fixture, "fixture", str(CLEANUP), str(private)],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))


if __name__ == "__main__":
    unittest.main()
