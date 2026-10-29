# Validation scope and reference evidence

**This page is not the status of your tenant.** A fresh clone contains no deployment manifest or live
acceptance-result file. Generate your own report with:

```powershell
python scripts\validate.py run --scope all
python scripts\validate.py render
```

Read `reports/deployment-status.md` and `state/acceptance-results.json`; both are git-ignored.
An exit code of zero for runnable checks does not mean the manual UI work was performed.
Read every **NOT RUN**, **not executed** and **blocked** result.

## What was observed in the reference deployment

The original single-presenter demonstration was built in October 2026 and revisited on October 27.
Its private tenant IDs, account details, operational reports and Git history are not shipped.
Cropped screenshots are illustrations of that build, not imported configuration.

| Capability | Observed result |
|---|---|
| Synthetic model | DAX reference totals matched the deterministic generator |
| Nine native sheets | Saved in the Plan item and opened in the native UI |
| Budget | One leaf moved 922743.48 -> 1172743.48; parent roll-ups and SQL persistence observed |
| Forecast | Europe/Oct moved 5662104.37 -> 6000000; one leaf and its ancestors changed in SQL |
| Scenario outcomes | Upside gross profit 171639881 > Base 162344367 > Downside 149655336 |
| PowerTable | MD-1248 status changed, MD-1500 inserted; 99 -> 100 asset records |
| Column rules | Required fields, status list, lookups and bounds saved; invalid form input rejected |
| SQL-to-reporting | Refreshed budget/forecast measures matched SQL totals, regions, edited cells and monthly series |
| Executive Overview | Four KPI cards and four charts rendered; geography/product slicing observed |
| Scenario Overview | Six sales/COGS cards and two charts rendered from the planning sheet |
| Infobridge | Saved source/query/consumer linkage and initial source/destination values matched |
| Comment | Budget cell comment persisted in writeback without an assignee or mention |
| Operations | Deployment reuse preview and scoped reset/cleanup dry runs executed; nothing deleted |
| Refresh recovery | Plain refresh failed with expired SQL credentials; fresh credential + refresh succeeded |

Reference forecast amounts and historical row counts are observations, not universal outputs of a changing
native forecast engine. Verify your actual forecast baseline and the exact intended delta.

## Reusable-asset validation

Offline coverage includes two synthetic tenant configurations, foundation creation with no SQL-source
dependency, idempotent redeployment, reporting-stage prerequisites, workspace identity/capacity guards,
model ownership, manifest ownership replacement, refresh request correlation, cleanup scope and evidence recording.

The GitHub Actions matrix runs the offline suite and repository checks on Windows and Linux with Python
3.11 and 3.13. It has no tenant secrets and cannot attest to a live deployment.
See the [CI runs](https://github.com/anuraagr/fabric-iq-medtech-planning-demo/actions/workflows/ci.yml) for actual outcomes.

**A second live tenant deployment has not been executed as part of packaging.** The parameterized
deployment tests use fake services. Each SE must execute the native setup and input-to-SQL-to-analysis
proof in their own tenant before presenting it as complete.

## Deliberately unproven or excluded

| Area | Boundary |
|---|---|
| Multi-user security (T-11) | Needs approved native-member test identities and per-layer checks |
| Approvals / notifications | Not configured; no real users should be notified during setup |
| Optimizer / Cube | Not part of the minimum demo |
| Forecast rollover | Advancing the closed/open boundary and extending a later horizon were not proven |
| Live From Sheets / Infobridge updates | Initial values and saved bindings were checked, not later automatic propagation |
| Scenario no-edit Writeback | Not proven idempotent across different historical sheet shapes |
| Durable portal/scheduled credentials | API-bound token is short-lived; refresh script is the demonstrated path |
| Cross-tenant Plan import | No tested portable Plan-definition importer is provided |
| Production governance | Demo rules, ownership guards and tests are not a production security certification |

## Capacity qualification

The reference small shared trial exhibited roughly 20-second query delays and occasional rejection.
Errors may also result from authentication, permissions, connection or product issues; do not label every
Internal Server Error as capacity throttling. Run `capacity_check.py`, inspect actual error details and
consult capacity metrics. Trial expiration is specific to your tenant, not a date baked into this asset.
