"""Statistical anomaly detection with robust (median/MAD) z-scores.

Robust statistics are used because the distributions are small and skewed:
a single bad entry would inflate a mean/stdev and hide itself.
"""
from __future__ import annotations

from statistics import median

from . import config
from .detectors import Finding
from .ingest import SourceData
from .position import SkuPosition


def robust_z(values: list[float]) -> list[float]:
    """Modified z-score. Most runs wrap exactly what was ordered, which drives
    the MAD to ~0, so the scale is floored at the mean absolute deviation
    (Iglewicz & Hoaglin)."""
    med = median(values)
    deviations = [abs(v - med) for v in values]
    scale = max(1.4826 * median(deviations), 1.2533 * sum(deviations) / len(deviations))
    if scale == 0:
        return [0.0] * len(values)
    return [(v - med) / scale for v in values]


def _outliers(samples: list[tuple[float, dict]]) -> list[tuple[float, float, dict, float]]:
    if len(samples) < 8:
        return []
    values = [v for v, _ in samples]
    med = median(values) + 0.0  # avoid printing "-0"
    return [(v, z, ctx, med) for (v, ctx), z in zip(samples, robust_z(values))
            if abs(z) >= config.ROBUST_Z_THRESHOLD]


def detect(src: SourceData, positions: dict[str, SkuPosition]) -> list[Finding]:
    findings = []

    # 1. Wrap overrun per run: (wrapped - ordered) / ordered.
    runs = [r for r in src.fg_runs if r.net_units_ordered > 0]
    samples = [((r.wrapped_units - r.net_units_ordered) / r.net_units_ordered, {"run": r}) for r in runs]
    for v, z, ctx, med in _outliers(samples):
        r = ctx["run"]
        p = positions.get(r.sku)
        findings.append(Finding(
            "R17", f"OVERRUN-{r.formula_type}-{r.sku}", r.sku, r.description,
            r.wrapped_units - r.net_units_ordered, p.unit_price if p else config.DEFAULT_UNIT_PRICE,
            evidence={"metric": "wrap overrun %", "value": f"{v:+.1%}", "plant_median": f"{med:+.1%}",
                      "robust_z": round(z, 1), "ordered": r.net_units_ordered, "wrapped": r.wrapped_units,
                      "run": f"{r.formula_type} {r.line}/{r.shift}"},
            recommendation=(
                f"Wrapped {r.wrapped_units:,.0f} against {r.net_units_ordered:,.0f} ordered ({v:+.0%}; plant "
                f"median {med:+.0%}, robust z = {z:.1f}). Verify the wrapped count and the ordered quantity "
                f"for a keying error before the surplus is carried forward."),
            severity="High" if abs(z) >= 2 * config.ROBUST_Z_THRESHOLD else "Medium",
            systems=["ODI"]))

    # 2. Final piece variance per batch run.
    batches = [b for b in src.batches if b.theo_pieces > 0]
    samples = [(b.final_piece_variance, {"b": b}) for b in batches]
    for v, z, ctx, med in _outliers(samples):
        b = ctx["b"]
        findings.append(Finding(
            "R17", f"PIECEVAR-{b.formula_type}-{b.line}", "", b.description, 0, 0.0,
            evidence={"metric": "final piece variance", "value": round(v, 1), "plant_median": round(med, 1),
                      "robust_z": round(z, 1), "formula": b.formula_type, "line": b.line},
            recommendation=(
                f"{b.formula_type} ({b.description}, {b.line}) shows a final piece variance of {v:,.0f} pieces "
                f"(plant median {med:,.0f}, robust z = {z:.1f}). Check scaling/scrap entries for this batch."),
            systems=["ODI"], base_priority=1500))
    return findings
