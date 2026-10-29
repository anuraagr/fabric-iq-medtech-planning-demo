-- 05_budget_for_actual_months.sql   (database: FabricPlanningDemo_Writeback, read-only)
-- The SQL half of the actual-vs-budget variance that T-10 reconciles: the committed FY2026 budget, by region, for the months that already have actuals (Jan-Sep 2026).
-- The actuals are not in SQL (they live in the semantic model and the deterministic generator): variance = actual revenue - this budget.
--   Actual Jan-Sep 2026 = 195,831,920  ->  variance = 195,831,920 - SUM(budget_jan_sep)
-- Leaf rows only (no 'All' sentinel in Region, Category or MonthShort), latest values only.
SELECT
    Region,
    SUM(CAST(Value AS DECIMAL(38, 2))) AS budget_jan_sep
FROM dbo.medtech_revenue_budget_fy2026
WHERE IsLatest = 1
  AND ValueColumnName = 'FY2026 Revenue Budget'
  AND Region <> 'All' AND Category <> 'All' AND MonthShort <> 'All'
  AND Year = '2026'
  AND MonthShort IN ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep')
GROUP BY Region
ORDER BY Region;
