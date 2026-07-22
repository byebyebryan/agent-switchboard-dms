import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build-plugin"
INSTALL = ROOT / "scripts" / "install-plugin"


class ArtifactV1Tests(unittest.TestCase):
    def build(self, destination: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(BUILD), "--output", str(destination)],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "SOURCE_DATE_EPOCH": "1784073600"},
        )

    def install(
        self, plugin_dir: Path, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(INSTALL), "--plugin-dir", str(plugin_dir), *arguments],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_build_is_reproducible_and_contains_only_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.zip"
            second = root / "second.zip"
            self.assertEqual(self.build(first).returncode, 0)
            self.assertEqual(self.build(second).returncode, 0)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
                combined = "\n".join(names).casefold()
                self.assertIn("switchboard/artifact-manifest.json", names)
                self.assertIn("switchboard/switchboardentrymodelv1.js", combined)
                for removed in (
                    "modelv5",
                    "switchboard-projects",
                    "snapshot",
                    "fleet",
                    "projects.py",
                ):
                    self.assertNotIn(removed, combined)
                manifest = json.loads(
                    archive.read("switchboard/artifact-manifest.json")
                )
                self.assertEqual(manifest["adapterVersion"], "0.5.0")
                self.assertEqual(manifest["pairedCoreVersion"], "0.3.0")

    def test_stage_is_inactive_then_activation_is_atomic_and_owned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plugin_dir = root / "plugins"
            plugin_dir.mkdir()
            archive = root / "plugin.zip"
            self.assertEqual(self.build(archive).returncode, 0)

            staged_result = self.install(plugin_dir, "stage", "--archive", str(archive))
            self.assertEqual(staged_result.returncode, 0, staged_result.stderr)
            staged = Path(json.loads(staged_result.stdout)["staged"])
            self.assertTrue(staged.is_dir())
            self.assertFalse((plugin_dir / "switchboard").exists())
            self.assertEqual(
                (staged / ".artifact-sha256").read_text(encoding="ascii").strip(),
                hashlib.sha256(archive.read_bytes()).hexdigest(),
            )

            repeated = self.install(plugin_dir, "stage", "--archive", str(archive))
            self.assertEqual(repeated.returncode, 0, repeated.stderr)
            self.assertEqual(Path(json.loads(repeated.stdout)["staged"]), staged)

            activated = self.install(plugin_dir, "activate", "--staged", str(staged))
            self.assertEqual(activated.returncode, 0, activated.stderr)
            destination = plugin_dir / "switchboard"
            self.assertTrue(destination.is_symlink())
            self.assertEqual(destination.resolve(), staged.resolve())
            status = self.install(plugin_dir, "status")
            self.assertEqual(status.returncode, 0)
            self.assertEqual(
                json.loads(status.stdout),
                {"active": True, "owned": True, "target": str(staged)},
            )

    def test_activation_refuses_foreign_active_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plugin_dir = root / "plugins"
            plugin_dir.mkdir()
            archive = root / "plugin.zip"
            self.assertEqual(self.build(archive).returncode, 0)
            staged = Path(
                json.loads(
                    self.install(plugin_dir, "stage", "--archive", str(archive)).stdout
                )["staged"]
            )
            (plugin_dir / "switchboard").mkdir()
            result = self.install(plugin_dir, "activate", "--staged", str(staged))
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue((plugin_dir / "switchboard").is_dir())


if __name__ == "__main__":
    unittest.main()
