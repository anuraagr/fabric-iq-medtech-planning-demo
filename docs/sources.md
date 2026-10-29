# Sources and product boundaries

Microsoft Learn is authoritative. The original reference build used documentation read on **2026-10-21**.
The specific prerequisites, Plan-definition, billing and pinned-license references below were rechecked
on **2026-10-28** for publication. A native UI observation is evidence about that environment, not a universal product contract.

## Primary references

| Reference | Used for |
|---|---|
| [Planning overview](https://learn.microsoft.com/en-us/fabric/iq/plan/overview) | Native Planning scope and documentation navigation |
| [Planning prerequisites](https://learn.microsoft.com/en-us/fabric/iq/plan/overview-prerequisites) | Supported capacity families, XMLA and optional collaboration/writeback connections |
| [Plan definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/plan-definition) | Root, sheet and visual parts; create/get/update definition support |
| [Planning billing](https://learn.microsoft.com/en-us/fabric/iq/plan/resources/billing-fabric-plan) | 30-day sessions scoped to tenant/user/capacity, not browser tab |
| [Capacity throttling](https://learn.microsoft.com/en-us/fabric/enterprise/throttling) | Interpreting capacity delays and rejection |
| [Fabric trials](https://learn.microsoft.com/en-us/fabric/fundamentals/fabric-trial) | Trial expiry and reactivation guidance; check your tenant's dates |
| [Semantic model definition](https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/semantic-model-definition) | Generated TMDL deployment |
| [Refresh dataset in group](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/refresh-dataset-in-group) | Explicit refresh with no notification |
| [Update datasource](https://learn.microsoft.com/en-us/rest/api/power-bi/gateways/update-datasource) | Binding the SQL OAuth credential |
| [Execute queries](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/execute-queries) | DAX reconciliation |
| [Official Planning samples](https://github.com/microsoft/fabric-samples/tree/main/docs-samples/iq/plan) | FMCG PBIX and tutorial workbook patterns |

The original research followed the overview's Planning, PowerTable, Intelligence, Infobridge, writeback,
closing-period and scenario tutorial links. Use the current navigation rather than assuming the UI or
tutorial numbering is immutable.

## Definition APIs are not a turnkey deployment claim

Microsoft documents detailed Plan definitions, including sheet and visual configuration.
This asset uses Plan creation/read-back, but does **not** implement a verified cross-tenant reconstruction
of all sheet data, connection references, comments, scenario state and visuals.
Native UI configuration remains an explicit stage. No undocumented browser endpoints or fabricated
sheet-creation/writeback APIs are used as a substitute.

Exports are saved under `state/plan-exports` for local inspection only. They are not distributed templates.

## Sample provenance and license

The four official assets are pinned to commit `9379bb140202eb4d256f44e2625c80b24c939131`,
with size/SHA-256 checks in `config/demo.config.example.yaml`. They download to ignored `assets/official`
and are not republished as binaries in this repository.

The [license at the pinned commit](https://raw.githubusercontent.com/microsoft/fabric-samples/9379bb140202eb4d256f44e2625c80b24c939131/LICENSE)
is MIT, copyright Microsoft Corporation. See [third-party notices](../THIRD_PARTY_NOTICES.md).
The healthcare data and workbook are generated synthetic content, not real customer rows or patient data.

## Observed deviations and untested behavior

- A shared PowerBIDatasets connection was required by the reference UI despite newer identity-based guidance.
- The writeback ribbon's Add table to semantic model option was absent; explicit TMDL reporting attachment was used.
- Formulas required references selected from the editor's suggestions.
- The author-only demo retained a viewer collaboration banner. Multi-user collaboration was not thereby proven.
- SQL-backed Import reporting needs refresh and usable credentials; no instantaneous synchronization is promised.
- Some chart states initially lacked a usable theme; selecting Default Theme and saving fixed the observed case.
- A stale authentication state could produce a blank Editing page. Reauthenticate and inspect errors; never bypass policy.
- From Sheets and Infobridge bindings were observed with initial values. Later automatic propagation was not verified.
- PowerTable rules constrain its UI, not all database access.
- Approvals, Optimizer/Cube, multi-user enforcement and later forecast-boundary/horizon movement remain outside proven scope.

Reference screenshots illustrate synthetic outcomes. Fresh tenant validation must be executed and recorded separately.
