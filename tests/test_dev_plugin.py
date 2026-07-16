import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dev-plugin"


class DevPluginTests(unittest.TestCase):
    def run_script(self, plugin_dir: Path, action: str):
        return subprocess.run(
            [str(SCRIPT), "--plugin-dir", str(plugin_dir), action],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": "C"},
        )

    def test_install_status_remove_is_reversible_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            plugin_dir = Path(temporary) / "plugins"
            plugin_dir.mkdir()
            destination = plugin_dir / "switchboard"

            installed = self.run_script(plugin_dir, "install")
            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertTrue(destination.is_symlink())
            self.assertEqual(destination.resolve(), ROOT.resolve())

            repeated = self.run_script(plugin_dir, "install")
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertIn("already installed", repeated.stdout)

            status = self.run_script(plugin_dir, "status")
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertIn("installed", status.stdout)

            removed = self.run_script(plugin_dir, "remove")
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse(destination.exists())

            repeated_remove = self.run_script(plugin_dir, "remove")
            self.assertEqual(repeated_remove.returncode, 0, repeated_remove.stderr)
            self.assertIn("not installed", repeated_remove.stdout)

    def test_refuses_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            plugin_dir = Path(temporary) / "plugins"
            plugin_dir.mkdir()
            destination = plugin_dir / "switchboard"
            destination.write_text("keep\n", encoding="utf-8")

            result = self.run_script(plugin_dir, "install")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(destination.read_text(encoding="utf-8"), "keep\n")

            remove = self.run_script(plugin_dir, "remove")
            self.assertNotEqual(remove.returncode, 0)
            self.assertTrue(destination.is_file())

    def test_refuses_foreign_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plugin_dir = root / "plugins"
            foreign = root / "foreign"
            plugin_dir.mkdir()
            foreign.mkdir()
            destination = plugin_dir / "switchboard"
            destination.symlink_to(foreign, target_is_directory=True)

            status = self.run_script(plugin_dir, "status")
            self.assertEqual(status.returncode, 2)
            self.assertIn("foreign", status.stdout)

            remove = self.run_script(plugin_dir, "remove")
            self.assertNotEqual(remove.returncode, 0)
            self.assertTrue(destination.is_symlink())

    def test_refuses_symlinked_plugin_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            actual = root / "actual"
            linked = root / "plugins"
            actual.mkdir()
            linked.symlink_to(actual, target_is_directory=True)

            result = self.run_script(linked, "install")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not itself be a symlink", result.stderr)


if __name__ == "__main__":
    unittest.main()
