import shlex

from test_uts import UtsFixture


RUN_REAL = 'run() { record "run $*"; command "$@"; }\n'


class FileSafetyTests(UtsFixture):
    def setUp(self):
        super().setUp()
        self.installation = self.root / "usr/local/qcloud/YunJing"
        self.installation.mkdir(parents=True)

    def test_file_symlink_removal_preserves_its_target(self):
        target = self.root / "var/keep.txt"
        target.write_text("keep\n")
        link = self.root / "usr/local/bin/tat_agent"
        link.parent.mkdir(parents=True)
        link.symlink_to(target)
        result = self.run_shell(
            '''
            run() {
              record "run $*"
              if [[ "$1" != rm || "$2" != -f || "$3" != -- || "$4" != "$UTS_TEST_ROOT/"* ]]; then
                blocked run "$@"
                return 97
              fi
              command "$@"
            }
            remove_path "$UTS_TEST_ROOT/usr/local/bin/tat_agent"
            '''
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(link.is_symlink())
        self.assertEqual(target.read_text(), "keep\n")
        self.assertEqual(self.events.read_text(), f"run rm -f -- {link}\n")

    def test_file_only_paths_never_remove_directories_recursively(self):
        for relative, command in (
            ("var/run/tat_agent.pid", 'remove_path "$UTS_TEST_ROOT/var/run/tat_agent.pid"'),
            ("tmp/stargate.lock", "uninstall_monitor"),
        ):
            with self.subTest(path=relative):
                leftover = self.root / relative
                leftover.mkdir(parents=True)
                keep = leftover / "unrelated.txt"
                keep.write_text("keep\n")
                result = self.run_shell(
                    RUN_REAL
                    + r'''
                    INCLUDE_MONITOR=1
                    '''
                    + command
                    + "\n"
                )
                self.assert_failed(result)
                self.assertIn(f"Refusing directory at a file-only cleanup path: {leftover}.", result.stderr)
                self.assertFalse(self.removal_requested(leftover), self.events.read_text())
                self.assertEqual(keep.read_text(), "keep\n")

    def test_remove_paths_keeps_going_after_a_failure(self):
        result = self.run_shell(
            'remove_path() { record "remove $1 $2"; [[ "$1" != b ]]; }\n'
            "remove_paths tree a b c\n"
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.events.read_text().splitlines(), ["remove a tree", "remove b tree", "remove c tree"])

    def test_remove_empty_dir_only_removes_a_real_empty_directory(self):
        monitor = self.root / "usr/local/qcloud/monitor"
        monitor.mkdir()
        link = self.root / "usr/local/qcloud/link"
        link.symlink_to(monitor)
        missing = self.root / "usr/local/qcloud/missing"
        busy = self.root / "usr/local/qcloud/busy"
        (busy / "data").mkdir(parents=True)
        result = self.run_shell(
            RUN_REAL
            + "\n".join(f"remove_empty_dir {shlex.quote(str(path))}" for path in (link, missing, busy, monitor))
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text(), f"run rmdir -- {monitor}\n")
        self.assertTrue(link.is_symlink())
        self.assertTrue((busy / "data").is_dir())
        self.assertFalse(monitor.exists())

