-- 01_budget_reconciliation.sql   (database: FabricPlanningDemo_Writeback, read-only)
-- Planning stores EVERY aggregation node with 'All' sentinels. A leaf has no 'All' in Region, Category or MonthShort.
-- The leaves must add up to the year total, to each region node and to each quarter node.
WITH latest AS (
    SELECT Region, Category, Year, Quarter, MonthShort, Value
    FROM dbo.medtech_revenue_budget_fy2026
    WHERE IsLatest = 1 AND ValueColumnName = 'FY2026 Revenue Budget'
)
SELECT
    (SELECT COUNT(*) FROM latest)                                                                                        AS latest_nodes,
    (SELECT COUNT(*) FROM latest WHERE Region <> 'All' AND Category <> 'All' AND MonthShort <> 'All')                    AS leaf_nodes,
    (SELECT SUM(Value) FROM latest WHERE Region <> 'All' AND Category <> 'All' AND MonthShort <> 'All')                  AS leaf_total,
    (SELECT Value FROM latest WHERE Region = 'All' AND Category = 'All' AND Year = '2026' AND Quarter = 'All' AND MonthShort = 'All') AS year_total_node,
    (SELECT SUM(Value) FROM latest WHERE Region <> 'All' AND Category = 'All' AND Year = '2026' AND Quarter = 'All' AND MonthShort = 'All') AS region_nodes_total;
