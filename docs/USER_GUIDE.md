# Inventory Reconciliation Agent: User and Business Guide

| Item | Value |
|---|---|
| Document type | User and business guide |
| Language standard | ASD-STE100 Simplified Technical English, Issue 9 |
| Application | Inventory Reconciliation Agent (exception worklist) |
| Plant | Bimbo Bakeries USA, Lehigh Valley bakery (Breinigsville, PA) |
| Phase | Phase 1 (the agent reads data and recommends corrections; it does not write to source systems) |
| Data in this guide | Validation run for sales date 12 September 2026 |

---

## 1. About this guide

### 1.1 Purpose

This guide tells you what the Inventory Reconciliation Agent does. It also tells you how to use it.

### 1.2 Persons who use this guide

- Production planners.
- Shift supervisors.
- Plant productivity managers.
- Business persons who decide if the plant will use the agent.

### 1.3 Language rules

This guide uses ASD-STE100 Simplified Technical English (STE). STE uses short sentences and a controlled vocabulary. Thus, persons who do not speak English as a first language can also read it.

Technical names (for example, MIPS, SKU, "Accept") are not in the STE dictionary. Section 4 gives the meaning of each technical name.

### 1.4 Conventions

| Convention | Meaning |
|---|---|
| **Bold text** | A button, a tab, or a label on the screen. |
| `Monospace text` | A value that you type, or a code that the screen shows. |
| WARNING | A condition that can cause injury to persons. (This guide has no warnings.) |
| CAUTION | A condition that can cause damage to data or unwanted production. |
| NOTE | Information that helps you. |

---

## 2. The application for business persons

### 2.1 The problem

The bakery uses three systems to plan, make and ship product:

- **MIPS** plans the production. It holds the customer orders and the production schedule.
- **ODI / Oracle** records the production. It holds the quantity that each production run made.
- **WMS** controls the warehouse. It holds the customer orders, the stock allocations and the shipments.

These three systems frequently show different quantities for the same product. Today, supervisors compare the MIPS screens with the WMS screens manually. They use their experience and tell the next shift by speech. This procedure is slow. It also depends on the knowledge of each person.

When the systems do not agree, these problems can occur:

- The plant makes product that it already has (over-production).
- Surplus product stays on one sales day and is not used on the next sales day.
- The warehouse ships stock that MIPS does not know about (for example, stock from the freezer).
- Shipments do not get to Oracle. Thus, finance cannot close the period correctly.

### 2.2 The solution

The Inventory Reconciliation Agent compares the data from the three systems for each product (SKU) and each sales day. It does these steps:

1. It calculates one correct position for each SKU (the "reconciled position").
2. It uses 17 fixed rules to find each mismatch between the systems.
3. It writes each mismatch as a "flag". Each flag tells you the problem, the system that is wrong, and the correction.
4. It puts the flags in sequence. The flag with the largest retail value at risk is first.
5. It records each planner decision. It uses these decisions to change the sequence of the subsequent flags.

### 2.3 Main principles

- **The rules calculate. The AI only explains.** Fixed rules calculate each quantity. Each flag shows the source records (the "evidence"). An AI model writes the explanation text only. The AI model does not calculate or change a number.
- **The agent adds all demand before it applies inventory.** The agent adds all MIPS order lines for a SKU first. Then it applies the inventory. This prevents the most frequent cause of over-production.
- **The agent gives corrections, not only differences.** For example: "Apply the 1,200 units to the active sales-order lines and decrease the production requirement to 800."
- **Persons make the decisions.** Planners accept or reject each flag. In Phase 1, the agent does not change data in MIPS, WMS or Oracle.

### 2.4 Results from the validation run

The agent did a validation run on real plant data for sales date Saturday, 12 September 2026.

| Measurement | Result |
|---|---|
| MIPS order lines read | 157 |
| ODI production runs read | 86 |
| Production batches read | 36 |
| WMS orders read | 18 |
| SKUs compared | 78 |
| SKUs with no mismatch | 31 |
| Flags found | 104 (8 High, 76 Medium, 20 Low) |
| Retail value at risk | Approximately $227,000 |
| Units at risk of over-production | 12,952 |
| Surplus units not moved to the next sales day | 35,205 |
| Units shipped from stock that MIPS cannot see | 2,571 |
| WMS orders shipped with no trailer load scan | 18 of 18 |

