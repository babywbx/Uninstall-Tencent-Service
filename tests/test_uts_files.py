import shlex

from test_uts import UtsFixture


RUN_REAL = 'run() { record "run $*"; command "$@"; }\n'


class FileSafetyTests(UtsFixture):
    def setUp(self):
        super().setUp()
        self.installation = self.root / "usr/local/qcloud/YunJing"
        self.installation.mkdir(parents=True)
        self.enable_proc()
        self.mountinfo = self.root / "proc/self/mountinfo"

    def test_tree_removal_rejects_mounts_and_decodes_escaped_paths(self):
        spaced = self.installation.with_name("YunJing data")
        spaced.mkdir()
        for tree, mounted in (
            (self.installation, self.installation),
            (self.installation, self.installation / "nested"),
            (spaced, spaced),
        ):
            with self.subTest(tree=tree, mounted=mounted):
                encoded = str(mounted).replace("\\", "\\134").replace(" ", "\\040")
                self.mountinfo.write_text(f"29 23 0:25 / {encoded} rw - ext4 /dev/mock rw\n")
                result = self.run_shell(
                    "stat() { printf '0\\n'; }\n"
                    f"remove_path {shlex.quote(str(tree))} tree"
                )
                self.assert_failed(result)
                self.assertIn("Mounted subtree", result.stderr)
                self.assertFalse(self.removal_requested(tree), self.events.read_text())
                self.assertTrue(tree.is_dir())

    def test_tree_removal_rejects_malformed_mountinfo(self):
        self.mountinfo.write_text("not a mountinfo record\n")
        result = self.run_shell(
            "stat() { printf '0\\n'; }\n"
            'remove_path "${YUNJING_DIRS[0]}" tree'
        )
        self.assert_failed(result)
        self.assertIn("Cannot inspect mount table", result.stderr)
        self.assertFalse(self.removal_requested(self.installation), self.events.read_text())

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
                    root_crontab() { :; }
                    stop_services() { :; }
                    stop_agent_processes() { :; }
                    cleanup_services() { :; }
                    safe_tree() { :; }
                    '''
                    + command
                    + "\n"
                )
                self.assert_failed(result)
                self.assertIn(f"Refusing directory at a file-only cleanup path: {leftover}.", result.stderr)
                self.assertFalse(self.removal_requested(leftover), self.events.read_text())
                self.assertEqual(keep.read_text(), "keep\n")

    def test_file_cleanup_accepts_standard_var_run_alias(self):
        alias = self.root / "var/run"
        alias.rmdir()
        alias.symlink_to("../run")
        pidfile = self.root / "run/tat_agent.pid"
        pidfile.write_text("1234\n")
        result = self.run_shell(RUN_REAL + 'remove_path "$UTS_TEST_ROOT/var/run/tat_agent.pid"\n')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(pidfile.exists())
        self.assertTrue(alias.is_symlink())

    def test_preflight_rejects_symlinked_qcloud_parent(self):
        self.installation.rmdir()
        self.installation.parent.rmdir()
        unrelated = self.base / "unrelated/YunJing"
        unrelated.mkdir(parents=True)
        kept = unrelated / "keep.txt"
        kept.write_text("unrelated data\n")
        self.installation.parent.symlink_to(unrelated.parent)
        result = self.run_shell(
            "stat() { printf '0\\n'; }\n"
            + self.real_function("preflight_paths")
            + RUN_REAL
            + 'preflight_paths || exit $?\nremove_path "${YUNJING_DIRS[0]}" tree\n'
        )
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(kept.read_text(), "unrelated data\n")
        self.assertIn("Refusing symlinked parent directory", result.stderr)
        self.assertEqual(self.events.read_text(), "")

    def test_cleanup_rejects_symlinked_monitor_parent(self):
        unrelated = self.base / "unrelated/monitor"
        (unrelated / "barad").mkdir(parents=True)
        kept = unrelated / "barad/keep.txt"
        kept.write_text("unrelated data\n")
        child = self.base / "unrelated/empty/child"
        child.mkdir(parents=True)
        for link, command in (
            ("monitor", 'remove_path "${MONITOR_DIRS[0]}/barad" tree'),
            ("empty", 'remove_empty_dir "$UTS_TEST_ROOT/usr/local/qcloud/empty/child"'),
        ):
            with self.subTest(command=command):
                (self.root / f"usr/local/qcloud/{link}").symlink_to(self.base / f"unrelated/{link}")
                result = self.run_shell("stat() { printf '0\\n'; }\n" + RUN_REAL + command + "\n")
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Refusing symlinked parent directory", result.stderr)
                self.assertEqual(self.events.read_text(), "")
                self.assertEqual(kept.read_text(), "unrelated data\n")
                self.assertTrue(child.is_dir())

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

