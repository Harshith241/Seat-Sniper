"""Run: python3 -m unittest discover tests"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import check_seats as cs  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "cse355_real.json")


def sec(nbr="12345", open_=0, total=40, wl=3, ins=("Ada Smith",), comp="LEC", campus="TEMPE", mode="P"):
    return {"class_number": nbr, "subject": "CSE", "catalog": "355", "title": "", "instructors": list(ins),
            "days": "MWF", "time": "10:30 AM", "campus": campus, "mode": mode, "component": comp,
            "enroll_deadline": "2099-01-01", "open": open_, "total": total, "waitlist": wl,
            "status": "open" if open_ else "closed"}


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        os.environ["SEAT_SNIPER_HOME"] = self.home
        cs.PAUSE = 0

    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cs.main(list(argv))
        return code, buf.getvalue()

    def state(self):
        with open(os.path.join(self.home, "watches.json")) as f:
            return json.load(f)

    def add_section(self, s, calls="yes"):
        self.run_cli("prefs", "--calls", calls)
        with mock.patch.object(cs, "fetch", return_value=[s]):
            code, out = self.run_cli("add", "--term", "2271", "--class-number", s["class_number"],
                                     "--now", "2026-10-24T12:00", "--confirm")
        self.assertEqual(code, 0, out)
        self.assertIn("SAVED w1", out)

    def check(self, secs, now="2026-10-24T16:30", *extra):
        with mock.patch.object(cs, "fetch", side_effect=secs if callable(secs) else lambda *a, **k: secs):
            return self.run_cli("check", "--now", now, *extra)


class TestParse(Base):
    def test_real_fixture(self):
        with open(FIX) as f:
            secs = {s["class_number"]: s for s in map(cs.parse, json.load(f)["classes"])}
        self.assertEqual(secs["66447"]["open"], 0)       # 150/150 closed
        self.assertEqual(secs["66448"]["open"], 57)      # 93/150 open
        self.assertEqual(secs["76374"]["open"], 0)       # 151/150 over-enrolled
        self.assertEqual(secs["66447"]["days"], "TTh")
        self.assertEqual(cs.prof(secs["66447"]), "Prof Claveau")


class TestDiff(Base):
    def test_closed_to_open_alerts_once(self):
        self.add_section(sec(open_=0))
        _, out = self.check([sec(open_=1)])
        self.assertIn("ALERT w1 seats=1", out)
        self.assertIn("call=yes", out)
        self.assertIn("ASU_CLASS_NBR=12345", out)
        _, out = self.check([sec(open_=1)])
        self.assertNotIn("ALERT", out)
        self.assertTrue(out.strip().endswith("QUIET"))

    def test_zero_to_two(self):
        self.add_section(sec(open_=0))
        _, out = self.check([sec(open_=2)])
        self.assertIn("ALERT w1 seats=2", out)

    def test_open_to_closed_rearms(self):
        self.add_section(sec(open_=0))
        self.check([sec(open_=1)])
        _, out = self.check([sec(open_=0)])
        self.assertIn("CLOSED_AGAIN w1", out)
        _, out = self.check([sec(open_=1)])
        self.assertIn("ALERT w1", out)

    def test_quiet_hours_text_no_call(self):
        self.add_section(sec(open_=0))
        _, out = self.check([sec(open_=1)], "2026-10-24T23:15")
        self.assertIn("ALERT w1", out)
        self.assertIn("call=no", out)
        self.assertIn("QUIET_HOURS_HOLD w1", out)

    def test_calls_off(self):
        self.add_section(sec(open_=0), calls="no")
        _, out = self.check([sec(open_=1)])
        self.assertIn("call=no", out)
        self.assertNotIn("QUIET_HOURS_HOLD", out)


class TestFilters(Base):
    def test_professor_filter_picks_right_section(self):
        secs = [sec("1", open_=0, ins=("Ada Smith",)), sec("2", open_=5, ins=("Bob Jones",))]
        with mock.patch.object(cs, "fetch", return_value=secs):
            code, out = self.run_cli("add", "--term", "2271", "--subject", "CSE", "--catalog", "355",
                                     "--instructor", "smith", "--now", "2026-10-24T12:00", "--confirm")
        self.assertIn("FOUND | 1 ", out)
        self.assertNotIn("FOUND | 2 ", out)
        self.assertNotIn("ALREADY_OPEN", out)
        _, out = self.check([sec("1", open_=0, ins=("Ada Smith",)), sec("2", open_=9, ins=("Bob Jones",))])
        self.assertNotIn("ALERT", out)
        _, out = self.check([sec("1", open_=3, ins=("Ada Smith",)), sec("2", open_=9, ins=("Bob Jones",))])
        self.assertIn("cls=1 ", out)

    def test_course_mode_ignores_recitations(self):
        secs = [sec("1", open_=0), sec("2", open_=7, comp="REC")]
        with mock.patch.object(cs, "fetch", return_value=secs):
            _, out = self.run_cli("add", "--term", "2271", "--subject", "CSE", "--catalog", "355",
                                  "--now", "2026-10-24T12:00", "--confirm")
        self.assertNotIn("ALREADY_OPEN", out)

    def test_missing_section_is_error_not_quiet(self):
        self.add_section(sec(open_=0))
        _, out = self.check([])
        self.assertIn("ERROR w1 reason=section_not_found", out)
        self.assertNotIn("QUIET\n", out)


class TestFailures(Base):
    def test_three_fetch_failures_need_human(self):
        self.add_section(sec(open_=0))

        def boom(*a, **k):
            raise cs.FetchError("http_401")
        for _ in range(2):
            _, out = self.check(boom)
            self.assertNotIn("NEEDS_HUMAN", out)
        _, out = self.check(boom)
        self.assertIn("ERROR w1 reason=http_401", out)
        self.assertIn("NEEDS_HUMAN w1", out)

    def test_corrupt_state_is_kept_aside(self):
        with open(os.path.join(self.home, "watches.json"), "w") as f:
            f.write("{not json")
        code, out = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("WARN", out)
        self.assertTrue(any(n.startswith("watches.json.corrupt-") for n in os.listdir(self.home)))

    def test_unpadded_quiet_hours_still_block_calls(self):
        self.add_section(sec(open_=0))
        self.run_cli("prefs", "--quiet", "22:00-7:30")
        _, out = self.check([sec(open_=1)], "2026-10-24T23:00")
        self.assertIn("call=no", out)

    def test_malformed_response_is_error_not_crash(self):
        self.add_section(sec(open_=0))
        with mock.patch.object(cs, "SAMPLE", True), \
                mock.patch("builtins.open", mock.mock_open(read_data='{"classes": [null]}')):
            with self.assertRaises(cs.FetchError):
                cs.fetch("2271", class_number="12345")

    def test_missing_state_lists_none(self):
        code, out = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("NONE", out)

    def test_watch_limit(self):
        st = {"watches": [dict(id="w%d" % i) for i in range(cs.MAX_WATCHES)]}
        with open(os.path.join(self.home, "watches.json"), "w") as f:
            json.dump(st, f)
        with mock.patch.object(cs, "fetch", return_value=[sec("1")]):
            code, out = self.run_cli("add", "--term", "2271", "--class-number", "1")
        self.assertEqual(code, 5)


class TestCommands(Base):
    def test_preview_does_not_save(self):
        with mock.patch.object(cs, "fetch", return_value=[sec()]):
            _, out = self.run_cli("add", "--term", "2271", "--class-number", "12345")
        self.assertIn("PREVIEW", out)
        self.assertFalse(os.path.exists(os.path.join(self.home, "watches.json")))

    def test_snooze_skips_then_resumes(self):
        self.add_section(sec(open_=0))
        self.run_cli("snooze", "w1", "--minutes", "60", "--now", "2026-10-24T16:00")
        _, out = self.check([sec(open_=1)], "2026-10-24T16:30")
        self.assertIn("SNOOZED w1", out)
        _, out = self.check([sec(open_=1)], "2026-10-24T17:10")
        self.assertIn("ALERT w1", out)

    def test_ack_and_remove(self):
        self.add_section(sec(open_=0))
        self.assertIn("ACKED w1", self.run_cli("ack", "w1")[1])
        self.assertTrue(self.state()["watches"][0]["acked"])
        self.assertIn("REMOVED w1", self.run_cli("remove", "w1")[1])
        self.assertEqual(self.state()["watches"], [])

    def test_expired_watch_stops(self):
        self.add_section(sec(open_=0))
        _, out = self.check([sec(open_=0)], "2026-12-30T09:00")
        self.assertIn("EXPIRED w1", out)

    def test_enrollment_closed(self):
        s = sec()
        s["enroll_deadline"] = "2026-08-26"
        with mock.patch.object(cs, "fetch", return_value=[s]):
            code, out = self.run_cli("add", "--term", "2267", "--class-number", "12345", "--now", "2026-10-24T12:00")
        self.assertEqual(code, 3)
        self.assertIn("enrollment_closed", out)

    def test_sample_mode(self):
        code, out = self.run_cli("check", "--sample", "--now", "2026-10-24T16:30")
        self.assertEqual(code, 0)
        self.assertIn("SAMPLE ALERT w1", out)

    def test_json_output(self):
        code, out = self.run_cli("check", "--sample", "--json", "--now", "2026-10-24T16:30")
        self.assertEqual(json.loads(out)["events"][0]["kind"], "ALERT")


if __name__ == "__main__":
    unittest.main()