NOTE: These results come from one sales day only. They are not measured savings. The plant must use the agent for a longer time to measure savings.

### 2.5 Phase 1 targets

Phase 1 is complete when these two conditions are true:

- Planners accept 90% or more of the flags from each rule, for 4 weeks.
- For 95% of events, the time from the source event to the alert is less than 2 minutes.

NOTE: The second target is for the real-time version. The current version reads daily Excel files. Refer to Section 11.

---

## 3. Persons and their tasks

| Role | Tasks in the application |
|---|---|
| Production planner | Examines flags. Accepts or rejects flags. Makes the correction in MIPS. |
| Shift supervisor | Acknowledges High flags in 15 minutes. Claims rows. Reads the shift briefing. Writes notes for the next shift. |
| Plant productivity manager | Gets the High flags that nobody acknowledged. In Phase 2, this person lets the agent write corrections automatically. |
| IT / integration team | Corrects interface problems (for example, transactions that did not get to Oracle). |
| Master data team | Corrects item data (for example, units per container). |
| Business decision-maker | Reads the dollar value at risk and the flag totals. Uses the validation results to decide on the project. |

---

## 4. Technical names

| Name | Meaning |
|---|---|
| MIPS | The bakery production planning system. It holds the schedule lines, the production runs and the inventory that it applies to demand. |
| BUSS | The system that sends customer sales orders to MIPS. |
| OCloud / Oracle Supply Planning | The system that sends supply-plan lines to MIPS. |
| ODI / Oracle | The system that records production results (units ordered, units wrapped, ending inventory) and batch data. |
| WMS | The warehouse management system. It holds customer orders, allocations, shipments and freezer stock. |
| SKU | One product. The SKU number is the Oracle finished-good (FG) number, for example `150080`. |
| Sales day | The day the customer gets the product. Each sales day has a color tag (for example, SAT is yellow). |
| Run | One production run on one line and one shift, for example `SU290080A`. |
| Wrapped units | The units that the line packed. This is the completed production. |
| Demand | The total quantity on all MIPS lines for a SKU. |
| Inventory applied | The inventory that MIPS subtracted from demand before it sent the order to production. |
| Reconciled position | The correct quantity for a SKU after the agent compares all three systems. |
| Net requirement | The quantity that the plant must still make. A negative value is a surplus. |
| Carry forward | To move surplus units to the next sales day as inventory. |
| Flag | One mismatch that one rule found. Also called an "exception". |
| Rule code | The code of the rule that found the flag, for example `R05`. |
| Severity | The importance of a flag: High, Medium or Low. |
| Priority | The number that sets the sequence of the flags. Refer to Section 5.4. |
| Rule confidence | A number that shows how frequently planners accept the flags of a rule. |
| Claim | A mark that tells other persons that you work on a row. |
| Acknowledge | To tell the agent that you saw a High flag. |
| Shift briefing | A short text that tells the next shift the most important problems. |
| Assistant | A panel where you type a question about the reconciliation. |
| Evidence | The source values that caused a flag. |

---

## 5. How the agent finds problems

### 5.1 Input data

In Phase 1, the agent reads five Excel files for each sales day. These files are in the `data` folder:

| File | Source system | Contents |
|---|---|---|
| MIPS Order Data | MIPS | The schedule lines for each SKU (sales orders and supply-plan lines). |
| ODI Finished Good Data | ODI / Oracle | The production result of each run. |
| ODI Batch Data | ODI / Oracle | The batches mixed for each run. |
| WMS Fill Rate Report | WMS | Each customer order and its shipment status. |
| WMS Order Summary Report by Item | WMS | The orders and allocations for each SKU. |

When a person puts new files in the `data` folder, the agent reads them again automatically.

### 5.2 The reconciled position

For each SKU, the agent uses this equation:

```
Net requirement = Total demand (all MIPS lines)
                − Inventory applied
                − Completed production (wrapped units, all runs)
                + Returns and adjustments
```

