# What Goes Wrong in the Simulation, and How the Agent Spots It

> **Data model changed (v3, 9 Nov 2026).** The agent now uses the business team's mapping: BUSS orders, Oracle forecast, ODI batch mapping, WMS stock and MIPS work orders, with 13 rules. See [DATA_MODEL.md](../../../data-model/DATA_MODEL.md) and [EVENT_CONTRACT.md](../../../data-model/EVENT_CONTRACT.md). Where this guide describes MIPS order lines, ODI runs, tickets, rules R01-R19 or carry-forward, it describes v2 and is due for a rewrite.

A plain-language walk through every problem in the practice data, with the numbers worked out step by step. The detailed technical version is [SCENARIO_WALKTHROUGH.md](SCENARIO_WALKTHROUGH.md).

---

## Before you start

### The four systems

| System | Think of it as | What it tells us |
|---|---|---|
| **Planning system** (MIPS) | the bakery's to-do list | how much of each bread is needed for the day |
| **Production records** (ODI) | the bakery floor's report | how much each production run was told to make, and how much it actually made |
| **Warehouse system** (WMS) | the warehouse's order book and logbook | what customers ordered, what was set aside for them, what was received, shipped or sent back |
| **Accounting system** (Oracle) | the company's books | a copy of every stock movement in the warehouse |

### What the agent does

For each bread, the agent adds up what each system says and checks that the totals agree:

- how much was **needed**;
- how much was **made**;
- how much customers **ordered**;
- how much was **set aside** for them.

When something doesn't add up, it raises a **warning** and explains it. Each warning gets:

- **an urgency level:** *urgent*, *needs attention* or *for information*;
- **the money at stake:** the number of loaves involved × the price of one loaf;
- **a code on the worklist screen** (such as R05), so you can find it.

The worklist puts the warnings with the most money at stake and the most urgency at the top.

### The practice day

The practice data describes one invented Saturday with 12 kinds of bread and 4 customers. Ten breads each have one problem built in, and two breads are perfect, to show what "all fine" looks like. On top of that, eight short **scenarios** can be played live to break things and watch new warnings appear.

---

## Part A. The problems built into the practice day

At the start, the agent finds **14 warnings**, with **$17,500** at stake.

### 1. Potato bread: a top-up run made far too much
*On screen: R05, urgent · $5,628*

- The plan needed **6,000** loaves.
- The first run was told to make 6,000 but only made **5,200**, so **800** were missing.
- A second, "top-up" run was then ordered. It should have made 800, but it made **2,000**.
- Total made: 5,200 + 2,000 = **7,200**.
- Extra loaves nobody needs: 7,200 − 6,000 = **1,200**.
- Money at stake: 1,200 × $4.69 = **$5,628**.

**Why it matters:** the top-up run was sized to the whole order instead of the gap. The extra bread has to be carried to the next day or frozen.

The same top-up run also mixed **4** batches of dough when **3** were planned, which is about 500 extra pieces. That gets a separate *for information* warning (on screen: R14), so the extra output is expected and not treated as a mystery.

### 2. Route 12: shipped, but never scanned onto the truck
*On screen: R12, urgent*

- Route 12's order of **7,900** loaves shows as **shipped**.
- But **0** loaves were scanned onto the truck.

**Why it matters:** without the truck scan, nobody can prove which goods went on which truck.

### 3. Whole-wheat bread: leftover stock counted against only one order
*On screen: R01, urgent · $4,690*

- The bread has three order lines: **1,300** (moved from the day before) + **2,400** + **7,600** = **11,300** needed.
- There were **2,300** loaves already in stock, so the bakery only needed to make 11,300 − 2,300 = **9,000**.
- But the planning system counted the stock against the first order line only, and that line was just 1,300. Only 1,300 of the 2,300 got used: 11,300 − 1,300 = **10,000** to make.
- Risk of baking too much: 10,000 − 9,000 = **1,000** loaves.
- Money at stake: 1,000 × $4.69 = **$4,690**.

**Why it matters:** leftover stock should count against the bread's whole order, not just one line of it.

