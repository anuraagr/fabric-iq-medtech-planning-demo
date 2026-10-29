# Deployment and proof guide

Start with the [README](../README.md). Use one fresh clone and one manifest per target deployment.
Keep `.env`, `state/` and `reports/` private. Do not reuse the reference tenant's resource IDs or evidence.
All examples use the default workspace/prefix `FabricPlanningDemo`.

## Stage gates

| Stage | Required state | Next action |
|---|---|---|
| Preflight | Correct native tenant identity, permissions, active capacity, ODBC, pinned downloads | Preview and approve foundation |
| Foundation | Two SQL databases, two semantic models and two empty Plan items | Configure native sheets |
| Native configuration | Budget/forecast/scenario values written to product-created tables; PowerTable saved | Attach SQL reporting |
| Reporting | Owned generated model imports committed budget/forecast leaves | Refresh and reconcile |
| Demo ready | Current results, UI render checks, healthy capacity, explicit limitations | Present the talk track |

`deploy.py apply` without `--include-writeback` is safe for the first stage: it creates the base model
without referencing business tables that do not yet exist. Source-table entries in the YAML are
specifications for the later reporting stage, not a reason to create SQL tables yourself.

After native writes, use the README's `--update-medtech-model --include-writeback` command.
It checks columns and committed values before replacing the generated model. Save any model customizations
separately: the update is a full generator-managed definition replacement, not a patch to arbitrary custom models.

If model creation succeeds but its refresh fails, the manifest retains `refreshPending`.
Re-running foundation resumes the pending refresh without creating a second model.
If an update was interrupted, repeat the same explicit update command; do not remove the manifest.

## Capture your own writeback proof

Run these from the repository root with the environment configured and the virtual environment active,
or replace `python` with `.venv\Scripts\python.exe`.

### Budget: first write, snapshot, edit, write, compare

Follow manual setup B–C to allocate the **268000000** target and perform the first Writeback.
The intended baseline cell is Europe / Diagnostics & Imaging / Oct 2026.

```powershell
python scripts\verify_writeback.py register `
  --table dbo.medtech_revenue_budget_fy2026 `
  --grain "Region,Category,Year,Quarter,MonthShort,ValueColumnName,IrScenario" `
  --value Value --latest-column IsLatest --scenario-column IrScenario

python scripts\verify_writeback.py snapshot `
  --table dbo.medtech_revenue_budget_fy2026 `
  --grain "Region,Category,Year,Quarter,MonthShort" `
  --value Value --latest-column IsLatest `
  --where "ValueColumnName = 'FY2026 Revenue Budget' AND Region <> 'All' AND Category <> 'All' AND MonthShort <> 'All'" `
  --out state\budget-before.json
```

In the native sheet, change only **Europe / Diagnostics & Imaging / Oct 2026** to **1172743.48**.
Save, then **Writeback > Writeback** for the current sheet. Enter raw units; the grid displays millions.

```powershell
python scripts\verify_writeback.py diff `
  --table dbo.medtech_revenue_budget_fy2026 `
  --grain "Region,Category,Year,Quarter,MonthShort" `
  --value Value --latest-column IsLatest `
  --where "ValueColumnName = 'FY2026 Revenue Budget' AND Region <> 'All' AND Category <> 'All' AND MonthShort <> 'All'" `
  --before state\budget-before.json `
  --expect "Europe|Diagnostics & Imaging|2026|Q4|Oct=1172743.48" --record T-09
python scripts\run_sql.py sql\01_budget_reconciliation.sql --db writeback
```

Expect exactly one changed **leaf**, with no other leaves added or removed. The reference allocation
started at 922743.48 and moved the total to 268250000.01. Rounding of aggregation nodes can differ by a cent.
The native history also records the affected ancestors; the leaf diff does not assert an exact historical row count.

### Forecast: repeat the native input-to-SQL proof

Complete manual setup D–D2 through its first Writeback. Fix closed periods through **September 2026**,
not the machine's current quarter. Product forecasting algorithms may change; record the baseline your tenant produces.

```powershell
python scripts\verify_writeback.py register `
  --table dbo.medtech_rolling_forecast_fy2026 `
  --grain "Region,Year,Quarter,MonthShort,ValueColumnName,IrScenario" `
  --value Value --latest-column IsLatest --scenario-column IrScenario

python scripts\verify_writeback.py snapshot `
  --table dbo.medtech_rolling_forecast_fy2026 `
  --grain "Region,Year,Quarter,MonthShort" --value Value --latest-column IsLatest `
  --where "ValueColumnName = 'FY2026 Forecast' AND Region <> 'All' AND MonthShort <> 'All'" `
  --out state\forecast-before.json