- A positive net requirement is the quantity that the plant must still make.
- A negative net requirement is a surplus. You must carry it forward to the next sales day.

NOTE: The current extracts do not contain returns data. Thus, "Returns and adjustments" shows `—` on the screen.

### 5.3 The rules

The agent uses 17 rules. Each rule has a code, a base severity, and an owner.

| Code | The rule finds this problem | Base severity | Usual owner |
|---|---|---|---|
| R01 | MIPS applied the inventory to the first order line only. The other lines can cause over-production. | High | Production planner |
| R02 | MIPS subtracted inventory, but it did not record the inventory. Nobody can trace it. | Medium | Production planner |
| R03 | A production line has zero quantity and zero output (a "blind" line). | Low | Production planner |
| R04 | A production run moved to a different line. A zero-quantity copy stays on the old line. | Medium | Production planner |
| R05 | A recovery run made more than the remaining demand. | High | Shift supervisor |
| R06 | Surplus production did not move to the next demand. | Medium | Production planner |
| R07 | WMS shipped stock that MIPS cannot see (for example, freezer stock). | High | Warehouse / WMS |
| R08 | Inventory is more than demand. The net order is negative. | Medium | Production planner |
| R09 | The MIPS order quantity and the WMS order quantity are different. | Medium | IT / integration |
| R10 | WMS allocated less than the order quantity. | Medium | Warehouse / WMS |
| R11 | MIPS and WMS use different units per container for the same SKU. | Medium | Master data |
| R12 | WMS shows orders as shipped, but no trailer load scan occurred. | High | Warehouse / WMS |
| R13 | Two WMS reports show different totals. | Medium | IT / integration |
| R14 | The batches mixed are different from the MIPS schedule. | Low | Shift supervisor |
| R15 | The SKU number has a different format in one source system (for example, extra spaces). | Low | IT / integration |
| R16 | The ODI copy of the MIPS order quantity is different from MIPS. | Medium | IT / integration |
| R17 | A value is very different from the plant median. It can be a data-entry error. | Medium | Shift supervisor |

Some rules change the severity for large quantities. For example, R05 is High at 1,000 units or more, and Medium below 1,000 units.

NOTE: Two more rules (R18 and R19) need real-time data. Thus, they operate only in the simulation (the top bar shows "Simulation"). R18 finds WMS transactions that do not get to Oracle in 10 minutes. R19 finds returns that do not decrease the received quantity.

### 5.4 Priority

The agent puts the flags in sequence by priority. The flag with the highest priority is first.

```
Priority = (Retail value at risk + base value) × Severity weight × Rule confidence
```

| Severity | Weight |
|---|---|
| High | 3 |
| Medium | 2 |
| Low | 1 |

- The retail value at risk is the number of units multiplied by the retail price of the SKU.
- The base value makes sure that flags with no dollar value (for example, R12) also get a position in the sequence.
- Rule confidence starts at 1.0. Refer to Section 9.

### 5.5 Explanations

Each flag has an explanation text. The explanation can come from two sources:

- **Rule text.** The rule writes the text. This text is always available.
- **AI explanation.** An AI model (Gemini 2.5 Pro on OCI Generative AI) writes the text. It also gives a root cause and an owner.

The label above each explanation tells you the source. If the AI model is not available, the agent uses the rule text. The agent continues to operate correctly without the AI model.

---

## 6. The screen

The main screen is the **Exception worklist**. It has these areas:

| Area | Location | Contents |
|---|---|---|
| Top strip | Top | The data source, the load time, the change indicator, **My claims**, **Ask**, your name, and the sign-out button. |
| Shift briefing | Top of the left column | The first sentence of the briefing and the **Read briefing** button. |
| Sales-day filter | Left column | One tile for each open sales day. Each tile shows the day tag, the number of rows, the number of open flags, and the dollar value. |
| View tabs | Left column | **Open**, **Needs acknowledgment**, **Claimed by me**, **Decided**. Each tab shows a count. |
| Search | Left column | A box where you type a SKU number or an item name. |
| Queue | Left column | The rows, in priority sequence. One row is one SKU (or one plant-level problem). |
| Detail | Right column | The reconciled position, the flags, and the source records for the selected row. |
| Assistant | Right side panel | The question box and the answers. |

