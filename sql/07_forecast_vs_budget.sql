-- 07_forecast_vs_budget.sql   (database: FabricPlanningDemo_Writeback, read-only)
-- Plan-year rolling forecast against the plan-year budget by region, both from the latest leaf values (the budget is planned by Region x Category x month, the
-- forecast by Region x month). Variance = Forecast - Budget; the percentage divides by the absolute budget. These are the numbers the semantic model's
-- [Forecast vs Budget Variance] measure must return (T-10).
WITH forecast AS (
    SELECT Region, SUM(Value) AS amount
    FROM dbo.medtech_rolling_forecast_fy2026
    WHERE IsLatest = 1 AND ValueColumnName = 'FY2026 Forecast' AND Region <> 'All' AND MonthShort <> 'All' AND Year = '2026'
    GROUP BY Region
), budget AS (
    SELECT Region, SUM(Value) AS amount
    FROM dbo.medtech_revenue_budget_fy2026
    WHERE IsLatest = 1 AND ValueColumnName = 'FY2026 Revenue Budget' AND Region <> 'All' AND Category <> 'All' AND MonthShort <> 'All' AND Year = '2026'
    GROUP BY Region
), by_region AS (
    SELECT f.Region, f.amount AS forecast, b.amount AS budget, f.amount - b.amount AS variance
    FROM forecast f
    JOIN budget b ON b.Region = f.Region
)
SELECT Region, forecast, budget, variance, variance / NULLIF(ABS(budget), 0) AS variance_pct
FROM by_region
UNION ALL
SELECT 'Total', SUM(forecast), SUM(budget), SUM(variance), SUM(variance) / NULLIF(ABS(SUM(budget)), 0)
FROM by_region
ORDER BY Region;
