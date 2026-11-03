# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Server-rendered from Python: FastAPI with Jinja templates and htmx. There is no separate JavaScript build. The front end lives in the same Python codebase as the reconciliation engine (`recon_agent/`) and runs as containers on OCI Kubernetes Engine (OKE) behind OCI API Gateway.

The prototype today is a CLI (`python -m recon_agent run|ask|feedback`). It writes a static HTML dashboard, an Excel workbook and a JSON file per sales date to `output/`. The server-rendered front end replaces that static HTML as the user interface.

## Users

The UI serves two audiences with equal weight.

1. **Production planners and shift supervisors** at Bimbo Bakeries USA's Lehigh Valley bakery (Breinigsville, PA). About 11 supervisors cover three shifts. Their job:
   - triage reconciliation flags
   - understand why MIPS, WMS and Oracle disagree
   - make or approve the correction
   - accept or reject each flag
   - hand open issues to the next shift

   Today they do this by hand, comparing MIPS screens with WMS and relying on experience and shift-to-shift word of mouth. High-severity alerts reach the shift supervisor and production planner, who must acknowledge within 15 minutes. Unacknowledged alerts go to the plant productivity manager.
2. **Decision-makers evaluating the product**: Jason (BBU productivity manager, Lehigh Valley), Bimbo leadership, and Oracle (Isauro Serrano, OCI account manager), with ACSE Solutions presenting. The same production UI is shown in live demos. It must show value quickly (dollars at risk, overproduction avoided) and also hold up as the real tool.

Other roles named in the architecture are the plant productivity manager (escalations, enabling automatic write-back), the IT/integration team (failed events, missing Oracle transactions) and the master data team (unit-per-container corrections). Every endpoint checks the user's role (planner, supervisor or manager) before returning data.

## Product Purpose

