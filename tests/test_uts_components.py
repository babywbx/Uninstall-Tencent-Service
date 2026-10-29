import os
import re
import shlex
import shutil
import sys

from test_uts import EXECUTE, ROOT_STAT, UtsFixture


TAT_SERVICES = "tat_agent tat_agent_service tat_install"
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
    stop_services() { record "stop-services $*"; }
    stop_agent_processes() { record stop-processes; }
    cleanup_services() { record "cleanup-services $*"; }
    remove_path() { record "remove $1"; }
    remove_empty_dir() { record "remove-empty $1"; }
    INCLUDE_MONITOR=1
"""


def install_monitor(root, prefix, scripts=True):
    monitor = root / prefix / "monitor"
    stargate = root / prefix / "stargate"
    for directory in (monitor / "barad/admin", monitor / "python26/bin", stargate / "admin", stargate / "bin"):
        directory.mkdir(parents=True, exist_ok=True)
    (monitor / "python26/bin/python").touch()
    (monitor / "config").mkdir(exist_ok=True)
    (monitor / "config/proc_conf.ini").write_text("[process]\nproclist = \n")
    (stargate / "bin/sgagent64").touch()
    if scripts:
        for script in (monitor / "barad/admin/uninstall.sh", stargate / "admin/uninstall.sh"):
            script.write_text("#!/bin/bash\nexit 0\n")
            script.chmod(0o755)
    return monitor, stargate


def tat_paths(root):
    directories = [root / "usr/local/qcloud/tat_agent", root / "var/lib/qcloud/tat_agent"]
    leftovers = [
        root / "usr/local/bin/tat_agent",
        root / "usr/sbin/tat_agent",
        root / "opt/bin/tat_agent",
        root / "var/run/tat_agent.pid",
    ]
    return directories, leftovers


def install_tat(root):
    directories, leftovers = tat_paths(root)
    for directory in directories:
        directory.mkdir(parents=True)
        (directory / "tat_agent").write_bytes(b"ELF")
    for link in leftovers[:3]:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(directories[0] / "tat_agent")
    leftovers[3].write_text("4242\n")
    return directories, leftovers


class YunjingTests(UtsFixture):
    local_stubs = r"""
        stop_services() { record "stop-services $*"; }
        stop_agent_processes() { record "stop-processes $*"; }
        cleanup_services() { record "cleanup-services $*"; }
        root_crontab() { :; }
        remove_path() { record "remove $1"; }
        guard_check() { record guard; }
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
            "stop-services YDService YDLive",
            f"stop-processes {self.primary} {self.fallback}",
            "cleanup-services YDService YDLive",
            *self.removals(),
            "guard",
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
            uninstall_tat() { record tat; }
            verify_uninstall() { record verify; }
            main --yes --include-monitor --include-tat
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text().splitlines()[-3:], ["monitor", "tat", "verify"])
        self.assertIn("local uninstall verified", result.stdout)

    def test_official_uninstaller_failure_stops_local_cleanup(self):
        cases = (
            ("unknown format", b"MZ\x90\x00 not a shell script\n", 1, "Unrecognized uninstaller format", False),
            ("not executable", None, 1, "Empty or non-executable uninstaller", False),
            ("empty", b"", 1, "Empty or non-executable uninstaller", False),
            ("symlink", None, 1, "Missing or symlinked uninstaller", False),
            ("broken binary", ELF, None, "Command failed (", True),
            ("exit 7", b"#!/bin/bash\nexit 7\n", 7, "Command failed (7)", True),
            ("exit 124", b"#!/bin/bash\nexit 124\n", 124, "Command failed (124)", True),
            ("exit 137", b"#!/bin/bash\nexit 137\n", 137, "Command failed (137)", True),
        )
        for name, payload, status, warning, executed in cases:
            with self.subTest(case=name):
                self.reset()
                script = self.primary / "uninst.sh"
                if name == "symlink":
                    self.primary.mkdir(parents=True)
                    script.symlink_to(self.vendor_script(self.base / "target.sh", self.logging_script("target")))
                elif name == "not executable":
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
                self.assertTrue(script.is_symlink() or script.is_file())

    def test_untrusted_official_uninstaller_is_skipped_for_local_cleanup(self):
        script = self.vendor_script(self.primary / "uninst.sh", self.logging_script("untrusted"))
        for untrusted, field, value in (
            (script, "%u", "501"),
            (script, "%a", "666"),
            (self.primary, "%u", "1000"),
            (self.primary, "%a", "775"),
        ):
            with self.subTest(path=untrusted.name, field=field):
                result = self.run_shell(
                    self.local_stubs
                    + EXECUTE
                    + self.real_function("run")
                    + f"""
                    stat() {{
                      if [[ "${{@: -1}}" == {shlex.quote(str(untrusted))} && "$2" == {field} ]]; then
                        printf '{value}\\n'
                        return 0
                      fi
                      case "$2" in
                        %u) printf '0\\n' ;;
                        %a) printf '755\\n' ;;
                        *) return 1 ;;
                      esac
                    }}
                    uninstall_yunjing
                    """
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(self.vendor_log.exists())
                self.assertIn(f"Skipping official {script}: {untrusted} is not a root-owned", result.stderr)
                self.assertEqual(self.events.read_text().splitlines(), self.local_cleanup())

    def test_skipped_untrusted_uninstaller_does_not_block_running_agent_cleanup(self):
        self.vendor_script(self.primary / "uninst.sh", self.logging_script("untrusted"))
        agent = str(self.primary / "YDEyes/YDService")
        self.process(4242, agent, [agent])
        result = self.run_shell(
            self.local_stubs
            + EXECUTE
            + self.real_function("run")
            + ROOT_STAT.replace("755", "666")
            + "uninstall_yunjing\n"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.vendor_log.exists())
        self.assertEqual(self.events.read_text().splitlines(), self.local_cleanup())

    def test_kernel_protection_blocks_cleanup_after_official_uninstaller(self):
        self.enable_proc()
        for official, dry_run, expected in (
            (False, 0, 6),
            (True, 0, 6),
            (False, 1, 6),
            (True, 1, 0),
        ):
            with self.subTest(official=official, dry_run=dry_run):
                self.reset()
                script = self.primary / "uninst.sh"
                if official:
                    self.vendor_script(script, "#!/bin/bash\nexit 0\n")
                result = self.run_shell(
                    ROOT_STAT
                    + r"""
                    path_present() { [[ "$1" == /dev/txyd || -e "$1" || -L "$1" ]]; }
                    remove_cron_lines() { record cron; }
                    stop_services() { record stop; }
                    remove_path() { record "remove $1"; }
                    """
                    + f"exec 3>&1\nDRY_RUN={dry_run}\nuninstall_yunjing\n"
                )
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                events = self.events.read_text().splitlines()
                if expected:
                    self.assertIn("Kernel protection device /dev/txyd remains", result.stderr)
                    self.assertEqual(events, [self.official(script)] if official and not dry_run else [])
                else:
                    self.assertIn("the official uninstaller must remove it", result.stderr)
                    self.assertIn(f"[DRY-RUN] would run official {script}", result.stdout)
                    self.assertEqual(events, ["cron", "stop", *self.removals()])

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
        expected = ["stop-services YDService YDLive", "signal TERM"] + ["sleep 1"] * 5
        self.assertEqual(self.events.read_text().splitlines(), expected)
        self.assertIn("Agent processes remain; preserving their installation files:", result.stderr)
        self.assertIn("PID 4242", result.stderr)
        self.assertIn("turn off Agent uninstall protection", result.stderr)

    def test_guard_check_polls_every_five_seconds_for_a_minute(self):
        self.enable_proc()
        for dry_run in (0, 1):
            with self.subTest(dry_run=dry_run):
                result = self.run_shell(f"DRY_RUN={dry_run}\nguard_check\n")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.events.read_text().splitlines(), ["sleep 5"] * 12 * (1 - dry_run))
                self.assertEqual("Watching 60s for uninstall protection" in result.stdout, not dry_run)
                self.assertEqual(result.stderr, "")

    def test_guard_detects_existing_remnants_on_first_poll(self):
        self.primary.mkdir(parents=True)
        result = self.run_shell("guard_check\n")
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text().splitlines(), ["sleep 5"])

    def test_guard_detects_process_reappearing_on_final_check(self):
        result = self.run_shell(
            r"""
            polls=0
            sleep() { record "sleep $*"; polls=$((polls + 1)); }
            agent_processes() { [[ "$polls" -lt 12 ]] || printf '4242|agent|1|args\n'; return 0; }
            guard_check
            """
        )
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text().splitlines(), ["sleep 5"] * 12)

    def test_dry_run_does_not_run_the_official_uninstaller(self):
        self.vendor_script(self.primary / "uninst.sh", self.logging_script("unexpected"))
        snapshot = self.file_snapshot()
        result = self.run_shell(self.executed("exec 3>&1\nDRY_RUN=1\n"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.file_snapshot(), snapshot)
        self.assertFalse(self.vendor_log.exists())
        self.assertNotIn("sleep ", self.events.read_text())

    def test_directory_reappearing_on_third_poll_returns_6(self):
        self.enable_proc()
        result = self.run_shell(
            r"""
            polls=0
            sleep() {
              record "sleep $*"
              polls=$((polls + 1))
              [[ "${polls}" -ne 3 ]] || mkdir -p -- "${YUNJING_DIRS[0]}"
            }
            guard_check
            """
        )
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text().splitlines(), ["sleep 5"] * 3)
        self.assertIn("Host Security agent files or processes reappeared after removal.", result.stderr)
        self.assertIn("turn off Agent uninstall protection", result.stderr)

    def test_guard_window_runs_only_when_agent_was_present(self):
        self.enable_proc()
        for present in (False, True):
            with self.subTest(present=present):
                if present:
                    self.primary.mkdir(parents=True)
                result = self.run_shell(
                    r"""
                    root_crontab() { :; }
                    stop_services() { :; }
                    cleanup_services() { :; }
                    remove_path() { command rmdir -- "$1" 2>/dev/null || true; }
                    uninstall_yunjing
                    """
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual("Watching 60s for uninstall protection" in result.stdout, present)
                self.assertEqual(self.events.read_text().count("sleep 5"), 12 if present else 0)
                self.assertFalse(self.primary.exists())

    def test_yunjing_present_detects_directory_or_process(self):
        self.enable_proc()
        agent = str(self.primary / "YDService")
        self.assertEqual(self.run_shell("yunjing_present").returncode, 1)
        proc = self.process(900001, agent, [agent])
        self.assertEqual(self.run_shell("yunjing_present").returncode, 0)
        shutil.rmtree(proc)
        self.primary.mkdir(parents=True)
        self.assertEqual(self.run_shell("yunjing_present").returncode, 0)

    def test_process_scan_failure_returns_2(self):
        with self.subTest(stage="initial"):
            result = self.run_shell(
                r"""
                agent_processes() { record scan-failed; return 1; }
                stop_services() { record stop; }
                uninstall_yunjing
                """
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertEqual(self.events.read_text(), "scan-failed\n")
        with self.subTest(stage="guard"):
            (self.primary / "agent").parent.mkdir(parents=True)
            (self.primary / "agent").touch()
            self.enable_proc()
            result = self.run_shell(
                r"""
                stat() {
                  case "$2" in
                    %u) printf '0\n' ;;
                    %a) printf '700\n' ;;
                    *) return 1 ;;
                  esac
                }
                stop_services() { :; }
                stop_agent_processes() { :; }
                cleanup_services() { :; }
                root_crontab() { :; }
                agent_processes() { record scan-failed; return 1; }
                run() { record "run $*"; command "$@"; }
                GUARD_WAIT=60
                uninstall_yunjing
                """
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertFalse(self.primary.exists())
            self.assertIn("scan-failed", self.events.read_text())


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
                "usr/bin/cagent_tools",
                "opt/bin/cagent_tools",
                "var/run/stargate.tencentyun.pid",
                "var/run/stargat.tencentyun.pid",
            )
        ]
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.name == "cagent_tools":
                path.unlink(missing_ok=True)
                path.symlink_to("/usr/local/qcloud/monitor/barad/client/cagent_tools.py")
            else:
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
                    events[:5],
                    [
                        self.official(barad, "timeout -k 5 120"),
                        self.official(sgagent, "timeout -k 5 120"),
                        "stop-services stargate",
                        "stop-processes",
                        "cleanup-services stargate",
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
                    f"run rm -rf -- {monitor}/python26",
                    f"run rm -rf -- {monitor}/config",
                    f"run rmdir -- {monitor}",
                ])
                self.assertFalse(monitor.exists())
                self.assertFalse(stargate.exists())

    def test_non_root_owned_vendor_tree_is_cleaned_locally(self):
        stargate = self.root / "usr/local/qcloud/stargate"
        self.vendor_script(stargate / "admin/uninstall.sh", self.logging_script("sgagent"))
        result = self.run_shell(
            self.real_function("preflight_paths")
            + EXECUTE
            + r"""
            stat() { printf '501\n'; }
            root_crontab() { :; }
            stop_services() { :; }
            run() {
              record "run $*"
              case "$1" in rm|rmdir) command "$@" ;; esac
            }
            INCLUDE_MONITOR=1
            preflight_paths || exit 97
            uninstall_monitor
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(stargate.exists())
        self.assertFalse(self.vendor_log.exists())
        self.assertIn("not owned by root", result.stderr)
        self.assertIn(f"Skipping official Sgagent {stargate}/admin/uninstall.sh", result.stderr)
        self.assertEqual(self.events.read_text(), f"run rm -rf -- {stargate}\n")

    def test_stale_barad_copy_and_config_are_removed_with_the_monitor_root(self):
        monitor, stargate = install_monitor(self.root, "usr/local/qcloud", scripts=False)
        stale = monitor / "barad.STARGATE/client"
        stale.mkdir(parents=True)
        (stale / "cagent_tools.py").write_text("# stale copy\n")
        result = self.run_shell(MOCKS + "uninstall_monitor")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text().splitlines(), [
            f"run rm -rf -- {stargate}",
            f"run rm -rf -- {monitor}/barad",
            f"run rm -rf -- {monitor}/barad.STARGATE",
            f"run rm -rf -- {monitor}/python26",
            f"run rm -rf -- {monitor}/config",
            f"run rmdir -- {monitor}",
        ])
        self.assertFalse(monitor.exists())

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
                self.assertIn(f"run rm -rf -- {monitor}/python26\n", events)
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
              printf '%s\n' "*/5 * * * * flock -xn /tmp/stargate.lock -c '${STARGATE_DIRS[0]}/admin/start.sh > /dev/null 2>&1 &'"
              printf '%s\n' "*/5 * * * * ${MONITOR_DIRS[0]}/barad/admin/trystart.sh"
              printf '%s\n' "0 3 * * * /usr/local/bin/backup.sh"
              printf '%s\n' "*/5 * * * * /usr/local/qcloud/monitor/other/agent.sh"
              )"
            }
            stop_services() { record "services $*"; }
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
                "usr/local/qcloud/monitor/barad.STARGATE",
                "usr/local/qcloud/monitor/python26",
                "usr/local/qcloud/monitor/config",
                "var/lib/qcloud/monitor/barad",
                "var/lib/qcloud/monitor/barad.STARGATE",
                "var/lib/qcloud/monitor/python26",
                "var/lib/qcloud/monitor/config",
            )
        )
        events = self.events.read_text().splitlines()
        self.assertEqual(events[:4], [
            f"run crontab -u root {self.root}/tmp/crontab",
            f"run rm -f -- {task}",
            "services stargate",
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

    def test_monitor_reloads_systemd_before_stopping_services(self):
        script = self.vendor_script(self.root / "usr/local/qcloud/stargate/admin/uninstall.sh", "#!/bin/bash\nexit 0\n")
        (self.root / "run/systemd/system").mkdir(parents=True)
        result = self.run_shell(
            r"""
            stat() { printf '0\n'; }
            root_crontab() { :; }
            command_exists() { [[ "$1" == systemctl ]]; }
            stop_services() { record "stop $*"; }
            stop_agent_processes() { :; }
            cleanup_services() { :; }
            remove_path() { :; }
            remove_empty_dir() { :; }
            INCLUDE_MONITOR=1
            uninstall_monitor
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.events.read_text().splitlines(),
            [self.official(script), "run systemctl daemon-reload", "stop stargate"],
        )

    def test_monitor_process_sweep_ignores_symlinked_parent_directory(self):
        monitor = self.root / "usr/local/qcloud/monitor"
        monitor.parent.mkdir(parents=True)
        monitor.symlink_to(self.root)
        other = str(self.root / "usr/sbin/sshd")
        self.process(900001, other, [other])
        result = self.run_shell(
            r"""
            root_crontab() { :; }
            stop_services() { :; }
            cleanup_services() { :; }
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
        self.assertIn(f"{monitor}/python26", sweep[0])


class TatTests(UtsFixture):
    def setUp(self):
        super().setUp()
        self.tat_dirs, self.leftovers = tat_paths(self.root)

    def install_service_files(self):
        unit = self.root / "etc/systemd/system/tat_agent.service"
        wants = unit.parent / "multi-user.target.wants/tat_agent.service"
        init_script = self.root / "etc/init.d/tat_agent_service"
        upstart = self.root / "etc/init/tat_agent_service.conf"
        wants.parent.mkdir(parents=True)
        unit.write_text("[Service]\nExecStart=/usr/local/qcloud/tat_agent/tat_agent\n")
        wants.symlink_to(unit)
        init_script.parent.mkdir()
        init_script.write_text("#!/bin/bash\n")
        upstart.parent.mkdir()
        upstart.write_text("respawn\n")
        return [unit, wants, init_script, upstart]

    def test_selected_tat_stops_services_and_processes_then_removes_files(self):
        install_tat(self.root)
        files = self.install_service_files()
        for pid, directory in zip((900001, 900002), self.tat_dirs):
            binary = str(directory / "tat_agent")
            self.process(pid, binary, [binary])
        self.enable_proc()
        result = self.run_shell(
            r"""
            stat() { printf '0\n'; }
            root_crontab() { :; }
            stop_services() { record "stop-services $*"; }
            kill() { record "kill $*"; mv "${UTS_TEST_ROOT}/proc/$2" "${UTS_TEST_ROOT}/tmp/exited-$2"; }
            run() { record "run $*"; [[ "$1" != rm ]] || command "$@"; }
            INCLUDE_TAT=1
            uninstall_tat
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("==> TencentCloud Automation Tools (tat_agent)", result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            self.events.read_text().splitlines(),
            [
                f"stop-services {TAT_SERVICES}",
                "kill -TERM 900001",
                "kill -TERM 900002",
                "sleep 1",
                *(f"run rm -f -- {path}" for path in files),
                *(f"run rm -rf -- {path}" for path in self.tat_dirs),
                *(f"run rm -f -- {path}" for path in self.leftovers),
            ],
        )
        for path in (*self.tat_dirs, *files, *self.leftovers):
            self.assertFalse(path.exists() or path.is_symlink(), path)

    def test_failed_leftover_removal_fails_tat(self):
        install_tat(self.root)
        self.enable_proc()
        result = self.run_shell(
            r"""
            stat() { printf '0\n'; }
            root_crontab() { :; }
            stop_services() { :; }
            stop_agent_processes() { :; }
            cleanup_services() { :; }
            run() {
              record "run $*"
              [[ "$*" != *tat_agent.pid ]] || return 1
              [[ "$1" != rm ]] || command "$@"
            }
            INCLUDE_TAT=1
            uninstall_tat
            """
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(
            self.events.read_text().splitlines(),
            [
                *(f"run rm -rf -- {path}" for path in self.tat_dirs),
                *(f"run rm -f -- {path}" for path in self.leftovers),
            ],
        )
        self.assertTrue(self.leftovers[3].exists())
        for path in (*self.tat_dirs, *self.leftovers[:3]):
            self.assertFalse(path.exists() or path.is_symlink(), path)

    def test_tat_self_update_workdir_is_removed_only_when_root_owned(self):
        workdir = self.root / "tmp/tat_agent/self_update/agent_update_unzip"
        for owner, mode, removed in (("0", "755", True), ("1000", "755", False), ("0", "777", False)):
            with self.subTest(owner=owner, mode=mode):
                workdir.mkdir(parents=True, exist_ok=True)
                (workdir / "tat_agent").write_bytes(b"ELF")
                self.enable_proc()
                result = self.run_shell(
                    f"""
                    stat() {{
                      case "$2" in
                        %u) printf '{owner}\\n' ;;
                        %a) printf '{mode}\\n' ;;
                        *) return 1 ;;
                      esac
                    }}
                    root_crontab() {{ :; }}
                    stop_services() {{ :; }}
                    stop_agent_processes() {{ :; }}
                    cleanup_services() {{ :; }}
                    run() {{ record "run $*"; [[ "$1" != rm ]] || command "$@"; }}
                    INCLUDE_TAT=1
                    uninstall_tat
                    """
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                tree = self.root / "tmp/tat_agent"
                self.assertEqual(self.events.read_text(), f"run rm -rf -- {tree}\n" if removed else "")
                self.assertEqual(tree.exists(), not removed)
                self.assertEqual("Keeping" in result.stderr, not removed)

    def test_tat_cron_is_removed_before_stopping_and_other_jobs_are_kept(self):
        result = self.run_shell(
            r'''
            WORKDIR="$UTS_TEST_ROOT/tmp"
            INCLUDE_TAT=1
            root_crontab() {
              QUERY_OUTPUT="$(
              printf '%s\n' '* * * * * /usr/local/qcloud/tat_agent/install/init_install.sh'
              printf '%s\n' '0 3 * * * /usr/local/bin/backup.sh'
              )"
            }
            stop_services() { record stop; }
            stop_agent_processes() { :; }
            cleanup_services() { :; }
            uninstall_tat
            '''
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        written = self.root / "tmp/crontab"
        self.assertEqual(written.read_text(), "0 3 * * * /usr/local/bin/backup.sh\n")
        self.assertEqual(
            self.events.read_text().splitlines(),
            [f"run crontab -u root {written}", "stop"],
        )


class ComponentTests(UtsFixture):
    def test_timeout_stops_component_cleanup_before_later_steps(self):
        self.enable_proc()
        for component in ("yunjing", "monitor", "tat"):
            for failing in ("remove_cron_lines", "cleanup_services", "remove_paths"):
                for status in (124, 137, 143):
                    with self.subTest(component=component, failing=failing, status=status):
                        result = self.run_shell(
                            "INCLUDE_MONITOR=1\nINCLUDE_TAT=1\n"
                            + "remove_cron_lines() { :; }\nremove_system_cron_lines() { :; }\n"
                            + "stop_services() { :; }\nstop_agent_processes() { :; }\n"
                            + "cleanup_services() { :; }\nremove_paths() { record remove; }\n"
                            + f"{failing}() {{ record timed-out; return {status}; }}\n"
                            + f"uninstall_{component}\n"
                        )
                        self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                        self.assertEqual(self.events.read_text().splitlines(), ["timed-out"])

    def test_optional_components_are_untouched_unless_selected(self):
        install_monitor(self.root, "usr/local/qcloud")
        install_tat(self.root)
        (self.root / "etc/cron.d").mkdir()
        (self.root / "etc/cron.d/sgagenttask").write_text("* * * * * root /usr/local/qcloud/stargate/admin/start.sh\n")
        before = self.file_snapshot()
        for component in ("monitor", "tat"):
            with self.subTest(component=component):
                result = self.run_shell(
                    r"""
                    remove_cron_lines() { record cron; }
                    stop_services() { record "stop-services $*"; }
                    stop_agent_processes() { record "stop-processes $*"; }
                    cleanup_services() { record "cleanup-services $*"; }
                    """
                    + f"uninstall_{component}\n"
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout + result.stderr, "")
                self.assertEqual(self.events.read_text(), "")
                self.assertEqual(before, self.file_snapshot())

    def test_cron_failure_stops_component_before_services(self):
        yunjing = self.root / "usr/local/qcloud/YunJing"
        yunjing.mkdir(parents=True)
        install_monitor(self.root, "usr/local/qcloud", scripts=False)
        install_tat(self.root)
        before = self.file_snapshot()
        for component, functions in (
            ("yunjing", ("remove_cron_lines", "remove_system_cron_lines")),
            ("monitor", ("remove_cron_lines", "remove_system_cron_lines")),
            ("tat", ("remove_cron_lines",)),
        ):
            for function in functions:
                with self.subTest(component=component, function=function):
                    result = self.run_shell(
                        r"""
                        INCLUDE_MONITOR=1
                        INCLUDE_TAT=1
                        root_crontab() { :; }
                        stop_services() { record stop; }
                        stop_agent_processes() { record processes; }
                        """
                        + f'{function}() {{ record "failed {function}"; return 1; }}\n'
                        + f"uninstall_{component}\n"
                    )
                    self.assert_failed(result)
                    self.assertEqual(self.events.read_text(), f"failed {function}\n")
                    self.assertEqual(before, self.file_snapshot())

    def test_selection_helpers_follow_component_flags(self):
        tat_dirs, tat_leftovers = tat_paths(self.root)
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
            self.root / "usr/local/qcloud/monitor/barad.STARGATE",
            self.root / "usr/local/qcloud/monitor/python26",
            self.root / "usr/local/qcloud/monitor/config",
            self.root / "var/lib/qcloud/monitor/barad",
            self.root / "var/lib/qcloud/monitor/barad.STARGATE",
            self.root / "var/lib/qcloud/monitor/python26",
            self.root / "var/lib/qcloud/monitor/config",
        ]
        yunjing_leftovers = [
            self.root / "tmp/yunjing_install",
            self.root / "tmp/yunjing_install_nohup",
        ]
        monitor_leftovers = [
            self.root / "tmp/stargate.lock",
            self.root / "usr/bin/cagent_tools",
            self.root / "opt/bin/cagent_tools",
            self.root / "var/run/stargate.tencentyun.pid",
            self.root / "var/run/stargat.tencentyun.pid",
            self.root / "var/run/.barad_agent.pid",
            self.root / "var/run/barad_agent.lock",
        ]
        for monitor in (0, 1):
            for tat in (0, 1):
                with self.subTest(monitor=monitor, tat=tat):
                    result = self.run_shell(
                        f"INCLUDE_MONITOR={monitor}\nINCLUDE_TAT={tat}\n"
                        "selected_dirs; echo ---; selected_services; echo ---; "
                        "selected_cron_patterns; echo ---; selected_leftovers"
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(result.stderr, "")
                    dirs, services, patterns, leftovers = (
                        part.splitlines() for part in result.stdout.split("---\n")
                    )
                    self.assertEqual(
                        dirs,
                        [str(path) for path in yunjing_dirs + monitor_dirs * monitor + tat_dirs * tat],
                    )
                    self.assertEqual(
                        services,
                        ["YDService", "YDLive"] + ["stargate"] * monitor + TAT_SERVICES.split() * tat,
                    )
                    self.assertEqual(
                        patterns,
                        ["/qcloud/YunJing/"]
                        + ["/qcloud/stargate/", "/qcloud/monitor/barad/", "/qcloud/monitor/python26/"] * monitor
                        + ["/qcloud/tat_agent/"] * tat,
                    )
                    self.assertEqual(
                        leftovers,
                        [str(path) for path in yunjing_leftovers + monitor_leftovers * monitor + tat_leftovers * tat],
                    )
