"""Outputs: planner workbook (Excel), dashboard (HTML) and machine-readable JSON."""
from __future__ import annotations

import datetime as dt
import html
import json
from collections import Counter
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import accountability, feedback
from .detectors import RULES
from .engine import RunResult
from .position import propose_allocation

CHALLENGE_AREAS = [
    ("System Landscape", "Single reconciled SKU position across MIPS, ODI/Oracle and WMS"),
    ("Inventory Allocation", "SKU-level consolidation, recovery-run sizing, carry-forward proposal"),
    ("Inventory Synchronization", "Untraceable inventory netting, moved runs, negative net orders"),
    ("Freezer Inventory Management", "Detects demand filled from stock MIPS cannot see"),
    ("Order-to-Batch Traceability", "Blind lines and batch-vs-schedule variances"),
    ("Production Visibility", "MIPS-vs-WMS order drift and statistical outliers"),
    ("Returns & Adjustments", "Hook in net requirement; needs WMS returns-to-plant / ticket feed"),
    ("Data Flow Transparency", "Data lineage sheet, key hygiene, master-data conflicts"),
    ("Warehouse Traceability", "Load-scan gaps and short allocations"),
    ("Integration Architecture", "Cross-report and interface total checks"),
]

HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
SEV_FILL = {"High": PatternFill("solid", fgColor="F8D7DA"), "Medium": PatternFill("solid", fgColor="FFF3CD"),
            "Low": PatternFill("solid", fgColor="E2EFDA")}


def _sheet(wb: Workbook, title: str, rows: list[dict], widths: dict[str, int] | None = None, wrap=()):
    ws = wb.create_sheet(title)
    if not rows:
        ws.append(["No rows"])
        return ws
    headers = list(rows[0].keys())
    ws.append(headers)
    for r in rows:
        ws.append([r.get(h) if not isinstance(r.get(h), (list, dict)) else json.dumps(r.get(h), default=str)
                   for h in headers])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEADER_FILL
        c.alignment = Alignment(wrap_text=True, vertical="top")
    for i, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(h, max(10, min(28, len(h) + 4)))
        if h in wrap:
            for cell in ws.iter_cols(min_col=i, max_col=i, min_row=2):
                for c in cell:
                    c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    return ws


