# SQL verification queries

Read-only T-SQL for the demo-owned Fabric SQL databases. Run any file with the guarded runner (it refuses anything but a single
`SELECT`/`WITH` statement, so nothing here can change data):

```powershell
python scripts/run_sql.py sql/01_budget_reconciliation.sql --db writeback
python scripts/run_sql.py sql/03_scenario_gross_profit.sql --db writeback
python scripts/run_sql.py sql/04_powertable_assets.sql --db powertable
python scripts/run_sql.py sql/05_budget_for_actual_months.sql --db writeback
python scripts/run_sql.py sql/06_forecast_reconciliation.sql --db writeback
python scripts/run_sql.py sql/07_forecast_vs_budget.sql --db writeback
python scripts/run_sql.py sql/08_cell_comments.sql --db writeback
```

You can also paste them into the Fabric portal SQL editor of the database item. Never query `__fabric_plan_sys` (product-managed).

| File | Database | Proves |
| --- | --- | --- |
| `01_budget_reconciliation.sql` | `FabricPlanningDemo_Writeback` | 648 latest nodes; the 360 leaves add up to the year node and to the region nodes (T-09) |
| `02_budget_history.sql` | `FabricPlanningDemo_Writeback` | `Long with Changes` keeps superseded values (`IsLatest = 0`) next to the current ones (T-09) |
| `03_scenario_gross_profit.sql` | `FabricPlanningDemo_Writeback` | sales, COGS and gross profit by region for every written-back scenario (Upside, Downside) (T-06) |
| `04_powertable_assets.sql` | `FabricPlanningDemo_PowerTable` | the updated and the inserted device rows (T-07) |
| `05_budget_for_actual_months.sql` | `FabricPlanningDemo_Writeback` | the committed budget for Jan-Sep 2026 by region, the SQL side of the actual-vs-budget variance reconciled in T-10 |
| `06_forecast_reconciliation.sql` | `FabricPlanningDemo_Writeback` | 516 latest forecast nodes; the 300 leaves add up to the 2026 year node and the region nodes (T-14) |
| `07_forecast_vs_budget.sql` | `FabricPlanningDemo_Writeback` | plan-year forecast against budget by region (total -380,529.29), the SQL side of the forecast-vs-budget variance reconciled in T-10 |
| `08_cell_comments.sql` | `FabricPlanningDemo_Writeback` | the latest budget rows that carry a cell comment: text, author, UTC time, `threadAssignee` null, value equal to its previous value (T-16) |

Grain reminder: a writeback table stores **every aggregation node** with `'All'` sentinels (`Region`, `Category`, `Quarter`, `MonthShort`; `Year = 'All'` is the
grand total). A **leaf** has no `'All'` in Region, Category or MonthShort (the rolling forecast has no Category column). Always filter `IsLatest = 1` and one `ValueColumnName` before summing.
