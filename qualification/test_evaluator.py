"""Negative controls for exact regression identity and outcomes."""
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from native_proof import regression_outcome


class EvaluatorControls(unittest.TestCase):
    def setUp(self):
        self.expected = json.loads(Path(__file__).with_name("candidate-manifest.json").read_text())["regressions"]

    def check(self, mode, mutation=None):
        root = ET.Element("testsuite")
        for name in self.expected["names"]:
            test = ET.SubElement(root, "testcase", name=name)
            if mode == "baseline" and name in self.expected["baseline_failures"]:
                ET.SubElement(test, "failure")
        if mutation:
            mutation(root)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.xml"
            ET.ElementTree(root).write(path)
            return regression_outcome(path, self.expected, mode)

    def test_valid_fixed_and_baseline(self):
        self.assertEqual(self.check("fixed")["tests"], 15)
        self.assertEqual(len(self.check("baseline")["failures"]), 11)

    def test_missing_test_rejected(self):
        with self.assertRaises(RuntimeError):
            self.check("fixed", lambda root: root.remove(root[0]))

    def test_skip_rejected(self):
        with self.assertRaises(RuntimeError):
            self.check("fixed", lambda root: ET.SubElement(root[0], "skipped"))

    def test_unexpected_failure_rejected(self):
        with self.assertRaises(RuntimeError):
            self.check("fixed", lambda root: ET.SubElement(root[0], "failure"))

    def test_wrong_identity_rejected(self):
        with self.assertRaises(RuntimeError):
            self.check("fixed", lambda root: root[0].set("name", "not-the-target"))


if __name__ == "__main__":
    unittest.main()
