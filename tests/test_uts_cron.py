import os
import shlex
import stat

from test_uts import UtsFixture


SGAGENT_LINE = (
    "*/5 * * * * flock -xn /tmp/stargate.lock -c "
    "'/usr/local/qcloud/stargate/admin/start.sh > /dev/null 2>&1 &'"
)
SGAGENTTASK_LINE = (
    "* * * * * root flock -xn /tmp/stargate.lock -c "
    "'/usr/local/qcloud/stargate/admin/start.sh > /dev/null 2>&1 &'"
)
YUNJING_LINE = "0 0 * * * /usr/local/qcloud/YunJing/YDCrontab.sh > /dev/null 2>&1 &"
BARAD_LINE = "*/5 * * * * /usr/local/qcloud/monitor/barad/admin/trystart.sh"
TAT_LINE = "* * * * * /usr/local/qcloud/tat_agent/install/init_install.sh"
OTHER_COMPONENT_LINE = "*/5 * * * * /usr/local/qcloud/monitor/other/agent.sh"
OTHER_LINES = (
    "SHELL=/bin/bash",
    "# nightly backup",
    "",
    "0 3 * * * /usr/local/bin/backup.sh",
    "  0  4  *  *  * \t/opt/tool/run.sh   --flag  ",
)
MIXED_LISTING = "\n".join((*OTHER_LINES[:2], SGAGENT_LINE, *OTHER_LINES[2:])) + "\n"
MONITOR_PATTERNS = "/qcloud/stargate/ /qcloud/monitor/barad/ /qcloud/monitor/python26/"
COMPOUND_COMMANDS = (
    "/usr/local/qcloud/YunJing/YDCrontab.sh && /opt/backup.sh",
    "/opt/backup.sh && /usr/local/qcloud/YunJing/YDCrontab.sh",
    "/usr/local/qcloud/YunJing/YDCrontab.sh & /opt/backup.sh",
    "/usr/local/qcloud/YunJing/YDCrontab.sh 2>&1 & /opt/backup.sh &",
    "sh -c '/usr/local/qcloud/YunJing/YDCrontab.sh'",
    "flock /tmp/other.lock /usr/local/qcloud/YunJing/YDCrontab.sh",
    "flock -xn /tmp/stargate.lock -c '/usr/local/qcloud/YunJing/YDCrontab.sh' && /opt/backup.sh",
    "flock -xn /tmp/stargate.lock -c '/usr/local/qcloud/YunJing/YDCrontab.sh' > /tmp/agent.log",
    "/usr/bin/env -S 'bash /usr/local/qcloud/YunJing/YDCrontab.sh'",
)
WRAPPED_AGENT_COMMANDS = (
    "/usr/bin/env bash /usr/local/qcloud/YunJing/YDCrontab.sh",
    "env -- /bin/bash '/usr/local/qcloud/YunJing/YDCrontab.sh'",
    "flock -xn /tmp/stargate.lock -c '/usr/local/qcloud/YunJing/YDCrontab.sh > /dev/null 2>&1 &' # keep running",
    'flock -xn /tmp/stargate.lock -c "env bash /usr/local/qcloud/YunJing/YDCrontab.sh" # keep running',
    "env flock -xn /tmp/stargate.lock -c '/usr/local/qcloud/YunJing/YDCrontab.sh'",
)
WRAPPED_REFERENCES = (
    "/usr/bin/env /bin/echo '/usr/local/qcloud/YunJing/YDCrontab.sh'",
    "env echo /usr/local/qcloud/YunJing/YDCrontab.sh",
    "flock -xn /tmp/backup.lock -c '/opt/backup' # /usr/local/qcloud/YunJing/YDCrontab.sh",
    "flock -xn /tmp/backup.lock -c '/bin/echo /usr/local/qcloud/YunJing/YDCrontab.sh' # keep reference",
)
YUNJING_JOBS = (
    "*/30 * * * * root /usr/local/qcloud/YunJing/YDCrontab.sh > /dev/null 2>&1\n"
    "@reboot root sleep 30 && /usr/local/qcloud/YunJing/YDCrontab.sh > /dev/null 2>&1\n"
)
KEEP_JOBS = (
    "SHELL=/bin/bash\n"
    "# unrelated backup\n"
    "0 3 * * * root /opt/backup --exclude=/usr/local/qcloud/YunJing/\n"
)


