"""The ML layer: statistical anomaly detection against each item's own history.

The 13 rules (detectors.py) check that the systems agree with each other on today's sales day.
They cannot see a value that every system agrees on but that is wrong: an order keyed with an
extra zero that BUSS, ODI and MIPS all carried through, a doubled forecast, a line that ran
twice as long as usual, stock that vanished overnight. These detectors compare today with the
same item's earlier sales days and flag what lies far outside its normal range:

    R14  Unusual order quantity   a BUSS order line against that customer's earlier orders of the item
    R15  Forecast bias            the Oracle forecast against the item's earlier forecasts
    R16  Production anomaly       the work order's wrap duration against the item's earlier work orders
    R17  Inventory drift          the latest change in WMS on hand against the row's earlier changes

Method: the modified z-score of Iglewicz and Hoaglin, z = (x - median) / scale, with the scale
1.4826 x the median absolute deviation (MAD), or 1.2533 x the mean absolute deviation when the
MAD is 0 (more than half the history identical). Median and MAD are robust: one earlier outlier
cannot hide the next. A value is flagged at |z| >= 3.5
(config.ML_Z_THRESHOLD), High at twice that. Nothing is flagged without at least
config.ML_MIN_HISTORY earlier values, so a single-day workbook raises no ML flags.

History comes from earlier sales days in the same tables (ingest.split_current) and, for WMS,
whose rows carry no date, from the row's change log in the event feed.
"""
from __future__ import annotations

from statistics import mean, median

from . import config
from .detectors import Finding
from .ingest import SourceData
from .position import SkuPosition

METHOD = "robust z-score (median/MAD)"


def robust_z(x: float, history: list[float]) -> tuple[float, float, float]:
    """(z, median, scale) of `x` against `history`."""
    med = median(history)
    deviations = [abs(v - med) for v in history]
    # The MAD when there is one; only when more than half the history is identical (MAD = 0) the mean
    # absolute deviation. Never the larger of the two: the mean is not robust, and one big earlier
    # outlier would inflate it and hide the next one.
    scale = 1.4826 * median(deviations) or 1.2533 * mean(deviations)
    if scale == 0:
        scale = max(abs(med) * 0.01, 1.0)   # a perfectly flat history: 1% of the level
    return (x - med) / scale, med, scale


def _check(x: float, history: list[float]):
    """(z, median, scale) when `x` is an outlier with enough history, else None."""
    if len(history) < config.ML_MIN_HISTORY:
        return None
    z, med, scale = robust_z(x, history)
    return (z, med, scale) if abs(z) >= config.ML_Z_THRESHOLD else None


def _severity(z: float) -> str:
    return "High" if abs(z) >= 2 * config.ML_Z_THRESHOLD else "Medium"


def _n(x: float) -> str:
    return f"{x:,.0f}"


def _evidence(value, med, z, n, what: str) -> dict:
    return {"method": METHOD, "value": value, "history_median": round(med, 2), "robust_z": round(z, 1),
            "history_points": n, "compared_with": what, "threshold": config.ML_Z_THRESHOLD}


def unusual_orders(src: SourceData, positions: dict[str, SkuPosition]) -> list[Finding]:
    hist = src.history.sales_orders if src.history else []
    out = []
    for p in positions.values():
        for o in p.orders:
            past = [h.qty for h in hist if h.sku == o.sku and h.customer == o.customer]
            hit = _check(o.qty, past)
            if not hit:
                continue
            z, med, _ = hit
            out.append(Finding(
                "R14", o.line_id, p.sku, p.description, o.qty - med, p.unit_price,
                evidence={"order": f"{o.order_nbr} line {o.line}", "customer": o.customer, "quantity": o.qty,
                          **_evidence(o.qty, med, z, len(past), f"{o.customer}'s last {len(past)} orders of {p.sku}")},
                recommendation=(
                    f"BUSS order {o.order_nbr} from {o.customer} is for {_n(o.qty)} units of {p.sku}, against a usual "
                    f"{_n(med)} (median of its last {len(past)} orders; robust z = {z:+.1f}). Every system carries it, so "
                    f"the rules see nothing wrong, but {'more' if z > 0 else 'less'} than this customer ever orders is "
                    f"usually a keying error. Confirm the quantity with the customer before the plant produces for it."),
                severity=_severity(z), systems=["BUSS", "ODI", "MIPS"], base_priority=500))
    return out


