import os
import shlex
import subprocess

from test_uts import UtsFixture


VENDOR_UPSTART = (
    'description "TAT agent"\nauthor "Tencent Cloud"\n\n'
    "start on runlevel [2345]\nstop on runlevel [!2345]\n\n"
    "respawn\nrespawn limit unlimited\n\nexpect daemon\n\n"
    "pre-start exec sleep 0.2\nexec /usr/local/qcloud/tat_agent/tat_agent\n"
)


class ServiceTests(UtsFixture):
    def setUp(self):
        super().setUp()
        self.units = self.root / "etc/systemd/system"
        self.units.mkdir(parents=True)
        self.unit = self.units / "tat_agent.service"
        self.init_script = self.root / "etc/init.d/tat_agent_service"
        self.upstart_conf = self.root / "etc/init/tat_agent_service.conf"

    def enable_systemd(self):
        (self.root / "run/systemd/system").mkdir(parents=True, exist_ok=True)

    def write_init_script(self, body="#!/bin/bash\n"):
        self.init_script.parent.mkdir(exist_ok=True)
        self.init_script.write_text(body)
        self.init_script.chmod(0o755)

    def tat_files(self):
        self.unit.write_text("[Service]\nExecStart=/usr/local/qcloud/tat_agent/tat_agent\n")
        wants = self.units / "multi-user.target.wants/tat_agent.service"
        wants.parent.mkdir()
        wants.symlink_to(self.unit)
        self.write_init_script()
        self.upstart_conf.parent.mkdir()
        self.upstart_conf.write_text('description "TAT agent"\n')
        rc_link = self.root / "etc/rc3.d/S88tat_agent_service"
        rc_link.parent.mkdir()
        rc_link.symlink_to("../init.d/tat_agent_service")
        return [self.unit, wants, self.init_script, self.upstart_conf, rc_link]

    def systemd_mocks(self):
        return r'''
            command_exists() { [[ "$1" == systemctl ]]; }
            timeout() {
              local unit="${@: -1}"
              if [[ -L "$UTS_TEST_ROOT/etc/systemd/system/$unit" ]]; then
                printf 'LoadState=masked\n'
              elif [[ -f "$UTS_TEST_ROOT/etc/systemd/system/$unit" ]]; then
                printf 'LoadState=loaded\n'
              else
                printf 'LoadState=not-found\n'
              fi
            }
        '''

    def test_service_files_lists_tat_units_and_nothing_for_absent_ones(self):
        files = self.tat_files()
        result = self.run_shell('service_files tat_agent tat_agent_service tat_install')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertCountEqual(result.stdout.splitlines(), [str(path) for path in files])
        result = self.run_shell("service_files stargate tat_install")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")

    def test_init_scripts_are_deregistered_without_being_executed(self):
        self.write_init_script("#!/bin/bash\nprintf ran > /dev/null\nexit 99\n")
        for tools, deregister in (
            ("chkconfig", "chkconfig --del tat_agent_service"),
            ("update-rc.d", "update-rc.d -f tat_agent_service remove"),
            ("rc-service rc-update", "rc-update del tat_agent_service --all"),
        ):
            with self.subTest(tools=tools):
                result = self.run_shell(
                    f'command_exists() {{ [[ " {tools} " == *" $1 "* ]]; }}\n'
                    'stop_services tat_agent tat_agent_service tat_install\n'
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.events.read_text().splitlines(), [f"run {deregister}"])

    def test_sysv_pidfile_stop_hook_cannot_kill_bystander(self):
        marker = self.root / "tmp/vendor-stop-ran"
        pidfile = self.root / "var/run/tat_agent.pid"
        self.write_init_script(
            "#!/bin/bash\n"
            + f"PID=$(cat {shlex.quote(str(pidfile))})\n"
            + 'if ps "$PID" | grep -q tat_agent; then kill -9 "$PID"; fi\n'
            + f"printf invoked > {shlex.quote(str(marker))}\n"
        )
        binaries = self.base / "bin"
        binaries.mkdir()
        ps = binaries / "ps"
        ps.write_text('#!/bin/sh\nprintf "%s tat_agent_bystander\\n" "$1"\n')
        ps.chmod(0o755)
        bystander = subprocess.Popen(
            ["sleep", "30"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            pidfile.write_text(f"{bystander.pid}\n")
            result = self.run_shell(
                f"export PATH={shlex.quote(str(binaries))}:\"$PATH\"\n"
                + r'''
                command_exists() { [[ "$1" == update-rc.d ]]; }
                systemd_available() { return 1; }
                run() {
                  record "run $*"
                  if [[ "$1" == "$UTS_TEST_ROOT/etc/init.d/tat_agent_service" ]]; then
                    command "$@"
                  fi
                  return 0
                }
                stop_services tat_agent_service
                '''
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIsNone(bystander.poll(), "Vendor stop hook killed the unrelated process")
            self.assertFalse(marker.exists())
            self.assertEqual(self.events.read_text(), "run update-rc.d -f tat_agent_service remove\n")
            self.assertEqual(pidfile.read_text(), f"{bystander.pid}\n")
        finally:
            if bystander.poll() is None:
                bystander.terminate()
            bystander.wait(timeout=3)

    def test_upstart_jobs_with_stop_hooks_are_refused_for_every_selected_component(self):
        self.write_init_script()
        (self.root / "etc/init.d/stargate").write_text("#!/bin/sh\nexit 0\n")
        self.upstart_conf.parent.mkdir()
        override = self.root / "etc/init/tat_agent_service.override"
        for case, conf, override_kind in (
            ("post-stop script", "post-stop script\n  exit 0\nend script\n", None),
            ("pre-stop exec", "respawn\n  pre-stop exec /usr/local/qcloud/tat_agent/stop.sh\n", None),
            ("override hook", VENDOR_UPSTART, "post-stop exec /bin/true\n"),
            ("unreadable override", VENDOR_UPSTART, "directory"),
        ):
            with self.subTest(case=case):
                self.upstart_conf.write_text(conf)
                if override.is_dir():
                    override.rmdir()
                override.unlink(missing_ok=True)
                if override_kind == "directory":
                    override.mkdir()
                elif override_kind:
                    override.write_text(override_kind)
                before = self.file_snapshot()
                for services in ("tat_agent tat_agent_service tat_install", "stargate tat_agent_service"):
                    result = self.run_shell(
                        'command_exists() { [[ "$1" == initctl || "$1" == update-rc.d ]]; }\n'
                        f"stop_services {services}\n"
                    )
                    self.assert_failed(result)
                    self.assertIn("Upstart job tat_agent_service has vendor stop hooks", result.stderr)
                    self.assertEqual(self.events.read_text(), "")
                    self.assertEqual(before, self.file_snapshot())

    def test_upstart_job_without_stop_hooks_is_stopped_with_initctl(self):
        self.upstart_conf.parent.mkdir()
        self.upstart_conf.write_text(VENDOR_UPSTART)
        for state, expected in (
            ("tat_agent_service start/running, process 1506", ["run initctl stop tat_agent_service"]),
            ("tat_agent_service stop/waiting", []),
            ("", []),
        ):
            with self.subTest(state=state):
                result = self.run_shell(
                    'command_exists() { [[ "$1" == initctl ]]; }\n'
                    "timeout() {\n"
                    '  [[ "$*" == "-k 5 120 initctl status tat_agent_service" ]] || { blocked timeout "$@"; return 97; }\n'
                    f"  [[ -n {shlex.quote(state)} ]] || return 1\n"
                    f"  printf '%s\\n' {shlex.quote(state)}\n"
                    "}\n"
                    'stop_services tat_agent tat_agent_service tat_install\n'
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.events.read_text().splitlines(), expected)
                self.assertTrue(self.upstart_conf.is_file())

    def test_loaded_unit_is_stopped_and_disabled_while_missing_units_are_skipped(self):
        self.enable_systemd()
        self.unit.write_text("[Service]\n")
        result = self.run_shell(self.systemd_mocks() + 'stop_services tat_agent tat_agent_service tat_install')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.events.read_text().splitlines(),
            ["run systemctl stop tat_agent.service", "run systemctl disable tat_agent.service"],
        )
        self.assertFalse(self.unit.is_symlink())

    def test_cleanup_removes_service_files_then_reloads_systemd(self):
        self.enable_systemd()
        files = self.tat_files()
        result = self.run_shell(
            r'''
            command_exists() { [[ "$1" == systemctl ]]; }
            removed=$'\n'
            run() { record "run $*"; [[ "$1" != rm ]] || removed+="$4"$'\n'; }
            path_present() { [[ "$removed" != *$'\n'"$1"$'\n'* && ( -e "$1" || -L "$1" ) ]]; }
            timeout() {
              case "${@: -1}" in
                tat_agent.service) printf 'ActiveState=failed\n' ;;
                *) printf 'ActiveState=inactive\n' ;;
              esac
            }
            cleanup_services tat_agent tat_agent_service tat_install
            '''
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        events = self.events.read_text().splitlines()
        self.assertCountEqual(events[:-2], [f"run rm -f -- {path}" for path in files])
        self.assertEqual(
            events[-2:],
            ["run systemctl daemon-reload", "run systemctl reset-failed tat_agent.service"],
        )

    def test_preexisting_mask_is_left_alone_and_passes_verification(self):
        self.enable_systemd()
        self.unit.symlink_to("/dev/null")
        result = self.run_shell(
            r'''
            stat() { printf '0\n'; }
            command_exists() { [[ "$1" == systemctl ]]; }
            timeout() {
              case "$*" in
                *"FragmentPath tat_agent.service")
                  printf 'LoadState=masked\nActiveState=inactive\nFragmentPath=/dev/null\n' ;;
                *FragmentPath*) printf 'LoadState=not-found\nActiveState=inactive\nFragmentPath=\n' ;;
                *"LoadState tat_agent.service") printf 'LoadState=masked\n' ;;
                *LoadState*) printf 'LoadState=not-found\n' ;;
                *) printf 'ActiveState=inactive\n' ;;
              esac
            }
            stop_services tat_agent
            cleanup_services tat_agent
            verify_services tat_agent
            '''
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            self.events.read_text().splitlines(),
            ["run systemctl stop tat_agent.service", "run systemctl daemon-reload"],
        )
        self.assertEqual(os.readlink(self.unit), "/dev/null")

    def test_verify_services_passes_only_when_units_are_gone(self):
        self.enable_systemd()
        gone = "LoadState=not-found\\nActiveState=inactive\\nFragmentPath=\\n"
        remaining = f"LoadState=loaded\\nActiveState=active\\nFragmentPath={self.unit}\\n"
        self.unit.symlink_to("/dev/null")
        for case, state, warning in (
            ("gone", gone, None),
            ("remaining", remaining, f"Service remains or cannot be verified: tat_agent.service (loaded, active, {self.unit})."),
        ):
            with self.subTest(case=case):
                result = self.run_shell(
                    f'''
                    command_exists() {{ [[ "$1" == systemctl ]]; }}
                    timeout() {{
                      case "${{@: -1}}" in
                        tat_agent.service) printf '{state}' ;;
                        *) printf '{gone}' ;;
                      esac
                    }}
                    verify_services tat_agent tat_agent_service tat_install
                    '''
                )
                if warning:
                    self.assert_failed(result)
                    self.assertIn(warning, result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(result.stderr, "")