### 4. Hot-dog buns: the warehouse delivered buns that were never made
*On screen: R07, urgent · $2,513*

- The plan needed **4,000** packs, and the bakery was told to make 4,000.
- The bakery actually made **3,300**.
- Yet the warehouse set aside the full **4,000** for customers.
- So 4,000 − 3,300 = **700** packs came from somewhere the planning system can't see, most likely the freezer.
- Money at stake: 700 × $3.59 = **$2,513**.

**Why it matters:** if the freezer stock isn't written off, the records show more frozen stock than there really is.

### 5. White round-top bread: stock was used, but nobody wrote it down
*On screen: R02, needs attention · $3,192*

- The plan needed **5,000** loaves, but the bakery was only asked to make **4,200**.
- So the planning system assumed 5,000 − 4,200 = **800** loaves were already in stock.
- But the records show **no** stock for this bread.
- Money at stake: 800 × $3.99 = **$3,192**.

**Why it matters:** if those 800 loaves don't really exist, customers will be short.

### 6. Multigrain bread: more stock than needed, but a run was still scheduled
*On screen: R08, needs attention · $1,437*

- The plan needed **1,200** loaves, and **1,500** were already in stock.
- So 1,500 − 1,200 = **300** too many. The production record even shows the order as **minus 300**.
- Money at stake: 300 × $4.79 = **$1,437**.

**Why it matters:** no production is needed. The run should come off the schedule, and someone should check the 300 extra loaves really exist.

### 7. Square wheat bread: a production run moved, and left an empty copy behind
*On screen: R04, needs attention*

- The run was moved from Line 1 to Line 2, where it made all **3,000** loaves. The numbers match.
- But an **empty copy** of the run, with no shift, was left behind on Line 1.

**Why it matters:** stock or orders might still be pointing at the empty copy instead of the real run.

### 8. Honey wheat bread: extra loaves not carried to tomorrow
*On screen: R06, for information · $1,796*

- The plan needed **2,500**; the bakery made **2,900**.
- Extra: 2,900 − 2,500 = **400** loaves.
- Money at stake: 400 × $4.49 = **$1,796**.

**Why it matters:** the 400 extra loaves should reduce tomorrow's baking.

### 9. Hamburger buns: the warehouse has a bigger order than the plan
*On screen: R09 and R10, needs attention · $718 each*

- The planning system expects **3,500** packs (2,000 + 1,500).
- In the warehouse, Route 27 raised its order from 800 to **1,000** *after* the planning system last updated. The warehouse total is now 1,500 + 1,200 + 1,000 = **3,700**.
- **Warning 1:** the warehouse wants 3,700 − 3,500 = **200** more than planned. 200 × $3.59 = **$718**.
- **Warning 2:** the warehouse has only set aside 3,500, so Route 27 is **200 short**. Also **$718**.

**Why it matters:** unless the plan is updated, the bakery makes 200 too few.

### 10. Italian bread: the two systems count a tray differently
*On screen: R11, needs attention*

- The planning system says a tray holds **10** loaves; the warehouse says **12**.
- Every quantity matches (1,800 everywhere); only the tray size differs.

**Why it matters:** tray and stack counts will never agree between the two systems until someone fixes the product setup.

### 11. White round-top 2-pack: an empty line sitting in a live run
*On screen: R03, for information*

- This bread is listed in a production run with **0** ordered and **0** made, and nobody ordered it.

**Why it matters:** if any stock was counted against this empty line, it never reduces the real need.

### 12. Product codes with extra spaces
*On screen: R15, for information*

- **7** of the 17 plan lines (all the customer-order lines) have a product code with a space at the end, like "900101 ".

**Why it matters:** other systems might fail to match that code to the same bread. The agent cleans the codes, so nothing is missed here.

### 13. Butter top and cinnamon raisin: everything adds up

| Bread | Needed | Made | Ordered in the warehouse | Set aside |
|---|---|---|---|---|
| Butter top | 4,000 | 4,000 | 4,000 | 4,000 |
| Cinnamon raisin | 900 | 900 | 900 | 900 |

