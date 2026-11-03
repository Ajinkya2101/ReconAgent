---
name: Exception Worklist
description: A rack of tagged product. Bag-film white and ink, with the sales-day closure tag as the only colour.
colors:
  ground: "#eff1f3"
  panel: "#f6f7f9"
  raised: "#ffffff"
  hover: "#e5e8eb"
  ink: "#16191d"
  ink-hover: "#2c3238"
  ink-2: "#3f454c"
  ink-3: "#5c636b"
  rule: "#d3d7db"
  rule-2: "#b3b9bf"
  inventory-grey: "#8e959c"
  tag-mon: "#1f5fbf"
  tag-tue: "#23803a"
  tag-wed: "#e0762b"
  tag-thu: "#c62f2a"
  tag-fri: "#ffffff"
  tag-sat: "#f2c230"
  tag-sun: "#6a46a8"
typography:
  figure-xl:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, -apple-system, Helvetica Neue, Roboto, sans-serif"
    fontSize: "2.25rem"
    fontWeight: 650
    lineHeight: 1.05
    letterSpacing: "-0.025em"
    fontFeature: "tnum"
  figure-lg:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, -apple-system, Helvetica Neue, Roboto, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 650
    lineHeight: 1.45
    letterSpacing: "-0.015em"
    fontFeature: "tnum"
  figure-md:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, -apple-system, Helvetica Neue, Roboto, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 650
    lineHeight: 1.2
    letterSpacing: "-0.01em"
    fontFeature: "tnum"
  title:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, -apple-system, Helvetica Neue, Roboto, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 600
    lineHeight: 1.25
  headline:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, -apple-system, Helvetica Neue, Roboto, sans-serif"
    fontSize: "1.0625rem"
    fontWeight: 650
    lineHeight: 1.3
  body:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, -apple-system, Helvetica Neue, Roboto, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.45
    fontFeature: "tnum"
  body-sm:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, -apple-system, Helvetica Neue, Roboto, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, -apple-system, Helvetica Neue, Roboto, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 700
    lineHeight: 1.45
    letterSpacing: "0.05em"
  caption:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, -apple-system, Helvetica Neue, Roboto, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 400
    lineHeight: 1.2
  code:
    fontFamily: "Cascadia Mono, Cascadia Code, Consolas, ui-monospace, SF Mono, Menlo, monospace"
    fontSize: "0.95em"
    fontWeight: 700
    letterSpacing: "0"
  tag-face:
    fontFamily: "Doto, Cascadia Mono, Consolas, monospace"
    fontSize: "15px"
    fontWeight: 900
    lineHeight: 1
    letterSpacing: "0.02em"
rounded:
  hairline: "2px"
  tag: "3px"
  base: "4px"
  badge: "9px"
  round: "50%"
spacing:
  xxs: "4px"
  xs: "8px"
  sm: "12px"
  md: "16px"
  lg: "20px"
  gutter: "24px"
  section: "28px"
  tail: "64px"
components:
  button:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.base}"
    padding: "0 14px"
    height: "36px"
  button-hover:
    backgroundColor: "{colors.hover}"
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.raised}"
    rounded: "{rounded.base}"
    padding: "0 14px"
    height: "36px"
  button-primary-hover:
    backgroundColor: "{colors.ink-hover}"
  button-reject:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.base}"
    padding: "0 14px"
    height: "36px"
  button-reject-hover:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
  button-sm:
    padding: "0 10px"
    height: "30px"
    typography: "{typography.body-sm}"
  tag:
    textColor: "{colors.ink}"
    typography: "{typography.tag-face}"
    rounded: "{rounded.tag}"
    padding: "0 8px 0 19px"
    height: "24px"
  tag-lg:
    rounded: "{rounded.tag}"
    padding: "0 11px 0 24px"
    height: "32px"
  tag-xs:
    rounded: "{rounded.tag}"
    padding: "0 5px 0 14px"
    height: "18px"
  severity-high:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.raised}"
    typography: "{typography.label}"
    rounded: "{rounded.hairline}"
    padding: "0 6px"
    height: "20px"
  severity-medium:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.label}"
    rounded: "{rounded.hairline}"
    padding: "0 6px"
    height: "20px"
  severity-low:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    typography: "{typography.label}"
    padding: "0 6px 0 0"
    height: "20px"
  chip-due:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.hairline}"
    padding: "0 6px"
    height: "20px"
  chip-overdue:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.raised}"
    rounded: "{rounded.hairline}"
    padding: "0 6px"
    height: "20px"
  view-tab:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.base}"
    padding: "0 10px"
    height: "32px"
  view-tab-selected:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.raised}"
  day-face:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.base}"
    padding: "8px 14px 8px 10px"
    height: "52px"
  day-face-selected:
    backgroundColor: "{colors.raised}"
  input-search:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.base}"
    padding: "0 30px 0 34px"
    height: "34px"
  input-field:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.base}"
    padding: "0 12px"
    height: "44px"
  top-strip:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink}"
    padding: "0 24px"
    height: "52px"
  queue-row-current:
    backgroundColor: "{colors.raised}"
    padding: "12px 24px"
