import os
import shlex
import shutil

from test_uts import UtsFixture


class ProcessTests(UtsFixture):
    def setUp(self):
        super().setUp()
        self.yunjing = self.root / "usr/local/qcloud/YunJing"
        self.stargate = self.root / "usr/local/qcloud/stargate"
        self.monitor = self.root / "usr/local/qcloud/monitor"
        self.yunjing.mkdir(parents=True)
        (self.stargate / "bin").mkdir(parents=True)
        (self.monitor / "barad/admin").mkdir(parents=True)

    def sgagent(self):
        (self.stargate / "bin/sgagent64").write_bytes(b"ELF")
        (self.stargate / "bin/sgagent").symlink_to("sgagent64")
        return os.path.realpath(self.stargate / "bin/sgagent64")

    def selected_scan(self):
        return (
            "INCLUDE_MONITOR=1\ndirs=()\n"
            'while IFS= read -r dir; do dirs+=("${dir}"); done < <(selected_dirs)\n'
            'agent_processes "${dirs[@]}"\n'
        )

    def test_sgagent_symlink_resolves_to_real_binary(self):
        real = self.sgagent()
        self.process(900010, str(self.stargate / "bin/sgagent"), ["sgagent", "-d"])
        self.process(900011, f"{real} (deleted)", [f"{self.stargate}/bin/sgagent", "-d"])
        result = self.run_shell('agent_processes "${STARGATE_DIRS[@]}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"900010|{real}\n", result.stdout)
        self.assertIn(f"900011|{real}\n", result.stdout)
        self.assertNotIn("(deleted)", result.stdout)

    def test_arguments_that_only_mention_agent_paths_are_not_matched(self):
        start = f"{self.stargate}/admin/start.sh"
        startyd = f"{self.yunjing}/startYD.sh"
        bystanders = (
            ("/usr/bin/tail", ["tail", "-f", f"{self.yunjing}/log/ydservice.log"]),
            ("/usr/bin/vim", ["vim", start]),
            ("/bin/sh", ["sh", "-c", f"{start} > /dev/null 2>&1 &"]),
            ("/bin/bash", ["bash", "-c", ":", startyd]),
            ("/bin/sh", ["sh", "-s", startyd]),
            ("/bin/bash", ["bash", "--rcfile", startyd]),
            ("/bin/bash", ["bash", f"{self.root}/home/admin/wrapper.sh", start]),
        )
        for index, (executable, arguments) in enumerate(bystanders):
            self.process(900040 + index, executable, arguments)
        result = self.run_shell(self.selected_scan())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_unreadable_process_fails_scan_and_stop(self):
        service = str(self.yunjing / "YDService")
        self.process(900062, service, [service])
        for missing, warning in (("cmdline", "command line"), ("exe", "executable")):
            with self.subTest(missing=missing):
                proc = self.process(900060, service, [service])
                (proc / missing).unlink()
                try:
                    result = self.run_shell(
                        r"""
                        status=0; agent_processes "${YUNJING_DIRS[@]}" || status=$?
                        record "scan-status=${status}"
                        kill() { record "kill $*"; }
                        status=0; stop_agent_processes "${YUNJING_DIRS[@]}" || status=$?
                        record "stop-status=${status}"
                        """
                    )
                finally:
                    shutil.rmtree(proc)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.events.read_text().splitlines(), ["scan-status=1", "stop-status=1"])
                self.assertIn(f"Cannot inspect {warning} for PID 900060.", result.stderr)
                self.assertIn("900062|", result.stdout)

    def test_with_real_paths_adds_resolved_directory_and_matches_processes(self):
        real_root = os.path.realpath(self.root)
        data = self.root / "data/qcloud"
        data.parent.mkdir()
        shutil.move(str(self.root / "usr/local/qcloud"), str(data))
        (self.root / "usr/local/qcloud").symlink_to(data)
        real = self.sgagent()
        self.assertTrue(real.startswith(f"{real_root}/data/qcloud/stargate/"))
        self.process(900030, str(self.stargate / "bin/sgagent"), ["sgagent", "-d"])
        fallback = shlex.quote(f"{real_root}/var/lib/qcloud/YunJing")
        result = self.run_shell(
            f'with_real_paths "${{STARGATE_DIRS[0]}}" "${{MONITOR_DIRS[0]}}/barad" {fallback}\n'
            'agent_processes "${STARGATE_DIRS[@]}"\n'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[:5], [
            str(self.stargate),
            f"{real_root}/data/qcloud/stargate",
            f"{self.monitor}/barad",
            f"{real_root}/data/qcloud/monitor/barad",
            f"{real_root}/var/lib/qcloud/YunJing",
        ])
        self.assertEqual(len(lines), 6)
        self.assertEqual(lines[5], f"900030|{real}")

    def test_dry_run_stop_previews_without_signalling_or_waiting(self):
        service = str(self.yunjing / "YDService")
        self.process(900095, service, [service])
        result = self.run_shell('exec 3>&1\nDRY_RUN=1\nstop_agent_processes "${YUNJING_DIRS[@]}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"[DRY-RUN] kill -TERM 900095 ({os.path.realpath(service)})", result.stdout)
        self.assertEqual(self.events.read_text(), "")
