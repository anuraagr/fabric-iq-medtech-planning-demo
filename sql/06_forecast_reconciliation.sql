-- 06_forecast_reconciliation.sql   (database: FabricPlanningDemo_Writeback, read-only)
-- The rolling forecast is planned by Region x month (there is no Category column). Planning stores every aggregation node with 'All' sentinels;
-- a leaf has no 'All' in Region or MonthShort. Closed periods (through Sep 2026) hold the actuals, open periods the forecast. The leaves must add up to the
-- year node and to the region nodes of the plan year - the SQL side of the forecast reconciled against the semantic model in T-10.
WITH latest AS (
    SELECT Region, Year, Quarter, MonthShort, Value
    FROM dbo.medtech_rolling_forecast_fy2026
    WHERE IsLatest = 1 AND ValueColumnName = 'FY2026 Forecast'
)
SELECT
    (SELECT COUNT(*) FROM latest)                                                                                              AS latest_nodes,
    (SELECT COUNT(*) FROM latest WHERE Region <> 'All' AND MonthShort <> 'All')                                                AS leaf_nodes,
    (SELECT SUM(Value) FROM latest WHERE Region <> 'All' AND MonthShort <> 'All' AND Year = '2026')                            AS leaf_total_2026,
    (SELECT Value FROM latest WHERE Region = 'All' AND Year = '2026' AND Quarter = 'All' AND MonthShort = 'All')               AS year_node_2026,
    (SELECT SUM(Value) FROM latest WHERE Region <> 'All' AND Year = '2026' AND Quarter = 'All' AND MonthShort = 'All')         AS region_nodes_total_2026;
