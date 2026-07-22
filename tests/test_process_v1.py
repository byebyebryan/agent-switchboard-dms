import os
import stat
import tempfile
import textwrap
import unittest
from pathlib import Path

from switchboard_dms.process import ProcessRunError, run_process


class ProcessV1Tests(unittest.TestCase):
    def executable(self, root: Path, body: str) -> Path:
        path = root / "command"
        path.write_text(
            "#!/usr/bin/env python3\n" + textwrap.dedent(body), encoding="utf-8"
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def test_runs_without_shell_and_drains_both_streams(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            command = self.executable(
                Path(temporary),
                """
                import os
                os.write(1, b"model")
                os.write(2, b"diagnostic")
                """,
            )
            result = run_process([str(command), "literal;not-shell"], timeout_ms=1000)
            self.assertEqual(result.stdout, b"model")
            self.assertEqual(result.stderr, b"diagnostic")
            self.assertEqual(result.exit_code, 0)

    def test_timeout_kills_and_reaps_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            command = self.executable(Path(temporary), "import time\ntime.sleep(30)\n")
            with self.assertRaises(ProcessRunError) as caught:
                run_process([str(command)], timeout_ms=100)
            self.assertEqual(caught.exception.code, "process_timeout")

    def test_missing_executable_is_structured(self) -> None:
        with self.assertRaises(ProcessRunError) as caught:
            run_process([f"/missing/{os.getpid()}"], timeout_ms=100)
        self.assertEqual(caught.exception.code, "executable_not_found")


if __name__ == "__main__":
    unittest.main()
