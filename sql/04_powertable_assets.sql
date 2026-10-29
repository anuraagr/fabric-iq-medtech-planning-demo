-- 04_powertable_assets.sql   (database: FabricPlanningDemo_PowerTable, read-only)
-- Evidence for T-07 and a health check of the asset registry.
-- 1) the updated row: MD-1248 was 'In Service' before the field-safety-notice update
SELECT [Asset Id], [Asset Tag], [Asset Type], [Status], [Location]
FROM dbo.assets
WHERE [Asset Tag] IN ('MD-1248', 'MD-1500');

-- 2) status mix: 100 rows after the insert (99 imported + MD-1500); In Service 27, Available 29, Under Maintenance 25, Retired 19
-- SELECT [Status], COUNT(*) AS devices FROM dbo.assets GROUP BY [Status] ORDER BY [Status];

-- 3) referential health: assets whose assigned engineer or location does not exist (expected: none)
-- SELECT a.[Asset Tag] FROM dbo.assets a
-- LEFT JOIN dbo.employees e ON e.[Employee Id] = a.[Assigned To]
-- LEFT JOIN dbo.locations l ON l.[Location Id] = a.[Location]
-- WHERE (a.[Assigned To] IS NOT NULL AND e.[Employee Id] IS NULL) OR l.[Location Id] IS NULL;
