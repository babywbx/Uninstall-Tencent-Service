import os
import shlex
import shutil
import signal
import subprocess
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

    def test_real_timeout_stops_command(self):
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
            + 'printf "status=%s\\n" "$rc"\n'
            + '[[ "$rc" -eq 124 ]]\n'
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
            self.assertIn("status=124\n", stdout)
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
