# Inventory Reconciliation Agent (MIPS · WMS · Oracle)

Reconciles MIPS planning, ODI/Oracle production and WMS warehouse data for Bimbo Bakeries USA's Lehigh Valley bakery. It flags every mismatch with a quantified correction and serves an exception worklist for planners and shift supervisors.

- **Planners, supervisors and business readers:** [docs/USER_GUIDE.md](docs/USER_GUIDE.md) (ASD-STE100)
- **Everyone using the live simulation (worklist + source simulator):** [docs/SYSTEMS_GUIDE.md](docs/SYSTEMS_GUIDE.md)
- **Engineers and IT:** [docs/TECHNICAL_OVERVIEW.md](docs/TECHNICAL_OVERVIEW.md), [docs/EVENT_CONTRACT.md](docs/EVENT_CONTRACT.md)

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env                 # optional: OCI credentials for AI explanations
python -m recon_agent serve          # worklist at http://127.0.0.1:8000/
python -m recon_agent run --offline  # write reports to output/
```

Or, on Windows with Docker: `start_container.bat` (worklist at http://localhost:8001/).

**Simulated real-time feed (POC, synthetic data):** publish source-system changes as events and watch the worklist react. See [docs/EVENT_CONTRACT.md](docs/EVENT_CONTRACT.md).

```bash
python -m recon_agent events seed --reset           # synthetic starting day, 12 SKUs, 14 designed flags
RECON_SOURCE=events python -m recon_agent serve     # System A: worklist on the event feed (:8000)
python -m recon_agent simulate                      # System B: source simulator (:8100), change data live
python -m recon_agent events set wms.item_order WL-0015 "Order Qty=800"   # or change it from the CLI
python -m recon_agent events scenario run S8          # play one of the 8 scripted scenarios
python -m unittest discover -s tests -v             # tests
```

## Layout

| Path | Contents |
|---|---|
| `recon_agent/` | Application package (engine, AI layer, web app, `events/` simulated feed, `simulator/` operator app) |
| `tests/` | unittest suite |
| `data/` | The five daily source extracts (.xlsx). Client data, not committed: get them from the project team and place them here |
| `output/` | Generated reports and planner decisions (`feedback.json`). Not committed |
| `docs/` | User guide, technical overview, and `reference/` (requirement brief and decks; client material, not committed) |
| `PRODUCT.md`, `DESIGN.md` | Product context and design system (kept at root for the design tooling) |
