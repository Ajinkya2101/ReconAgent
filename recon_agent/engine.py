"""Pipeline: ingest -> reconcile -> detect -> learn -> rank -> explain."""
from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import accountability, ai, anomaly, detectors, feedback, ingest, position, sources
from .detectors import Finding
from .position import SkuPosition


@dataclass
class RunResult:
    src: ingest.SourceData
    positions: dict[str, SkuPosition]
    findings: list[Finding]
    kpis: dict
    narration: dict


def _units(findings: list[Finding], *rules: str) -> float:
    return sum(abs(f.units) for f in findings if f.rule in rules)


def compute_kpis(src: ingest.SourceData, positions: dict[str, SkuPosition], findings: list[Finding]) -> dict:
    dates = Counter(l.schedule_date for l in src.mips)
    sales_date = dates.most_common(1)[0][0] if dates else (src.as_of or dt.datetime.now()).date()
    skus_with_findings = {f.sku for f in findings if f.sku}
    demand_skus = [p for p in positions.values() if p.lines]
    return {
        "sales_date": sales_date.isoformat(),
        "skus_reconciled": len(demand_skus),
        "skus_clean": sum(1 for p in demand_skus if p.sku not in skus_with_findings),
        "mips_lines": len(src.mips),
        "odi_runs": len(src.fg_runs),
        "wms_orders": len(src.fill_orders),
        "findings": len(findings),
        "by_severity": dict(Counter(f.severity for f in findings)),
        "by_rule": dict(Counter(f.rule for f in findings)),
        "dollar_exposure": round(sum(f.dollars for f in findings if f.rule in
                                     ("R01", "R05", "R06", "R07", "R08", "R09", "R10", "R19"))),
        "overproduction_risk_units": _units(findings, "R01", "R05"),
        "carryover_units": _units(findings, "R06", "R08"),
        "untracked_supply_units": _units(findings, "R07"),
        "order_mismatch_units": _units(findings, "R09"),
        "total_demand_units": sum(p.demand_total for p in positions.values()),
        "total_wrapped_units": sum(p.wrapped for p in positions.values()),
        "total_wms_allocated": sum(p.wms_allocated for p in positions.values()),
        # Where today's flags started and what they do, from each flag's accountability record.
        **accountability.rollup(findings),
    }


def run(data_dir: Path | None = None, use_ai: bool | None = None,
        src: ingest.SourceData | None = None) -> RunResult:
    """Run the pipeline on `src`, or on whatever the configured feed (RECON_SOURCE) holds now."""
    src = src or sources.open_feed(data_dir).load()
    positions = position.build_positions(src)
    findings = detectors.run_all(src, positions) + anomaly.detect(src, positions)
    findings = feedback.apply(findings)
    findings.sort(key=lambda f: f.priority, reverse=True)
    accountability.assign(findings, positions)
    kpis = compute_kpis(src, positions, findings)

    if use_ai is False:
        narration = ai.offline_narration(findings, kpis)
    else:
        narration = ai.narrate(findings, kpis)
    res = RunResult(src, positions, findings, kpis, narration)
    apply_narration(res, narration)
    return res


def apply_narration(res: RunResult, narration: dict) -> None:
    res.narration = narration
    for f in res.findings:
        n = narration["by_id"].get(f.id)
        f.narrative = n["explanation"] if n else f.recommendation
