import shlex

from test_uts import UtsFixture


SGAGENT_LINE = "*/5 * * * * /usr/local/qcloud/stargate/admin/start.sh > /dev/null 2>&1 &"
YUNJING_LINE = "0 0 * * * /usr/local/qcloud/YunJing/YDCrontab.sh > /dev/null 2>&1 &"
BARAD_LINE = "*/5 * * * * /usr/local/qcloud/monitor/barad/admin/trystart.sh"
OTHER_COMPONENT_LINE = "*/5 * * * * /usr/local/qcloud/monitor/other/agent.sh"
OTHER_LINES = (
    "SHELL=/bin/bash",
    "# nightly backup",
    "",
    "0 3 * * * /usr/local/bin/backup.sh",
    "  0  4  *  *  * \t/opt/tool/run.sh   --flag  ",
)
MIXED_LISTING = "\n".join((*OTHER_LINES[:2], SGAGENT_LINE, *OTHER_LINES[2:])) + "\n"
MONITOR_PATTERNS = "/qcloud/stargate/ /qcloud/monitor/barad/"
COMPOUND_COMMANDS = (
    "/usr/local/qcloud/YunJing/YDCrontab.sh && /opt/backup.sh",
    "/opt/backup.sh && /usr/local/qcloud/YunJing/YDCrontab.sh",
    "/usr/local/qcloud/YunJing/YDCrontab.sh & /opt/backup.sh",
    "/usr/local/qcloud/YunJing/YDCrontab.sh 2>&1 & /opt/backup.sh &",
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
        lines = (YUNJING_LINE, SGAGENT_LINE, BARAD_LINE, OTHER_COMPONENT_LINE, decoy)
        self.listing.write_text("\n".join(lines) + "\n")
        for patterns, removed in (
            ("/qcloud/YunJing/", (YUNJING_LINE,)),
            (MONITOR_PATTERNS, (SGAGENT_LINE, BARAD_LINE)),
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

    def test_known_agent_command_forms_are_removed(self):
        path = "/usr/local/qcloud/YunJing/YDCrontab.sh"
        commands = (
            path,
            f"{path}&",
            f"'{path}'",
            f'"{path}" > /dev/null 2>&1 &',
        )
        lines = [f"*/30 * * * * {command}" for command in commands]
        lines.append(f"@reboot {path} > /dev/null 2>&1")
        self.listing.write_text("\n".join(lines) + "\n")
        result = self.run_shell(self.crontab_mocks() + "remove_cron_lines /qcloud/YunJing/")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text(), "run crontab -u root -r\n")
        self.assertEqual(result.stdout.count("Removing root crontab line:"), len(lines))

    def test_compound_agent_cron_is_preserved_and_reported(self):
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
