import fcntl
import os
from pathlib import Path
import re
import select
import shlex
import shutil
import signal
import subprocess
import tempfile
import termios
import textwrap
import threading
import time
import unittest


SCRIPT = Path(os.environ.get("UTS_SCRIPT", Path(__file__).resolve().parents[1] / "UTS.sh"))
BASH = shutil.which("bash")
BASH_MAJOR = int(subprocess.run([BASH, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True).stdout or 0) if BASH else 0
ROOT_STAT = r"""
stat() {
  case "$2" in
    %u) printf '0\n' ;;
    %a) printf '755\n' ;;
    *) return 1 ;;
  esac
}
"""
EXECUTE = 'timeout() { record "timeout $*"; shift 3; "$@"; }\n'
CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")
MESSAGE_CALL = re.compile(
    r'(?:^|[\s;{(|&])(log|warn|die|ok|msg|preview|section|record_failure)'
    r'\s+("[^"]*")((?:\s+"[^"]*")*)'
)


@unittest.skipUnless(BASH, "Bash is required")
class UtsFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="uts-tests-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "root"
        self.root.mkdir()
        for directory in ("tmp", "etc", "proc", "sys", "run", "var", "var/lib", "var/run", "var/spool", "opt"):
            (self.root / directory).mkdir()
        (self.root / "etc/os-release").write_text(
            'ID=debian\nPRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n'
        )
        self.events = self.base / "events"
        self.events.touch()
        self.vendor_log = self.base / "vendor.log"
        source = SCRIPT.read_text()
        source = re.sub(
            r"(?<![A-Za-z0-9_/])/(etc|usr|opt|lib|lib64|proc|sys|run|var|tmp|sbin)(?=/|[\s\"'])",
            lambda match: f"{self.root}/{match[1]}",
            source,
        )
        self.source = self.base / "UTS.sh"
        self.source.write_text(source)

    def file_snapshot(self):
        return {
            str(path.relative_to(self.root)): (
                path.stat().st_mode,
                path.read_bytes() if path.is_file() else None,
            )
            for path in self.root.rglob("*")
        }

    def enable_proc(self):
        (self.root / "proc/self").mkdir(exist_ok=True)
        (self.root / "proc/self/status").touch()
        (self.root / "proc/self/mountinfo").touch()

    def process(self, pid, executable, arguments, started=100):
        proc = self.root / f"proc/{pid}"
        proc.mkdir(parents=True)
        (self.root / "proc/self").mkdir(exist_ok=True)
        (self.root / "proc/self/status").touch()
        (proc / "exe").symlink_to(os.path.realpath(executable))
        (proc / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in arguments) + b"\0")
        fields = ["S", *(["0"] * 18), str(started)]
        (proc / "stat").write_text(f"{pid} (agent worker) {' '.join(fields)}\n")
        return proc

    def vendor_script(self, path, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            path.write_bytes(body)
        else:
            path.write_text(body)
        path.chmod(0o755)
        return path

    def logging_script(self, label):
        return (
            "#!/bin/bash\n"
            + f'printf "%s %s\\n" {shlex.quote(label)} "$PWD" >> {shlex.quote(str(self.vendor_log))}\n'
        )

    def official(self, path, prefix="run"):
        return f'{prefix} sh -c cd -- "${{1%/*}}" && exec "$1" uts {path}'

    def shell_body(self, script):
        prelude = r"""
            record() { printf '%s\n' "$*" >> "$UTS_TEST_EVENTS"; }
            blocked() { record "unexpected-external $*"; return 97; }
            curl() { blocked curl "$@"; }
            wget() { blocked wget "$@"; }
            ssh() { blocked ssh "$@"; }
            sudo() { blocked sudo "$@"; }
            rm() { record "rm $*"; return 0; }
            rmdir() { record "rmdir $*"; return 0; }
            kill() { blocked kill "$@"; }
            pkill() { blocked pkill "$@"; }
            killall() { blocked killall "$@"; }
            chattr() { blocked chattr "$@"; }
            crontab() { blocked crontab "$@"; }
            systemctl() { blocked systemctl "$@"; }
            service() { blocked service "$@"; }
            initctl() { blocked initctl "$@"; }
            chkconfig() { blocked chkconfig "$@"; }
            update-rc.d() { blocked update-rc.d "$@"; }
            timeout() { blocked timeout "$@"; }
            sleep() { record "sleep $*"; SECONDS=$((SECONDS + $1)); }
            uname() {
              case "${1:--s}" in
                -m) printf 'x86_64\n' ;;
                *) printf 'Linux\n' ;;
              esac
            }
        """
        overrides = r"""
            UI_LANG=en
            WORKDIR="$UTS_TEST_ROOT/tmp"
            cleanup() { :; }
            require_root() { :; }
            prepare_runtime() { :; }
            preflight_paths() { :; }
            run() { record "run $*"; return 0; }
        """
        return (
            textwrap.dedent(prelude)
            + f"\nsource {shlex.quote(str(self.source))}\n"
            + textwrap.dedent(overrides)
            + textwrap.dedent(script)
        )

    def shell_environment(self):
        return {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(self.base),
            "TMPDIR": str(self.root / "tmp"),
            "NO_COLOR": "1",
            "TERM": "dumb",
            "LC_ALL": "C",
            "UTS_TEST_ROOT": str(self.root),
            "UTS_TEST_EVENTS": str(self.events),
        }

    def run_shell(self, script, **options):
        self.events.write_text("")
        body = self.shell_body(script)
        environment = self.shell_environment()
        options.setdefault("stdin", subprocess.DEVNULL)
        if "preexec_fn" not in options:
            options.setdefault("start_new_session", True)
        environment.update(options.pop("env", {}))
        options.setdefault("stdout", subprocess.PIPE)
        options.setdefault("stderr", subprocess.PIPE)
        result = subprocess.run(
            [BASH, "--noprofile", "--norc", "-c", body],
            cwd=self.base,
            env=environment,
            text=True,
            timeout=10,
            **options,
        )
        self.assertNotIn(
            "unexpected-external",
            self.events.read_text(),
            f"Unexpected external command:\n{self.events.read_text()}\n{result.stderr}",
        )
        return result

    def assert_failed(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(": local uninstall verified.", result.stdout)

    def real_function(self, name):
        source = self.source.read_text()
        body = source.split(f"\n{name}() {{\n", 1)[1].split("\n}\n", 1)[0]
        return f"{name}() {{\n{body}\n}}\n"

    def removal_requested(self, directory):
        return any(
            line.startswith("run rm ") and str(directory) in line
            for line in self.events.read_text().splitlines()
        )


class MainTests(UtsFixture):
    def test_script_runs_when_piped_to_bash(self):
        result = subprocess.run(
            [BASH, "-s", "--", "--help"],
            input=SCRIPT.read_text(),
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("用法：", result.stdout)
        self.assertIn("--include-monitor", result.stdout)
        self.assertIn("--include-tat", result.stdout)

    def test_options_are_validated(self):
        for arguments, accepted in (
            ("--timeout 1", "1"),
            ("--timeout 3600", "3600"),
            ("--timeout 0", None),
            ("--timeout 3601", None),
            ("--timeout 012", None),
            ("--timeout 1e3", None),
            ("--timeout ''", None),
            ("--timeout", None),
            ("--bogus", None),
            ("--timeout 9", "9"),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_shell(f'parse_args {arguments}\nprintf "%s\\n" "$COMMAND_TIMEOUT"\n')
                if accepted:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, f"{accepted}\n")
                else:
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("[FAIL]", result.stderr)
        for arguments, message in (
            ("--bogus", "[FAIL] 未知选项：--bogus"),
            ("--timeout 0", "[FAIL] --timeout 需要 1 到 3600 之间的秒数。"),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_shell(f"UI_LANG=zh\nparse_args {arguments}\n")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(message, result.stderr)

    def test_help_lists_every_option_and_exit_status(self):
        def invoke(arguments, **environment):
            return subprocess.run(
                [BASH, str(SCRIPT), *arguments],
                env={"PATH": os.environ.get("PATH", os.defpath), **environment},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
            )

        for arguments, environment in ((["--help"], {}), (["-h"], {"LANG": "en_US.UTF-8", "LC_ALL": "C"})):
            with self.subTest(arguments=arguments, environment=environment):
                result = invoke(arguments, **environment)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertIn("Uninstall-Tencent-Service 1.0.0", result.stdout)
                self.assertRegex(result.stdout, CJK)
                self.assertIn("用法：", result.stdout)
        english = self.run_shell("UI_LANG=en\nusage\n").stdout
        chinese = invoke(["--help"]).stdout
        self.assertIn("Usage:", english)
        options = re.findall(r"^\s+(?:-\w, )?(--[a-z-]+)", english, re.M)
        self.assertGreaterEqual(len(options), 7)
        self.assertEqual(options, re.findall(r"^\s+(?:-\w, )?(--[a-z-]+)", chinese, re.M))
        for code in ("0", "1", "6", "124, 137", "130, 143"):
            self.assertRegex(english, rf"(?m)^  {code}\s+\w")
            self.assertRegex(chinese, rf"(?m)^  {code.replace(', ', '、')}\s+\S")

    def test_every_message_has_an_english_and_a_chinese_text(self):
        source = re.sub(r"\\\n\s*", " ", SCRIPT.read_text())
        calls = [match for match in MESSAGE_CALL.finditer(source) if match[2] != '"$@"']
        self.assertGreater(len(calls), 20)
        for match in calls:
            if "${MSG}" in match[2]:
                continue
            with self.subTest(call=match[0].strip()):
                texts = re.findall(r'"[^"]*"', match[3])
                self.assertEqual(len(texts), 1, match[0])
                self.assertIsNone(CJK.search(match[2]), match[0])
                self.assertIsNotNone(CJK.search(texts[0]), match[0])

    def test_distribution_name_is_read_without_quotes(self):
        for source, expected in (
            ("os-release", "Debian GNU/Linux 12 (bookworm)"),
            ("lsb_release", "CentOS release 6.10 (Final)"),
            ("redhat-release", "CentOS release 6.10 (Final)"),
            ("none", "unknown"),
        ):
            with self.subTest(source=source):
                if source != "os-release":
                    (self.root / "etc/os-release").unlink(missing_ok=True)
                release = self.root / "etc/redhat-release"
                release.unlink(missing_ok=True)
                if source == "redhat-release":
                    release.write_text("CentOS release 6.10 (Final)\n")
                result = self.run_shell(
                    f'command_exists() {{ [[ "$1" == lsb_release && {source!r} == lsb_release ]]; }}\n'
                    + """lsb_release() { printf '"CentOS release 6.10 (Final)"\\n'; }\n"""
                    + 'detect_distro\nprintf "%s\\n" "$DISTRO_PRETTY"\n'
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"{expected}\n")

    def test_timeout_without_kill_after_falls_back_to_sigkill(self):
        self.enable_proc()
        for supports_k, expected in ((1, "timeout -k 5 120 true"), (0, "timeout -s KILL 120 true")):
            with self.subTest(supports_k=supports_k):
                result = self.run_shell(
                    self.real_function("prepare_runtime")
                    + self.real_function("run")
                    + f"SUPPORTS_K={supports_k}\n"
                    + r"""
                    command_exists() { :; }
                    timeout() {
                      if [[ "$1" == -k && "$SUPPORTS_K" -eq 0 ]]; then
                        printf "timeout: invalid option -- 'k'\n" >&2
                        return 125
                      fi
                      [[ "$*" != *" 1 true" ]] || return 0
                      record "timeout $*"
                    }
                    DRY_RUN=1
                    prepare_runtime
                    DRY_RUN=0
                    run true
                    """
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.events.read_text().splitlines(), [expected])
                self.assertEqual("commands still running at the deadline receive SIGKILL" in result.stderr, not supports_k)
                self.assertNotIn("invalid option", result.stderr)

    def test_dry_run_previews_without_changing_files(self):
        yunjing = self.root / "usr/local/qcloud/YunJing"
        self.vendor_script(yunjing / "uninst.sh", "#!/bin/sh\nexit 0\n")
        stargate = self.root / "usr/local/qcloud/stargate"
        self.vendor_script(stargate / "admin/uninstall.sh", "#!/bin/bash\nexit 0\n")
        barad = self.root / "usr/local/qcloud/monitor/barad"
        self.vendor_script(barad / "admin/uninstall.sh", "#!/bin/bash\nexit 0\n")
        (self.root / "etc/cron.d").mkdir()
        (self.root / "etc/cron.d/sgagenttask").write_text("* * * * * root start.sh\n")
        tat = self.root / "usr/local/qcloud/tat_agent"
        tat.mkdir(parents=True)
        (self.root / "etc/systemd/system").mkdir(parents=True)
        (self.root / "etc/systemd/system/tat_agent.service").write_text("[Unit]\n")
        self.enable_proc()
        before = self.file_snapshot()
        result = self.run_shell(
            self.real_function("run")
            + r"""
            stat() { printf '0\n'; }
            root_crontab() {
              QUERY_OUTPUT="$(
              printf '%s\n' "*/5 * * * * flock -xn /tmp/stargate.lock -c '${STARGATE_DIRS[0]}/admin/start.sh > /dev/null 2>&1 &'"
              printf '%s\n' "0 3 * * * /usr/local/bin/backup.sh"
              )"
            }
            command_exists() { [[ "$1" != systemctl ]]; }
            verify_uninstall() { record verify; }
            main --dry-run --include-monitor --include-tat
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text(), "")
        previews = [
            f"[DRY-RUN] would run official {yunjing}/uninst.sh",
            f"[DRY-RUN] would run official BaradAgent {barad}/admin/uninstall.sh",
            f"[DRY-RUN] would run official Sgagent {stargate}/admin/uninstall.sh",
            "[DRY-RUN] remove root crontab line: */5 * * * * flock",
            f"[DRY-RUN] rm -rf -- {yunjing}",
            f"[DRY-RUN] rm -rf -- {stargate}",
            f"[DRY-RUN] rm -rf -- {barad}",
            f"[DRY-RUN] rm -f -- {self.root / 'etc/systemd/system/tat_agent.service'}",
            f"[DRY-RUN] rm -rf -- {tat}",
        ]
        positions = [result.stdout.find(preview) for preview in previews]
        self.assertNotIn(-1, positions, result.stdout)
        self.assertEqual(positions[:3], sorted(positions[:3]))
        steps = [
            "==> Host Security agent (YunJing)",
            "==> Cloud Monitor (BaradAgent / Sgagent)",
            "==> TencentCloud Automation Tools (tat_agent)",
            "[ OK ] Dry run finished; nothing was changed.",
        ]
        positions = [result.stdout.find(line) for line in steps]
        self.assertNotIn(-1, positions, result.stdout)
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("Verification", result.stdout)
        self.assertNotIn("Continue?", result.stdout)
        self.assertNotIn("backup.sh", result.stdout)
        self.assertNotIn("Running official", result.stdout)
        self.assertEqual(before, self.file_snapshot())

    STUBBED_MAIN = r"""
        uninstall_yunjing() { section "Host Security agent (YunJing)" "主机安全 Agent（YunJing）"; record yunjing; }
        uninstall_monitor() { record monitor; }
        uninstall_tat() { section "TencentCloud Automation Tools (tat_agent)" "腾讯云自动化助手（tat_agent）"; record tat; }
        verify_uninstall() { record verify; }
    """

    def test_confirmation_needs_a_terminal_unless_yes_or_dry_run(self):
        for options, code, events in (
            ("", 1, []),
            ("--force --include-tat", 1, []),
            ("--yes", 0, ["yunjing", "verify"]),
            ("--dry-run", 0, ["yunjing"]),
        ):
            with self.subTest(options=options):
                result = self.run_shell(self.STUBBED_MAIN + f"main {options}\n")
                self.assertEqual(result.returncode, code, result.stdout + result.stderr)
                self.assertEqual(self.events.read_text().splitlines(), events)
                self.assertNotIn("Continue?", result.stdout)
                if code == 1:
                    self.assertIn("No terminal for confirmation. Add -y to run unattended.", result.stderr)

    def answer(self, reply, mode="stdin"):
        master, slave = os.openpty()
        attributes = termios.tcgetattr(slave)
        attributes[3] &= ~termios.ECHO
        attributes[1] &= ~termios.ONLCR
        termios.tcsetattr(slave, termios.TCSANOW, attributes)
        if reply is not None:
            os.write(master, reply.encode() + b"\n")
        terminal = bytearray()
        terminal_closed = threading.Event()
        self.terminal = terminal

        def drain():
            while not terminal_closed.is_set():
                try:
                    if not select.select([master], [], [], 0.1)[0]:
                        continue
                    data = os.read(master, 4096)
                except OSError:
                    return
                if not data:
                    return
                terminal.extend(data)

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()

        def close_terminal():
            terminal_closed.set()
            reader.join(timeout=1)
            os.close(slave)
            os.close(master)

        self.addCleanup(close_terminal)
        if mode == "stdin":
            return {"stdin": slave}
        if mode == "stdout":
            return {"stdin": slave, "stdout": slave}

        def attach():
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        return {"stdin": subprocess.DEVNULL, "preexec_fn": attach}

    def terminal_text(self, until="[y/N]"):
        deadline = time.monotonic() + 2
        while until.encode() not in self.terminal and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.terminal.decode()

    def test_confirmation_accepts_only_yes(self):
        (self.root / "usr/local/qcloud/YunJing").mkdir(parents=True)
        (self.root / "usr/local/qcloud/stargate").mkdir(parents=True)
        plan = ["[INFO] System: Debian GNU/Linux 12 (bookworm), x86_64"]
        summary = [
            "==> Host Security agent (YunJing)",
            "==> TencentCloud Automation Tools (tat_agent)",
            "==> Verification",
            "[ OK ] No agent files, processes, services or cron entries remain.",
            "[ OK ] Uninstall-Tencent-Service: local uninstall verified.",
        ]
        for reply, proceeds in (("y", True), ("yes", True), ("YES", True), ("n", False), ("", False), ("yess", False)):
            for mode in ("stdin", "stdout"):
                with self.subTest(reply=reply, mode=mode):
                    result = self.run_shell(self.STUBBED_MAIN + "main --include-tat\n", **self.answer(reply, mode))
                    if mode == "stdout":
                        output = self.terminal_text("Uninstall-Tencent-Service: " if proceeds else "[y/N]")
                        self.assertIn("\nContinue? [y/N] ", output)
                    else:
                        output = result.stdout
                        self.assertIn("\nContinue? [y/N] ", output)
                    positions = [output.find(line) for line in plan + (summary if proceeds else [])]
                    self.assertNotIn(-1, positions, output)
                    self.assertEqual(positions, sorted(positions))
                    if proceeds:
                        self.assertEqual(result.returncode, 0, output + result.stderr)
                        self.assertEqual(self.events.read_text().splitlines(), ["yunjing", "tat", "verify"])
                    else:
                        self.assertEqual(result.returncode, 1, output + result.stderr)
                        self.assertEqual(self.events.read_text(), "")
                        self.assertIn("Canceled; nothing was changed.", result.stderr)
                        self.assertNotIn("==> Host Security agent", output)

    def test_ctrl_c_at_the_prompt_changes_nothing(self):
        mask_state = self.root / "var/lib/uts/created-masks"
        mask_state.parent.mkdir(mode=0o700)
        mask_state.write_text("/etc/systemd/system/gone.service\n")
        stubs = ROOT_STAT + self.STUBBED_MAIN + "prepare_runtime() { trap 'interrupt 130' INT; trap 'interrupt 143' TERM; }\n"
        result = self.run_shell(stubs + "main\n", **self.answer("n"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text(), "")
        if BASH_MAJOR >= 4:
            self.events.write_text("")
            process = subprocess.Popen(
                [BASH, "--noprofile", "--norc", "-c", self.shell_body(stubs + "main\n")],
                cwd=self.base,
                env=self.shell_environment(),
                stderr=subprocess.PIPE,
                start_new_session=True,
                **self.answer(None, "stdout"),
            )
            try:
                self.assertIn("Continue? [y/N]", self.terminal_text())
                process.send_signal(signal.SIGINT)
                stdout, stderr = process.communicate(timeout=5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
            self.assertEqual(process.returncode, 130, stderr.decode())
            self.assertIn("Interrupted; run the script again to finish the cleanup.", stderr.decode())
            self.assertEqual(self.events.read_text(), "")
            self.assertEqual(mask_state.read_text(), "/etc/systemd/system/gone.service\n")
        result = self.run_shell(stubs + "main\n", **self.answer("y"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text().splitlines(), [f"rm -f -- {mask_state}", f"rmdir -- {mask_state.parent}", "yunjing", "verify"])

    def test_previous_terminal_output_cannot_satisfy_the_current_prompt(self):
        previous = self.answer(None)
        previous_output = self.terminal
        current = self.answer(None)
        os.write(previous["stdin"], b"old prompt")
        deadline = time.monotonic() + 2
        while b"old prompt" not in previous_output and b"old prompt" not in self.terminal:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        self.assertIn(b"old prompt", previous_output)
        self.assertEqual(self.terminal, b"")
        os.write(current["stdin"], b"current prompt")
        self.assertEqual(self.terminal_text("current prompt"), "current prompt")

    def test_preflight_failure_stops_before_the_prompt(self):
        result = self.run_shell(
            self.STUBBED_MAIN + "preflight_paths() { return 1; }\nmain\n", **self.answer("y")
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text(), "")
        self.assertIn("Unsafe installation paths. No uninstall was attempted.", result.stderr)
        self.assertNotIn("Continue?", result.stdout)

    def test_dry_run_with_a_failed_step_is_not_reported_as_finished(self):
        result = self.run_shell(
            self.STUBBED_MAIN + "uninstall_tat() { return 1; }\nmain --dry-run --include-tat\n"
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("Dry run finished", result.stdout)
        self.assertIn("TencentCloud Automation Tools (tat_agent) uninstall is incomplete (1).", result.stderr)
        self.assertIn("Uninstall-Tencent-Service: incomplete.", result.stderr)

    def test_chinese_output_has_no_untranslated_lines(self):
        (self.root / "usr/local/qcloud/stargate").mkdir(parents=True)
        for language in ("zh", "en"):
            with self.subTest(language=language):
                result = self.run_shell(
                    self.STUBBED_MAIN
                    + 'uninstall_monitor() { section "Cloud Monitor (BaradAgent / Sgagent)" "云监控（BaradAgent / Sgagent）"; return 1; }\n'
                    + "verify_uninstall() { warn \"Agent processes remain:\" \"仍有 Agent 进程：\"; return 1; }\n"
                    + f"UI_LANG={language}\nmain --yes --force --include-monitor --include-tat\n"
                )
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                lines = [line for line in (result.stdout + result.stderr).splitlines() if line.strip()]
                english = [line for line in lines if not CJK.search(line)]
                self.assertIn("系统：" if language == "zh" else "System: ", result.stdout)
                if language == "en":
                    self.assertEqual(lines, english)
                else:
                    self.assertEqual(english, [], result.stdout + result.stderr)
                    self.assertIn("云监控（BaradAgent / Sgagent）卸载未完成（1）。", result.stderr)
                    self.assertIn("Uninstall-Tencent-Service：未完成", result.stderr)

    def test_yunjing_status_controls_optional_components(self):
        for status, force, continued in (
            (1, 0, False),
            (7, 0, False),
            (6, 0, False),
            (124, 1, False),
            (137, 1, False),
            (1, 1, True),
            (6, 1, True),
        ):
            with self.subTest(status=status, force=force):
                options = "--yes --include-monitor --include-tat" + (" --force" if force else "")
                result = self.run_shell(
                    f"uninstall_yunjing() {{ record yunjing; return {status}; }}\n"
                    + r"""
                    uninstall_monitor() { record monitor; }
                    uninstall_tat() { record tat; }
                    verify_uninstall() { record verify; }
                    """
                    + f"main {options}\n"
                )
                self.assertEqual(result.returncode, status if status in (6, 124, 137) else 1, result.stdout + result.stderr)
                expected = ["yunjing"] + (["monitor", "tat", "verify"] if continued else [])
                self.assertEqual(self.events.read_text().splitlines(), expected)
                self.assertIn(f"Host Security agent (YunJing) uninstall is incomplete ({status})", result.stderr)
                self.assertEqual("optional components were not processed" in result.stderr, not continued)
                self.assertNotIn("local uninstall verified", result.stdout)
                self.assertIn("Uninstall-Tencent-Service: incomplete.", result.stderr)

    def test_optional_component_timeout_stops_remaining_work(self):
        for component in ("monitor", "tat"):
            for status in (124, 137, 143):
                with self.subTest(component=component, status=status):
                    result = self.run_shell(
                        "uninstall_yunjing() { :; }\n"
                        "uninstall_monitor() { record monitor; }\n"
                        "uninstall_tat() { record tat; }\n"
                        f"uninstall_{component}() {{ record {component}; return {status}; }}\n"
                        "verify_uninstall() { record verify; }\n"
                        "main --yes --force --include-monitor --include-tat"
                    )
                    self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                    self.assertEqual(
                        self.events.read_text().splitlines(),
                        ["monitor"] if component == "monitor" else ["monitor", "tat"],
                    )
                    self.assertNotIn("local uninstall verified", result.stdout)
                    self.assertIn(f"uninstall is incomplete ({status})", result.stderr)
                    self.assertIn("Uninstall-Tencent-Service: incomplete.", result.stderr)

    def test_service_timeout_is_preserved_through_real_component_and_main(self):
        for component in ("yunjing", "monitor", "tat"):
            for status in (124, 137, 143):
                for force in (False, True):
                    with self.subTest(component=component, status=status, force=force):
                        stubs = ""
                        for other in ("yunjing", "monitor", "tat"):
                            if other != component:
                                stubs += f"uninstall_{other}() {{ record {other}; }}\n"
                        result = self.run_shell(
                            stubs
                            + "agent_processes() { :; }\n"
                            + "remove_cron_lines() { :; }\n"
                            + "remove_system_cron_lines() { :; }\n"
                            + "systemd_available() { return 1; }\n"
                            + f"stop_services() {{ record service-timeout; return {status}; }}\n"
                            + "verify_uninstall() { record verify; }\n"
                            + "main --yes --include-monitor --include-tat"
                            + (" --force" if force else "")
                        )
                        self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                        previous = {"yunjing": [], "monitor": ["yunjing"], "tat": ["yunjing", "monitor"]}
                        self.assertEqual(self.events.read_text().splitlines(), previous[component] + ["service-timeout"])
                        self.assertNotIn("local uninstall verified", result.stdout)

    def test_reappearing_agent_is_reported_as_uninstall_protection(self):
        primary = self.root / "usr/local/qcloud/YunJing"
        primary.mkdir(parents=True)
        result = self.run_shell(
            r"""
            stop_services() { :; }
            stop_agent_processes() { :; }
            cleanup_services() { :; }
            remove_cron_lines() { :; }
            remove_path() { record "remove $1"; }
            agent_processes() { :; }
            uninstall_monitor() { record monitor; }
            uninstall_tat() { record tat; }
            verify_uninstall() { record verify; }
            main --yes --include-monitor --include-tat
            """
        )
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        self.assertIn("reappeared after removal", result.stderr)
        self.assertIn("turn off Agent uninstall protection", result.stderr)
        self.assertEqual(
            self.events.read_text().splitlines(),
            [
                *(
                    f"remove {self.root / path}"
                    for path in (
                        "usr/local/qcloud/YunJing",
                        "var/lib/qcloud/YunJing",
                        "run/ydeyes",
                        "var/run/ydeyes",
                        "tmp/yunjing_install",
                        "tmp/yunjing_install_nohup",
                    )
                ),
                "sleep 5",
            ],
        )

    def test_verified_only_when_no_step_or_verification_failed(self):
        for failing in (None, "uninstall_monitor", "uninstall_tat"):
            for verify_rc in (0, 1):
                with self.subTest(failing=failing, verify_rc=verify_rc):
                    script = "uninstall_yunjing() { :; }\nuninstall_monitor() { :; }\nuninstall_tat() { :; }\n"
                    if failing:
                        script += f"{failing}() {{ record failed-{failing}; return 1; }}\n"
                    result = self.run_shell(
                        script
                        + f"verify_uninstall() {{ record verify; return {verify_rc}; }}\n"
                        + "main --yes --include-monitor --include-tat\n"
                    )
                    expected = ([f"failed-{failing}"] if failing else []) + ["verify"]
                    self.assertEqual(self.events.read_text().splitlines(), expected)
                    if failing or verify_rc:
                        self.assert_failed(result)
                        self.assertEqual(result.returncode, 1, result.stderr)
                        self.assertIn("Uninstall-Tencent-Service: incomplete.", result.stderr)
                    else:
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertIn("Uninstall-Tencent-Service: local uninstall verified.", result.stdout)


class VerifyTests(UtsFixture):
    def setUp(self):
        super().setUp()
        self.enable_proc()

    def verify(self, stubs=""):
        return stubs + "\nINCLUDE_MONITOR=1\nINCLUDE_TAT=1\nverify_uninstall\n"

    def main(self):
        return (
            'timeout() { shift 3; "$@"; }\n'
            "crontab() { printf 'no crontab for root\\n' >&2; return 1; }\n"
            "main --yes --include-monitor --include-tat\n"
        )

    def test_clean_root_passes_verification(self):
        result = self.run_shell(self.verify("agent_processes() { :; }\nverify_services() { return 0; }"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.events.read_text(), "")

    def test_remaining_installation_directory_fails_verification(self):
        stargate = self.root / "usr/local/qcloud/stargate"
        stargate.mkdir(parents=True)
        result = self.run_shell(self.verify())
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"Installation path remains: {stargate}.", result.stderr)

    def test_remaining_agent_process_fails_verification(self):
        agent = str(self.root / "usr/local/qcloud/stargate/bin/sgagent64")
        self.process(900001, agent, [agent, "-d"])
        result = self.run_shell(self.verify())
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Agent processes remain:", result.stderr)
        self.assertIn("PID 900001", result.stderr)
        self.assertIn("sgagent64", result.stderr)

    def test_unreadable_cmdline_is_not_successful_verification(self):
        agent = str(self.root / "usr/local/qcloud/YunJing/YDService")
        (self.process(900002, agent, [agent]) / "cmdline").unlink()
        result = self.run_shell(self.verify())
        self.assert_failed(result)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Cannot inspect command line for PID 900002.", result.stderr)

    def test_remaining_init_script_fails_verification(self):
        script = self.root / "etc/init.d/tat_agent_service"
        script.parent.mkdir()
        script.write_text("#!/bin/sh\n")
        result = self.run_shell(self.verify())
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Service or startup files remain:", result.stderr)
        self.assertIn(str(script), result.stderr)

    def test_cron_entry_in_unrelated_file_fails_verification(self):
        cron = self.root / "etc/cron.d/other"
        cron.parent.mkdir()
        line = "* * * * * root flock -xn /tmp/stargate.lock -c '/usr/local/qcloud/stargate/admin/start.sh > /dev/null 2>&1 &'"
        cron.write_text(f"0 3 * * * root /usr/local/bin/backup.sh\n{line}\n")
        result = self.run_shell(self.verify())
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"Cron entry remains in {cron}:", result.stderr)
        self.assertIn(line, result.stderr)
        self.assertNotIn("backup.sh", result.stderr)

    def test_leftover_tat_symlink_fails_verification(self):
        link = self.root / "usr/local/bin/tat_agent"
        link.parent.mkdir(parents=True)
        link.symlink_to(self.root / "usr/local/qcloud/tat_agent/tat_agent")
        result = self.run_shell(self.verify())
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"Leftover file remains: {link}.", result.stderr)

    def test_clean_second_run_exits_zero_without_warnings(self):
        result = self.run_shell(self.main())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertIn("No official uninst.sh found; cleaning local remnants only.", result.stdout)
        self.assertIn("Uninstall-Tencent-Service: local uninstall verified.", result.stdout)
        self.assertEqual(
            [line for line in self.events.read_text().splitlines() if line.startswith("run ")], []
        )

    def test_main_reports_incomplete_for_remnant_outside_cleanup_scope(self):
        cron = self.root / "etc/cron.d/other"
        cron.parent.mkdir()
        cron.write_text("* * * * * root /usr/local/qcloud/stargate/admin/start.sh\n")
        result = self.run_shell(self.main())
        self.assert_failed(result)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"Cron entry remains in {cron}:", result.stderr)
        self.assertIn("Uninstall-Tencent-Service: incomplete.", result.stderr)


if __name__ == "__main__":
    unittest.main()