class RootCrontabTests(UtsFixture):
    def setUp(self):
        super().setUp()
        self.listing = self.root / "tmp/root.crontab"
        self.written = self.root / "tmp/crontab"

    def crontab_mocks(self, status=0, diagnostic="no crontab for root"):
        return f'''
            WORKDIR="$UTS_TEST_ROOT/tmp"
            LC_ALL=POSIX
            timeout() {{
              if [[ "$*" != "-k 5 120 crontab -u root -l" || "$LC_ALL" != C ]]; then blocked timeout "$@"; return 97; fi
              [[ ! -f "$UTS_TEST_ROOT/tmp/root.crontab" ]] || cat "$UTS_TEST_ROOT/tmp/root.crontab"
              [[ {status} -eq 0 ]] || printf '%s\\n' {shlex.quote(diagnostic)} >&2
              return {status}
            }}
        '''

    def test_agent_line_is_removed_and_other_lines_are_kept_byte_for_byte(self):
        self.listing.write_text(MIXED_LISTING)
        result = self.run_shell(self.crontab_mocks() + f"remove_cron_lines {MONITOR_PATTERNS}")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, f"[INFO] Removing root crontab line: {SGAGENT_LINE}\n")
        self.assertEqual(self.written.read_bytes(), ("\n".join(OTHER_LINES) + "\n").encode())
        self.assertEqual(self.events.read_text(), f"run crontab -u root {self.written}\n")

    def test_crontab_holding_only_agent_line_is_removed_entirely(self):
        for listing in (SGAGENT_LINE + "\n", "\n" + SGAGENT_LINE + "\n\n"):
            with self.subTest(listing=listing):
                self.listing.write_text(listing)
                result = self.run_shell(self.crontab_mocks() + f"remove_cron_lines {MONITOR_PATTERNS}")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"Removing root crontab line: {SGAGENT_LINE}", result.stdout)
                self.assertEqual(self.events.read_text(), "run crontab -u root -r\n")
                self.assertFalse(self.written.exists())

    def test_dry_run_previews_crontab_change_without_writing(self):
        self.listing.write_text(MIXED_LISTING)
        before = self.file_snapshot()
        result = self.run_shell(
            self.crontab_mocks() + f"exec 3>&1\nDRY_RUN=1\nremove_cron_lines {MONITOR_PATTERNS}"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, f"[DRY-RUN] remove root crontab line: {SGAGENT_LINE}\n")
        self.assertEqual(self.events.read_text(), "")
        self.assertEqual(before, self.file_snapshot())

    def test_concurrent_crontab_changes_are_not_overwritten(self):
        for original in (SGAGENT_LINE + "\n", MIXED_LISTING):
            with self.subTest(original=original):
                self.listing.write_text(original)
                result = self.run_shell(
                    r'''
                    WORKDIR="$UTS_TEST_ROOT/tmp"
                    root_crontab() {
                      QUERY_OUTPUT="$(cat "$WORKDIR/root.crontab")"
                      printf '%s\n' '0 4 * * * /usr/local/bin/new-backup.sh' >> "$WORKDIR/root.crontab"
                    }
                    remove_cron_lines /qcloud/stargate/
                    '''
                )
                self.assert_failed(result)
                self.assertIn("changed during cleanup", result.stderr)
                self.assertEqual(self.events.read_text(), "")
                self.assertIn("new-backup.sh", self.listing.read_text())
                self.assertFalse(self.written.exists())

    def test_missing_root_crontab_is_treated_as_empty(self):
        for status, diagnostic in (
            (1, "no crontab for root"),
            (2, "no crontab for root"),
            (1, "crontab: can't open 'root': No such file or directory"),
        ):
            with self.subTest(status=status, diagnostic=diagnostic):
                result = self.run_shell(
                    self.crontab_mocks(status, diagnostic) + "root_crontab\nremove_cron_lines /qcloud/YunJing/"
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout + result.stderr, "")
                self.assertEqual(self.events.read_text(), "")

    def test_unreadable_root_crontab_stops_cron_cleanup(self):
        spool = self.root / "var/spool/cron/crontabs/root"
        cases = [(f"status {status}", status, "", None) for status in (2, 124, 125, 126, 127, 137, 143)]
        cases += [
            ("permission denied", 1, "permission denied", None),
            ("no diagnostic", 1, "", None),
            ("partial output", 1, "no crontab for root", SGAGENT_LINE + "\n"),
            ("spool file", 1, "no crontab for root", "spool"),
        ]
        for name, status, diagnostic, listing in cases:
            with self.subTest(case=name):
                self.listing.unlink(missing_ok=True)
                if listing == "spool":
                    spool.parent.mkdir(parents=True, exist_ok=True)
                    spool.write_text(SGAGENT_LINE + "\n")
                elif listing:
                    self.listing.write_text(listing)
                result = self.run_shell(
                    self.crontab_mocks(status, diagnostic) + f"remove_cron_lines {MONITOR_PATTERNS}"
                )
                self.assert_failed(result)
                self.assertIn("Cannot read the root crontab.", result.stderr)
                self.assertEqual(self.events.read_text(), "")
                self.assertFalse(self.written.exists())

    def test_removal_is_scoped_to_component_patterns(self):
        decoy = "0 1 * * * /home/admin/YDCrontab.sh"
        lines = (YUNJING_LINE, SGAGENT_LINE, BARAD_LINE, TAT_LINE, OTHER_COMPONENT_LINE, decoy)
        self.listing.write_text("\n".join(lines) + "\n")
        for patterns, removed in (
            ("/qcloud/YunJing/", (YUNJING_LINE,)),
            (MONITOR_PATTERNS, (SGAGENT_LINE, BARAD_LINE)),
            ("/qcloud/tat_agent/", (TAT_LINE,)),
        ):
            with self.subTest(patterns=patterns):
                result = self.run_shell(self.crontab_mocks() + f"remove_cron_lines {patterns}")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(
                    result.stdout,
                    "".join(f"[INFO] Removing root crontab line: {line}\n" for line in removed),
                )
                kept = [line for line in lines if line not in removed]
                self.assertEqual(self.written.read_text(), "\n".join(kept) + "\n")
                self.assertEqual(self.events.read_text(), f"run crontab -u root {self.written}\n")
                self.written.unlink()

    def test_agent_paths_in_backup_and_echo_arguments_are_preserved(self):
        references = (
            "0 1 * * * /opt/backup --exclude=/usr/local/qcloud/YunJing/",
            "0 2 * * * /bin/echo '/usr/local/qcloud/YunJing/YDCrontab.sh'",
            "0 3 * * * /usr/bin/tar -C /usr/local/qcloud/YunJing/ -cf /backup/agent.tar .",
            "# /usr/local/qcloud/YunJing/YDCrontab.sh",
        )
        self.listing.write_text("\n".join((*references, YUNJING_LINE)) + "\n")
        result = self.run_shell(self.crontab_mocks() + "remove_cron_lines /qcloud/YunJing/")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.written.read_text(), "\n".join(references) + "\n")
        spool = self.root / "var/spool/cron/root"
        spool.parent.mkdir(parents=True)
        spool.write_text("\n".join(references) + "\n")
        verified = self.run_shell("cron_remnants /qcloud/YunJing/")
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)

    def test_known_agent_command_forms_are_removed(self):
        path = "/usr/local/qcloud/YunJing/YDCrontab.sh"
        commands = (
            path,
            f"{path}&",
            f"'{path}'",
            f'"{path}" > /dev/null 2>&1 &',
            f"bash {path}",
            f"/bin/bash -- '{path}'",
            f"flock -xn /tmp/stargate.lock -c '{path} > /dev/null 2>&1 &'",
            f"/usr/bin/flock -xn /tmp/stargate.lock -c '/bin/bash {path}'",
        )
        lines = [f"*/30 * * * * {command}" for command in commands]
        lines.append(f"@reboot sleep 30 && {path} > /dev/null 2>&1")
        self.listing.write_text("\n".join(lines) + "\n")
        result = self.run_shell(self.crontab_mocks() + "remove_cron_lines /qcloud/YunJing/")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text(), "run crontab -u root -r\n")
        self.assertEqual(result.stdout.count("Removing root crontab line:"), len(lines))

    def test_wrapped_agent_jobs_are_removed_without_deleting_references(self):
        kept = "".join(f"* * * * * {command}\n" for command in WRAPPED_REFERENCES)
        jobs = "".join(f"* * * * * {command}\n" for command in WRAPPED_AGENT_COMMANDS)
        self.listing.write_text(jobs + kept)
        result = self.run_shell(self.crontab_mocks() + "remove_cron_lines /qcloud/YunJing/")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.written.exists(), result.stdout + result.stderr)
        self.assertEqual(self.written.read_text(), kept)
        self.assertEqual(result.stdout.count("Removing root crontab line:"), len(WRAPPED_AGENT_COMMANDS))

    def test_compound_agent_cron_is_preserved_and_reported(self):
        spool = self.root / "var/spool/cron/root"
        spool.parent.mkdir(parents=True)
        for command in COMPOUND_COMMANDS:
            with self.subTest(command=command):
                line = f"0 * * * * {command}\n"
                self.listing.write_text(line)
                result = self.run_shell(self.crontab_mocks() + "remove_cron_lines /qcloud/YunJing/")
                self.assert_failed(result)
                self.assertIn("Complex agent cron command requires manual review", result.stderr)
                self.assertEqual(self.events.read_text(), "")
                self.assertEqual(self.listing.read_text(), line)
                self.assertFalse(self.written.exists())
                spool.write_text(line)
                verified = self.run_shell("cron_remnants /qcloud/YunJing/")
                self.assert_failed(verified)
                self.assertIn(str(spool), verified.stderr)


