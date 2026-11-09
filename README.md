# Inventory Reconciliation Agent (MIPS · WMS · Oracle)

Reconciles BUSS sales orders, the Oracle forecast, the ODI batch mapping, WMS stock and MIPS work orders for Bimbo Bakeries USA's Lehigh Valley bakery (plant UBD). It checks one demand-to-delivery equation per SKU, `Work Order Qty = (Sales Orders + Forecast) − Allocated − Available`, flags every mismatch with a quantified correction, and serves an exception worklist for planners and shift supervisors.

- **Data model and rules:** [docs/DATA_MODEL_9NOV.md](docs/DATA_MODEL_9NOV.md) (the business team's 9 Nov mapping: 6 sources, 13 rules, 3 dummy SKUs)

- **Planners, supervisors and business readers:** [docs/USER_GUIDE.md](docs/USER_GUIDE.md) (ASD-STE100)
- **Everyone using the live simulation (worklist + source simulator):** [docs/SYSTEMS_GUIDE.md](docs/SYSTEMS_GUIDE.md)
- **Engineers and IT:** [docs/TECHNICAL_OVERVIEW.md](docs/TECHNICAL_OVERVIEW.md), [docs/EVENT_CONTRACT.md](docs/EVENT_CONTRACT.md)
- **Deploying for several people (server, HTTPS, accounts, releases):** [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

Version: see [CHANGELOG.md](CHANGELOG.md) (`python -m recon_agent --version`).

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env                 # optional: OCI credentials for AI explanations
python -m recon_agent serve          # worklist at http://127.0.0.1:8000/
                                     # first start prints the demo accounts' passwords (planner1, supervisor1, manager1, ...)
python -m recon_agent run --offline  # reconcile the workbook under data/ and write reports to output/
```

Or, on Windows with Docker: `start_container.bat` (worklist at http://localhost:8001/). On a server: `docker compose up -d` (see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).

Everyone signs in with a username and password; the role comes with the account. Manage accounts with `python -m recon_agent users list | add | passwd | role | remove`.

**Simulated real-time feed (POC, dummy data):** publish source-system changes as events and watch the worklist react. See [docs/EVENT_CONTRACT.md](docs/EVENT_CONTRACT.md).

```bash
python -m recon_agent events seed --reset           # starting day: 3 SKUs (76108 clean), 5 designed flags
RECON_SOURCE=events python -m recon_agent serve     # System A: worklist on the event feed (:8000)
python -m recon_agent simulate                      # System B: source simulator (:8100), change data live
python -m recon_agent events set buss.sales_order 2263245-1 "Order Quantity=7800"   # or change it from the CLI
python -m recon_agent events scenario run S1          # play one of the 10 scripted scenarios
pip install -r requirements-dev.txt && python -m unittest discover -s tests -v   # tests
```

## Layout

| Path | Contents |
|---|---|
| `recon_agent/` | Application package (engine, AI layer, web app, `events/` simulated feed, `simulator/` operator app) |
| `tests/` | unittest suite |
| `data/` | The source workbook `BIMBO_MIPS_WMS_ODI_Data*.xlsx` (found anywhere under `data/`, e.g. `data/new_dummy_data/`). Client material, not committed |
| `output/` | Generated reports and planner decisions (`feedback.json`). Not committed |
| `docs/` | User guide, technical overview, and `reference/` (requirement brief and decks; client material, not committed) |
| `PRODUCT.md`, `DESIGN.md` | Product context and design system (kept at root for the design tooling) |
