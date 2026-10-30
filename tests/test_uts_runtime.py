import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time
import unittest

from test_uts import BASH, UtsFixture


GNU_TIMEOUT = shutil.which("timeout") or shutil.which("gtimeout")
if GNU_TIMEOUT:
    try:
        version = subprocess.run(
            [GNU_TIMEOUT, "--version"], capture_output=True, text=True, timeout=3
        )
        if version.returncode or not any(name in version.stdout for name in ("GNU coreutils", "uutils coreutils")):
            GNU_TIMEOUT = None
    except (OSError, subprocess.TimeoutExpired):
        GNU_TIMEOUT = None

FLOCK = shutil.which("flock")


@unittest.skipUnless(GNU_TIMEOUT, "GNU or uutils timeout is required for real runtime tests")
class RuntimeTests(UtsFixture):
    @unittest.skipUnless(FLOCK, "flock is required for the real lock test")
    def test_lock_rejects_inheriting_instance_and_releases_on_exit(self):
        proc = self.root / "proc/self"
        proc.mkdir()
        (proc / "mountinfo").touch()
        (proc / "status").touch()
        stat_override = r'''
            stat() {
              if [[ "$2" == %u:%a && "${@: -1}" == "$LOCK_DIR" ]]; then
                printf '0:700\n'
              else
                command stat "$@"
              fi
            }
        '''
        timeout_override = f"timeout() {{ {shlex.quote(GNU_TIMEOUT)} \"$@\"; }}\n"
        contender = self.base / "contender.sh"
        contender.write_text(
            f"source {shlex.quote(str(self.source))}\n"
            + stat_override
            + timeout_override
            + 'prepare_runtime\nprintf "lock acquired\\n"\n'
        )
        runtime = (
            self.real_function("prepare_runtime")
            + self.real_function("cleanup")
            + stat_override
            + timeout_override
            + "unset -f rm\n"
        )
        result = self.run_shell(
            runtime
            + 'prepare_runtime\nprintf "parent locked\\n"\n'
            + "child_rc=0\n"
            + f"{shlex.quote(BASH)} {shlex.quote(str(contender))} || child_rc=$?\n"
            + '[[ "$child_rc" -eq 1 ]]\n'
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("parent locked", result.stdout)
        self.assertNotIn("lock acquired", result.stdout)
        self.assertIn("[FAIL] 已有另一个 UTS 实例在运行。", result.stderr)
        self.assertFalse(list((self.root / "tmp").glob("uts.*")))

        reacquired = self.run_shell(runtime + 'prepare_runtime\nprintf "reacquired\\n"\n')
        self.assertEqual(reacquired.returncode, 0, reacquired.stdout + reacquired.stderr)
        self.assertIn("reacquired", reacquired.stdout)
        self.assertFalse(list((self.root / "tmp").glob("uts.*")))

    def test_real_timeout_stops_command_and_clears_run_pid(self):
        binaries = self.base / "bin"
        binaries.mkdir()
        timeout_pid = self.base / "timeout.pid"
        child_pid = self.base / "child.pid"
        timeout_shim = binaries / "timeout"
        timeout_shim.write_text(
            "#!/bin/sh\n"
            + f'printf "%s\\n" "$$" > {shlex.quote(str(timeout_pid))}\n'
            + f'exec {shlex.quote(GNU_TIMEOUT)} "$@"\n'
        )
        timeout_shim.chmod(0o755)
        body = (
            f"source {shlex.quote(str(self.source))}\n"
            + "COMMAND_TIMEOUT=1\nrc=0\n"
            + f"run {shlex.quote(BASH)} -c "
            + shlex.quote('printf "%s\\n" "$$" > "$1"; exec sleep 30')
            + f" uts-runtime {shlex.quote(str(child_pid))} || rc=$?\n"
            + 'printf "status=%s\\nrun_pid=%s\\n" "$rc" "$RUN_PID"\n'
            + '[[ "$rc" -eq 124 && -z "$RUN_PID" ]]\n'
        )
        process = subprocess.Popen(
            [BASH, "--noprofile", "--norc", "-c", body],
            cwd=self.base,
            env={
                "PATH": str(binaries) + os.pathsep + os.environ.get("PATH", os.defpath),
                "HOME": str(self.base),
                "TMPDIR": str(self.root / "tmp"),
                "NO_COLOR": "1",
                "TERM": "dumb",
                "LC_ALL": "C",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertIn("status=124\nrun_pid=\n", stdout)
            self.assertIn("[WARN] 命令失败（124）：", stderr)
            self.assertTrue(child_pid.is_file())
            with self.assertRaises(ProcessLookupError):
                os.kill(int(child_pid.read_text()), 0)
        finally:
            owned_pids = [process.pid]
            for pidfile in (timeout_pid, child_pid):
                if pidfile.is_file():
                    value = pidfile.read_text().strip()
                    if value.isdecimal():
                        owned_pids.append(int(value))
            for pid in owned_pids:
                try:
                    if os.getsid(pid) == process.pid:
                        os.killpg(os.getpgid(pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=3)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()


@unittest.skipUnless(GNU_TIMEOUT, "GNU or uutils timeout is required")
class InterruptTests(UtsFixture):
    def test_interrupt_stops_term_ignoring_child_and_preserves_bystander(self):
        self.check_interrupt_cleanup()

    def test_interrupt_stops_service_query_without_waiting_for_deadline(self):
        self.check_interrupt_cleanup(query=True)

    def test_interrupt_stops_child_with_timeout_kill_fallback(self):
        self.check_interrupt_cleanup(kill_after=False)

    def test_interrupt_stops_root_crontab_query_without_waiting_for_deadline(self):
        self.check_interrupt_cleanup(query="crontab")

    def check_interrupt_cleanup(self, query=False, kill_after=True):
        binaries = self.base / "bin"
        binaries.mkdir()
        timeout_pid = self.base / "timeout.pid"
        child_pid = self.base / "child.pid"
        workdir = self.root / "tmp/runtime-work"
        shim = binaries / "timeout"
        shim.write_text(
            "#!/bin/sh\n"
            + f'printf "%s\\n" "$$" > {shlex.quote(str(timeout_pid))}\n'
            + f'exec {shlex.quote(GNU_TIMEOUT)} "$@"\n'
        )
        shim.chmod(0o755)
        child_command = 'trap "" TERM; printf "%s\\n" "$$" > "$1"; exec sleep 30'
        body = (
            f"source {shlex.quote(str(self.source))}\n"
            + f"WORKDIR={shlex.quote(str(workdir))}\n"
            + 'mkdir "$WORKDIR"\n'
            + "trap cleanup EXIT\ntrap 'interrupt 143' TERM\n"
            + "COMMAND_TIMEOUT=6\nSTEP=1\n"
            + ("TIMEOUT_KILL=(-k 1)\n" if kill_after else "TIMEOUT_KILL=(-s KILL)\n")
        )
        if query:
            self.vendor_script(
                binaries / ("crontab" if query == "crontab" else "systemctl"),
                f"#!{BASH}\n"
                + 'trap "" TERM\n'
                + f'printf "%s\\n" "$$" > {shlex.quote(str(child_pid))}\n'
                + "exec sleep 30\n",
            )
            body += (
                "remove_cron_lines /qcloud/YunJing/\n"
                if query == "crontab" else "prepare_service_stop tat_agent.service\n"
            )
        else:
            body += (
                f"run {shlex.quote(BASH)} -c {shlex.quote(child_command)} "
                + f"uts-interrupt {shlex.quote(str(child_pid))}\n"
            )
        environment = {
            "PATH": str(binaries) + os.pathsep + os.environ.get("PATH", os.defpath),
            "HOME": str(self.base),
            "TMPDIR": str(self.root / "tmp"),
            "NO_COLOR": "1",
            "TERM": "dumb",
            "LC_ALL": "C",
        }
        bystander = subprocess.Popen(
            ["sleep", "30"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        process = None
        try:
            process = subprocess.Popen(
                [BASH, "--noprofile", "--norc", "-c", body],
                cwd=self.base,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            deadline = time.monotonic() + 3
            child = None
            while time.monotonic() < deadline and process.poll() is None:
                if child_pid.is_file():
                    value = child_pid.read_text().strip()
                    if value.isdecimal():
                        child = int(value)
                        break
                time.sleep(0.02)
            self.assertIsNotNone(child, "Managed child did not start")
            self.assertEqual(os.getsid(child), process.pid)
            interrupted_at = time.monotonic()
            os.kill(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
            self.assertLess(time.monotonic() - interrupted_at, 3, "Interrupt waited for the command deadline")
            self.assertEqual(process.returncode, 143, stdout + stderr)
            self.assertIn("[WARN] 已中断，重新运行本脚本即可继续清理。", stderr)
            self.assertFalse(workdir.exists())
            self.assertIsNone(bystander.poll(), "Unrelated process received a signal")
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    os.kill(child, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.02)
            else:
                self.fail("Managed child survived interrupt escalation")
        finally:
            if process is not None:
                owned_pids = [process.pid]
                for pidfile in (timeout_pid, child_pid):
                    if pidfile.is_file() and pidfile.read_text().strip().isdecimal():
                        owned_pids.append(int(pidfile.read_text()))
                for pid in owned_pids:
                    try:
                        if os.getsid(pid) == process.pid:
                            os.killpg(os.getpgid(pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait(timeout=3)
                if process.stdout:
                    process.stdout.close()
                if process.stderr:
                    process.stderr.close()
            if bystander.poll() is None:
                bystander.terminate()
            bystander.wait(timeout=3)


@unittest.skipUnless(sys.platform == "linux" and os.geteuid() == 0, "Linux root /proc access is required")
class LinuxProcessTests(UtsFixture):
    def setUp(self):
        super().setUp()
        self.installation = self.root / "usr/local/qcloud/YunJing"
        self.installation.mkdir(parents=True)
        self.sleep = shutil.which("sleep")
        if not self.sleep:
            self.skipTest("sleep is required")

    def spawn(self, arguments, cwd=None):
        process = subprocess.Popen(
            arguments,
            cwd=cwd or self.base,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env={
                "PATH": os.environ.get("PATH", os.defpath),
                "HOME": str(self.base),
                "TMPDIR": str(self.root / "tmp"),
                "LC_ALL": "C",
            },
        )
        self.addCleanup(self.cleanup_process, process)
        return process

    def cleanup_process(self, process):
        try:
            if process.poll() is None:
                self.assertEqual(os.getsid(process.pid), process.pid)
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
        except ProcessLookupError:
            process.wait(timeout=2)
        finally:
            if process.stdin:
                process.stdin.close()

    def stop_agents(self, agent, bystander):
        result = self.run_shell(
            f'''
            PROC_ROOT=/proc
            unset -f sleep
            kill() {{
              case "${{@: -1}}" in
                {agent.pid}|{bystander.pid}) record "signal $*"; command kill "$@" ;;
                *) blocked kill "$@"; return 97 ;;
              esac
            }}
            targets="$(agent_processes "${{YUNJING_DIRS[@]}}")" || exit $?
            printf '%s\\n' "$targets"
            stop_agent_processes "${{YUNJING_DIRS[@]}}"
            '''
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            [line.split("|", 1)[0] for line in result.stdout.splitlines()],
            [str(agent.pid)],
        )
        self.assertIsNotNone(agent.poll(), "The selected agent is still running")
        self.assertIsNone(bystander.poll(), "An unrelated process was stopped")
        self.assertEqual(self.events.read_text(), f"signal -TERM {agent.pid}\n")

    def spawn_agent(self):
        binary = self.installation / "YDService"
        shutil.copyfile(os.path.realpath(BASH), binary)
        binary.chmod(0o755)
        agent = self.spawn([str(binary), "-c", "while :; do read -r -t 1 line || :; done"])
        deadline = time.monotonic() + 2
        while os.readlink(f"/proc/{agent.pid}/exe") != str(binary):
            self.assertLess(time.monotonic(), deadline, "The agent did not start")
            time.sleep(0.01)
        return binary, agent

    def test_real_agent_stop_preserves_spoofed_argv_bystander(self):
        binary, agent = self.spawn_agent()
        bystander = self.spawn(
            [BASH, "-c", 'exec -a "$1" "$2" 30', "uts-test", str(binary), self.sleep]
        )
        deadline = time.monotonic() + 2
        while (
            os.readlink(f"/proc/{bystander.pid}/exe") != os.path.realpath(self.sleep)
            or Path(f"/proc/{bystander.pid}/cmdline").read_bytes().split(b"\0")[0] != os.fsencode(binary)
        ):
            self.assertLess(time.monotonic(), deadline, "The bystander did not exec sleep with a spoofed argv")
            time.sleep(0.01)
        self.stop_agents(agent, bystander)

    def test_deleted_agent_executable_is_still_stopped(self):
        binary, agent = self.spawn_agent()
        bystander = self.spawn([self.sleep, "30"])
        binary.unlink()
        self.assertTrue(os.readlink(f"/proc/{agent.pid}/exe").endswith(" (deleted)"))
        self.stop_agents(agent, bystander)

    def test_relative_agent_script_preserves_external_symlink_script(self):
        body = "trap 'exit 0' TERM\nwhile :; do read -r -t 1 line || :; done\n"
        script = self.installation / "startYD.sh"
        script.write_text(body)
        outside = self.base / "other/start.sh"
        outside.parent.mkdir()
        outside.write_text(body)
        link = self.installation / "start.sh"
        link.symlink_to(outside)
        agent = self.spawn([BASH, "./startYD.sh"], cwd=self.installation)
        bystander = self.spawn([BASH, str(link)])
        self.stop_agents(agent, bystander)
        self.assertEqual(outside.read_text(), body)
        self.assertTrue(link.is_symlink())
