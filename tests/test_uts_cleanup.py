from test_uts import ROOT_STAT, UtsFixture


class BackupCleanupTests(UtsFixture):
    def setUp(self):
        super().setUp()
        self.enable_proc()
        self.current = self.root / "var/backups/uts-units.current"
        self.history = self.root / "var/backups/uts-units.previous"
        for directory in (self.current, self.history):
            directory.mkdir(parents=True, mode=0o700)
            (directory / "tat_agent.service").write_text("[Service]\nExecStart=/agent\n")

    def main(self, extra="", options="--yes --force --include-monitor --include-tat"):
        return self.run_shell(
            ROOT_STAT
            + r'''
            MASK_BACKUP="$UTS_TEST_ROOT/var/backups/uts-units.current"
            uninstall_yunjing() { record yunjing; }
            uninstall_monitor() { record monitor; }
            uninstall_tat() { record tat; }
            verify_uninstall() { record verify; }
            run() {
              record "run $*"
              [[ "$1" == rm && "${@: -1}" == "$MASK_BACKUP" ]] || return 97
              command "$@"
            }
            '''
            + extra
            + f"\nmain {options}\n"
        )

    def assert_backups_kept(self):
        for directory in (self.current, self.history):
            self.assertEqual((directory / "tat_agent.service").read_text(), "[Service]\nExecStart=/agent\n")

    def test_verified_success_removes_only_this_runs_backup(self):
        result = self.main()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.current.exists())
        self.assertEqual((self.history / "tat_agent.service").read_text(), "[Service]\nExecStart=/agent\n")
        self.assertEqual(
            self.events.read_text().splitlines(),
            ["yunjing", "monitor", "tat", "verify", f"run rm -rf -- {self.current}"],
        )
        self.assertIn("local uninstall verified", result.stdout)

    def test_uninstall_failure_failed_verification_and_interrupt_keep_backups(self):
        cases = (
            ("uninstall_yunjing() { return 6; }", 6),
            ("uninstall_monitor() { return 1; }", 1),
            ("verify_uninstall() { record verify; return 1; }", 1),
            ("uninstall_tat() { STEP=1; interrupt 143; }", 143),
        )
        for extra, status in cases:
            with self.subTest(extra=extra):
                result = self.main(extra)
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                self.assert_backups_kept()
                self.assertNotIn("run rm ", self.events.read_text())
                self.assertNotIn("local uninstall verified", result.stdout)

    def test_backup_removal_failure_cannot_report_success(self):
        for status in (1, 124, 137):
            with self.subTest(status=status):
                result = self.main(f'run() {{ record "run $*"; return {status}; }}')
                self.assertEqual(result.returncode, status, result.stdout + result.stderr)
                self.assert_backups_kept()
                self.assertIn("Cannot remove this run's service backup", result.stderr)
                self.assertIn("Uninstall-Tencent-Service: incomplete.", result.stderr)
                self.assertNotIn("local uninstall verified", result.stdout)
                self.assertNotIn("No agent files, processes, services or cron entries remain", result.stdout)

    def test_dry_run_keeps_backups_without_attempting_removal(self):
        result = self.main(options="--dry-run --force --include-monitor --include-tat")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_backups_kept()
        self.assertNotIn("verify", self.events.read_text())
        self.assertNotIn("run rm ", self.events.read_text())
        self.assertIn("Dry run finished", result.stdout)

    def test_mounted_backup_is_preserved_and_reported_as_incomplete(self):
        (self.root / "proc/self/mountinfo").write_text(
            f"29 23 0:25 / {self.current} rw - ext4 /dev/mock rw\n"
        )
        result = self.main()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assert_backups_kept()
        self.assertIn("Mounted subtree", result.stderr)
        self.assertNotIn("run rm ", self.events.read_text())
        self.assertNotIn("local uninstall verified", result.stdout)
