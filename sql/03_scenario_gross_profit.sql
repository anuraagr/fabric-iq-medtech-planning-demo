-- 03_scenario_gross_profit.sql   (database: FabricPlanningDemo_Writeback, read-only)
-- Every scenario written back by Planning, by region: Upside (sales +3 %, COGS -2 %) and Downside (sales -4 %, COGS +3 %), each against the plan-year baseline.
-- Base is not stored: it is the semantic-model baseline (Baseline Revenue 250,848,029; Baseline COGS 88,503,662 -> gross profit 162,344,367).
WITH latest AS (
    SELECT IrScenario, Region, Quarter, MonthShort, ValueColumnName, Value
    FROM dbo.medtech_scenario_plan_fy2026
    WHERE IsLatest = 1 AND Year = '2026'
)
SELECT
    IrScenario                                                                                                          AS scenario,
    Region,
    SUM(CASE WHEN ValueColumnName = 'Sales Plan' THEN Value END)                                                        AS sales_plan,
    SUM(CASE WHEN ValueColumnName = 'COGS Plan'  THEN Value END)                                                        AS cogs_plan,
    SUM(CASE WHEN ValueColumnName = 'Sales Plan' THEN Value END) - SUM(CASE WHEN ValueColumnName = 'COGS Plan' THEN Value END) AS gross_profit
FROM latest
WHERE Quarter = 'All' AND MonthShort = 'All'   -- one node per region plus the 'All' total, for each scenario
GROUP BY IrScenario, Region
ORDER BY IrScenario, CASE WHEN Region = 'All' THEN 0 ELSE 1 END, Region;