```

Change **Europe / Oct 2026 / FY2026 Forecast** to **6000000**, Save and Writeback.
If it already equals 6000000, this is not a new proof: rehearse a distinct input and restore it, rather than recording a no-op as passed.

```powershell
python scripts\verify_writeback.py diff `
  --table dbo.medtech_rolling_forecast_fy2026 `
  --grain "Region,Year,Quarter,MonthShort" --value Value --latest-column IsLatest `
  --where "ValueColumnName = 'FY2026 Forecast' AND Region <> 'All' AND MonthShort <> 'All'" `
  --before state\forecast-before.json --expect "Europe|2026|Q4|Oct=6000000" --record T-14
python scripts\run_sql.py sql\06_forecast_reconciliation.sql --db writeback
```

The expected delta is **your entered value minus your captured baseline**, not a guaranteed cross-tenant
forecast amount. After attaching reporting, `refresh_model.py --verify` compares SQL with DAX at the actual stored grain.

### Scenario registration

After writing Upside and Downside from their respective active native tabs:

```powershell
python scripts\verify_writeback.py register `
  --table dbo.medtech_scenario_plan_fy2026 `
  --grain "IrScenario,ValueColumnName,Region,Year,Quarter,MonthShort" `
  --value Value --latest-column IsLatest --scenario-column IrScenario
python scripts\validate.py run --only T-06,T-12
```

Do not type scenario values into Base. Longer sheet horizons can produce NULL placeholders outside the plan year.
The scenario verifier checks FY2026 numbers and separation; it does not infer that a new no-edit replay wrote nothing.

### PowerTable: capture before editing

After importing the workbook's **99** Assets rows (and its Employees/Locations sheets):

```powershell
python scripts\verify_powertable.py snapshot --table dbo.assets --key "Asset Tag" --out state\assets-before.json
```

Change **MD-1248 / Status** from In Service to **Under Maintenance**. Insert **MD-1500** as an
**Infusion Pump**, with Status **Available**, Location selected from the lookup and other required fields populated.
Use **Save to Database**. Do not manually supply the identity column.

```powershell
python scripts\verify_powertable.py diff --table dbo.assets --key "Asset Tag" `
  --before state\assets-before.json `
  --expect-update "MD-1248|Status=Under Maintenance" `
  --expect-insert "MD-1500|Asset Type=Infusion Pump|Status=Available" --record T-07
python scripts\run_sql.py sql\04_powertable_assets.sql --db powertable
```

Expect one changed row and one new row, not an arbitrary increase in the count.
If prior edits already exist, choose another explicit rehearsal and capture its own baseline.

## Finish and record only observed evidence

After the reporting-stage command:

```powershell
python scripts\refresh_model.py --verify
python scripts\validate.py run --scope all
python scripts\cleanup.py
python scripts\validate.py render
```

Open every sheet and compare rendered cards/values. `T-05` proves sheet structure, not that a visual painted.
`T-08` proves the saved Infobridge connection, not automatic update propagation.
Record manual UI results only after the action was executed. Use the CLI's `record` help for the observed result and evidence path.

`T-11` remains **not executed** until approved test identities exercise each access layer.
`T-13` is recorded after inspecting the cleanup dry run; never run destructive cleanup just to get a pass.
The local results file starts empty; reference screenshots and reference results are not copied into it.

## Troubleshooting

| Symptom | Next action |
|---|---|
| Missing native table/columns when attaching reporting | Complete that sheet's first Writeback; do not pre-create guessed tables |
| Existing model is not owned | Use a new prefix/workspace, or investigate your own missing manifest; never adopt a foreign model by name |
| Workspace name/ID or capacity mismatch | Correct configuration; existing workspaces are not automatically moved |
| Refresh fails with invalid SQL credentials | Run `refresh_model.py`; inspect `--status`; ordinary portal refresh may use an expired token |
| Repeated ~20s query delay / explicit capacity rejection | Stop heavy work, inspect capacity metrics and wait; do not resume/resize capacity automatically |
| Generic Internal Server Error or blank page | Capture the actual error; investigate auth, permissions, connectivity and capacity rather than assuming one cause |
| UI route or selector differs | Follow current Microsoft Learn UI guidance; optional Playwright helpers are not a portability guarantee |
| Forecast disappears under a category filter | Intentional: the region-only forecast cannot be allocated to product categories by assumption |

Never copy access tokens, sign-in URLs/codes, cookies, or connection credentials into screenshots, issue reports or Git commits.
