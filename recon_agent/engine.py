"""Pipeline: ingest -> reconcile -> detect -> learn -> rank -> explain."""
from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import accountability, ai, detectors, feedback, ingest, position, sources
from .detectors import Finding
from .position import SkuPosition


@dataclass
class RunResult:
    src: ingest.SourceData
    positions: dict[str, SkuPosition]
    findings: list[Finding]
    kpis: dict
    narration: dict


def _units(findings: list[Finding], rules: tuple[str, ...], sign: int = 0) -> float:
    """Units of the given rules; sign -1 counts only negative units (too many), +1 only positive (too few)."""
    return sum(abs(f.units) for f in findings if f.rule in rules and (not sign or f.units * sign > 0))


def exposure(findings: list[Finding]) -> float:
    """$ at risk, the largest flag per SKU: two rules on one SKU often describe the same units."""
    by_sku: dict[str, float] = {}
    for f in findings:
        by_sku[f.sku or f.id] = max(by_sku.get(f.sku or f.id, 0.0), f.dollars)
    return round(sum(by_sku.values()))


def compute_kpis(src: ingest.SourceData, positions: dict[str, SkuPosition], findings: list[Finding]) -> dict:
    dates = Counter(p.sales_date for p in positions.values() if p.sales_date)
    sales_date = dates.most_common(1)[0][0] if dates else (src.as_of or dt.datetime.now()).date()
    skus_with_findings = {f.sku for f in findings if f.sku}
    return {
        "sales_date": sales_date.isoformat(),
        "skus_reconciled": len(positions),
        "skus_clean": sum(1 for p in positions.values() if p.sku not in skus_with_findings),
        "sales_order_lines": len(src.sales_orders),
        "forecast_lines": sum(len(p.forecasts) for p in positions.values()),
        "odi_rows": len(src.odi),
        "work_orders": len(src.work_orders),
        "findings": len(findings),
        "by_severity": dict(Counter(f.severity for f in findings)),
        "by_rule": dict(Counter(f.rule for f in findings)),
        "dollar_exposure": exposure(findings),
        # Work orders against the shortfall: too many (over-production) or too few (short).
        "overproduction_units": _units(findings, ("R05",), -1),
        "underproduction_units": _units(findings, ("R05",), +1) + _units(findings, ("R06",)),
        "demand_not_in_odi_units": _units(findings, ("R01", "R02"), +1),
        "total_sales_order_units": sum(p.so_qty for p in positions.values()),
        "total_forecast_units": sum(p.forecast_qty for p in positions.values()),
        "total_demand_units": sum(p.demand for p in positions.values()),
        "total_wms_allocated": sum(p.allocated for p in positions.values()),
        "total_wms_available": sum(p.available for p in positions.values()),
        "total_shortfall_units": sum(p.shortfall for p in positions.values()),
        "total_work_order_units": sum(p.wo_qty for p in positions.values()),
        # Where today's flags started and what they do, from each flag's accountability record.
        **accountability.rollup(findings),
    }


def run(data_dir: Path | None = None, use_ai: bool | None = None,
        src: ingest.SourceData | None = None) -> RunResult:
    """Run the pipeline on `src`, or on whatever the configured feed (RECON_SOURCE) holds now."""
    src = src or sources.open_feed(data_dir).load()
    positions = position.build_positions(src)
    findings = detectors.run_all(src, positions)
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
