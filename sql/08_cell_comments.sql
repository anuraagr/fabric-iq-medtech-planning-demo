-- Cell comments persisted by Writeback (T-16): the latest rows of the budget table that carry a comment.
-- IrComments is a JSON list [{comment, updatedByUser, dateTime, threadStatus, threadAssignee}]; threadAssignee must be null (nobody was assigned).
-- A comment-only writeback adds one row whose Value equals PreviousValue, so the number did not move.
SELECT Region, Category, Year, Quarter, MonthShort, ValueColumnName,
       Value, PreviousValue, IrComments, IrWritebackTimestamp
FROM dbo.medtech_revenue_budget_fy2026
WHERE IsLatest = 1 AND IrComments IS NOT NULL
ORDER BY IrWritebackTimestamp DESC