def forecast_bias(src: SourceData, positions: dict[str, SkuPosition]) -> list[Finding]:
    hist = src.history.forecasts if src.history else []
    out = []
    for p in positions.values():
        for f in p.forecasts:
            past = [h.qty for h in hist if h.sku == f.sku]
            hit = _check(f.qty, past)
            if not hit:
                continue
            z, med, _ = hit
            out.append(Finding(
                "R15", f.forecast_id, p.sku, p.description, f.qty - med, p.unit_price,
                evidence={"forecast": f.forecast_id, "quantity": f.qty,
                          **_evidence(f.qty, med, z, len(past), f"{p.sku}'s last {len(past)} forecasts")},
                recommendation=(
                    f"Oracle forecast {f.forecast_id} for {p.sku} is {_n(f.qty)}, against a usual {_n(med)} (median of "
                    f"the last {len(past)} sales days; robust z = {z:+.1f}). ODI and MIPS follow it, so the work order is "
                    f"sized to it. Check the forecast with demand planning before the plant "
                    f"{'makes the extra' if z > 0 else 'cuts production'}."),
                severity=_severity(z), systems=["Oracle", "ODI", "MIPS"], base_priority=500))
    return out


def _wrap_hours(odi_rows, wo: str) -> float | None:
    spans = [(e.wrap_start, e.wrap_end) for e in odi_rows if e.wo == wo and e.wrap_start and e.wrap_end]
    if not spans:
        return None
    start, end = max(set(spans), key=spans.count)   # the span most of the work order's rows carry
    hours = (end - start).total_seconds() / 3600
    return hours if hours > 0 else None


def production_anomalies(src: SourceData, positions: dict[str, SkuPosition]) -> list[Finding]:
    hist = src.history
    out = []
    for p in positions.values():
        past = []
        if hist:
            for w in hist.work_orders:
                if w.sku == p.sku:
                    h = _wrap_hours(hist.odi, w.wo)
                    if h:
                        past.append(h)
        for w in p.work_orders:
            hours = _wrap_hours(p.odi, w.wo)
            if hours is None:
                continue
            hit = _check(hours, past)
            if not hit:
                continue
            z, med, _ = hit
            out.append(Finding(
                "R16", w.wo, p.sku, p.description, 0, p.unit_price,
                evidence={"work_order": w.wo, "wrap_hours": round(hours, 2), "work_order_qty": w.wo_qty,
                          "units_per_hour": float(round(w.wo_qty / hours)), "line": w.line, "shift": w.shift,
                          **_evidence(round(hours, 2), med, z, len(past), f"{p.sku}'s last {len(past)} work orders")},
                recommendation=(
                    f"Work order {w.wo} ({w.line} {w.shift}) took {hours:.1f} hours to wrap {_n(w.wo_qty)} units, against "
                    f"a usual {med:.1f} hours (median of the last {len(past)}; robust z = {z:+.1f}). "
                    + ("The line ran far slower than normal: check for a breakdown, a staffing gap or a wrong wrap time "
                       "in ODI, and whether later work orders on the line will slip."
                       if z > 0 else
                       "The line ran far faster than normal, which usually means a wrong wrap time in ODI: confirm the "
                       "times before they are used for scheduling.")),
                severity=_severity(z), systems=["ODI", "MIPS"], base_priority=1500))
    return out


def inventory_drift(src: SourceData, positions: dict[str, SkuPosition]) -> list[Finding]:
    out = []
    for p in positions.values():
        for s in p.stock:
            snaps = src.stock_history.get(s.inventory_id, [])
            changes = [b[1] - a[1] for a, b in zip(snaps, snaps[1:])]
            if len(changes) < 2:
                continue
            latest, past = changes[-1], changes[:-1]
            hit = _check(latest, past)
            if not hit:
                continue
            z, med, _ = hit
            when = snaps[-1][0]
            typical = median(abs(c) for c in past)
            out.append(Finding(
                "R17", s.inventory_id, p.sku, p.description, latest - med, p.unit_price,
                evidence={"inventory": s.inventory_id, "locator": s.locator, "on_hand_before": snaps[-2][1],
                          "on_hand_now": snaps[-1][1], "change": latest, "changed_at": when.strftime("%m/%d %H:%M"),
                          **_evidence(latest, med, z, len(past), f"the last {len(past)} changes of {s.inventory_id}")},
                recommendation=(
                    f"WMS on hand for {p.sku} at {s.locator} went from {_n(snaps[-2][1])} to {_n(snaps[-1][1])} "
                    f"({latest:+,.0f}) at {when:%m/%d %H:%M}. Its last {len(past)} changes moved it by {_n(typical)} units "
                    f"either way (median size; robust z = {z:+.1f}). The stock still balances, so the rules see nothing wrong, but a "
                    f"move this size with no matching production or shipment needs a recount: if the count is wrong, the "
                    f"work order sized from it is wrong too."),
                severity=_severity(z), systems=["WMS", "MIPS"], base_priority=500))
    return out


DETECTORS = (unusual_orders, forecast_bias, production_anomalies, inventory_drift)


def detect(src: SourceData, positions: dict[str, SkuPosition]) -> list[Finding]:
    """Every ML flag for the current sales day (empty without history)."""
    return [f for d in DETECTORS for f in d(src, positions)]
