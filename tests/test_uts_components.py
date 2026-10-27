import os
import re
import shutil
import sys

from test_uts import EXECUTE, ROOT_STAT, UtsFixture


ELF = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 56
MOCKS = r"""
    stat() { printf '0\n'; }
    root_crontab() { :; }
    run() {
      record "run $*"
      case "$1" in
        rm|rmdir|cp|mv|chmod) command "$@" ;;
      esac
    }
    INCLUDE_MONITOR=1
"""
LATER_STEPS = r"""
    root_crontab() { :; }
    stop_agent_processes() { record stop-processes; }
    remove_path() { record "remove $1"; }
    remove_empty_dir() { record "remove-empty $1"; }
    INCLUDE_MONITOR=1
"""


def install_monitor(root, prefix, scripts=True):
    monitor = root / prefix / "monitor"
    stargate = root / prefix / "stargate"
    for directory in (monitor / "barad/admin", stargate / "admin", stargate / "bin"):
        directory.mkdir(parents=True, exist_ok=True)
    (stargate / "bin/sgagent64").touch()
    if scripts:
        for script in (monitor / "barad/admin/uninstall.sh", stargate / "admin/uninstall.sh"):
            script.write_text("#!/bin/bash\nexit 0\n")
            script.chmod(0o755)
    return monitor, stargate