An AI inventory reconciliation agent. It continuously compares production requirements in **MIPS** (Bimbo's in-house planning app) with activity in **WMS**: inventory, tickets, fills, shipments, returns to plant, freezer lots and trailer allocations. It also compares both with production and inventory transactions in **ODI / Oracle Fusion**. It then:

1. Builds one reconciled position for each SKU and sales date:
   `Net requirement = Total valid demand − Usable inventory − Completed production + Valid returns/adjustments`.
2. Detects mismatches with 19 fixed rules (R01–R19) mapped to eight known scenarios (listed under Capabilities and Constraints).
3. Explains each flag as an operational instruction with a root cause and an owner, prioritizes it, and sends an alert.
4. Learns from planner accept/reject decisions.

Success means the plant stops producing product it already has, removes the manual reconciliation work, and stops depending on what individual supervisors happen to know. Phase 1 exit criteria:
- planners accept 90% or more of each rule's flags for 4 weeks
- source event to alert takes under 2 minutes for 95% of events

## Positioning

- **Rules compute, AI explains.** Fixed rules produce every quantity, and each flag carries its evidence, so every flag can be audited. The language model only writes the explanation, the root cause, the owner and the shift briefing. It never calculates or invents a number.
- **Demand is consolidated before inventory is applied.** All demand lines for a SKU (sales order, supply planning, moved runs) are added up first. This fixes the root cause of scenario 1 instead of reporting the symptom.
- **Corrections, not variance reports.** Each flag says which system is wrong and the exact correction, e.g. "Apply the 1,200 units to the active sales-order lines and reduce the production requirement to 800."

## Operating Context

- **Source systems:** MIPS (schedule, runs, inventory applied), BUSS (customer sales orders), Oracle Supply Planning, WMS (tickets, shipments, returns, freezer), ODI / Oracle Fusion (production results, batches, inventory transactions).
- **Today's inputs:** five daily Excel extracts in `data/`: MIPS Order Data, ODI Batch Data, ODI Finished Good Data, WMS Fill Rate Report and WMS Order Summary by Item. The production design replaces them with real-time feeds: OCI GoldenGate, Oracle Integration 3, OCI Streaming.
- **Devices:** shared desktop workstations at lines and offices; tablets carried on the floor and in the freezer/warehouse; phones for opening an SMS or Teams alert and acting on it. All three are first-class targets.
- **Rituals:**
  - Alerts go out by severity:
    - High: SMS + Teams + worklist, acknowledge in 15 min
    - Medium: Teams + worklist, 2 h
    - Low: worklist + handoff briefing, end of shift
  - Each flag has one alert. A changed flag updates its alert instead of sending a new one.
  - An AI-written briefing is produced at the end of every shift.
  - Finance reconciles before the period closes.
- **Known pain in the usage scene:** two workstations can show different quantities because one screen hasn't refreshed (scenario 6). Stale data in this UI repeats the problem it exists to solve.

## Capabilities and Constraints

- **Eight mismatch scenarios:**
  1. many order lines for one SKU
  2. inventory on a zero-quantity line
  3. moved production run
  4. recovery run
  5. return to plant not reducing the received quantity
  6. stale screens
  7. freezer inventory not deducted
  8. WMS transactions missing in Oracle
- **19 rules (R01–R19).** R18 (WMS transaction not in Oracle within 10 minutes) and R19 (return to plant does not reduce received quantity) need the real-time WMS ticket feed and Oracle posting status. The current extracts don't contain that data.
- **Priority** = retail value at risk × severity weight (High 3, Medium 2, Low 1) × rule confidence. Rule confidence starts at 1.0 and moves with each accept/reject. A rejected variance with an unchanged quantity is suppressed on the next run.
- **Phase 1 is read-only.** The UI provides:
  - a worklist and dashboard
  - flag detail with evidence
  - accept/reject with a note
  - a conversational assistant (plain-English questions, answered only through fixed Reconciliation API endpoints)
  - shift briefings
- **Phase 2 adds write-back with approval:** Flagged → Proposed → Approved (planner) → Sent (Corrections API → OIC) → Confirmed (change seen in the feed). Rejected closes the flag and lowers rule confidence. Failed returns the correction to Approved for a retry. Automatic write-back is allowed only for rules with 98% acceptance over 8 weeks, enabled by the productivity manager. Every correction keeps a unique ID and before/after values for reversal.
- **Back-end endpoints the UI consumes:** `GET /positions/{sku}`, `GET /flags`, `POST /flags/{id}/decision`, `GET /briefing/{shift}`, `POST /ask`; Phase 2 adds `POST /corrections` and `GET /corrections/{id}`.
- **AI provider:** OCI Generative AI only, called through the OCI SDK for Python. The base model is Gemini 2.5 Pro (`google.gemini-2.5-pro`, on-demand in `us-chicago-1`). Credentials are the OCI API-key values in `.env`. No other AI provider is used. The pipeline must still work with rule-generated text when the model is unavailable.
- **Security:** OCI IAM Identity Domains (least privilege per role), OCI Vault, TLS 1.2+, no source system exposed to the public internet, plant reached over FastConnect or VPN, 2-year retention of flags, decisions and audit records.
- **Terminology:** SKU (Oracle FG number; legacy BUSS code also exists), sales date, production date, line, shift, run (e.g. SU290080A), work order, ticket, lot, retail units, units per container (UPC), flag/exception, rule code.
- **Open items:**
  - WMS product, version and APIs
  - MIPS database type and GoldenGate access
  - BUSS order feed
  - Oracle Fusion business events
  - whether WMS data includes ticket, return and freezer-lot detail
  - plant network
  - alert recipients for each shift
  - Phase 2 write-back APIs

## Brand Commitments

Neutral and unbranded. The UI carries no ACSE Solutions, Bimbo or Oracle identity. It is simply the reconciliation tool. ACSE branding stays in the pitch decks.

## Evidence on Hand

- **Validation run on real data, Saturday sales date 12 September 2026:**
  - inputs: 157 MIPS lines, 86 ODI runs, 36 batches, 18 WMS orders
  - findings: 78 SKUs reconciled, 31 with no mismatch, so 47 SKUs carry flags. 104 flags in total (8 High, 76 Medium, 20 Low): 97 on SKUs and 7 at plant or product-family level. About $227K retail value at risk, 12,952 units of overproduction risk, 35,205 surplus units not carried forward, 2,571 units shipped from stock MIPS cannot see. Note: the architecture deck's "104 flags on 78 SKUs" counts reconciled SKUs, not flagged SKUs.
  - outputs: `output/recon_2026-09-12.{html,xlsx,json}`
- **Worked examples from that data:**
  - SKU 150080 (scenario 1)
  - SKU 8526 (scenario 4)
  - runs SU290080A, SU290082A and SU260145 (scenario 3)
  - SKU 75481 (scenario 7)
  - all 18 WMS orders shipped with a loaded quantity of zero (scenario 8)
- **Source material:**
  - requirement brief: `docs/reference/WMS_AI_Agent_Requirement.docx`
  - architecture deck: `docs/reference/Bimbo Real-Time Inventory Reconciliation Agent.pptx`
  - challenges deck: `docs/reference/Bimbo WMS Inventory Reconciliation PPT.pptx`
- **Absent, never fabricate:** no customer testimonials, no measured production savings, no live real-time data, no confirmed WMS/MIPS APIs, no Phase 2 results. Every number shown must come from the engine or the cited validation run.

## Product Principles

1. **Every number is traceable.** Any quantity on screen comes from the rules and can be traced back to its source records. AI-written text is clearly an explanation, never the source of a figure.
2. **Lead with the action, not the variance.** A flag tells the reader what is wrong, in which system, what to do, and who owns it, in plain operational language a supervisor can act on mid-shift.
3. **One shared truth across screens and shifts.** Everyone sees the same current state and its freshness. The tool must never become another workstation that shows a different number.
4. **Humans decide; the agent learns.** Planners accept, reject or approve. Their decisions visibly shape priority, and nothing writes back to a source system without the approval rules for its phase.
5. **Money and units at risk set the order.** Priority reflects retail value, severity and rule confidence, so the most expensive problem is always first.

## Accessibility & Inclusion

No formal standard has been set yet; WCAG 2.2 AA is the working assumption until Bimbo confirms. Known needs from the usage scene:
- shared workstations, tablets and phones
- operational readers on all three shifts, including overnight
- severity must never be shown by color alone
