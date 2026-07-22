import json
import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ScaffoldV1Tests(unittest.TestCase):
    def test_manifest_and_replacement_files_are_exact(self) -> None:
        manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], "switchboard")
        self.assertEqual(manifest["version"], "0.5.0")
        self.assertEqual(manifest["component"], "./SwitchboardLauncher.qml")
        self.assertEqual(manifest["settings"], "./SwitchboardSettings.qml")
        for name in (
            "SwitchboardLauncher.qml",
            "SwitchboardSettings.qml",
            "SwitchboardEntryModelV1.js",
            "switchboard-bridge",
            "switchboard-open",
        ):
            self.assertTrue((ROOT / name).is_file(), name)
        self.assertFalse((ROOT / "SwitchboardModelV5Badges.js").exists())
        self.assertFalse((ROOT / "switchboard-projects").exists())
        self.assertTrue(os.access(ROOT / "switchboard-bridge", os.X_OK))
        self.assertTrue(os.access(ROOT / "switchboard-open", os.X_OK))

    def test_qml_uses_only_entry_model_cache_and_fixed_actions(self) -> None:
        launcher = (ROOT / "SwitchboardLauncher.qml").read_text(encoding="utf-8")
        settings = (ROOT / "SwitchboardSettings.qml").read_text(encoding="utf-8")
        combined = launcher + "\n" + settings
        self.assertIn('modelStateKey: "last_good_switchboard_entry_model_v1"', launcher)
        self.assertIn('import "SwitchboardEntryModelV1.js" as EntryModel', combined)
        self.assertIn("property bool modelFresh: false", launcher)
        self.assertIn('["view", "project", "recovery"].indexOf', launcher)
        self.assertNotIn("last_good_model_v5_bridge4", combined)
        for marker in (
            "SwitchboardModelV5",
            "switchboard-projects",
            "--create",
            "--task",
            "--history",
            "--stop",
            "--session",
            "--checkout",
            "--provider",
        ):
            self.assertNotIn(marker, combined)
        self.assertNotIn("sh -c", combined)
        self.assertNotIn("/bin/sh", combined)


if __name__ == "__main__":
    unittest.main()
