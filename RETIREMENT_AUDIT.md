# Legacy Lab Builder Retirement Audit

**Audit date:** 2026-07-16  
**Legacy repository:** `stevehogeveen/lab-builder`  
**Canonical repository:** `stevehogeveen/infra-config-portal`  
**Product name:** Lab Builder

## Decision

`stevehogeveen/infra-config-portal` is the canonical Lab Builder codebase.

`stevehogeveen/lab-builder` is retired as a legacy prototype and historical reference. No new product work, provider work, operator UI work, or fixes should begin in this repository unless the task is explicitly a migration or historical audit.

The legacy repository should remain readable because it contains useful operational history and a small number of capabilities that may be selectively migrated. It must not continue as a second active implementation.

## Why The Canonical Repository Was Selected

The canonical repository has become the more complete and maintainable product foundation:

- separate FastAPI backend and React/Vite frontend;
- saved lab profiles and one active-lab model;
- centralized provider-mode and action-policy controls;
- guarded real-lab actions and explicit confirmation boundaries;
- Build Verification, provider readiness, reports, and recovery flows;
- the dependency-aware Lab Build Engine;
- the unified Build Plan, Run Console, and Completion Report direction;
- Linux and Windows CI with backend, component, build, and Playwright coverage;
- active product work and the current Lab Builder simplicity contract.

The legacy repository remains a FastAPI/Jinja application centered on a large `app/main.py`, server-rendered setup pages, local run artifacts, and older module/stage abstractions. That architecture was valuable as a prototype, but maintaining both implementations would split safety decisions, operator behavior, and provider fixes.

## Audit Scope

The audit reviewed:

- the legacy README and architecture documentation;
- the open legacy pull requests;
- recent commit history in both repositories;
- provider and workflow surfaces called out by the legacy documentation;
- distinctive legacy utilities and reference material;
- equivalent or superseding behavior in the canonical repository;
- items that must not be migrated.

## Capability Disposition

| Legacy item | Disposition | Reason |
| --- | --- | --- |
| Offline ONTAP REST API compatibility catalog | **Retain in archive; migration candidate** | This is a distinct offline catalog with parser, SQLite builder, manifest validation, diff logic, CLI, and tests. No equivalent was found in the canonical repository. Migrate only as a separate reviewed capability. |
| Standalone ESXi OVF deployment fallback | **Retain in archive; migration candidate** | The legacy path tries pyVmomi NFC import, then falls back to standalone ESXi SSH deployment. The canonical path currently uses guarded `govc import.spec` / `govc import.ovf` and does not contain the same fallback. Any migration must preserve current action-policy, evidence, confirmation, concurrency, and no-secret boundaries. |
| Release archive, checksum, dependency snapshot, and image export scripts | **Retain as reference; do not port yet** | Useful packaging ideas exist, but the canonical repository has a different frontend/backend/container layout. Define the canonical release format first, then selectively reuse concepts. |
| `PROJECT_FAILURES_AND_LESSONS.md` | **Retain as historical operational reference** | The document records real failure classes and useful principles such as readback after writes, capability detection, explicit stage boundaries, kit isolation, and safe retry behavior. Legacy file paths and incident-specific details must not be treated as current architecture. |
| Missing ESXi-media repair in legacy PR #2 | **Close as superseded** | The canonical repository already treats missing ESXi media as an explicit readiness blocker and exposes a next safe action rather than requiring the legacy Run Center fallback implementation. |
| Simplicity contract and Operator Home work in legacy PR #3 | **Close as superseded** | The canonical repository contains the active Lab Builder simplicity and unified build-journey work. Keeping a second implementation would recreate the duplication the contract is intended to prevent. |
| QNAP module service | **No migration** | The legacy service is a success-shaped placeholder for discover/plan/validate/preview/apply/status/repair, not a production provider implementation. |
| Windows module service | **No migration** | The legacy module service is also a success-shaped placeholder. Useful Windows/OVF behavior must be evaluated from concrete implementation files, not the stub contract. |
| General legacy provider code | **No blanket port** | The canonical repository already owns the active provider-policy, readiness, workflow, report, and guarded-action architecture. Migrate only a proven missing capability through a focused issue and PR. |
| Local configs, media, credentials, generated artifacts, run logs, caches, and environment files | **Never migrate** | These are local operational state or sensitive material and must remain outside source control and outside any repository migration. |