No warnings. These two are the "before" picture for the scenarios.

---

## Part B. The eight live scenarios

Each scenario starts from the practice day as it is at **8:00 pm on Friday** and breaks one thing. Every scenario only *adds* warnings; the 14 above stay as they are.

### S1. Leftover stock counted against only one order (butter top)

**What happens:** the planning system finds 2,000 leftover loaves and cuts production from 4,000 to **2,000**. But it counts the leftovers against the first order line only.

**The sums**
- Needed: 1,500 (first line) + 2,500 (second line) = **4,000**.
- With 2,000 in stock, the bakery should make 4,000 − 2,000 = **2,000**.
- Counting the stock against the 1,500 first line only covers 1,500, so the plan asks for 4,000 − 1,500 = **2,500**.
- Risk of baking too much: 2,500 − 2,000 = **500**.

**Warning:** *needs attention* (R01), 500 × $3.99 = **$1,995**.

### S2. Leftover stock hidden on an empty line (cinnamon raisin)

**What happens:** the planning system adds an **empty** plan line and uses it to take 200 loaves of stock into account, cutting production from 900 to **700**. Then the bakery schedules that empty line as its own production run.

**The sums**
- Needed: **900**. Asked to make: **700**. So 900 − 700 = **200** loaves of stock were assumed.
- The records show **no** stock for this bread.

**Warnings**
- *Needs attention* (R02): 200 × $5.29 = **$1,058**. Stock was used but never written down.
- *For information* (R03): an empty line sits in a live production run.

### S3. A production run moves and leaves an empty copy (butter top)

**What happens:** the butter-top run moves from Line 2 to Line 1 and makes all **4,000** loaves there. The old Line 2 entry stays behind, emptied and without a shift.

**The sums:** needed 4,000, made 4,000. Everything adds up; the only problem is the leftover empty record.

**Warning:** *needs attention* (R04): an empty copy of a moved run.

### S4. A top-up run makes the whole order again (cinnamon raisin)

**What happens, in two steps**

1. The run makes **800** instead of 900, so **100** are missing. For a moment, the warehouse has already set aside all 900, so the agent warns that 100 loaves came from unseen stock (R07, temporary).
2. Ninety minutes later, a top-up run makes the **full 900** again instead of just the missing 100.

**The sums after step 2**
- Made: 800 + 900 = **1,700**. Needed: **900**.
- Extra: 1,700 − 900 = **800**.
- The temporary warning disappears, because there is now more than enough.

**Warning:** *needs attention* (R05), 800 × $5.29 = **$4,232**.

### S5. Damaged bread sent back, but the receiving slip isn't updated (butter top)

**What happens:** the warehouse sends **120** damaged loaves back to the bakery and records the return. The accounting system records it too. But the original receiving slip still says all **4,000** loaves arrived.

**The sums**
- The slip should now say 4,000 − 120 = **3,880**, but it still says **4,000**.
- The warehouse still has 4,000 loaves set aside for customers, but only **3,880** good loaves exist.

**Warnings**
- *Needs attention* (R19): 120 × $3.99 = **$478.80**. The receiving slip is wrong.
- *Needs attention* (R07): also **$478.80**. The warehouse has promised 120 loaves it doesn't have.

### S6. A customer raises an order, and the plan never hears about it (cinnamon raisin)

**What happens:** Route 12 raises its order in the warehouse from **900** to **1,100**. The planning system hasn't refreshed, so it still plans **900**.

**The sums**
- Warehouse order 1,100 − planned 900 = **200** more than planned.
- Set aside: still 900, so 1,100 − 900 = **200 short**.

**Warnings**
- *Needs attention* (R09): **$1,058** (200 × $5.29). The plan doesn't match the order.
- *Needs attention* (R10): **$1,058**. The customer is 200 short.

### S7. Bread taken from the freezer, but the plan thinks it was baked (butter top)

**What happens:** Route 27 needs **300** more loaves. The plan goes up by 300 and the bakery is told to make 4,300, but it still only makes **4,000**. The warehouse fills the extra 300 from the **freezer**, and the accounting system records it. Nobody tells the planning system that the 300 came from the freezer.

