import os
import re
import shutil
import sys

from test_uts import EXECUTE, ROOT_STAT, UtsFixture


ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 56


class YunjingTests(UtsFixture):
    local_stubs = r"""
        remove_path() { record "remove $1"; }
    """

    def setUp(self):
        super().setUp()
        self.primary = self.root / "usr/local/qcloud/YunJing"
        self.fallback = self.root / "var/lib/qcloud/YunJing"

    def reset(self):
        for directory in (self.primary, self.fallback):
            shutil.rmtree(directory, ignore_errors=True)
        self.vendor_log.unlink(missing_ok=True)

    def local_cleanup(self):
        return [
            *self.removals(),
        ]

    def removals(self):
        paths = (
            self.primary,
            self.fallback,
            self.root / "run/ydeyes",
            self.root / "var/run/ydeyes",
            self.root / "tmp/yunjing_install",
            self.root / "tmp/yunjing_install_nohup",
        )
        return [f"remove {path}" for path in paths]

    def executed(self, extra=""):
        return self.local_stubs + ROOT_STAT + EXECUTE + self.real_function("run") + extra + "uninstall_yunjing\n"

    def test_official_uninstaller_runs_from_its_directory_before_local_cleanup(self):
        for chosen, other in ((self.primary, self.fallback), (self.fallback, None)):
            with self.subTest(directory=chosen):
                self.reset()
                script = self.vendor_script(chosen / "uninst.sh", self.logging_script("yunjing"))
                if other is not None:
                    self.vendor_script(other / "uninst.sh", self.logging_script("other"))
                result = self.run_shell(self.executed())
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertIn(f"Running official {script}", result.stdout)
                self.assertEqual(self.vendor_log.read_text(), f"yunjing {chosen}\n")
                self.assertEqual(
                    self.events.read_text().splitlines(),
                    [self.official(script, "timeout -k 5 120"), *self.local_cleanup()],
                )

    def test_official_uninstaller_is_executed_directly_like_the_vendor_command(self):
        script = self.vendor_script(
            self.primary / "uninst.sh",
            f"#!{sys.executable}\n"
            + "import os\n"
            + f"open({str(self.vendor_log)!r}, 'a').write('python ' + os.getcwd() + '\\n')\n",
        )
        result = self.run_shell(self.executed())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.vendor_log.read_text(), f"python {os.path.realpath(self.primary)}\n")
        self.assertEqual(
            self.events.read_text().splitlines(),
            [self.official(script, "timeout -k 5 120"), *self.local_cleanup()],
        )

    def test_official_uninstall_continues_to_verification(self):
        self.vendor_script(
            self.primary / "uninst.sh",
            "#!/bin/bash\nprintf 'remove YunJing software success\\n'\n",
        )
        result = self.run_shell(
            self.local_stubs + ROOT_STAT + EXECUTE + self.real_function("run")
            + r"""
            verify_uninstall() { record verify; }
            main --yes
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text().splitlines()[-1:], ["verify"])
        self.assertIn("local uninstall verified", result.stdout)

    def test_official_uninstaller_failure_stops_local_cleanup(self):
        cases = (
            ("unknown format", b"MZ\x90\x00 not a shell script\n", 1, "Unrecognized uninstaller format", False),
            ("not executable", None, 1, "Empty or non-executable uninstaller", False),
            ("empty", b"", 1, "Empty or non-executable uninstaller", False),
            ("broken binary", ELF, None, "Command failed (", True),
            ("exit 7", b"#!/bin/bash\nexit 7\n", 7, "Command failed (7)", True),
            ("exit 124", b"#!/bin/bash\nexit 124\n", 124, "Command failed (124)", True),
            ("exit 137", b"#!/bin/bash\nexit 137\n", 137, "Command failed (137)", True),
        )
        for name, payload, status, warning, executed in cases:
            with self.subTest(case=name):
                self.reset()
                script = self.primary / "uninst.sh"
                if name == "not executable":
                    self.vendor_script(script, self.logging_script("not-executable")).chmod(0o644)
                else:
                    self.vendor_script(script, payload)
                result = self.run_shell(self.executed())
                official = [self.official(script, "timeout -k 5 120")] if executed else []
                expected = status
                if expected is None:
                    reported = re.search(r"Official uninst\.sh exited with status (\d+)\.", result.stderr)
                    self.assertIsNotNone(reported, result.stderr)
                    expected = int(reported[1])
                    self.assertTrue(0 < expected < 128 and expected != 124, expected)
                self.assertFalse(self.vendor_log.exists())
                self.assertIn(warning, result.stderr)
                self.assertIn(f"Official uninst.sh exited with status {expected}.", result.stderr)
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                self.assertIn("Local cleanup was not started.", result.stderr)
                self.assertEqual(self.events.read_text().splitlines(), official)
                self.assertTrue(script.is_file())

    def test_missing_uninstaller_still_cleans_local_remnants(self):
        self.primary.mkdir(parents=True)
        result = self.run_shell(self.local_stubs + "uninstall_yunjing\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No official uninst.sh found; cleaning local remnants only.", result.stdout)
        self.assertEqual(self.events.read_text().splitlines(), self.local_cleanup())

class ComponentTests(UtsFixture):
    def test_timeout_stops_component_cleanup_before_later_steps(self):
        for status in (124, 137, 143):
            with self.subTest(status=status):
                result = self.run_shell(f"remove_paths() {{ record timed-out; return {status}; }}\nuninstall_yunjing\n")
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                self.assertEqual(self.events.read_text().splitlines(), ["timed-out"])

    def test_selection_helpers_list_yunjing_paths(self):
        result = self.run_shell("selected_dirs; echo ---; selected_leftovers")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        dirs, leftovers = (part.splitlines() for part in result.stdout.split("---\n"))
        self.assertEqual(dirs, [
            str(self.root / "usr/local/qcloud/YunJing"),
            str(self.root / "var/lib/qcloud/YunJing"),
            str(self.root / "run/ydeyes"),
            str(self.root / "var/run/ydeyes"),
        ])
        self.assertEqual(leftovers, [str(self.root / "tmp/yunjing_install"), str(self.root / "tmp/yunjing_install_nohup")])
