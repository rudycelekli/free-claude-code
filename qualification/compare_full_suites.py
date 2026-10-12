"""Reject test loss, incomplete reports, new skips and changed original outcomes."""
import argparse
import json
import sys
from pathlib import Path


def validate_suite(receipt):
    if receipt["exitstatus"] != 0:
        raise RuntimeError("Canonical suite exited nonzero")
    ids = receipt["collected"]
    if not ids or len(ids) != len(set(ids)):
        raise RuntimeError("Empty or duplicate canonical collection")
    if any(ids != worker for worker in receipt["worker_collections"]):
        raise RuntimeError("Workers collected different case identities")
    if receipt["collection_problems"]:
        raise RuntimeError("Canonical collection skipped or errored")
    if set(receipt["reports"]) != set(ids):
        raise RuntimeError("Missing or unexpected test reports")
    outcomes = {}
    for nodeid, phases in receipt["reports"].items():
        by_phase = {entry["phase"]: entry for entry in phases}
        if len(by_phase) != len(phases):
            raise RuntimeError("Duplicate phase reports")
        if "setup" not in by_phase or "teardown" not in by_phase:
            raise RuntimeError("Incomplete phase reports")
        if any(entry["outcome"] == "failed" for entry in phases):
            raise RuntimeError("Test setup, call or teardown failed")
        if by_phase["setup"]["outcome"] == "passed" and "call" not in by_phase:
            raise RuntimeError("Missing call outcome")
        skipped = [entry for entry in phases if entry["outcome"] == "skipped"]
        outcomes[nodeid] = {
            "outcome": "skipped" if skipped else "passed",
            "skip_reasons": [entry["skip_reason"] for entry in skipped],
        }
    return outcomes


def compare_suites(baseline, fixed, regression_path, names, lane):
    original, candidate = validate_suite(baseline), validate_suite(fixed)
    expected_new = {regression_path + "::" + name for name in names} if lane == "pytest" else set()
    if set(candidate) - set(original) != expected_new or set(original) - set(candidate):
        raise RuntimeError("Canonical case identities lost or unexpectedly added")
    for nodeid, outcome in original.items():
        if candidate[nodeid] != outcome:
            raise RuntimeError("Original test outcome or skip reason changed: " + nodeid)
    for nodeid in expected_new:
        if candidate[nodeid]["outcome"] != "passed":
            raise RuntimeError("Candidate regression did not pass")
    return {"original_cases": len(original), "fixed_cases": len(candidate),
            "preserved_skips": sum(r["outcome"] == "skipped" for r in original.values()),
            "new_cases": len(expected_new), "status": "PASS"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(Path(__file__).with_name("candidate-manifest.json").read_text())
    pairs = {}
    for receipt_path in args.artifacts.rglob("receipt.json"):
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("mode") not in ["baseline", "fixed"]:
            continue
        key = (receipt["runner_os"], receipt["mode"])
        if key in pairs:
            raise RuntimeError("Duplicate native job receipt")
        if receipt["status"] != "PASS":
            raise RuntimeError("Native job failed: " + str(key))
        pairs[key] = receipt_path.parent
    expected = {(os, mode) for os in ["Linux", "Windows", "macOS"] for mode in ["baseline", "fixed"]}
    if set(pairs) != expected:
        raise RuntimeError("Missing native OS/mode proof")
    results = {}
    for os in ["Linux", "Windows", "macOS"]:
        suites = {}
        for mode in ["baseline", "fixed"]:
            for path in (pairs[os, mode] / "collections").glob("*.json"):
                receipt = json.loads(path.read_text())
                if receipt["stage"] != "complete-native-sequence":
                    continue
                key = (mode, receipt["lane"])
                if key in suites:
                    raise RuntimeError("Duplicate canonical suite invocation")
                suites[key] = receipt
        if set(suites) != {(mode, lane) for mode in ["baseline", "fixed"] for lane in ["pytest", "e2e"]}:
            raise RuntimeError("Full canonical suite receipts missing")
        results[os] = {lane: compare_suites(suites["baseline", lane], suites["fixed", lane],
            manifest["regression_path"], manifest["regressions"]["names"], lane) for lane in ["pytest", "e2e"]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"status": "PASS", "comparisons": results}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if "--output" in sys.argv:
            output = Path(sys.argv[sys.argv.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({"status": "FAIL", "error": str(error)}, indent=2))
        raise