---

# Design System: Exception Worklist

## Overview

**Creative North Star: "The Tagged Rack"**

The worklist is a rack of bagged bread on a plant floor. Everything is bag-film white and printed ink; the only saturated thing in sight is the plastic closure tag that says which sales day a loaf belongs to. Each queue row wears its sales day's tag, the day strip is a row of tags with their counts and dollars, and when a planner accepts a carry-forward the surplus on the demand track visibly re-tags into the next sales day's colour. Colour therefore always means "which day", never "how bad" and never "brand".

Density is Operate-mode: a strict two-column grid (queue 7 / detail 5) of hairline-ruled rows, tabular figures everywhere, and hierarchy carried by type scale and weight alone. Money is the largest thing on any row because money at risk sets the order. Severity is an ink label (filled, outlined, plain) with a three-bar meter, readable in greyscale and on an overnight shift.

Surfaces are flat. Separation comes from hairline rules and inset outlines, and the only drop shadows belong to things that physically slide over the page (the detail sheet on tablet and phone, the assistant drawer, the error toast). The UI is neutral and unbranded: no company identity appears anywhere.

**Key Characteristics:**
- Bag-film white and ink, with the sales-day closure tag as the only saturated colour.
- Tags are flat injection-plastic fills with a punched notch and a Doto dot-matrix date code.
- One strict column grid, hairline rules, no elevated cards.
- Segoe/system workhorse sans with tabular figures; monospace for SKU, run and rule codes.
- Severity by ink and a three-bar meter, never by colour.
- One motion: a 220ms ease-out re-tag; reduced motion swaps instantly.

## Colors

A cool grey-white ink world with one family of saturated plastic fills, used only to name a sales day.

