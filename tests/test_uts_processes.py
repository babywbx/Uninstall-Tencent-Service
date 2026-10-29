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
        self.tat = self.root / "usr/local/qcloud/tat_agent"
        self.yunjing.mkdir(parents=True)
        (self.stargate / "bin").mkdir(parents=True)
        (self.monitor / "barad/admin").mkdir(parents=True)

    def sgagent(self):
        (self.stargate / "bin/sgagent64").write_bytes(b"ELF")
        (self.stargate / "bin/sgagent").symlink_to("sgagent64")
        return os.path.realpath(self.stargate / "bin/sgagent64")

    def selected_scan(self):
        return (
            "INCLUDE_MONITOR=1\nINCLUDE_TAT=1\ndirs=()\n"
            'while IFS= read -r dir; do dirs+=("${dir}"); done < <(selected_dirs)\n'
            'agent_processes "${dirs[@]}"\n'
        )

    def test_sgagent_symlink_resolves_to_real_binary(self):
        real = self.sgagent()
        self.process(900010, str(self.stargate / "bin/sgagent"), ["sgagent", "-d"])
        self.process(900011, f"{real} (deleted)", [f"{self.stargate}/bin/sgagent", "-d"])
        result = self.run_shell('agent_processes "${STARGATE_DIRS[@]}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"900010|{real}|", result.stdout)
        self.assertIn(f"900011|{real}|", result.stdout)
        self.assertNotIn("(deleted)", result.stdout)

    def test_bundled_python_running_barad_plugin_is_matched(self):
        python = str(self.monitor / "python26/bin/python")
        plugin = f"{self.monitor}/barad/plugin/report.py"
        self.process(900015, python, ["python", plugin])
        self.process(900016, "/usr/bin/python3", ["python3", plugin])
        result = self.run_shell(self.selected_scan())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"900015|{os.path.realpath(python)}|", result.stdout)
        self.assertNotIn("900016|", result.stdout)

    def test_shell_wrappers_match_only_scripts_inside_selected_dirs(self):
        inside = (
            ("/bin/sh", ["sh", f"{self.stargate}/admin/start.sh"]),
            ("/bin/bash", ["bash", f"{self.monitor}/barad/admin/trystart.sh"]),
            ("/bin/sh", ["sh", "-e", f"{self.yunjing}/startYD.sh"]),
            ("/bin/dash", ["dash", "-eu", "--", f"{self.yunjing}/stopYDCore.sh"]),
            ("/bin/bash", ["/bin/bash", f"{self.tat}/install/init_install.sh", "-service"]),
        )
        outside = (
            ("/bin/sh", ["sh", f"{self.root}/opt/other/admin/start.sh"]),
            ("/bin/bash", ["bash", f"{self.root}/usr/local/qcloud/other/admin/trystart.sh"]),
            ("/bin/sh", ["sh", "-e", f"{self.root}/home/admin/startYD.sh"]),
            ("/bin/bash", ["bash", f"{self.yunjing}/uninst.sh"]),
        )
        for index, (shell, arguments) in enumerate(inside):
            self.process(900020 + index, shell, arguments)
        for index, (shell, arguments) in enumerate(outside):
            self.process(900030 + index, shell, arguments)
        result = self.run_shell(self.selected_scan())
        self.assertEqual(result.returncode, 0, result.stderr)
        for index in range(len(inside)):
            self.assertIn(f"{900020 + index}|", result.stdout)
        for index in range(len(outside)):
            self.assertNotIn(f"{900030 + index}|", result.stdout)

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

    def test_relative_shell_scripts_are_resolved_from_process_cwd(self):
        script = self.yunjing / "startYD.sh"
        script.write_text("exit 0\n")
        for pid, argument in ((900044, "./startYD.sh"), (900045, "startYD.sh")):
            proc = self.process(pid, "/bin/bash", ["bash", argument])
            (proc / "cwd").symlink_to(self.yunjing)
        result = self.run_shell('agent_processes "${YUNJING_DIRS[@]}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("900044|", result.stdout)
        self.assertIn("900045|", result.stdout)

    def test_shell_scripts_escaping_agent_directory_are_not_matched(self):
        other = self.yunjing.parent / "other"
        other.mkdir()
        (other / "start.sh").write_text("exit 0\n")
        outside = self.root / "opt/start.sh"
        outside.write_text("exit 0\n")
        link = self.yunjing / "start.sh"
        link.symlink_to(outside)
        admin = self.yunjing / "admin"
        admin.mkdir()
        self.process(900046, "/bin/bash", ["bash", f"{self.yunjing}/../other/start.sh"])
        self.process(900047, "/bin/bash", ["bash", str(link)])
        self.process(900057, "/bin/bash", ["bash", f"{admin}/../start.sh"])
        for removed in (False, True):
            with self.subTest(intermediate_directory_removed=removed):
                if removed:
                    admin.rmdir()
                result = self.run_shell('agent_processes "${YUNJING_DIRS[@]}"')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_shell_wrappers_remain_detectable_after_installation_is_deleted(self):
        admin = self.yunjing / "admin"
        admin.mkdir()
        (admin / "start.sh").write_text("exit 0\n")
        self.process(900048, "/bin/bash", ["bash", f"{admin}/./start.sh"])
        relative = self.process(900049, "/bin/bash", ["bash", "./start.sh"])
        (relative / "cwd").symlink_to(f"{os.path.realpath(admin)} (deleted)")
        shutil.rmtree(self.yunjing)
        result = self.run_shell('agent_processes "${YUNJING_DIRS[@]}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("900048|", result.stdout)
        self.assertIn("900049|", result.stdout)

    def test_unresolved_agent_script_symlink_fails_scan_without_signalling(self):
        release = self.yunjing / "releases/v1/admin"
        release.mkdir(parents=True)
        (release / "start.sh").write_text("exit 0\n")
        (self.yunjing / "admin").symlink_to("releases/v1/admin")
        script = str(self.yunjing / "admin/start.sh")
        self.process(900058, "/bin/bash", ["bash", script])
        result = self.run_shell('agent_processes "${YUNJING_DIRS[@]}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("900058|", result.stdout)
        shutil.rmtree(self.yunjing / "releases")
        for function in ("agent_processes", "stop_agent_processes"):
            with self.subTest(function=function):
                result = self.run_shell(f'{function} "${{YUNJING_DIRS[@]}}"')
                self.assert_failed(result)
                self.assertEqual(result.stdout, "")
                self.assertEqual(self.events.read_text(), "")

    def test_busybox_matching_requires_shell_applet(self):
        start = f"{self.stargate}/admin/start.sh"
        self.process(900050, "/bin/busybox", ["sh", "-e", start])
        self.process(900051, "/bin/busybox", ["ash", start])
        self.process(900052, "/bin/busybox", ["tail", "-f", start])
        for pid, applet in ((900053, "sh"), (900054, "ash"), (900055, "hush")):
            self.process(pid, "/bin/busybox", ["busybox", applet, "-e", start])
        self.process(900056, "/bin/busybox", ["busybox", "tail", "-f", start])
        result = self.run_shell('agent_processes "${STARGATE_DIRS[@]}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        for pid in (900050, 900051, 900053, 900054, 900055):
            self.assertIn(f"{pid}|", result.stdout)
        self.assertNotIn("900052|", result.stdout)
        self.assertNotIn("900056|", result.stdout)

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

    def test_signal_requires_unchanged_process_identity(self):
        self.sgagent()
        stale_stat = " ".join(["S", *(["0"] * 18), "101"])
        for pid, changed in ((900070, None), (900071, "exe"), (900072, "start"), (900073, "arguments")):
            with self.subTest(changed=changed):
                proc = self.process(pid, str(self.stargate / "bin/sgagent"), ["sgagent", "-d"])
                change = {
                    None: ":",
                    "exe": f"ln -sfn /usr/bin/tail {shlex.quote(str(proc / 'exe'))}",
                    "start": f"printf '%s\\n' '{pid} (agent worker) {stale_stat}' > {shlex.quote(str(proc / 'stat'))}",
                    "arguments": f"printf 'sgagent\\0-d\\0-v\\0' > {shlex.quote(str(proc / 'cmdline'))}",
                }[changed]
                try:
                    result = self.run_shell(
                        'kill() { record "kill $*"; }\n'
                        'targets="$(agent_processes "${STARGATE_DIRS[@]}")"\n'
                        + change
                        + '\nsignal_agent_processes TERM "${targets}"\n'
                    )
                finally:
                    shutil.rmtree(proc)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = [f"kill -TERM {pid}"] if changed is None else []
                self.assertEqual(self.events.read_text().splitlines(), expected)
                self.assertNotIn("Cannot signal", result.stderr)

    def test_process_reused_during_scan_is_not_signalled(self):
        original = self.real_function("is_agent_process").replace(
            "is_agent_process() {", "original_is_agent_process() {", 1
        )
        for changed, started in (("arguments", 100), ("pid", 101)):
            with self.subTest(changed=changed):
                proc = self.process(900074, "/bin/bash", ["bash", str(self.yunjing / "startYD.sh")])
                fields = " ".join(["S", *(["0"] * 18), str(started)])
                try:
                    result = self.run_shell(
                        original
                        + r'''
                        is_agent_process() {
                          original_is_agent_process "$@" || return $?
                          printf 'bash\0/home/admin/unrelated.sh\0' > "$1/cmdline"
                        '''
                        + f'  printf "%s\\n" "900074 (unrelated) {fields}" > "$1/stat"\n'
                        + r'''
                        }
                        kill() { record "kill $*"; }
                        targets="$(agent_processes "${YUNJING_DIRS[@]}")"
                        printf '%s' "$targets"
                        signal_agent_processes TERM "$targets"
                        '''
                    )
                finally:
                    shutil.rmtree(proc)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.events.read_text(), "")
                self.assertEqual(result.stdout, "")

    def test_candidate_recheck_failure_aborts_scan_without_signalling(self):
        proc = self.process(900075, "/bin/bash", ["bash", "startYD.sh"])
        (proc / "cwd").symlink_to(self.yunjing)
        original = self.real_function("process_token").replace(
            "process_token() {", "original_process_token() {", 1
        )
        result = self.run_shell(
            original
            + r'''
            process_token() {
              command rm -- "$1/cwd"
              original_process_token "$@"
            }
            kill() { record "kill $*"; }
            stop_agent_processes "${YUNJING_DIRS[@]}"
            '''
        )
        self.assert_failed(result)
        self.assertIn("Cannot inspect working directory for PID 900075", result.stderr)
        self.assertEqual(self.events.read_text(), "")

    def test_with_real_paths_adds_resolved_directory_and_matches_processes(self):
        real_root = os.path.realpath(self.root)
        data = self.root / "data/qcloud"
        data.parent.mkdir()
        shutil.move(str(self.root / "usr/local/qcloud"), str(data))
        (self.root / "usr/local/qcloud").symlink_to(data)
        real = self.sgagent()
        self.assertTrue(real.startswith(f"{real_root}/data/qcloud/stargate/"))
        self.process(900030, str(self.stargate / "bin/sgagent"), ["sgagent", "-d"])
        tat = shlex.quote(f"{real_root}/var/lib/qcloud/tat_agent")
        result = self.run_shell(
            f'with_real_paths "${{STARGATE_DIRS[0]}}" "${{MONITOR_DIRS[0]}}/python26" {tat}\n'
            'agent_processes "${STARGATE_DIRS[@]}"\n'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[:5], [
            str(self.stargate),
            f"{real_root}/data/qcloud/stargate",
            f"{self.monitor}/python26",
            f"{real_root}/data/qcloud/monitor/python26",
            f"{real_root}/var/lib/qcloud/tat_agent",
        ])
        self.assertEqual(len(lines), 6)
        self.assertTrue(lines[5].startswith(f"900030|{real}|"), lines[5])

    def test_stop_terms_respawned_process_once(self):
        live = str(self.yunjing / "YDLive")
        service = str(self.yunjing / "YDService")
        self.process(900080, live, [live])
        self.process(900081, service, [service])
        respawn = self.process(900082, service, [service])
        shutil.move(str(respawn), str(self.root / "tmp/respawn"))
        result = self.run_shell(
            r"""
            kill() {
              record "kill $*"
              mv "${UTS_TEST_ROOT}/proc/$2" "${UTS_TEST_ROOT}/tmp/exited-$2"
              if [[ "$2" == 900081 ]]; then mv "${UTS_TEST_ROOT}/tmp/respawn" "${UTS_TEST_ROOT}/proc/900082"; fi
            }
            stop_agent_processes "${YUNJING_DIRS[@]}"
            """
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text().splitlines(), [
            "kill -TERM 900080",
            "kill -TERM 900081",
            "sleep 1",
            "kill -TERM 900082",
            "sleep 1",
        ])

    def test_surviving_process_is_reported_and_only_force_kills(self):
        service = str(self.yunjing / "YDService")
        self.process(900090, service, [service])
        for force, dies in ((0, False), (1, False), (1, True)):
            with self.subTest(force=force, dies=dies):
                exit_on_kill = 'mv "${UTS_TEST_ROOT}/proc/$2" "${UTS_TEST_ROOT}/tmp/exited-$2"' if dies else ":"
                result = self.run_shell(
                    'kill() { record "kill $*"; if [[ "$1" == -KILL ]]; then ' + exit_on_kill + "; fi; }\n"
                    f'FORCE={force}\nstop_agent_processes "${{YUNJING_DIRS[@]}}"\n'
                )
                expected = ["kill -TERM 900090"] + ["sleep 1"] * 5
                if force:
                    expected += ["kill -KILL 900090", "sleep 1"]
                self.assertEqual(self.events.read_text().splitlines(), expected)
                if dies:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn("remain", result.stderr)
                else:
                    self.assert_failed(result)
                    self.assertIn("Agent processes remain", result.stderr)
                    self.assertIn(f"PID 900090   {os.path.realpath(service)}", result.stderr)

    def test_dry_run_stop_previews_without_signalling_or_waiting(self):
        service = str(self.yunjing / "YDService")
        self.process(900095, service, [service])
        result = self.run_shell('exec 3>&1\nDRY_RUN=1\nstop_agent_processes "${YUNJING_DIRS[@]}"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"[DRY-RUN] kill -TERM 900095 ({os.path.realpath(service)})", result.stdout)
        self.assertEqual(self.events.read_text(), "")


class LauncherTests(UtsFixture):
    def test_launcher_rejects_selected_agent_parent(self):
        yunjing = self.root / "usr/local/qcloud/YunJing"
        (yunjing / "startYD.sh").parent.mkdir(parents=True)
        (yunjing / "startYD.sh").write_text("exit 0\n")
        tat_agent = str(self.root / "usr/local/qcloud/tat_agent/tat_agent")
        for parent, include_tat, dry_run, status in (
            ("yunjing", 0, 0, 1),
            ("yunjing", 0, 1, 0),
            ("tat", 0, 0, 0),
            ("tat", 1, 0, 1),
            ("tat", 1, 1, 0),
        ):
            with self.subTest(parent=parent, include_tat=include_tat, dry_run=dry_run):
                if parent == "yunjing":
                    proc = self.process(os.getpid(), "/bin/bash", ["bash", "./startYD.sh"])
                    (proc / "cwd").symlink_to(yunjing)
                else:
                    proc = self.process(os.getpid(), tat_agent, [tat_agent])
                try:
                    result = self.run_shell(f"INCLUDE_TAT={include_tat}\nDRY_RUN={dry_run}\npreflight_launcher")
                finally:
                    shutil.rmtree(proc)
                self.assertEqual(result.returncode, status, result.stderr)
                rejected = parent == "yunjing" or include_tat == 1
                self.assertEqual("Parent process" in result.stderr, rejected)
                self.assertEqual("Automation Tools console" in result.stderr, rejected)

    def test_launcher_rejects_selected_service_cgroup(self):
        self.enable_proc()
        (self.root / "proc/self/cgroup").write_text("0::/system.slice/tat_agent.service\n")
        for include, dry_run, status in ((0, 0, 0), (1, 0, 1), (1, 1, 0)):
            with self.subTest(include=include, dry_run=dry_run):
                result = self.run_shell(f"INCLUDE_TAT={include}\nDRY_RUN={dry_run}\npreflight_launcher")
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual("runs inside tat_agent.service" in result.stderr, include == 1)