On a phone or a small tablet, the detail area replaces the queue. Use the **Queue** button to go back.

### 6.1 The row in the queue

Each row shows:

- The sales-day tag (color and date).
- The SKU number and the item name. Plant-level rows show `PLANT`.
- The largest dollar value of one open flag on the row.
- The severity, the title and the units of the top flag.
- "+N more" if the row has more open flags.
- A clock chip if a High flag needs acknowledgment.
- The initials of the person who claimed the row.

### 6.2 Data freshness

The top strip shows "Daily extracts, not live". It also shows the sales day and the time that the agent loaded the data.

Each 20 seconds, the screen asks the agent for changes. The strip then shows the time of the last check. If other persons changed rows, the strip shows a button: "N rows changed by others · Update". If the AI explanations become available, the strip shows "AI explanations ready · Reload".

CAUTION: If you see a "changed by others" button, push it before you make a decision. If you do not, you can make a decision on old data.

---

## 7. Procedures

### 7.1 Sign in

1. Open the worklist address in a web browser. Your IT team gives you this address (for example, `http://localhost:8001/`).
2. On the "Who's on shift?" screen, type your name in the **Name** box.
3. Select your role: **Planner**, **Supervisor** or **Manager**.
4. Push **Open the worklist**.

Result: The Exception worklist opens.

NOTE: The agent puts your name on each claim, acknowledgment and decision. Use your real name. In production, the plant sign-in system (OCI IAM Identity Domains) will replace this screen.

### 7.2 Read the shift briefing

1. Find the **Shift briefing** area at the top of the queue.
2. Read the first sentence.
3. To see all of the briefing, push **Read briefing**.
4. Read the briefing text and the **Top actions** list.
5. To open the SKU of an action, push the SKU number at the start of that action.
6. When you complete the briefing, push **Mark read**.

Result: The briefing closes. It stays closed on this computer until the agent loads new data.

To read the briefing again, push **Read again**.

### 7.3 Find a row

To show the rows for one sales day:

1. Push the tile of that sales day.

To show all sales days:

1. Push **All open days**.

To show a specified group of rows:

1. Push one of the view tabs:
   - **Open**: rows with one or more open flags.
   - **Needs acknowledgment**: rows with a High flag that nobody acknowledged.
   - **Claimed by me**: rows that you claimed.
   - **Decided**: rows where all flags have a decision.

To find a SKU or an item:

1. Push the `/` key, or push the search box.
2. Type the SKU number or a part of the item name (for example, `150080` or `brioche`).

Result: The queue shows only the rows that match.

To move in the queue with the keyboard:

1. Push `j` to go to the next row.
2. Push `k` to go to the previous row.
3. Push `Enter` to open the row.
4. Push `Esc` to close the detail.

### 7.4 Examine a row

1. Push the row in the queue.

Result: The detail area shows the row.

2. Read the **Reconciled position** equation:
   - Demand, all MIPS lines.
   - Minus inventory applied.
   - Minus completed production.
   - Plus returns and adjustments.
   - Equals the quantity to make, or the surplus to carry forward.
3. Read the bar. It shows inventory (grey), wrapped units, and the demand mark on one scale. A surplus shows after the demand mark. A shortage shows before it.
4. Read the systems table. It shows the MIPS, ODI and WMS quantities on the same scale.
5. Read each flag in the **Flags** area. Each flag shows:
   - The severity, the rule code and the title.
   - The units and the dollar value.
   - The explanation (rule text or AI explanation).
   - The owner and the root cause (or scenario).
   - The evidence values.
6. To see the original records, push **Source records**.

Result: The screen shows the MIPS schedule lines, the ODI production runs and the WMS customer orders for this SKU.

### 7.5 Claim a row

Claim a row to tell other supervisors that you work on it.

1. Open the row.
2. Push **Claim**.

Result: The detail shows "Claimed by you". The queue shows your initials on the row. The row shows in your **Claimed by me** view.

To release the claim:

1. Open the row.
2. Push **Release**.

NOTE: If a different person claimed the row, the detail shows the name of that person. You can still make a decision. The agent then records your name, not the name of the other person.

