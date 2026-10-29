# Security and permissions

This is a synthetic SE demonstration, not a production access-control design or certification.
Use a native member of the target tenant and a dedicated workspace. Do not invite users, change
tenant-wide settings, grant broad access or send notifications as part of unattended setup.

## Prerequisites by layer

| Layer | What to verify |
|---|---|
| Tenant | Planning access, XMLA integration settings and applicable service policy |
| Capacity | Supported active capacity, adequate memory/CUs, XMLA Read Only or Read Write |
| Workspace | Role sufficient for item creation and the native tutorial steps; reference setup used an author/admin |
| Plan item | Access to the item and the required native Planning role |
| Semantic model | Read/Build as required; ownership/rights for definition updates and refresh |
| SQL connection | Your identity can use the configured connection |
| Business SQL tables | Read for verification, appropriate write rights for native persistence |
| PowerTable | Native app rules and access configuration independently of semantic-model RLS |

[Official prerequisites](https://learn.microsoft.com/en-us/fabric/iq/plan/overview-prerequisites)
are authoritative. A token being obtainable is not proof of effective data access.
The preflight explicitly distinguishes what it can observe from administrator-only settings.

## Authentication and local state

Sign in interactively with Azure CLI to the chosen tenant using an isolated `AZURE_CONFIG_DIR`.
The CLI manages its authentication cache outside the repository. This asset holds acquired token values
in memory and does not intentionally persist them in its manifest, logs or source.
Do not use a browser's cookies or scraped tokens as an API credential.

Fabric, Power BI and SQL use different token audiences. The provider checks the token's tenant.
The local manifest rejects a different tenant, and an explicitly configured workspace ID must match the
recorded workspace. Keep a separate clone/state path for another deployment.

The reporting model uses an API-bound short-lived SQL OAuth token. `refresh_model.py` rebinds a fresh
token for an on-demand refresh. It is not a durable scheduled-refresh or production credential strategy.
Tokens configured through the portal were not validated as an alternative in the reference build.

Reports, snapshots, model backups and Plan exports are local under `reports/` or `state/`.
They can contain account/resource details even when they contain no credentials. Never commit them.
Export sanitization is a convenience, not a guarantee that an arbitrary native definition contains no sensitive data.

## Guardrails

- Foundation deployment requires matching tenant/workspace confirmations; no capacity creation/resume/resize.
- Existing workspace name, ID and capacity must agree; no silent reassignment.
- Generated model replacement requires a matching owned manifest ID and an explicit update flag.
- Reporting attachment checks product-created columns and committed values first.
- A missing refresh request ID cannot be replaced with an older completed refresh.
- Verification SQL is read-only; reset is a distinct, parameterized, confirmed deletion path.
- Cleanup is scoped to the current workspace and excludes unowned resources.
- A replacement resource with the same name does not inherit stale ownership.

The generated model's update flag intentionally replaces generator-managed model source. Back up and
reconcile custom model changes first. The guards do not make a maliciously edited manifest trustworthy.

## Multi-user checklist (not executed)

Use existing approved test identities. Do not create accounts or send invitations automatically.

| Test role | Exercise using that identity |
|---|---|
| Planner/author | Open, configure and save authorized sheets; commit only to intended SQL destinations |
| Stakeholder | Enter permitted inputs; confirm prohibited administration is denied |
| Viewer | Read/filter; confirm input, writeback and PowerTable writes are denied |
| Reviewer | Comment/review as configured; do not claim approvals unless an actual workflow exists |

For each identity run `python scripts/permissions.py --help` and the read-only probe, then perform native
UI allow/deny checks. Record workspace, model, database, table and native app outcomes separately.
Leave **T-11 not executed** until this matrix is actually exercised.

Semantic-model RLS does not automatically secure SQL writeback or PowerTable. PowerTable validation
rules are not database constraints and do not protect writes made outside the app.

## Collaboration and notifications

The reference demo has one unassigned planning-cell comment. It has no approvals or notification flow.
Keep **Assign to user** blank and do not @mention real people. Save, business Writeback, model refresh,
and approval are separate actions. Collaboration connection setup for other viewers requires explicit
sharing/permission decisions; an author-only demonstration does not prove it.

Screenshots in this repo are cropped reference illustrations using synthetic data. Inspect any new
screenshots before sharing; remove tenant/account chrome, sign-in URLs/codes and real user comments.