## Unique Assets To Preserve In The Archive

### 1. Offline ONTAP API catalog

Relevant legacy paths:

```text
app/api_catalog/ontap.py
scripts/ontap-api-catalog
api_catalog/manifests/lab-builder-netapp.yml
api_catalog/ontap/specs/
api_catalog/ontap/ontap_api_catalog.sqlite3
tests/test_ontap_api_catalog.py
docs/ontap-api-catalog.md
```

The catalog supports connected refresh, on-box or saved-spec import, offline validation, and version-to-version API diffing. It should be considered for later migration after the canonical Lab Builder safety branch is stable.

### 2. Standalone ESXi OVF fallback

Relevant legacy path:

```text
scripts/deploy_windows_ovf_to_esxi.py
```

The script attempts the normal vSphere/NFC path and falls back to a standalone ESXi SSH deployment path when NFC import is rejected. The canonical repository currently has a more strongly guarded deployment surface, so the fallback must not be copied wholesale. It requires a fresh design review and tests against the canonical action-policy model.

### 3. Packaging references

Relevant legacy paths include:

```text
scripts/build_release.sh
scripts/audit_release.sh
scripts/export_docker_image.sh
release-dependencies.yml
```

These remain reference material only. They are not the release process for the canonical React/FastAPI application.

### 4. Failure and incident history

Relevant legacy path:

```text
PROJECT_FAILURES_AND_LESSONS.md
```

The high-value principles are:

- validate early and fail before a long run;
- verify state after every write;
- choose providers and backends from proven capabilities, not labels;
- keep dependency and stage handoffs explicit;
- preserve evidence and logs as product features;
- isolate state by kit and target;
- never blindly retry a potentially successful write.

## Open Pull Request Disposition

### PR #2: Fix Run Center review when ESXi media is missing

**Disposition:** Close as superseded by the canonical ESXi readiness and blocker model.

The implementation is tightly coupled to the legacy Jinja Run Center and should not be merged into a retired application.

### PR #3: Canonical Operator Home simplicity slice

**Disposition:** Close as superseded by the canonical repository's Lab Builder simplicity and unified build journey.

The design work remains useful history, but there must be only one active Operator Home implementation.

## Canonical Repository Safety Note

Retiring this legacy repository does **not** approve or merge every open branch in the canonical repository.

The canonical cumulative Lab Builder branch must remain draft while any provider-contact, partial-write evidence, request-local confirmation, report truthfulness, or other merge-blocking safety finding is unresolved. Repository consolidation is not permission to weaken those gates.

## Retirement Actions

- [x] Select `stevehogeveen/infra-config-portal` as the canonical Lab Builder repository.
- [x] Record this audit.
- [x] Mark the legacy repository as retired in its README.
- [x] Stop new feature work in the legacy repository.
- [x] Close legacy PR #2 as superseded.
- [x] Close legacy PR #3 as superseded.
- [x] Record retained migration candidates in the canonical repository.
- [ ] Use GitHub repository settings to archive `stevehogeveen/lab-builder` after confirming the notice and PR closures.

The final GitHub archive toggle is an administrative repository-setting action. Until that toggle is applied, this README and audit are the authoritative no-new-work boundary.

## Rules After Retirement

1. New Lab Builder work belongs in `stevehogeveen/infra-config-portal`.
2. Do not fix or extend legacy code in place.
3. A legacy capability may move only through a focused canonical issue and reviewed PR.
4. Do not copy credentials, hostnames, customer data, media, artifacts, or environment files.
5. Do not infer that archived code meets current provider-safety or concurrency requirements.
6. Keep this repository available for history until all explicitly retained migration candidates are resolved or rejected.