### 7.6 Acknowledge a High flag

You must acknowledge each High flag in 15 minutes.

1. Push the **Needs acknowledgment** tab.
2. Open the row.
3. Find the flag that shows "acknowledge by HH:MM".
4. Push **Acknowledge**.

Result: The flag shows "Acknowledged by (your name) at (time)".

CAUTION: If nobody acknowledges a High flag in 15 minutes, the flag becomes overdue. The plan sends overdue flags to the plant productivity manager.

### 7.7 Accept or reject a flag

1. Open the row.
2. Read the flag, the evidence and the source records.
3. Make sure that you have the current data. Refer to Section 6.2.
4. If necessary, type a note in the **Note for the next shift** box. Write what you examined or changed.
5. Do one of these steps:
   - If the flag is correct, push **Accept**.
   - If the flag is not correct, push **Reject**.

Result for **Accept**: The flag shows "Accepted". The status is "awaiting correction in the source system".

Result for **Reject**: The flag shows "Rejected". The agent decreases the confidence of the rule. On the next run, the agent does not show this flag again if the quantity stays the same.

6. If you accepted the flag, make the correction in the source system. The text below **Accept** tells you the system (for example, MIPS).

CAUTION: In Phase 1, **Accept** does not change MIPS, WMS or Oracle. You must make the correction in the source system manually. If you do not, the problem stays.

To change a decision:

1. Open the row.
2. Push **Change decision** below the flag.
3. Push **Reject instead** or **Accept instead**.

### 7.8 Carry surplus forward

Rules R05, R06 and R08 find surplus units. When you accept one of these flags, the agent shows the carry-forward:

1. Accept the flag. Refer to Section 7.7.

Result: The flag shows "Carry N units (today's tag) → (next sales day tag)". The bar color changes to the tag of the next sales day. The sales-day filter shows a tile for the next sales day with "carried in · awaiting MIPS correction".

2. In MIPS, add the surplus units as beginning inventory for the next sales day.
3. In MIPS, decrease the production requirement for the next sales day by the same quantity.

NOTE: The current sales-day calendar (Monday to Saturday) is provisional. The plant must confirm it.

### 7.9 Ask the assistant a question

1. Push **Ask** in the top strip.
2. Type your question in the box. Use 500 characters or less. For example: `Why is MIPS asking for more 150080?`
3. Push **Ask**.

Result: The answer shows below the box. The answer gives links to the related rows.

4. To open a row from the answer, push the link.
5. To close the assistant, push the **X** button or push `Esc`.

NOTE: If the AI model is connected, the AI writes the answer. It gets all numbers from the agent data. If the AI model is not connected, the assistant shows the agent data for the SKU numbers or the item names in your question.

### 7.10 Sign out

1. Push the sign-out button next to your name in the top strip.

Result: The sign-in screen opens.

---

## 8. Reports

An administrator can start a full run from the command line. Refer to the technical overview. Each run writes three files to the `output` folder:

| File | Persons who use it | Contents |
|---|---|---|
| `recon_YYYY-MM-DD.xlsx` | Planners and finance | The Excel workbook. Refer to Section 8.1. |
| `recon_YYYY-MM-DD.html` | Managers | A dashboard page that you can open in a web browser. |
| `recon_YYYY-MM-DD.json` | Other systems | The same results in a machine-readable format. |

### 8.1 Workbook sheets

| Sheet | Contents |
|---|---|
| Briefing | The totals, the shift briefing and the top actions. |
| Exceptions | One line for each flag. It has a **Decision (accept/reject)** column and a **Planner Note** column. |
| SKU Positions | The reconciled position of each SKU. |
| Allocation Proposal | How to apply inventory and production to each order line, and the quantity to carry forward. |
| Rule Catalog | The rules and the problem area of each rule. |
| Data Lineage | The source file of each value. |

### 8.2 Record decisions from the workbook

You can also record decisions in the workbook when you do not use the worklist:

1. Open the workbook.
2. Go to the **Exceptions** sheet.
3. In the **Decision (accept/reject)** column, type `accept` or `reject` for each flag.
4. If necessary, type a note in the **Planner Note** column.
5. Save the workbook.
6. Give the workbook to the administrator.