def write_workbook(res: RunResult, path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Briefing"
    k = res.kpis
    ws.append(["MIPS-WMS-Oracle Inventory Reconciliation"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"Sales date {k['sales_date']} · generated {dt.datetime.now():%Y-%m-%d %H:%M} · "
               f"narratives: {res.narration['source']}"])
    ws.append([])
    for label, key in [("SKUs reconciled", "skus_reconciled"), ("SKUs fully reconciled", "skus_clean"),
                       ("Exceptions", "findings"), ("$ exposure (retail)", "dollar_exposure"),
                       ("Over-production risk (units)", "overproduction_risk_units"),
                       ("Surplus to carry forward (units)", "carryover_units"),
                       ("Shipped from untracked stock (units)", "untracked_supply_units"),
                       ("MIPS vs WMS order drift (units)", "order_mismatch_units")]:
        ws.append([label, k[key]])
    ws.append([])
    ws.append(["Shift briefing"])
    ws.append([res.narration["shift_briefing"]])
    ws.append([])
    ws.append(["Top actions"])
    for a in res.narration["top_actions"]:
        ws.append([a])
    ws.column_dimensions["A"].width = 120
    ws.column_dimensions["B"].width = 16
    for row in ws.iter_rows(min_row=4):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")

    by_id = res.narration["by_id"]
    exc_rows = []
    for rank, f in enumerate(res.findings, 1):
        n = by_id.get(f.id, {})
        a = f.accountability or {}
        roles = {s["system"]: f"{s['role']}: {s['detail']}" if s["detail"] else s["role"]
                 for s in a.get("systems", [])}
        chk = n.get("check") or {}
        exc_rows.append({
            "Rank": rank, "ID": f.id, "Severity": f.severity, "Rule": f.rule, "Exception": f.title,
            "Requirement Scenario": f.scenario, "Challenge Area": f.area, "SKU": f.sku, "Item": f.description,
            "Units": round(f.units), "$ Exposure": round(f.dollars), "Systems": ", ".join(f.systems),
            "Impact": a.get("impact", ""), "Started In": a.get("origin", ""), "Handoff": a.get("handoff") or "",
            "Failure Type": a.get("failure_type", ""), "Failure Point": a.get("failure_point", ""),
            "Attribution": a.get("basis", ""), "MIPS": roles.get("MIPS", ""), "ODI": roles.get("ODI", ""),
            "WMS": roles.get("WMS", ""), "Oracle": roles.get("Oracle", ""),
            "Recommended Correction": f.recommendation, "Agent Explanation": f.narrative,
            "Root Cause": n.get("root_cause", ""),
            "Cause Chain": "\n".join(f"{i}. {s['system']}: {s['step']}"
                                     for i, s in enumerate(n.get("cause_chain") or [], 1)),
            "AI Impact": n.get("impact", ""), "Confirm With": n.get("verify", ""),
            "Linked Flags": ", ".join(n.get("linked_findings") or []),
            "Figures Check": (f"Unverified: {', '.join(chk['unverified_figures'])}" if chk.get("unverified_figures")
                              else f"{chk['figures_checked']} figures match" if chk.get("figures_checked") else ""),
            "AI Audit ID": n.get("audit_id", ""),
            "Owner": n.get("owner", ""), "Status": f.status,
            "Evidence": f.evidence, "Decision (accept/reject)": "", "Planner Note": ""})
    long_text = ("Recommended Correction", "Agent Explanation", "Root Cause", "Evidence", "Exception",
                 "Failure Point", "MIPS", "ODI", "WMS", "Oracle", "Cause Chain", "AI Impact", "Confirm With")
    ws = _sheet(wb, "Exceptions", exc_rows,
                widths={"ID": 26, "Exception": 30, "Item": 30, "Recommended Correction": 70,
                        "Agent Explanation": 70, "Root Cause": 40, "Evidence": 50, "Planner Note": 30,
                        "Requirement Scenario": 30, "Challenge Area": 22, "Failure Point": 44,
                        "MIPS": 40, "ODI": 40, "WMS": 40, "Oracle": 30, "Cause Chain": 60, "AI Impact": 40,
                        "Confirm With": 40, "Figures Check": 24, "Impact": 20, "Failure Type": 20},
                wrap=long_text)
    sev_col = list(exc_rows[0]).index("Severity") + 1 if exc_rows else 0
    for row in ws.iter_rows(min_row=2):
        fill = SEV_FILL.get(row[sev_col - 1].value)
        if fill:
            row[sev_col - 1].fill = fill

    acct_rows = [{"Started In": name, "Flags": r["flags"], "High": r["high"], "Units": r["units"],
                  "$ Exposure": r["dollars"],
                  "Impacts": ", ".join(sorted({f.accountability.get("impact", "") for f in res.findings
                                               if accountability.origin_label(f) == name}))}
                 for name, r in k.get("by_origin", {}).items()]
    _sheet(wb, "Accountability", acct_rows, widths={"Started In": 30, "Impacts": 60})

    ids_by_sku: dict[str, list[str]] = {}
    for f in res.findings:
        ids_by_sku.setdefault(f.sku, []).append(f.rule)
    pos_rows = [{**p.to_row(), "Exceptions": ", ".join(sorted(set(ids_by_sku.get(p.sku, [])))) or "Reconciled"}
                for p in sorted(res.positions.values(), key=lambda p: -p.demand_total)]
    _sheet(wb, "SKU Positions", pos_rows, widths={"Description": 36})

    alloc_rows = []
    for p in sorted(res.positions.values(), key=lambda p: p.sku):
        if len(p.lines) > 1 or p.implied_inventory or p.net_requirement < 0:
            alloc_rows.extend(propose_allocation(p))
    _sheet(wb, "Allocation Proposal", alloc_rows, widths={"Description": 36})

    state = feedback.load()
    counts = Counter(f.rule for f in res.findings)
    _sheet(wb, "Rule Catalog", [
        {"Rule": code, "Exception": t, "Requirement Scenario": s, "Challenge Area": a, "Base Severity": sev,
         "Findings Today": counts.get(code, 0),
         "Learned Confidence": round(feedback.rule_confidence(state, code) / 2, 2)}
        for code, (t, s, a, sev) in RULES.items()], widths={"Exception": 44, "Requirement Scenario": 40})

    src = res.src
    matched = sum(1 for p in res.positions.values() if p.lines and p.runs and p.wms)
    _sheet(wb, "Data Lineage", [
        {"System": "MIPS", "Extract": src.files["mips_orders"], "Records": len(src.mips),
         "Grain": "Schedule line (SKU x source x sales date)", "Join Key": "FG Oracle Ref (trimmed)"},
        {"System": "ODI / Oracle", "Extract": src.files["odi_finished_goods"], "Records": len(src.fg_runs),
         "Grain": "Run x SKU", "Join Key": "OracleFGNumber; OracleTypeCode to batches"},
        {"System": "ODI / Oracle", "Extract": src.files["odi_batches"], "Records": len(src.batches),
         "Grain": "Batch run", "Join Key": "Oracle Type + Prod Line"},
        {"System": "WMS", "Extract": src.files["wms_order_summary"], "Records": len(src.wms_items),
         "Grain": "Item x customer order", "Join Key": "Item Nbr"},
        {"System": "WMS", "Extract": src.files["wms_fill_rate"], "Records": len(src.fill_orders),
         "Grain": "Customer order", "Join Key": "Order Nbr (no item detail)"},
        {"System": "Reconciled", "Extract": "SKU position", "Records": len(res.positions),
         "Grain": "SKU", "Join Key": f"{matched} SKUs present in all three systems"},
    ], widths={"Extract": 50, "Grain": 40, "Join Key": 44})

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def write_json(res: RunResult, path: Path) -> Path:
    path.write_text(json.dumps({
        "kpis": res.kpis, "briefing": res.narration["shift_briefing"],
        "top_actions": res.narration["top_actions"], "narration_source": res.narration["source"],
        "findings": [{"id": f.id, "rule": f.rule, "title": f.title, "severity": f.severity, "sku": f.sku,
                      "item": f.description, "units": f.units, "dollars": round(f.dollars, 2),
                      "priority": round(f.priority, 1), "evidence": f.evidence,
                      "recommendation": f.recommendation, "explanation": f.narrative, "status": f.status,
                      "accountability": f.accountability,
                      # Owner, root cause and - when the model wrote it - cause chain, checks and audit id.
                      "narration": {k: v for k, v in res.narration["by_id"].get(f.id, {}).items()
                                    if k not in ("id", "explanation")} or None}
                     for f in res.findings],
        "narration_audit_id": res.narration.get("audit_id"),
    }, indent=2, default=str), encoding="utf-8")
    return path


def write_html(res: RunResult, path: Path) -> Path:
    k, e = res.kpis, html.escape
    counts_by_area = Counter(f.area for f in res.findings)
    rule_counts = Counter(f.rule for f in res.findings)
    max_rule = max(rule_counts.values()) if rule_counts else 1

    tiles = "".join(f'<div class="tile"><div class="v">{v}</div><div class="l">{e(l)}</div></div>' for l, v in [
        ("SKUs reconciled", f"{k['skus_clean']}/{k['skus_reconciled']} clean"),
        ("Exceptions", f"{k['findings']} ({k['by_severity'].get('High', 0)} high)"),
        ("Retail $ exposure", f"${k['dollar_exposure']:,.0f}"),
        ("Over-production risk", f"{k['overproduction_risk_units']:,.0f} units"),
        ("Surplus to carry forward", f"{k['carryover_units']:,.0f} units"),
        ("Shipped from untracked stock", f"{k['untracked_supply_units']:,.0f} units")])
    bars = "".join(
        f'<div class="bar"><span class="bl">{code} · {e(RULES[code][0])}</span>'
        f'<span class="bt"><span style="width:{n / max_rule * 100:.0f}%"></span></span><span class="bn">{n}</span></div>'
        for code, n in sorted(rule_counts.items(), key=lambda x: -x[1]))
    coverage = "".join(
        f"<tr><td>{e(a)}</td><td>{e(d)}</td><td class='num'>{counts_by_area.get(a, '—')}</td></tr>"
        for a, d in CHALLENGE_AREAS)
    rows = "".join(
        f"<tr><td class='num'>{i}</td><td><span class='sev {f.severity.lower()}'>{f.severity}</span></td>"
        f"<td><b>{e(f.title)}</b><div class='sub'>{e(f.sku)} {e(f.description)}</div></td>"
        f"<td class='num'>{f.units:,.0f}</td><td class='num'>${f.dollars:,.0f}</td>"
        f"<td><b>{e(accountability.origin_label(f))}</b><div class='sub'>{e(f.accountability.get('impact', ''))}"
        f" · {e(f.accountability.get('basis', ''))}</div></td>"
        f"<td>{e(f.narrative)}</td></tr>"
        for i, f in enumerate(res.findings[:30], 1))
    actions = "".join(f"<li>{e(a)}</li>" for a in res.narration["top_actions"])

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Inventory Reconciliation Agent</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--ink:#1b2430;--mute:#5b6675;--line:#e3e6ea;--accent:#2b5c8a;
--hi:#b42318;--hibg:#fde8e7;--md:#8a5a00;--mdbg:#fff4d6;--lo:#2f6b3a;--lobg:#e5f3e8}}
@media (prefers-color-scheme:dark){{:root{{--bg:#12161c;--card:#1b2129;--ink:#e6e9ee;--mute:#9aa4b2;
--line:#2c343f;--accent:#7fb0e0;--hi:#ff9b91;--hibg:#3a1d1b;--md:#f3c56b;--mdbg:#3a2f14;--lo:#8fd19e;--lobg:#16301d}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:24px 16px 48px}}
h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:16px;margin:28px 0 10px}}
.meta{{color:var(--mute);font-size:13px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-top:18px}}
.tile,.card{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px}}
.tile .v{{font-size:20px;font-weight:650;font-variant-numeric:tabular-nums}}.tile .l{{color:var(--mute);font-size:12px}}
.grid{{display:grid;grid-template-columns:1.2fr 1fr;gap:14px}}@media (max-width:820px){{.grid{{grid-template-columns:1fr}}}}
.bar{{display:grid;grid-template-columns:1fr 120px 32px;gap:8px;align-items:center;font-size:12.5px;margin:4px 0}}
.bt{{background:var(--line);border-radius:3px;height:8px;overflow:hidden}}.bt span{{display:block;height:100%;background:var(--accent)}}
.bn{{text-align:right;font-variant-numeric:tabular-nums}}
table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{text-align:left;vertical-align:top;
padding:8px;border-bottom:1px solid var(--line)}}th{{color:var(--mute);font-weight:600;font-size:12px}}
.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}.sub{{color:var(--mute);font-size:12px}}
.sev{{font-size:11px;font-weight:650;padding:2px 7px;border-radius:10px}}
.sev.high{{color:var(--hi);background:var(--hibg)}}.sev.medium{{color:var(--md);background:var(--mdbg)}}
.sev.low{{color:var(--lo);background:var(--lobg)}}
.scroll{{overflow-x:auto}}ol{{margin:6px 0 0;padding-left:20px}}li{{margin:4px 0}}
</style></head><body><main>
<h1>MIPS · WMS · Oracle inventory reconciliation</h1>
<div class="meta">Lehigh Valley bakery · sales date {e(k['sales_date'])} · {k['mips_lines']} MIPS lines,
{k['odi_runs']} ODI runs, {k['wms_orders']} WMS orders · narratives: {e(res.narration['source'])}</div>
<div class="tiles">{tiles}</div>
<div class="grid">
<section><h2>Shift briefing</h2><div class="card">{e(res.narration['shift_briefing'])}
<h2 style="margin-top:14px">Top actions</h2><ol>{actions}</ol></div></section>
<section><h2>Exceptions by rule</h2><div class="card">{bars}</div></section>
</div>
<h2>Top 30 exceptions (ranked by $ exposure × severity × learned confidence)</h2>
<div class="card scroll"><table><thead><tr><th>#</th><th>Severity</th><th>Exception</th>
<th class="num">Units</th><th class="num">$ exposure</th><th>Started in</th><th>What to do</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2>Challenge coverage</h2>
<div class="card scroll"><table><thead><tr><th>Challenge area</th><th>How the agent addresses it</th>
<th class="num">Findings today</th></tr></thead><tbody>{coverage}</tbody></table></div>
</main></body></html>"""
    path.write_text(page, encoding="utf-8")
    return path
