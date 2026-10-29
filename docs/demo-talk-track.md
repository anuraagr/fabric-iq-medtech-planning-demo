# MedTech demo talk track (12–15 minutes)

**Audience:** finance/planning leaders and platform owners.
**Story:** a fictional MedTech manufacturer closes Q3 FY2026, plans its remaining year, compares alternative
outcomes and manages its medical-device reference data. No actual customer/patient information is used.

This is a **post-setup walkthrough**. First complete [native setup](manual-setup.md) and capture
[your own proof](deployment-guide.md). The example inputs are already saved after setup:
typing them again is a no-op, not a new writeback demonstration.

## Before the audience arrives

Run `python scripts/capacity_check.py` and wait for HEALTHY. Warm the two Overview pages and PowerTable
sheets once. Run full live reconciliation well before presentation time, not during the warm-up.
Keep a terminal in the repo with the same Azure CLI profile as deployment. Use your own workspace links
from the deployment output. One tab is easier to operate; billing is not per tab.

Stay in Reading mode for the saved-state tour. Switch to Editing only for deliberate input or configuration
steps, save pending work before changing sheets, and never type into row labels.
All entered amounts are raw units while the grid displays millions.

## 1. Executive performance (1 minute)

**Click:** Explorer > Executive Overview. Show the four cards, monthly actual/budget chart, regional
variance and prior-year trend. Select Europe, then Diagnostics & Imaging; clear both afterward.

**Reference outcome:** budget 268.3M, Jan–Sep revenue 195.8M, gross profit 125.2M, margin 63.9%.
The Europe cards were 66.2M / 47.5M / 29.6M / 62.3%. Compare with your own validated values.

**Say:** “The same governed measures drive the operational plan and executive analysis.”
**Recovery:** let visuals finish loading; inspect actual error details. If capacity is unavailable, label
the [reference screenshot](images/11-intelligence-page.jpg) as an illustration, not a live success.

## 2. Shared definitions and P&L (1 minute)

**Click:** Profitability P&L; expand a region into years. Optionally show the planning Data pane's
Measures Table, Date, Geography, Product and P&L Drivers.

**Reference outcome:** the P&L All row covers January 2023–September 2026: gross revenue 898.82M,
net revenue 817.52M, gross profit 503.94M, operating profit 156.36M and operating margin 19.1%.

**Say:** “We distinguish gross-to-net deductions, cost and operating profit. This is a read-only P&L
view, not an editable P&L plan.”
**Recovery:** clear unintended filters and check the year range rather than comparing all-years totals with YTD cards.

## 3. Top-down budget, bottom-up judgment (1.5 minutes)

**Click:** FY2026 Revenue Budget > Europe > Diagnostics & Imaging; expand 2026 > Q4 > Oct.

**Show:** the initial 268M target was distributed using historical mix. Setup added 250K to this leaf:
922743.48 -> 1172743.48, giving about 268.25M total. Those changes are already saved.

**Say:** “Corporate targets and regional knowledge coexist; changing a leaf updates its ancestors.”
**Recovery:** wrong input can rename a row if a label is selected. Select the numeric series cell and use raw units.

## 4. Rolling forecast (1.5 minutes)

**Click:** Rolling Forecast > 2026 > Q4 > Europe / Oct / FY2026 Forecast.

**Show:** actuals linked through Sep 2026, open forecast periods through Dec 2027, and the saved 6000000
Europe October override. The reference engine produced a 267.87M FY2026 outlook; another product version
may produce a different initial forecast. Present the value your SQL/DAX checks reconcile.

**Say:** “Closed actuals, a seasonal forecast and a planner's override form one outlook.”
**Recovery:** expand Q4 if October is hidden; do not reopen a closed actual period merely to make an edit.

## 5. Alternative scenarios (1.5 minutes)

**Click:** Scenario Comparison > 2026 column group; switch Base, Upside and Downside. Then open Scenario Overview.