class SystemCronTests(UtsFixture):
    def setUp(self):
        super().setUp()
        self.cron = self.root / "etc/cron.d/yunjing"
        self.cron.parent.mkdir()

    def clean(self, prefix=""):
        return self.run_shell(
            r'''
            run() {
              record "run $*"
              case "$1" in mv|rm) ;; *) blocked run "$@"; return 97 ;; esac
              [[ "${@: -1}" == "$UTS_TEST_ROOT/"* ]] || { blocked run "$@"; return 97; }
              if [[ "$1" == mv ]]; then
                [[ "$4" == "$UTS_TEST_ROOT/"* ]] || { blocked run "$@"; return 97; }
              fi
              command "$@"
            }
            '''
            + prefix
            + '\nremove_system_cron_lines "$UTS_TEST_ROOT/etc/cron.d/yunjing" /qcloud/YunJing/\n'
        )

    def test_official_schedule_is_removed_and_empty_file_deleted(self):
        self.cron.write_text(YUNJING_JOBS)
        result = self.clean()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.cron.exists())
        self.assertEqual(result.stdout.count("Removing "), 2)
        self.assertEqual(self.events.read_text(), f"run rm -f -- {self.cron}\n")

    def test_mixed_jobs_and_permissions_are_preserved(self):
        self.cron.write_text(YUNJING_JOBS + KEEP_JOBS)
        self.cron.chmod(0o640)
        owner = self.cron.stat().st_uid
        result = self.clean()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.cron.read_text(), KEEP_JOBS)
        self.assertEqual(stat.S_IMODE(self.cron.stat().st_mode), 0o640)
        self.assertEqual(self.cron.stat().st_uid, owner)
        self.assertFalse(list(self.cron.parent.glob(".uts-cron.*")))

    def test_wrapped_system_jobs_are_removed_without_deleting_references(self):
        kept = "".join(f"* * * * * root {command}\n" for command in WRAPPED_REFERENCES)
        jobs = "".join(f"* * * * * root {command}\n" for command in WRAPPED_AGENT_COMMANDS)
        self.cron.write_text(jobs + kept)
        result = self.clean()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.cron.read_text(), kept)
        self.assertEqual(result.stdout.count("Removing "), len(WRAPPED_AGENT_COMMANDS))

    def test_dry_run_preserves_contents_and_permissions(self):
        self.cron.write_text(YUNJING_JOBS + KEEP_JOBS)
        self.cron.chmod(0o600)
        before = self.file_snapshot()
        result = self.clean("exec 3>&1\nDRY_RUN=1\n")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("[DRY-RUN] remove"), 2)
        self.assertEqual(self.events.read_text(), "")
        self.assertEqual(self.file_snapshot(), before)

    def test_non_regular_file_is_rejected_untouched(self):
        target = self.root / "var/important.cron"
        target.write_text(YUNJING_JOBS + KEEP_JOBS)
        for kind in ("symlink", "directory"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    self.cron.symlink_to(target)
                else:
                    self.cron.unlink()
                    self.cron.mkdir()
                    (self.cron / "important.txt").write_text("keep\n")
                result = self.clean()
                self.assert_failed(result)
                self.assertIn("Refusing non-regular system cron file", result.stderr)
                self.assertEqual(self.events.read_text(), "")
                self.assertEqual(target.read_text(), YUNJING_JOBS + KEEP_JOBS)
                if kind == "symlink":
                    self.assertTrue(self.cron.is_symlink())
                else:
                    self.assertEqual((self.cron / "important.txt").read_text(), "keep\n")

    def test_hardlinked_original_is_preserved_when_replacing_mixed_file(self):
        target = self.root / "var/important.cron"
        target.write_text(YUNJING_JOBS + KEEP_JOBS)
        target.chmod(0o640)
        os.link(target, self.cron)
        original_inode = target.stat().st_ino
        result = self.clean()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(target.read_text(), YUNJING_JOBS + KEEP_JOBS)
        self.assertEqual(target.stat().st_ino, original_inode)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
        self.assertEqual(self.cron.read_text(), KEEP_JOBS)
        self.assertNotEqual(self.cron.stat().st_ino, original_inode)

    def test_compound_agent_jobs_abort_without_partial_edits(self):
        for command in COMPOUND_COMMANDS:
            with self.subTest(command=command):
                original = YUNJING_JOBS + f"0 * * * * root {command}\n" + KEEP_JOBS
                self.cron.write_text(original)
                self.cron.chmod(0o640)
                result = self.clean()
                self.assert_failed(result)
                self.assertEqual(self.cron.read_text(), original)
                self.assertEqual(stat.S_IMODE(self.cron.stat().st_mode), 0o640)
                self.assertEqual(self.events.read_text(), "")
                self.assertFalse(list(self.cron.parent.glob(".uts-cron.*")))
                verified = self.run_shell("cron_remnants /qcloud/YunJing/")
                self.assert_failed(verified)
                self.assertIn(str(self.cron), verified.stderr)


class CronRemnantTests(UtsFixture):
    def system_cron_files(self):
        cron_d = self.root / "etc/cron.d"
        cron_d.mkdir()
        (cron_d / "logrotate").write_text("0 2 * * * root /usr/sbin/logrotate /etc/logrotate.conf\n")
        (self.root / "etc/crontab").write_text(
            "SHELL=/bin/sh\n17 * * * * root run-parts /etc/cron.hourly\n"
            "*/5 * * * * root /usr/local/qcloud/monitor/other/agent.sh\n"
        )
        spool = self.root / "var/spool/cron"
        (spool / "crontabs").mkdir(parents=True)
        (spool / "root").write_text(f"30 6 * * * /usr/local/bin/backup.sh\n{OTHER_COMPONENT_LINE}\n")
        (spool / "crontabs/root").write_text("30 6 * * * /usr/local/bin/backup.sh\n")
        return cron_d, spool

    def selected(self, command):
        return (
            "INCLUDE_MONITOR=1\nINCLUDE_TAT=1\npatterns=()\n"
            'while IFS= read -r pattern; do patterns+=("$pattern"); done < <(selected_cron_patterns)\n'
            f'{command} "${{patterns[@]}}"\n'
        )

    def test_cron_remnants_reports_matching_system_cron_files(self):
        cron_d, spool = self.system_cron_files()
        (cron_d / "sgagenttask").write_text(SGAGENTTASK_LINE + "\n")
        (spool / "crontabs/root").write_text(f"30 6 * * * /usr/local/bin/backup.sh\n{YUNJING_LINE}\n")
        result = self.run_shell(self.selected("cron_remnants"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"Cron entry remains in {cron_d}/sgagenttask:\n{SGAGENTTASK_LINE}\n", result.stderr)
        self.assertIn(f"Cron entry remains in {spool}/crontabs/root:\n{YUNJING_LINE}\n", result.stderr)
        self.assertEqual(result.stderr.count("Cron entry remains"), 2)
        self.assertNotIn("backup.sh", result.stderr)
        self.assertNotIn("logrotate", result.stderr)
        self.assertNotIn("monitor/other", result.stderr)
        self.assertEqual(self.events.read_text(), "")

    def test_cron_remnants_passes_when_only_other_jobs_match(self):
        self.system_cron_files()
        result = self.run_shell(self.selected("cron_remnants"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.events.read_text(), "")

    def test_wrapped_jobs_are_detected_in_root_and_system_cron(self):
        cron_d, spool = self.system_cron_files()
        for path, user in ((cron_d / "yunjing", "root "), (spool / "root", "")):
            with self.subTest(path=path):
                for command in WRAPPED_AGENT_COMMANDS:
                    with self.subTest(command=command):
                        path.write_text(f"* * * * * {user}{command}\n")
                        result = self.run_shell("cron_remnants /qcloud/YunJing/")
                        self.assert_failed(result)
                        self.assertIn(str(path), result.stderr)
                path.write_text("".join(f"* * * * * {user}{command}\n" for command in WRAPPED_REFERENCES))
                result = self.run_shell("cron_remnants /qcloud/YunJing/")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stderr, "")

    def test_cron_verification_rejects_read_errors(self):
        cron = self.root / "etc/crontab"
        cron.write_text("*/5 * * * * root /usr/local/qcloud/YunJing/YDCrontab.sh\n")
        result = self.run_shell("grep() { return 2; }\ncron_remnants /qcloud/YunJing/")
        self.assert_failed(result)
        self.assertIn(f"Cannot inspect cron file: {cron}.", result.stderr)
        self.assertEqual(self.events.read_text(), "")
