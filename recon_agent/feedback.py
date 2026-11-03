"""Learn from planner decisions.

Planners accept or reject findings (CLI `feedback` command, or the Decision
column of the Excel workbook fed back in). The agent keeps a Beta(1,1) prior per
rule, so rules planners keep rejecting are ranked lower, and rules they keep
acting on rise. A rejected finding with an unchanged quantity is suppressed on
the next run as a known/accepted variance.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path

from . import config, security
from .detectors import Finding

_lock = threading.Lock()   # one read-modify-write of the decisions file at a time


def load(path: Path = config.FEEDBACK_FILE) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"rules": {}, "decisions": {}}


def save(state: dict, path: Path = config.FEEDBACK_FILE) -> None:
    security.write_json(path, state, indent=2)


def record(finding_id: str, units: float | None, decision: str, note: str = "", by: str = "") -> dict:
    decision = decision.lower()
    if decision not in ("accept", "reject"):
        raise ValueError("decision must be 'accept' or 'reject'")
    with _lock:
        state = load()
        rule = finding_id.split("-", 1)[0]
        stats = state["rules"].setdefault(rule, {"accept": 0, "reject": 0})
        previous = state["decisions"].get(finding_id)
        if previous:
            stats[previous["decision"]] -= 1
        stats[decision] += 1
        state["decisions"][finding_id] = {"decision": decision, "units": units, "note": note, "by": by,
                                          "at": dt.datetime.now().isoformat(timespec="seconds")}
        save(state)
        return state


def rule_confidence(state: dict, rule: str) -> float:
    """Posterior mean acceptance rate, normalised so an unseen rule scores 1.0."""
    s = state["rules"].get(rule, {"accept": 0, "reject": 0})
    return ((s["accept"] + 1) / (s["accept"] + s["reject"] + 2)) / 0.5


def apply(findings: list[Finding], state: dict | None = None) -> list[Finding]:
    state = state or load()
    kept = []
    for f in findings:
        f.confidence = rule_confidence(state, f.rule)
        d = state["decisions"].get(f.id)
        if d and d["decision"] == "reject" and d.get("units") == round(f.units, 2):
            continue  # planner already dismissed this exact variance
        if d and d["decision"] == "accept":
            f.status = "Accepted - awaiting correction"
        kept.append(f)
    return kept