**The sums**
- Needed: 1,500 + 2,800 = **4,300**. Made: **4,000**.
- Set aside for customers: **4,300**.
- From stock the planning system can't see: 4,300 − 4,000 = **300**.

**Warning:** *needs attention* (R07), 300 × $3.99 = **$1,197**.

### S8. Shipments that never reach the accounting system (whole plant)

**What happens:** the truck to SIM DC SOUTH is loaded and leaves at **8:00 pm** with six kinds of bread, and the warehouse records six shipments. Every shipment must appear in the accounting system within **10 minutes**.

| Bread | Loaves shipped | Accounting system |
|---|---|---|
| Whole wheat | 3,000 | **never received it** |
| Potato | 2,000 | recorded ✓ |
| White round top | 2,500 | recorded ✓ |
| Hot-dog buns | 1,600 | recorded ✓ |
| Honey wheat | 1,500 | recorded ✓ |
| Multigrain | 1,200 | **rejected it** ("Lot not found") |

**The timing:** at 8:02 pm the two missing records are only 2 minutes late, so there's no warning yet. At **8:11 pm** they are 11 minutes late, past the 10-minute limit, and the warnings appear. Nothing new happened; the clock simply ran out.

**Warnings**
- *Urgent* (R18), whole wheat: 3,000 × $4.69 = **$14,070**. This goes to the top of the list.
- *Urgent* (R18), multigrain: 1,200 × $4.79 = **$5,748**.

**Why it matters:** until these are recorded, the company's books show stock that has already left the building.

---

## Part C. Everything at a glance

| Where | Bread | What went wrong | Urgency | Loaves | Money at stake |
|---|---|---|---|---|---|
| Practice day | Potato | top-up run made too much | urgent | 1,200 | $5,628 |
| Practice day | (all) | shipped without a truck scan | urgent | 7,900 | – |
| Practice day | Whole wheat | stock counted against one line only | urgent | 1,000 | $4,690 |
| Practice day | Hot-dog buns | delivered more than was made | urgent | 700 | $2,513 |
| Practice day | White round top | stock used, not written down | needs attention | 800 | $3,192 |
| Practice day | Multigrain | more stock than needed | needs attention | 300 | $1,437 |
| Practice day | Square wheat | empty copy of a moved run | needs attention | – | – |
| Practice day | Honey wheat | extra not carried to tomorrow | for information | 400 | $1,796 |
| Practice day | Hamburger buns | bigger order than planned | needs attention | 200 | $718 |
| Practice day | Hamburger buns | customer short | needs attention | 200 | $718 |
| Practice day | Potato | extra dough batch | for information | – | – |
| Practice day | Italian bread | tray sizes differ | needs attention | – | – |
| Practice day | Round top 2-pack | empty line in a live run | for information | – | – |
| Practice day | (all) | product codes with spaces | for information | – | – |
| S1 | Butter top | stock counted against one line only | needs attention | 500 | $1,995 |
| S2 | Cinnamon raisin | stock used, not written down | needs attention | 200 | $1,058 |
| S2 | Cinnamon raisin | empty line in a live run | for information | – | – |
| S3 | Butter top | empty copy of a moved run | needs attention | – | – |
| S4 | Cinnamon raisin | top-up run made too much | needs attention | 800 | $4,232 |
| S5 | Butter top | returned bread still on the slip | needs attention | 120 | $478.80 |
| S5 | Butter top | promised bread that isn't there | needs attention | 120 | $478.80 |
| S6 | Cinnamon raisin | bigger order than planned | needs attention | 200 | $1,058 |
| S6 | Cinnamon raisin | customer short | needs attention | 200 | $1,058 |
| S7 | Butter top | freezer bread the plan can't see | needs attention | 300 | $1,197 |
| S8 | Whole wheat | shipment missing from the books | urgent | 3,000 | $14,070 |
| S8 | Multigrain | shipment rejected by the books | urgent | 1,200 | $5,748 |
