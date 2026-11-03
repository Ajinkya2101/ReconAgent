"""Command line entry point.

    python -m recon_agent run [--data-dir DIR] [--offline]
    python -m recon_agent ask "Why is MIPS asking for more 150080?"
    python -m recon_agent feedback R05-8526 accept --note "Applied to Monday"
    python -m recon_agent feedback --from-workbook output/recon_2026-09-12.xlsx
    python -m recon_agent serve [--host 0.0.0.0] [--port 8000]
    python -m recon_agent events seed | status | tail | show | set | insert | delete | clock | reset
    python -m recon_agent simulate [--host 0.0.0.0] [--port 8100]

RECON_SOURCE=events makes run, ask, feedback and serve read the simulated event
log instead of the Excel extracts (see docs/EVENT_CONTRACT.md).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import openpyxl

from . import ai, config, engine, feedback, report
from .events import cli as events_cli


def cmd_run(args) -> None:
    res = engine.run(args.data_dir, use_ai=False if args.offline else None)
    out = Path(args.out_dir)
    stem = f"recon_{res.kpis['sales_date']}"
    xlsx = report.write_workbook(res, out / f"{stem}.xlsx")
    page = report.write_html(res, out / f"{stem}.html")
    data = report.write_json(res, out / f"{stem}.json")
    k = res.kpis
    print(f"Sales date {k['sales_date']}: {k['skus_reconciled']} SKUs, {k['findings']} exceptions "
          f"({k['by_severity']}), ${k['dollar_exposure']:,} exposure")
    print(f"  over-production risk {k['overproduction_risk_units']:,.0f} units · carry-forward "
          f"{k['carryover_units']:,.0f} · untracked supply {k['untracked_supply_units']:,.0f}")
    print(f"  narratives: {res.narration['source']}")
    print(f"\nShift briefing:\n{res.narration['shift_briefing']}\n")
    for f in res.findings[:10]:
        print(f"  [{f.severity:<6}] {f.id:<30} {f.title}")
    print(f"\nWrote {xlsx}\n      {page}\n      {data}")


def cmd_ask(args) -> None:
    res = engine.run(args.data_dir, use_ai=False)
    try:
        print(ai.ask(" ".join(args.question), res.positions, res.findings))
    except ai.AIError as e:
        sys.exit(f"The assistant is unavailable: {e}")


def cmd_feedback(args) -> None:
    if args.from_workbook:
        ws = openpyxl.load_workbook(args.from_workbook, data_only=True)["Exceptions"]
        rows = list(ws.iter_rows(values_only=True))
        h = {name: i for i, name in enumerate(rows[0])}
        n = 0
        for r in rows[1:]:
            decision = (r[h["Decision (accept/reject)"]] or "").strip().lower()
            if decision in ("accept", "reject"):
                feedback.record(r[h["ID"]], round(float(r[h["Units"]] or 0), 2), decision,
                                r[h["Planner Note"]] or "")
                n += 1
        print(f"Recorded {n} planner decisions from {args.from_workbook}")
        return
    if not (args.finding_id and args.decision):
        sys.exit("Give a finding ID and accept/reject, or --from-workbook PATH")
    res = engine.run(args.data_dir, use_ai=False)
    match = next((f for f in res.findings if f.id == args.finding_id), None)
    if not match:
        sys.exit(f"No open finding {args.finding_id}")
    feedback.record(match.id, round(match.units, 2), args.decision, args.note)
    print(f"Recorded {args.decision} for {match.id}")


def cmd_serve(args) -> None:
    import uvicorn

    from .web.app import create_app
    uvicorn.run(create_app(args.data_dir), host=args.host, port=args.port)


def cmd_simulate(args) -> None:
    import uvicorn

    from .simulator.app import create_app
    uvicorn.run(create_app(), host=args.host, port=args.port)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="recon_agent", description="MIPS-WMS-Oracle reconciliation agent")
    ap.add_argument("--data-dir", default=str(config.DATA_DIR))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="Reconcile the extracts and write the workbook/dashboard")
    p.add_argument("--out-dir", default=str(config.REPORT_DIR))
    p.add_argument("--offline", action="store_true", help="Skip OCI Generative AI; use rule-generated narratives")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("ask", help="Ask the reconciliation copilot a question")
    p.add_argument("question", nargs="+")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("feedback", help="Record a planner decision on a finding")
    p.add_argument("finding_id", nargs="?")
    p.add_argument("decision", nargs="?", choices=["accept", "reject"])
    p.add_argument("--note", default="")
    p.add_argument("--from-workbook")
    p.set_defaults(func=cmd_feedback)

    p = sub.add_parser("serve", help="Run the exception worklist web app")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("simulate", help="Run the source simulator (operator app that publishes events)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8100)
    p.set_defaults(func=cmd_simulate)

    events_cli.add_parser(sub)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
