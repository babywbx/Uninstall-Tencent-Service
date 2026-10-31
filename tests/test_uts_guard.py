from test_uts import UtsFixture


class GuardTimingTests(UtsFixture):
    def test_slow_scans_count_toward_observation_window(self):
        result = self.run_shell(
            r'''
            SECONDS=0
            sleep() { record "sleep $*"; SECONDS=$((SECONDS + $1)); }
            yunjing_present() { record scan; SECONDS=$((SECONDS + 3)); return 1; }
            guard_check
            printf 'elapsed=%s\n' "$SECONDS"
            '''
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text().splitlines(), ["scan", "sleep 1", "scan"])
        self.assertIn("elapsed=7", result.stdout)

    def test_reappearance_on_final_slow_scan_still_fails(self):
        result = self.run_shell(
            r'''
            SECONDS=0
            sleep() { record "sleep $*"; SECONDS=$((SECONDS + $1)); }
            yunjing_present() { record scan; SECONDS=$((SECONDS + 3)); [[ "$SECONDS" -ge 5 ]]; }
            guard_check
            '''
        )
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        self.assertEqual(self.events.read_text().splitlines(), ["scan", "sleep 1", "scan"])
