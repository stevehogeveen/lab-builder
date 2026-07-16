# Lab Builder Operator Surface Inventory

## Scope

This inventory records the operator-facing surfaces present before the Operator Home simplicity slice. It separates normal operator facts from setup-specific controls and advanced evidence. The dependency engine, guarded workflows, and destructive confirmation gates remain unchanged.

## Route inventory

| Surface | Operator purpose | Information tier after this slice |
| --- | --- | --- |
| Operator Home (`/`, `/dashboard`, `/kits`) | Selected kit, current phase, plain-language state, equipment summary, actionable exceptions, next action, progress | Operator |
| Global Settings (`/global-settings`) | Shared network defaults and included equipment | Operator and Details |
| Upgrade Helper (`/upgrade-helper`) | Firmware discovery, policy review, and guarded upgrade planning | Operator, Details, and Advanced |
| iLO (`/ilo`) | Server management setup and read-only verification | Operator, Details, and Advanced |
| Storage (`/storage`) | Controller discovery, RAID planning, approval, and guarded apply | Operator, Details, and Advanced |
| ESXi (`/esxi`) | Hypervisor media and host setup | Operator, Details, and Advanced |
| Windows (`/windows`) | Windows and vCenter target setup | Operator, Details, and Advanced |
| Cisco (`/modules/cisco`) | Switch access, discovery, configuration planning, and approval | Operator, Details, and Advanced |
| NetApp (`/modules/netapp`) | ONTAP discovery and storage service planning | Operator, Details, and Advanced |
| QNAP (`/qnap`) | Optional storage setup | Operator, Details, and Advanced |
| OVF Templates (`/modules/ovf-templates`) | Local template registration and deployment planning | Operator and Details |
| Run Center (`/execution`) | Final review, guarded execution, current run, and run-specific evidence | Operator and Details |
| Reports (`/configs`) | Historical receipts, reports, logs, and technical evidence | Details and Advanced |

## Duplicate fact inventory

### Readiness displays

Before this slice, the same kit readiness was displayed independently in:

1. The global Dashboard hero status row.
2. The Dashboard hero metadata grid.
3. The sidebar kit meter.
4. The Deployment cockpit ring.
5. The setup-module signal card.
6. The Build path list.
7. The Operations summary include.

Resolution: Operator Home `Progress` is the one normal-mode owner. Setup pages retain only their page-scoped pre-check because it answers whether that specific page can proceed.

### Blocker messages

Before this slice, the same missing setting could appear in:

1. The global hero status row and Next label.
2. The hero metadata Blockers and Next Step cells.
3. The sidebar blocker count.
4. The Open issues drawer.
5. The Deployment cockpit summary.
6. The blocker signal card.
7. The Build path row.
8. The Operations summary and detailed checks.

Resolution: Operator Home `AttentionItems` is the one normal-mode owner. The global issue drawer and all Dashboard copies were removed. Each exception uses an explanation and a concrete fix instead of an internal blocker code.

### Console and manual actions

Before this slice, the Dashboard exposed multiple competing routes into execution and evidence:

1. Open next step.
2. Run Center beside the recommended action.
3. Run Center as the last Build path row.
4. Open log for each recent result.
5. Open current config and Download current config.
6. Create and switch kit controls in the default view.

Resolution: Operator Home shows one primary `NextAction`. Kit management is behind View details. Run logs and configuration evidence remain in Run Center and Reports, not on Home. Device-specific console tests stay on the owning device page.

### Device and setup summaries

Before this slice, module state was repeated in the setup-module count, Build path, Operations summary cards, sidebar readiness dots, and issue drawer.

Resolution: `DeviceSummary` gives one compact count in normal mode. Healthy setup areas are summarized. Only exceptions expand. The complete per-area list is available through View details.

### Provider and internal terminology

Operator-facing paths could expose implementation language through validation or evidence, including provider mode values, Redfish paths, ONTAP API wording, dependency labels, capability keys, raw errors, environment variables, and report paths.

Resolution: Operator Home is built from a small projection and never receives raw logs, API payloads, provider state, dependency graph nodes, or manual overrides. Its copy adapter translates implementation terms into operator language. Technical evidence remains in setup-page Details/Advanced sections, Run Center, and Reports.

## Replacement map

| Removed or demoted surface | Canonical owner |
| --- | --- |
| Dashboard global hero and metadata | Operator Home headline, phase, and Progress |
| Sidebar readiness meter and blocker count | Operator Home Progress and AttentionItems |
| Global Open issues drawer | Operator Home AttentionItems |
| Deployment cockpit | Operator Home Headline, SupportingMessage, and NextAction |
| Setup-module and blocker signal cards | Operator Home DeviceSummary and AttentionItems |
| Build path | NextAction plus setup links under View details |
| Operations summary on Dashboard | Operator Home model |
| Dashboard job status and Open log controls | Run Center and Reports |
| Dashboard config quick actions | Global Settings and Reports |
| Default kit-management forms | View details |

## Canonical model boundary

Operator Home consumes only:

- `KitName`
- `CurrentPhase`
- `DisplayState`
- `Headline`
- `SupportingMessage`
- `DeviceSummary`
- `AttentionItems`
- `NextAction`
- `Progress`

The following remain outside this model:

- full dependency graph
- provider states and provider modes
- raw logs and debug bundles
- verification evidence and report paths
- API payloads and raw device responses
- manual overrides
- destructive controls and confirmation gates

## Five-second test

The selected kit and plain-language state are at the top. Actionable exceptions are the only expanded list. The single visually dominant button names the next safe action. A reviewer does not need to compare totals, scan a green-card grid, open diagnostics, or decode implementation terminology.
