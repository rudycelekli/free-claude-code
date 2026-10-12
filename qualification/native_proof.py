"""Fork-only native qualification; no application credentials are provisioned."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def regression_outcome(path, expected, mode):
    tests = list(ET.parse(path).iter("testcase"))
    names = [t.attrib["name"] for t in tests]
    if sorted(names) != sorted(expected["names"]):
        raise RuntimeError("Regression identities differ from frozen evaluator")
    if any(t.find("skipped") is not None or t.find("error") is not None for t in tests):
        raise RuntimeError("Regression skipped or errored")
    failed = sorted(t.attrib["name"] for t in tests if t.find("failure") is not None)
    wanted = sorted(expected["baseline_failures"] if mode == "baseline" else [])
    if failed != wanted:
        raise RuntimeError("Regression failures differ from frozen evaluator")
    return {"tests": len(tests), "failures": failed, "skips": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--mode", choices=["baseline", "fixed"], required=True)
    parser.add_argument("--fixed-sha", required=True)
    args = parser.parse_args()
    if os.environ.get("GITHUB_REPOSITORY") != "rudycelekli/free-claude-code":
        raise RuntimeError("Personal-fork guard rejected repository")
    if not re.fullmatch(r"[0-9a-f]{40}", args.fixed_sha):
        raise RuntimeError("Fixed revision must be an immutable full commit SHA")
    source, proof = args.source.resolve(), args.proof.resolve()
    proof.mkdir(parents=True, exist_ok=True)
    own = Path(__file__).resolve().parent
    manifest = json.loads((own / "candidate-manifest.json").read_text())
    if args.fixed_sha != manifest["fixed_sha"]:
        raise RuntimeError("Fixed commit differs from frozen manifest identity")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    expected_head = manifest["base"] if args.mode == "baseline" else args.fixed_sha
    if head != expected_head:
        raise RuntimeError("Checkout revision mismatch")
    configs = {}
    for option in ["core.autocrlf", "core.eol"]:
        result = subprocess.run(["git", "config", "--show-origin", "--get-all", option], cwd=source, capture_output=True, text=True)
        configs[option] = {"exit": result.returncode, "output": result.stdout.strip()}
    git_executable = shutil.which("git")
    bash_executable = shutil.which("bash")
    derived_bash = str(Path(git_executable).resolve().parent.parent / "bin" / "bash.exe") if git_executable and sys.platform == "win32" else None
    git_layout = {
        "git_executable": git_executable,
        "bash_executable": bash_executable,
        "test_derived_bash": derived_bash,
        "test_derived_bash_exists": Path(derived_bash).is_file() if derived_bash else None,
    }
    (proof / "git-executable-inventory.json").write_text(json.dumps(git_layout, indent=2))
    checkout_files = {}
    for filename in manifest["fixed_files"]:
        blob = subprocess.run(["git", "show", head + ":" + filename], cwd=source, capture_output=True)
        file = source / filename
        if blob.returncode or not file.exists():
            checkout_files[filename] = {"exists_in_commit": blob.returncode == 0, "exists_in_checkout": file.exists()}
            continue
        raw = file.read_bytes()
        checkout_files[filename] = {
            "git_blob_sha256": hashlib.sha256(blob.stdout).hexdigest(),
            "checkout_raw_sha256": hashlib.sha256(raw).hexdigest(),
            "checkout_equals_blob": raw == blob.stdout,
            "raw_crlf_count": raw.count(b"\r\n"),
            "raw_lf_count": raw.count(b"\n"),
            "only_lf_to_crlf_difference": raw != blob.stdout and raw.replace(b"\r\n", b"\n") == blob.stdout,
        }
    (proof / "checkout-byte-inventory.json").write_text(json.dumps({"head": head, "git_config": configs, "files": checkout_files}, indent=2))
    if any(record.get("exists_in_commit", True) and not record.get("checkout_equals_blob", False) for record in checkout_files.values()):
        raise RuntimeError("Raw checkout bytes differ from exact Git blob; inventory preserved")
    if args.mode == "fixed":
        changed = subprocess.check_output(["git", "diff", "--name-only", manifest["base"], head], cwd=source, text=True).splitlines()
        if sorted(changed) != sorted(manifest["fixed_files"]):
            raise RuntimeError("Fixed commit scope differs from frozen candidate")
        for filename, digest in manifest["fixed_files"].items():
            if sha(source / filename) != digest:
                raise RuntimeError("Fixed source hash mismatch: " + filename)
    records = []

    def run(label, command, timeout=3600):
        log = proof / (label + ".log")
        with log.open("w", encoding="utf-8") as out:
            out.write(json.dumps(command) + "\n")
            out.flush()
            try:
                env = os.environ.copy()
                env["FCC_NATIVE_STAGE"] = label
                env["FCC_SOURCE_ROOT"] = str(source)
                env["FCC_COLLECTION_OUTPUT"] = str(proof / "collections")
                env["PYTEST_PLUGINS"] = ",".join(filter(None, [env.get("PYTEST_PLUGINS"), "qualification_collector"]))
                env["PYTHONPATH"] = str(own) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
                result = subprocess.run(command, cwd=source, env=env, stdout=out, stderr=subprocess.STDOUT, timeout=timeout)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = 124
        records.append({"label": label, "command": command, "exit": code, "log_sha256": sha(log)})
        (proof / "commands.json").write_text(json.dumps(records, indent=2))
        return code

    receipt = {"mode": args.mode, "base": manifest["base"], "head": head, "runner_os": os.environ.get("RUNNER_OS"), "execution": "hosted native; not local workstation", "status": "FAIL", "runner_sha256": sha(Path(__file__)), "manifest_sha256": sha(own / "candidate-manifest.json")}
    try:
        if run("uv-version", ["uv", "--version"]):
            raise RuntimeError("uv version command failed")
        if not re.search(r"^uv 0\.12\.13(?:[ \n]|$)", (proof / "uv-version.log").read_text(), re.MULTILINE):
            raise RuntimeError("Exact uv0.12.13 runtime unavailable")
        runtime_code = (
            "import json, os, platform, sys; from pathlib import Path; "
            "record = {'python': sys.version, 'system': platform.system(), "
            "'platform': platform.platform(), 'machine': platform.machine(), "
            "'runner_os': os.environ.get('RUNNER_OS')}; "
            "Path(sys.argv[1]).write_text(json.dumps(record, indent=2)); print(json.dumps(record)); "
            "assert sys.version_info[:3] == (3, 14, 7); "
            "assert record['system'] == {'Linux': 'Linux', 'Windows': 'Windows', 'macOS': 'Darwin'}[record['runner_os']]"
        )
        if run("runtime", ["uv", "run", "--no-project", "--python", "3.14.7", "python", "-c", runtime_code, str(proof / "runtime-environment.json")]):
            raise RuntimeError("Exact Python3.14.7 runtime unavailable")
        if run("dependency-sync", ["uv", "sync", "--locked", "--group", "dev"]):
            raise RuntimeError("Locked native dependency setup failed")
        if run("node-readiness", ["node", "--experimental-strip-types", "--input-type=module", "--eval", "import { stripTypeScriptTypes } from 'node:module'; stripTypeScriptTypes('const value: number = 1'); await new Response('ready').text(); console.log(process.version);"]):
            raise RuntimeError("Native Node subprocess runtime unavailable")
        if sys.platform.startswith("linux") and run("chromium-os-dependencies", ["uv", "run", "--locked", "playwright", "install-deps", "chromium"]):
            raise RuntimeError("Chromium OS dependencies failed")
        sequence = ["pwsh", "-NoProfile", "-File", "scripts/ci.ps1"] if sys.platform == "win32" else ["sh", "scripts/ci.sh"]
        sequence_exit = run("complete-native-sequence", sequence)
        receipt["complete_sequence_exit"] = sequence_exit
        # If the canonical sequence stops early, collect every stage's actual outcome.
        if sequence_exit:
            for check in ["suppressions", "ruff-format", "ruff-check", "ty", "pytest", "playwright"]:
                run("diagnostic-" + check, sequence + (["-Only", check] if sys.platform == "win32" else ["--only", check]))
        status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=source, text=True)
        (proof / "post-sequence-tracked-status.txt").write_text(status)
        if status:
            raise RuntimeError("Native repair commands changed frozen tracked source")
        if args.mode == "baseline":
            regression_file = source / manifest["regression_path"]
            if regression_file.exists():
                raise RuntimeError("Baseline unexpectedly has candidate regression file")
            shutil.copyfile(own / "test_markdown_table_fences.py", regression_file)
        junit = proof / "regressions.xml"
        regression_exit = run("exact-regressions", ["uv", "run", "--locked", "pytest", manifest["regression_path"], "-v", "--tb=short", "--junitxml=" + str(junit)])
        receipt["regression_exit"] = regression_exit
        receipt["regressions"] = regression_outcome(junit, manifest["regressions"], args.mode)
        if regression_exit != (1 if args.mode == "baseline" else 0):
            raise RuntimeError("Unexpected regression runner exit")
        if sequence_exit:
            raise RuntimeError("Full native sequence failed")
        from compare_full_suites import validate_suite
        canonical = {}
        for path in (proof / "collections").glob("*.json"):
            collection = json.loads(path.read_text())
            if collection["stage"] == "complete-native-sequence":
                if collection["lane"] in canonical:
                    raise RuntimeError("Duplicate canonical suite invocation")
                outcomes = validate_suite(collection)
                canonical[collection["lane"]] = {"cases": len(outcomes), "skipped": sum(r["outcome"] == "skipped" for r in outcomes.values()), "receipt": path.name, "sha256": sha(path)}
        if set(canonical) != {"pytest", "e2e"}:
            raise RuntimeError("Canonical pytest/browser receipt missing")
        receipt["canonical_suites"] = canonical
        receipt["status"] = "PASS"
    except Exception as error:
        receipt["error"] = str(error)
    finally:
        (proof / "receipt.json").write_text(json.dumps(receipt, indent=2))
        results = source / "test-results"
        if results.is_dir():
            for result in results.rglob("*"):
                if result.is_file() and result.suffix.lower() in {".png", ".jpg", ".jpeg", ".log", ".json", ".xml", ".txt"}:
                    target = proof / "test-results" / result.relative_to(results)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(result, target)
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
