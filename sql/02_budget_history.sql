-- 02_budget_history.sql   (database: FabricPlanningDemo_Writeback, read-only)
-- "Long with Changes" keeps history in the same table: IsLatest = 1 is the active value, older rows keep the value they replaced.
-- After the T-09 edit (Europe / Diagnostics & Imaging / Oct 922,743.48 -> 1,172,743.48) the leaf and each of its ancestor nodes
-- (12 changed nodes in total across the Region, Category, Quarter and Year hierarchies) have a superseded row (IsLatest = 0)
-- next to the current one; the query shows the ones on the Europe / Diagnostics & Imaging path and the grand-total path.
SELECT Region, Category, Year, Quarter, MonthShort, Value, PreviousValue, IsLatest, LastUpdatedAt
FROM dbo.medtech_revenue_budget_fy2026
WHERE ValueColumnName = 'FY2026 Revenue Budget'
  AND Region IN ('Europe', 'All')
  AND Category IN ('Diagnostics & Imaging', 'All')
  AND MonthShort IN ('Oct', 'All')
  AND (IsLatest = 0 OR PreviousValue IS NOT NULL)
ORDER BY Region DESC, Category DESC, Year, Quarter, MonthShort, LastUpdatedAt;