class YunjingTests(UtsFixture):
    local_stubs = r"""
        stop_agent_processes() { record "stop-processes $*"; }
        root_crontab() { :; }
        remove_path() { record "remove $1"; }
    """

    def setUp(self):
        super().setUp()
        self.enable_proc()
        self.primary = self.root / "usr/local/qcloud/YunJing"
        self.fallback = self.root / "var/lib/qcloud/YunJing"

    def reset(self):
        for directory in (self.primary, self.fallback):
            shutil.rmtree(directory, ignore_errors=True)
        self.vendor_log.unlink(missing_ok=True)

    def local_cleanup(self):
        return [
            f"stop-processes {self.primary} {self.fallback}",
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

    def test_verified_official_uninstall_continues_selected_components(self):
        self.vendor_script(
            self.primary / "uninst.sh",
            "#!/bin/bash\nprintf 'remove YunJing software success\\n'\n",
        )
        result = self.run_shell(
            self.local_stubs + ROOT_STAT + EXECUTE + self.real_function("run")
            + r"""
            uninstall_monitor() { record monitor; }
            verify_uninstall() { record verify; }
            main --yes --include-monitor
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text().splitlines()[-2:], ["monitor", "verify"])
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

    def test_missing_uninstaller_still_cleans_dirs_and_cron_lines(self):
        self.primary.mkdir(parents=True)
        result = self.run_shell(
            self.local_stubs
            + r"""
            WORKDIR="$UTS_TEST_ROOT/tmp"
            root_crontab() {
              QUERY_OUTPUT="$(
              printf '%s\n' "0 0 * * * ${YUNJING_DIRS[0]}/YDCrontab.sh > /dev/null 2>&1 &"
              printf '%s\n' "0 3 * * * /usr/local/bin/backup.sh"
              )"
            }
            uninstall_yunjing
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("No official uninst.sh found; cleaning local remnants only.", result.stdout)
        self.assertIn(f"Removing root crontab line: 0 0 * * * {self.primary}/YDCrontab.sh", result.stdout)
        self.assertEqual(
            self.events.read_text().splitlines(),
            [f"run crontab -u root {self.root}/tmp/crontab", *self.local_cleanup()],
        )
        self.assertEqual((self.root / "tmp/crontab").read_text(), "0 3 * * * /usr/local/bin/backup.sh\n")

    def test_unstoppable_processes_return_6_with_console_hint(self):
        result = self.run_shell(
            self.local_stubs
            + r"""
            agent_processes() { printf '4242|/a/YDService|1|x\n'; }
            signal_agent_processes() { record "signal $1"; }
            """
            + self.real_function("stop_agent_processes")
            + "uninstall_yunjing\n"
        )
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        expected = ["signal TERM"] + ["sleep 1"] * 5
        self.assertEqual(self.events.read_text().splitlines(), expected)
        self.assertIn("Agent processes remain; preserving their installation files:", result.stderr)
        self.assertIn("PID 4242", result.stderr)
        self.assertIn("turn off Agent uninstall protection", result.stderr)

    def test_dry_run_does_not_run_the_official_uninstaller(self):
        self.vendor_script(self.primary / "uninst.sh", self.logging_script("unexpected"))
        snapshot = self.file_snapshot()
        result = self.run_shell(self.executed("exec 3>&1\nDRY_RUN=1\n"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.file_snapshot(), snapshot)
        self.assertFalse(self.vendor_log.exists())
        self.assertNotIn("sleep ", self.events.read_text())

class MonitorTests(UtsFixture):
    def setUp(self):
        super().setUp()
        self.enable_proc()

    def reset(self):
        for prefix in ("usr/local/qcloud", "var/lib/qcloud"):
            shutil.rmtree(self.root / prefix, ignore_errors=True)
        self.vendor_log.unlink(missing_ok=True)

    def leftovers(self):
        (self.root / "etc/cron.d").mkdir(exist_ok=True)
        paths = [
            self.root / name
            for name in (
                "etc/cron.d/sgagenttask",
                "tmp/stargate.lock",
                "var/run/stargate.tencentyun.pid",
                "var/run/stargat.tencentyun.pid",
            )
        ]
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x\n")
        paths[0].write_text("* * * * * root /usr/local/qcloud/stargate/admin/start.sh\n")
        return paths

    def test_official_uninstallers_run_barad_then_sgagent_from_their_directories(self):
        for prefix in ("usr/local/qcloud", "var/lib/qcloud"):
            with self.subTest(prefix=prefix):
                self.reset()
                monitor, stargate = install_monitor(self.root, prefix, scripts=False)
                barad = self.vendor_script(monitor / "barad/admin/uninstall.sh", self.logging_script("barad"))
                sgagent = self.vendor_script(stargate / "admin/uninstall.sh", self.logging_script("sgagent"))
                result = self.run_shell(
                    ROOT_STAT + EXECUTE + self.real_function("run") + LATER_STEPS + "uninstall_monitor\n"
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(
                    self.vendor_log.read_text(),
                    f"barad {monitor}/barad/admin\nsgagent {stargate}/admin\n",
                )
                events = self.events.read_text().splitlines()
                self.assertEqual(
                    events[:3],
                    [
                        self.official(barad, "timeout -k 5 120"),
                        self.official(sgagent, "timeout -k 5 120"),
                        "stop-processes",
                    ],
                )

    def test_official_failure_stops_monitor_cleanup(self):
        for name, payload, status in (
            ("unknown format", b"MZ\x90\x00 not a shell script\n", 1),
            ("exit 7", b"#!/bin/bash\nexit 7\n", 7),
            ("exit 124", b"#!/bin/bash\nexit 124\n", 124),
        ):
            with self.subTest(case=name):
                self.reset()
                monitor, stargate = install_monitor(self.root, "usr/local/qcloud", scripts=False)
                barad = self.vendor_script(monitor / "barad/admin/uninstall.sh", payload)
                self.vendor_script(stargate / "admin/uninstall.sh", self.logging_script("sgagent"))
                result = self.run_shell(
                    ROOT_STAT + EXECUTE + self.real_function("run") + LATER_STEPS + "uninstall_monitor\n"
                )
                barad_run = [] if name == "unknown format" else [self.official(barad, "timeout -k 5 120")]
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                self.assertFalse(self.vendor_log.exists())
                self.assertEqual(self.events.read_text().splitlines(), barad_run)
                self.assertTrue(stargate.is_dir())

    def test_local_cleanup_removes_each_install_root(self):
        for prefix in ("usr/local/qcloud", "var/lib/qcloud"):
            with self.subTest(prefix=prefix):
                monitor, stargate = install_monitor(self.root, prefix)
                result = self.run_shell(MOCKS + "uninstall_monitor")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("[WARN]", result.stderr)
                self.assertEqual(self.events.read_text().splitlines(), [
                    self.official(monitor / "barad/admin/uninstall.sh"),
                    self.official(stargate / "admin/uninstall.sh"),
                    f"run rm -rf -- {stargate}",
                    f"run rm -rf -- {monitor}/barad",
                    f"run rmdir -- {monitor}",
                ])
                self.assertFalse(monitor.exists())
                self.assertFalse(stargate.exists())

    def test_monitor_directory_with_other_content_is_kept_without_failure(self):
        for other in ("other/agent.conf", ".cache"):
            with self.subTest(other=other):
                self.reset()
                monitor, stargate = install_monitor(self.root, "usr/local/qcloud")
                keep = monitor / other
                keep.parent.mkdir(parents=True, exist_ok=True)
                keep.write_text("keep\n")
                result = self.run_shell(MOCKS + "uninstall_monitor")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("[WARN]", result.stderr)
                events = self.events.read_text()
                self.assertIn(f"run rm -rf -- {monitor}/barad\n", events)
                self.assertNotIn("rmdir", events)
                self.assertEqual([path.name for path in monitor.iterdir()], [other.split("/")[0]])
                self.assertEqual(keep.read_text(), "keep\n")
                self.assertFalse(stargate.exists())

    def test_cron_entries_are_removed_before_services_and_processes_stop(self):
        task = self.leftovers()[0]
        other = "*/5 * * * * /usr/local/qcloud/monitor/other/agent.sh"
        result = self.run_shell(
            MOCKS
            + r"""
            WORKDIR="$UTS_TEST_ROOT/tmp"
            root_crontab() {
              QUERY_OUTPUT="$(
              printf '%s\n' "*/5 * * * * ${STARGATE_DIRS[0]}/admin/start.sh > /dev/null 2>&1 &"
              printf '%s\n' "*/5 * * * * ${MONITOR_DIRS[0]}/barad/admin/trystart.sh"
              printf '%s\n' "0 3 * * * /usr/local/bin/backup.sh"
              printf '%s\n' "*/5 * * * * /usr/local/qcloud/monitor/other/agent.sh"
              )"
            }
            stop_agent_processes() { record "processes $*"; }
            uninstall_monitor
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        scanned = " ".join(
            f"{self.root}/{path}"
            for path in (
                "usr/local/qcloud/stargate",
                "var/lib/qcloud/stargate",
                "usr/local/qcloud/monitor/barad",
                "var/lib/qcloud/monitor/barad",
            )
        )
        events = self.events.read_text().splitlines()
        self.assertEqual(events[:3], [
            f"run crontab -u root {self.root}/tmp/crontab",
            f"run rm -f -- {task}",
            f"processes {scanned}",
        ])
        self.assertEqual(
            (self.root / "tmp/crontab").read_text(),
            f"0 3 * * * /usr/local/bin/backup.sh\n{other}\n",
        )
        self.assertEqual(result.stdout.count("Removing root crontab line:"), 2)
        self.assertFalse(task.exists())

    def test_system_cron_lock_and_pid_files_are_removed(self):
        kept = (
            "# keep the backup schedule\n"
            "0 3 * * * root /opt/backup --exclude /usr/local/qcloud/stargate/admin/start.sh\n"
        )
        for mixed in (False, True):
            with self.subTest(mixed=mixed):
                leftovers = self.leftovers()
                task = leftovers[0]
                if mixed:
                    task.write_text(kept + "* * * * * root /usr/local/qcloud/stargate/admin/start.sh\n")
                result = self.run_shell(MOCKS + 'WORKDIR="$UTS_TEST_ROOT/tmp"\nuninstall_monitor')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                removed = leftovers[1:] if mixed else leftovers
                self.assertEqual(
                    [event for event in self.events.read_text().splitlines() if event.startswith("run rm")],
                    [f"run rm -f -- {path}" for path in removed],
                )
                for path in removed:
                    self.assertFalse(path.exists(), path)
                if mixed:
                    self.assertEqual(task.read_text(), kept)

    def test_monitor_process_sweep_ignores_symlinked_parent_directory(self):
        monitor = self.root / "usr/local/qcloud/monitor"
        monitor.parent.mkdir(parents=True)
        monitor.symlink_to(self.root)
        other = str(self.root / "usr/sbin/sshd")
        self.process(900001, other, [other])
        result = self.run_shell(
            r"""
            root_crontab() { :; }
            remove_path() { :; }
            remove_empty_dir() { :; }
            stop_agent_processes() { record "sweep $*"; agent_processes "$@" >/dev/null || return 1; }
            INCLUDE_MONITOR=1
            uninstall_monitor
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        sweep = [event for event in self.events.read_text().splitlines() if event.startswith("sweep ")]
        self.assertEqual(len(sweep), 1)
        self.assertNotIn(f" {monitor} ", sweep[0] + " ")
        self.assertIn(f"{monitor}/barad", sweep[0])


class ComponentTests(UtsFixture):
    def test_timeout_stops_component_cleanup_before_later_steps(self):
        self.enable_proc()
        for component in ("yunjing", "monitor"):
            for failing in ("remove_cron_lines", "remove_paths"):
                for status in (124, 137, 143):
                    with self.subTest(component=component, failing=failing, status=status):
                        result = self.run_shell(
                            "INCLUDE_MONITOR=1\n"
                            + "remove_cron_lines() { :; }\nremove_system_cron_lines() { :; }\n"
                            + "stop_agent_processes() { :; }\n"
                            + "remove_paths() { record remove; }\n"
                            + f"{failing}() {{ record timed-out; return {status}; }}\n"
                            + f"uninstall_{component}\n"
                        )
                        self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                        self.assertEqual(self.events.read_text().splitlines(), ["timed-out"])

    def test_optional_components_are_untouched_unless_selected(self):
        install_monitor(self.root, "usr/local/qcloud")
        (self.root / "etc/cron.d").mkdir()
        (self.root / "etc/cron.d/sgagenttask").write_text("* * * * * root /usr/local/qcloud/stargate/admin/start.sh\n")
        before = self.file_snapshot()
        result = self.run_shell(
            r"""
            remove_cron_lines() { record cron; }
            stop_agent_processes() { record "stop-processes $*"; }
            uninstall_monitor
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout + result.stderr, "")
        self.assertEqual(self.events.read_text(), "")
        self.assertEqual(before, self.file_snapshot())

    def test_cron_failure_stops_component_before_services(self):
        yunjing = self.root / "usr/local/qcloud/YunJing"
        yunjing.mkdir(parents=True)
        install_monitor(self.root, "usr/local/qcloud", scripts=False)
        before = self.file_snapshot()
        for component, functions in (
            ("yunjing", ("remove_cron_lines", "remove_system_cron_lines")),
            ("monitor", ("remove_cron_lines", "remove_system_cron_lines")),
        ):
            for function in functions:
                with self.subTest(component=component, function=function):
                    result = self.run_shell(
                        r"""
                        INCLUDE_MONITOR=1
                        root_crontab() { :; }
                        stop_agent_processes() { record processes; }
                        """
                        + f'{function}() {{ record "failed {function}"; return 1; }}\n'
                        + f"uninstall_{component}\n"
                    )
                    self.assert_failed(result)
                    self.assertEqual(self.events.read_text(), f"failed {function}\n")
                    self.assertEqual(before, self.file_snapshot())

    def test_selection_helpers_follow_component_flags(self):
        yunjing_dirs = [
            self.root / "usr/local/qcloud/YunJing",
            self.root / "var/lib/qcloud/YunJing",
            self.root / "run/ydeyes",
            self.root / "var/run/ydeyes",
        ]
        monitor_dirs = [
            self.root / "usr/local/qcloud/stargate",
            self.root / "var/lib/qcloud/stargate",
            self.root / "usr/local/qcloud/monitor/barad",
            self.root / "var/lib/qcloud/monitor/barad",
        ]
        yunjing_leftovers = [
            self.root / "tmp/yunjing_install",
            self.root / "tmp/yunjing_install_nohup",
        ]
        monitor_leftovers = [
            self.root / "tmp/stargate.lock",
            self.root / "var/run/stargate.tencentyun.pid",
            self.root / "var/run/stargat.tencentyun.pid",
        ]
        for monitor in (0, 1):
            with self.subTest(monitor=monitor):
                result = self.run_shell(
                    f"INCLUDE_MONITOR={monitor}\n"
                    "selected_dirs; echo ---; selected_leftovers"
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stderr, "")
                dirs, leftovers = (
                    part.splitlines() for part in result.stdout.split("---\n")
                )
                self.assertEqual(dirs, [str(path) for path in yunjing_dirs + monitor_dirs * monitor])
                self.assertEqual(
                    leftovers,
                    [str(path) for path in yunjing_leftovers + monitor_leftovers * monitor],
                )