Result: The administrator loads the decisions into the agent. The agent uses them on the next run.

---

## 9. How the agent learns

The agent records each decision. It keeps a count of accepted flags and rejected flags for each rule.

- When planners accept the flags of a rule, the confidence of that rule increases. Its flags go higher in the queue.
- When planners reject the flags of a rule, the confidence of that rule decreases. Its flags go lower in the queue.
- A rule with no decisions has a confidence of 1.0.
- If you reject a flag and the quantity does not change, the agent does not show that flag on the next run.

The change occurs immediately. All flags of the same rule move to their new position in the queue.

---

## 10. Problems and corrective actions

| Problem | Possible cause | Corrective action |
|---|---|---|
| The sign-in screen opens again. | Your browser deleted the sign-in cookie. | Sign in again. Refer to Section 7.1. |
| The briefing shows "Rule text · AI text on the way". | The AI model is writing the explanations. | Wait. When the strip shows "AI explanations ready · Reload", push it. |
| The briefing shows "Rule text · AI unavailable". | The agent cannot connect to the AI model. | Use the rule text. It contains all the numbers. Tell the IT team. |
| The assistant says "The AI assistant isn't connected here". | The AI credentials are not set. | Type a SKU number or an item name. The assistant shows the agent data. |
| The assistant says "Nothing matched". | The question has no known SKU number or item name. | Type a SKU number (for example, `150080`) or a part of an item name. |
| The queue says "Nothing open". | All SKUs agree, or all flags have a decision. | Push the **Decided** tab to see the decisions. |
| The numbers on two screens are different. | One screen did not get the last changes. | Push the "changed by others · Update" button. Refer to Section 6.2. |
| A claim or acknowledgment is not there after a restart. | The agent keeps claims and acknowledgments in memory only. | Claim or acknowledge the row again. Decisions are not lost. |
| The worklist does not open. | The server is stopped, or the `data` folder has no extract files. | Tell the administrator. |

---

## 11. Limits of Phase 1 and the plan for Phase 2

### 11.1 Limits of Phase 1

- The agent reads daily Excel extracts. It does not read live data.
- With the daily extracts, the agent does not write corrections to MIPS, WMS or Oracle. In the simulation, a planner or a manager can send a correction to the simulated systems. Use the "Approve and send" button below the flag.
- The agent does not send SMS or Microsoft Teams alerts.
- Rules R18 and R19 operate only in the simulation.
- The returns data is not in the daily extracts. The "Returns and adjustments" line shows `—`. In the simulation, the line shows the returns to the plant.
- The sign-in screen does not use a password. The plant sign-in system will replace it.
- Claims and acknowledgments are lost when the server restarts. Decisions are kept.

### 11.2 Plan for Phase 2

- **Real-time data.** OCI GoldenGate, Oracle Integration 3 and OCI Streaming will replace the Excel files.
- **Alerts by severity:**

| Severity | Alert channels | Time to acknowledge |
|---|---|---|
| High | SMS, Microsoft Teams and the worklist | 15 minutes |
| Medium | Microsoft Teams and the worklist | 2 hours |
| Low | The worklist and the shift briefing | End of shift |

- **Corrections with approval.** Each correction will go through these statuses: Flagged → Proposed → Approved (planner) → Sent → Confirmed. If the correction fails, it goes back to Approved.
- **Automatic corrections.** The productivity manager can let the agent correct a rule automatically. This is possible only if planners accepted 98% of the flags of that rule for 8 weeks.
- **Audit.** Each correction will have a unique ID and the values before and after the change. Thus, a person can reverse it. The agent will keep flags, decisions and audit records for 2 years.

---

## 12. Related documents

| Document | Location |
|---|---|
| Technical overview | `docs/TECHNICAL_OVERVIEW.md` |
| Requirement brief | `docs/reference/WMS_AI_Agent_Requirement.docx` |
| Architecture presentation | `docs/reference/Bimbo Real-Time Inventory Reconciliation Agent.pptx` |
| Challenges presentation | `docs/reference/Bimbo WMS Inventory Reconciliation PPT.pptx` |
| Product notes | `PRODUCT.md` |
