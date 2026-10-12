"""Read-only pytest receipts for the native runner, including xdist master IDs."""
import json
import os
from pathlib import Path
import uuid
import xml.etree.ElementTree as ET

import pytest

_state = {}


def pytest_sessionstart(session):
    _state.clear()
    _state.update(
        config=session.config,
        collected=[],
        reports={},
        collection_problems=[],
        worker_collections=[],
    )


def pytest_collection_finish(session):
    if session.items or not _state["worker_collections"]:
        _state["collected"] = [item.nodeid for item in session.items]


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids):
    _state["worker_collections"].append(list(ids))
    _state["collected"] = list(ids)


def pytest_collectreport(report):
    if report.failed or report.skipped:
        _state["collection_problems"].append(
            {"nodeid": report.nodeid, "outcome": report.outcome, "detail": str(report.longrepr)}
        )


def pytest_runtest_logreport(report):
    _state["reports"].setdefault(report.nodeid, []).append(
        {"phase": report.when, "outcome": report.outcome,
         "skip_reason": str(report.longrepr) if report.skipped else None}
    )


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    if hasattr(config, "workerinput"):
        return
    source_root = os.environ.get("FCC_SOURCE_ROOT")
    if source_root and Path(config.rootpath).resolve() != Path(source_root).resolve():
        return
    output = Path(os.environ["FCC_COLLECTION_OUTPUT"])
    output.mkdir(parents=True, exist_ok=True)
    args = [str(arg) for arg in config.args]
    lane = "e2e" if any(Path(arg).name == "e2e" for arg in args) else "pytest"
    receipt = {
        "stage": os.environ["FCC_NATIVE_STAGE"],
        "lane": lane,
        "args": args,
        "invocation_args": list(config.invocation_params.args),
        "exitstatus": int(exitstatus),
        "collected": _state["collected"],
        "worker_collections": _state["worker_collections"],
        "reports": _state["reports"],
        "collection_problems": _state["collection_problems"],
    }
    stem = receipt["stage"] + "-" + lane + "-" + uuid.uuid4().hex
    (output / (stem + ".json")).write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    xml = ET.Element("testsuite", name=lane, tests=str(len(receipt["collected"])))
    for nodeid in receipt["collected"]:
        case = ET.SubElement(xml, "testcase", name=nodeid)
        phases = receipt["reports"].get(nodeid, [])
        if not phases:
            ET.SubElement(case, "error", message="No phase reports received")
        for report in phases:
            if report["outcome"] == "skipped":
                ET.SubElement(case, "skipped", message=report["skip_reason"] or "")
            elif report["outcome"] == "failed":
                ET.SubElement(case, "failure", message=report["phase"] + " failed")
    for problem in receipt["collection_problems"]:
        case = ET.SubElement(xml, "testcase", name="collection::" + problem["nodeid"])
        ET.SubElement(case, "error", message=problem["detail"])
    ET.ElementTree(xml).write(output / (stem + ".xml"), encoding="utf-8", xml_declaration=True)
