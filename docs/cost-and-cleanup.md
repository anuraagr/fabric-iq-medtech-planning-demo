# Cost, capacity and cleanup

## Billing before deployment

Read the current [official Planning billing documentation](https://learn.microsoft.com/en-us/fabric/iq/plan/resources/billing-fabric-plan).
Checked for this asset on **2026-10-28**; verify it again before committing customer/demo capacity.

| Consumption | Documented basis |
|---|---|
| Planner | 847 CU-hours per active 30-day session |
| Stakeholder | 168 CU-hours per active 30-day session |
| Viewer | 37 CU-hours per active 30-day session |
| Native automation / connected planning jobs | Additional job-based consumption; consult the current rate |
| SQL, OneLake, model refresh/XMLA | Additional Fabric workload consumption |

Sessions are scoped to **tenant + user + capacity**, not one session per browser tab or Plan item.
Returning within the same session does not by itself create another session. Opening a plan in Reading
mode can start a session too. A session cannot be cancelled early, and deleting the Plan does not end it.
Pausing/deleting capacity can bill the remaining committed session consumption.

The scripts never create, resize or resume capacity and do not reassign existing workspaces.
Creating a new workspace associates it with the capacity you selected and approved.
No monthly cost estimate is implied by this asset; use current regional pricing and the capacity estimator.

## Capacity sizing and throttling

The reference shared trial was too small for repeated bursts of model refreshes, DAX checks and native
sheet loads without delays. That is evidence about one environment, not a guaranteed sizing threshold.

1. Use an active supported capacity, verify XMLA and leave headroom for other workloads.
2. Before a demo, run `python scripts/capacity_check.py`; start only on **HEALTHY**.
3. Warm the required sheets once. Run the full acceptance suite well before an audience arrives.
4. For an explicit capacity rejection or repeated roughly 20-second delays, stop heavy work and inspect
   Fabric Capacity Metrics. `--wait 45` performs bounded low-frequency readiness probes.
5. A generic error is not enough to diagnose throttling; also inspect authentication, permissions and connectivity.

Closing a tab stops its interactive requests; it does not cancel an active billed 30-day session.
Never resume a paused paid capacity or resize one as an automatic troubleshooting step.

## Trial expiry

Check **OneLake catalog > Govern > Capacities > Trial** in your tenant for the actual expiration date.
No originating-tenant expiration date is shipped. Microsoft documents that non-Power BI Fabric items
become inactive after expiry and describes a limited reactivation/retention window:
[Fabric trial capacity](https://learn.microsoft.com/en-us/fabric/fundamentals/fabric-trial).

Arrange a supported paid capacity or an approved trial extension before scheduling later demos.
Capacity reassignment is an administrator-approved operation outside these scripts.
Do not treat a Plan definition export as a tested cross-tenant backup/restore mechanism.

## Reset only named scenario rows

First register the demo's product-created tables as described in [the proof guide](deployment-guide.md).

```powershell
python scripts\reset.py --scenarios "Upside,Downside"
```

Inspect the dry run. To delete the matching rows deliberately:

```powershell
python scripts\reset.py --scenarios "Upside,Downside" --confirm RESET-FabricPlanningDemo
```

Replace the confirmation suffix if you changed the workspace name. Reset scopes to registered,
owned tables in the configured workspace/database. Base/Actuals are protected by default.
Reset changes SQL destination rows only; it does not reset native sheet inputs, comments or model data.
Replay/commit the scenario inputs and refresh dependent reporting as required. This does not reset the
budget or forecast to their original pre-demo inputs.

## Cleanup the demo

```powershell
python scripts\cleanup.py
python scripts\cleanup.py --include-workspace
```

Both are dry runs. Only after inspecting the exact list:

```powershell
python scripts\cleanup.py --confirm DELETE-FabricPlanningDemo
# Include the workspace only if the demo owns it and it contains no unrelated items:
python scripts\cleanup.py --include-workspace --confirm DELETE-FabricPlanningDemo
```

The manifest identifies ownership by resource ID, not by matching a friendly name. Preexisting resources
are excluded. Unknown workspace items stop workspace deletion; there is no force-unknown override.
If a child deletion fails, the workspace is retained. Keep the manifest so errors can be retried safely.
The product-managed metadata database is not explicitly deleted or modified by this asset.

## Separate manual cleanup

Cloud connections created through the UI, your isolated Azure CLI profile and optional Edge profile are
not removed by `cleanup.py`. Review their use before deleting them manually. Local `.env`, downloads,
reports and state are also retained. Do not revoke shared connections needed by someone else's demo.