**Show:** Upside uses sales +3%, COGS −2%; Downside uses sales −4%, COGS +3%.
Reference gross profit: Base 162.34M, Upside 171.64M, Downside 149.66M.
The executive scenario page has data-bound sales/COGS cards and lines; gross-profit commentary is static.

**Say:** “Alternative decisions are isolated from historical actuals.”
**Recovery:** save before switching tabs; scroll to the correct year. Do not repeat scenario Writeback as
an unproven idempotence demo or claim live cross-sheet propagation.

## 6. Comments, not approval (1 minute)

**Click:** FY2026 Revenue Budget > Comments > View All Comments; inspect the existing diagnostics-launch comment.
If authoring a new rehearsal comment, leave Assign to user empty and do not @mention anyone.

**Say:** “The discussion stays with the input. Save, business Writeback and approval are distinct.”
**Recovery:** select a planning cell before Add Comment; the reference PowerTable sheet did not expose that action.
No approval workflow exists in this asset.

## 7. Database proof (1.5 minutes)

```powershell
python scripts\run_sql.py sql\01_budget_reconciliation.sql --db writeback
python scripts\run_sql.py sql\06_forecast_reconciliation.sql --db writeback
python scripts\run_sql.py sql\03_scenario_gross_profit.sql --db writeback
```

**Show:** committed values, filtered at the correct grain. The reference budget was 268250000.01 and
forecast 267869470.72. Current-history counts increase as rehearsals add writes; do not promise a fixed total row count.

**Say:** “The plan is persisted and auditable in Fabric SQL, not trapped inside a visual.”
**Recovery:** inspect Writeback > Logs and SQL connectivity. A failed commit must not be described as complete.

## 8. Reporting after a commit (1 minute plus refresh time)

Only if a new write was made, run `python scripts/refresh_model.py`; wait for REFRESHED.
Open Executive Overview > Refresh Data and show forecast versus budget. Clear product Category filtering:
the region-only forecast deliberately returns BLANK for a product slice.

**Say:** “Import-mode reporting changes after the model refresh, not instantly.”
**Recovery:** use `refresh_model.py --status`. Portal Refresh now may use an expired SQL credential;
the script renews the API-bound credential. Do not rerun model definition deployment just to refresh data.

## 9. Clinical-engineering reference data (1.5 minutes)

**Click:** Assets. Show MD-1248 Under Maintenance and inserted MD-1500 (Infusion Pump). Open Employees
and Locations to explain lookups. The reference tables held 100 assets, 30 synthetic staff and 33 locations.

If demonstrating the form, leave Asset Tag empty or enter a lifetime of 99 to show rejection, then Cancel.
Do not insert another MD-1500. For a new update use an explicit snapshot/diff and Save to Database.

**Say:** “Required fields, controlled statuses and lookups govern this app's input. It is a companion
workflow, not a driver connected to revenue.”
**Recovery:** date fields use the native picker; a disabled Save button can mean no pending changes.

## 10. Connected planning and honest boundaries (1 minute)

**Click:** Scenario Comparison > Sum of FY2026 Forecast; show the Data > Queries entry for Rolling Forecast.
Explain that Infobridge initially shared a matching-grain measure.

**Say:** “We use native cross-sheet integration, not custom ETL.”
**Recovery:** compare with the source before asserting current equality. Later automatic updates were not proven.
Close with the scope limits: multi-user security/approvals, Optimizer/Cube, later horizon rollover and
production credentials are not part of the validated demonstration.

## Controlled live-edit variation

Rehearse this on healthy capacity; the exact variation is a suggested replay, not inherited proof.

1. Capture a forecast snapshot. Confirm the current Europe/Oct value is 6000000.
2. In Editing mode enter 6100000, Save and run Writeback for that forecast sheet.
3. Run the forecast SQL query, then refresh the model and Intelligence. Expect **your prior FY2026
   total + 100000** and a 100000 improvement in forecast-versus-budget variance.
4. Restore 6000000, Save, Writeback and refresh again. Keep history; do not erase unrelated scenario rows.

For the reference baseline this would be 267969470.72 total and −280529.29 variance.
Use your captured baseline, not those numbers blindly.
