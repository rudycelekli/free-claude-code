"""Independent full-suite receipt comparison controls."""
import copy
import unittest
from compare_full_suites import compare_suites, validate_suite


def suite(names):
    return {
        "exitstatus": 0, "collected": names, "worker_collections": [names[:], names[:]],
        "collection_problems": [], "reports": {name: [
            {"phase": phase, "outcome": "passed", "skip_reason": None}
            for phase in ["setup", "call", "teardown"]] for name in names},
    }


class FullSuiteControls(unittest.TestCase):
    def setUp(self):
        self.base = suite(["tests/test_original.py::test_pass"])
        self.fixed = suite(self.base["collected"] + ["tests/new.py::test_new"])

    def compare(self):
        return compare_suites(self.base, self.fixed, "tests/new.py", ["test_new"], "pytest")

    def test_valid_original_and_new(self):
        self.assertEqual(self.compare()["new_cases"], 1)

    def test_lost_original_rejected(self):
        self.fixed = suite(["tests/new.py::test_new"])
        with self.assertRaises(RuntimeError):
            self.compare()

    def test_wrong_new_identity_rejected(self):
        self.fixed = suite(self.base["collected"] + ["tests/new.py::test_other"])
        with self.assertRaises(RuntimeError):
            self.compare()

    def test_new_skip_rejected(self):
        self.fixed["reports"]["tests/new.py::test_new"][1].update(outcome="skipped", skip_reason="not run")
        with self.assertRaises(RuntimeError):
            self.compare()

    def test_original_skip_changed_rejected(self):
        self.fixed["reports"][self.base["collected"][0]][1].update(outcome="skipped", skip_reason="changed")
        with self.assertRaises(RuntimeError):
            self.compare()

    def test_preserved_original_skip_accepted(self):
        name = self.base["collected"][0]
        for receipt in [self.base, self.fixed]:
            receipt["reports"][name][1].update(outcome="skipped", skip_reason="same existing opt-in")
        self.assertEqual(self.compare()["preserved_skips"], 1)

    def test_missing_report_rejected(self):
        del self.fixed["reports"]["tests/new.py::test_new"]
        with self.assertRaises(RuntimeError):
            self.compare()

    def test_missing_call_rejected(self):
        self.fixed["reports"]["tests/new.py::test_new"].pop(1)
        with self.assertRaises(RuntimeError):
            self.compare()

    def test_collection_error_rejected(self):
        self.fixed["collection_problems"] = [{"outcome": "failed"}]
        with self.assertRaises(RuntimeError):
            self.compare()

    def test_nonzero_exit_rejected(self):
        self.fixed["exitstatus"] = 1
        with self.assertRaises(RuntimeError):
            self.compare()

    def test_different_worker_collection_rejected(self):
        self.fixed["worker_collections"][1].pop()
        with self.assertRaises(RuntimeError):
            self.compare()

    def test_browser_extra_test_rejected(self):
        with self.assertRaises(RuntimeError):
            compare_suites(self.base, self.fixed, "tests/new.py", ["test_new"], "e2e")


if __name__ == "__main__":
    unittest.main()