### Primary
- **Press Ink** (#16191d): Text, the strip title, selected states (view tabs, roles), primary buttons, High severity and overdue fills, the strip's bottom rule, wrapped-production track segments, focus rings and text selection. It is the system's only "action" colour.
- **Ink Hover** (#2c3238): Hover fill for ink-filled primary buttons only.

### Secondary
**Sales-Day Tag Family** (provisional). One flat fill per weekday, each with its own ink, defined in `recon_agent/config.py` DAY_CODES: Monday Blue (#1f5fbf, white ink), Tuesday Green (#23803a, white ink), Wednesday Orange (#e0762b, ink), Thursday Red (#c62f2a, white ink), Friday White (#ffffff, ink, drawn with a 1.5px inset ink outline so it reads on the ground), Saturday Yellow (#f2c230, ink), Sunday Purple (#6a46a8, white ink).

These values are a provisional placeholder mapping. They are **not** Lehigh Valley's closure-tag code; the plant has not confirmed its real colours or sales-day calendar. When the plant confirms, replace DAY_CODES and these tokens together; nothing else in the system should need to change. Thursday Red is a day, not an alarm, and must never be read or reused as a severity colour.

The tag colours appear in exactly three places: the tag itself, the surplus/carry-forward segment on the demand track, and the favicon (a Saturday tag).

### Neutral
- **Bag-Film Ground** (#eff1f3): Page ground, top strip ground.
- **Panel Film** (#f6f7f9): The detail column and the assistant's question field; one step lighter than the ground to separate the reading pane.
- **Raised White** (#ffffff): The current queue row, selected day face, inputs, inline notices, the assistant drawer, and text on ink fills.
- **Hover Film** (#e5e8eb): Hover fill for rows, buttons, day faces and view tabs.
- **Ink 2** (#3f454c): Secondary text, the shared-scale system bars, outlines of Medium severity.
- **Ink 3** (#5c636b): Tertiary text: captions, provenance, units, decided money, placeholders.
- **Rule** (#d3d7db): Row hairlines, ledger dividers, evidence grid, the shortfall hatch.
- **Rule 2** (#b3b9bf): Stronger hairlines: the detail column edge, input borders, unselected outlines, empty meter bars, scrollbar thumbs.
- **Inventory Grey** (#8e959c): The inventory-applied segment of the demand track and its legend key.

### Named Rules
**The Tag Is The Only Colour Rule.** Saturated colour exists only as a sales-day tag fill or that day's surplus on the demand track. If a colour does not answer "which sales day", it is ink or grey.

**The Ink Severity Rule.** Severity is always ink: High is an ink fill with three white bars, Medium is a 1px ink outline with two ink bars, Low is plain text with one bar. It never takes a tag colour, a red, or any hue.

**The Hatch Is State Rule.** Hatching encodes inventory state, never decoration. A 135° stripe of the current sales day's colour over white marks surplus not yet carried forward; a 90° rule-grey hatch inside an ink outline marks a production shortfall. Once surplus is re-tagged, the hatch is replaced by the next day's flat fill.

## Typography

**Body Font:** Segoe UI Variable Text (with Segoe UI, system-ui, -apple-system, Helvetica Neue, Roboto)
**Label/Mono Font:** Cascadia Mono (with Cascadia Code, Consolas, ui-monospace, SF Mono, Menlo)
**Tag Face:** Doto 700/900, self-hosted (OFL), with the mono stack as fallback

**Character:** A plant-office workhorse sans with tabular figures carries every word and number; the monospace marks things a supervisor types or reads off a label (SKU, run, rule codes). Doto, a dot-matrix face, is the ink-jet date code printed on the tag and appears nowhere else.

### Hierarchy
- **Figure XL** (650, 36px, 1.05, -0.025em): The open SKU's dollars at risk in the detail header.
- **Figure L** (650, 24px, -0.015em): Dollars on each queue row; the ledger total.
- **Figure M** (650, 20px, -0.01em): Dollars on each day face (17px on phone).
- **Title** (600, 20px, 1.25, balanced wrap): The detail header's SKU and item.
- **Headline** (650, 17px, 1.3): Flag titles, the assistant heading, the strip title (700), and queue rows' compact money when a SKU is open.
- **Body** (400, 14px, 1.45, tabular): The base. Explanations cap at 70–72ch.
- **Body Small** (13px): Row second lines, toolbar tabs, the as-of line, tables.
- **Label** (700, 12px, 0.05em, uppercase): Detail section heads ("Reconciled position", "Flags") and the severity label (650). Only real section headings and severity take uppercase.
- **Caption** (400–600, 12px): Units, provenance ("Rule text · 15:57"), sub-lines, legends.
- **Code** (mono, 700, 0.95em): SKU numbers, run IDs, rule codes, system names on the shared-scale bars.
- **Tag Face** (Doto 900 day / 700 date, 15px; 12px small, 19px large): Only inside a tag.

### Named Rules
**The Scale-Only Hierarchy Rule.** Importance is shown by size and weight within one family, never by colour. Money is always the largest figure in its container.

**The Dot-Matrix Stays On The Tag Rule.** Doto is the tag's printed date code. It does not set headings, numbers, or anything off a tag.

**The Tabular Rule.** All figures are tabular (`font-variant-numeric: tabular-nums` on the body) and right-aligned in columns, so dollars and units stack.

## Layout

One grid. On desktop (1100px and up) the page is a sticky 52px top strip over two columns: the queue at 7fr and the detail at 5fr. The detail column is sticky beneath the strip, scrolls independently, and sits on Panel Film behind a Rule 2 hairline. The page gutter is 24px (16px under 720px).

Queue rows are a three-area grid: a 104px tag column, then SKU and item over the rule title and counts, then money over chips at the right. Rows bleed to the gutter edge so the hover and current fills run full width. When a SKU is open on desktop, the other rows quiet to a single line (tag, ID, severity, chips, compact money); severity and acknowledgment deadlines stay visible.

The day strip wraps and never scrolls a sales day off-screen. On phone every day face takes a full row.

Between 720 and 1099px the layout collapses to one column and the detail becomes a slide-over sheet (up to 640px wide) with a Back button. Under 720px the strip drops names and secondary status, the toolbar stacks, rows reflow to tag-and-money over ID over second line, and the detail header stacks money under the title.

Spacing rhythm is 4 / 8 / 12 / 16 / 20 / 24, with 28px before detail sections and a 64px tail at the end of each column. Touch targets grow to 44px (40px small) on coarse pointers.

## Elevation & Depth

The system is flat. Depth inside the page is tonal (ground, panel, raised white) and drawn with hairlines and inset outlines (`inset 0 0 0 1px`), which are borders, not elevation. Drop shadows exist only on surfaces that physically move over the page.

### Shadow Vocabulary
- **Sheet** (`box-shadow: -16px 0 40px rgba(22, 25, 29, 0.14)`): The assistant drawer and the tablet/phone detail sheet, both with a 1px ink left edge.
- **Toast** (`box-shadow: 0 8px 24px rgba(22, 25, 29, 0.25)`): The inline error toast, an ink fill floating bottom-centre.

### Named Rules
**The Only Floating Things Cast Shadows Rule.** A shadow means "this slid in over the page". Rows, day faces, notices and panels in the flow stay flat.

## Shapes

Corners are nearly square. Interactive controls and inputs take a 4px radius; the tag takes 3px; small ink labels (severity, chips, notices, the changes pill) take 2px. Only the user's initials disc is round and only count badges are pill-shaped (9px).

The tag's silhouette is the system's one distinctive shape: a flat rounded rectangle with a circular notch punched out of its left end (a radial-gradient mask; 3.5px radius at 24px tall, 2.5px small, 4.5px large), so the ground shows through as on a real bread-bag closure.

Lines carry structure. Hairlines in Rule separate rows; Rule 2 marks stronger edges; full ink lines (1px, or 2px for the ledger total) close a band: the strip's bottom, the toolbar, the detail header, the first flag and the source records. A pending sales day with no work yet is drawn with a 1px dashed Ink 2 outline.

## Components

### Buttons
Outlined ink, deliberate, nearly square.
- **Shape:** Gently squared (4px), 36px tall (30px small), 14px side padding, 600 weight.
- **Default:** Transparent with a 1px ink border; hover fills Hover Film; active nudges down 1px.
- **Primary:** Ink fill, white text; hover Ink Hover. Used for the decisive action (Accept, Send, Sign in).
- **Reject:** Rule 2 border with Ink 2 text, hover returns to ink on white. It always sits apart from Accept (pushed to the far end of the action row).
- **Quiet:** No border; used in the top strip (Ask, sign out, my claims).
- **Focus:** 2px ink outline, 2px offset, everywhere.
- **Busy / disabled:** 0.55 opacity with a progress cursor while htmx is in flight.

### Day-Code Tag (signature)
The flat closure tag: fill and ink from the sales day, a punched notch at the left, the weekday in Doto 900 and the MM·DD date code in Doto 700. Three sizes (18 / 24 / 32px). Friday's white tag adds a 1.5px inset ink outline. It carries a screen-reader label with the full date and a title tooltip.

### Severity Label
Uppercase 12px label with a three-bar meter (3 × 11px bars, 2px apart). High: ink fill, white bars. Medium: 1px ink outline, two ink bars. Low: plain Ink 2 text, one bar. Never coloured.

### Chips
- **Acknowledge due:** 20px, 1px ink inset outline, clock icon, "Ack by 16:12".
- **Acknowledge overdue:** ink fill, white text, minutes overdue.
- **Claimed:** white with a Rule 2 outline and Ink 2 text.

### Day Strip
A wrapping row of day faces: a tag (or "All open days"), dollars in Figure M and a caption of rows and flags. Faces are outlined in Rule 2 (52px minimum height); hover fills Hover Film; selected becomes white with a 2px inset ink outline.

### Queue Row
Hairline-separated, full-bleed hover in Hover Film, the current row raised to white with a Rule 2 inset outline. Decided rows drop their money and title to Ink 3. A carry-forward line under the tag shows the arrow icon and the next day's tag.

### Ledger Equation and Demand Track
The detail opens on the reconciled position as a ruled equation: an operator column (−, +, =), a label with a caption source, right-aligned figures, and a 2px ink rule above the total. Below it, a 30px demand track on one shared scale: Inventory Grey, ink for wrapped production, a 2px ink demand marker extending past the bar, then either a hatched surplus segment in the current day's colour or a hatched shortfall. On re-tag, the next day's flat fill wipes left-to-right over the surplus (clip-path, 220ms, ease `cubic-bezier(0.16, 1, 0.3, 1)`) and the carry line wipes in with it. A shared-scale table of system bars (8px Ink 2 bars with a 1.5px ink demand mark) follows.

### Inputs / Fields
- **Style:** White, 1px Rule 2 border, 4px radius; search 34px tall with a leading search icon and a `/` key hint; sign-in fields 44px at 17px text.
- **Focus:** Border turns ink plus a 2px ink outline inset by 1px.
- **Selectable options (roles):** 40px outlined segments; selected becomes an ink fill.

### Navigation
- **Top strip:** Sticky, 52px, ground fill, 1px ink bottom rule. Strip title ("Exception worklist") in 17px/700, then the as-of line (mode in 650 behind a Rule 2 divider, a small tag for the loaded sales day, loaded and checked times), then claims count, Ask, and the user's initials disc.
- **View tabs:** 32px, 13px/600 Ink 2 labels with a 12px count; hover Hover Film; selected is an ink fill with white text.

### Flags and Notices
Each flag is a ruled block: severity and rule code, a 17px title, quantities, the explanation with its provenance caption, a bordered evidence grid (150px minimum cells), then acknowledgment and decision. Notices are inset-outlined boxes on white with 2px corners: due acknowledgment is outlined in ink, overdue is an ink fill with white controls.

### Icons
An authored stroke sprite (24px grid, 1.75 stroke, round caps and joins, drawn at 18px; 13–14px in chips and captions) in currentColor: search, chat, check, x, back, clock, claim, refresh, sign out, down, arrow.

## Do's and Don'ts

### Do:
- **Do** use a tag colour only to name a sales day, on the tag or on that day's surplus segment.
- **Do** show severity as an ink label plus the three-bar meter (filled / outlined / plain).
- **Do** use hatching only to encode state: un-carried surplus in the current day's colour, shortfall in rule grey.
- **Do** make money the largest figure in its container and set every number in tabular figures.
- **Do** set SKU, run and rule codes in the monospace.
- **Do** separate content with hairlines (Rule, Rule 2) and close bands with a 1px ink line.
- **Do** keep motion to 150ms hover transitions and the single 220ms re-tag/slide, all on `cubic-bezier(0.16, 1, 0.3, 1)`, collapsing to instant under reduced motion.
- **Do** swap the whole tag family from `DAY_CODES` when Lehigh Valley confirms its closure-tag code.

### Don't:
- **Don't** use red, Thursday's tag colour, or any hue to signal severity, error or urgency; overdue is an ink fill.
- **Don't** present the provisional DAY_CODES colours as the plant's real closure-tag code.
- **Don't** set anything except the tag's date code in Doto.
- **Don't** add drop shadows to rows, day faces, notices or in-flow panels; shadows are for sheets and toasts that slide over the page.
- **Don't** add company branding (ACSE, Bimbo, Oracle) to the interface.
- **Don't** let the day strip scroll a sales day out of view; it wraps.
