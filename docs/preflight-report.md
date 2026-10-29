# Generate your tenant's preflight report

No originating account, tenant, workspace, capacity, subscription or local machine details are included here.

```powershell
python scripts\preflight.py --offline
python scripts\preflight.py --write-docs
```

The second command queries the configured tenant read-only and writes the stable report to
**`reports/preflight-report.md`**, plus timestamped JSON/Markdown in `reports/`. These files are ignored by Git.
The historical flag name `--write-docs` does not write account data into tracked documentation.

| Classification | Meaning / action |
|---|---|
| Verified | Executed check and observed result |
| Requires administrator action | Have the tenant/capacity administrator verify settings or access |
| Requires user input | Choose IDs, confirm costs/scope, or supply approved test identities |
| Unsupported | Do not deploy that scenario on the indicated option |
| Not yet tested | No success claim; complete the indicated check |

A visible capacity or a valid F-SKU prefix is not proof of sufficient memory, available CUs or configured XMLA.
If several workspaces have the same name, configure an explicit ID. Its name must match your configuration.
Unrelated inactive capacities discovered by preflight do not authorize the script to resume them.

Preflight does not approve deployment for you and does not configure tenant-wide settings.
Follow [security prerequisites](security-and-permissions.md), [cost guidance](cost-and-cleanup.md)
and the [deployment guide](deployment-guide.md).
