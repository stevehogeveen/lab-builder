from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkflowDefinition:
    key: str
    title: str
    page: str
    history_scopes: tuple[str, ...]
    included_aliases: tuple[str, ...] = ()
    run_center_stage: bool = True
    setup_page: bool = True
    dashboard: bool = True


WORKFLOW_DEFINITIONS: tuple[WorkflowDefinition, ...] = (
    WorkflowDefinition("ilo", "iLO", "ilo", ("ilo",)),
    WorkflowDefinition("storage", "Storage / RAID", "storage", ("storage-apply", "storage-reboot")),
    WorkflowDefinition("netapp", "NetApp", "netapp", ("netapp",)),
    WorkflowDefinition("esxi", "ESXi", "esxi", ("esxi",)),
    WorkflowDefinition("vcenter", "vCenter", "vcenter", ("vcenter", "vmware"), included_aliases=("vmware",), run_center_stage=False),
    WorkflowDefinition("windows", "Windows", "windows", ("windows",)),
    WorkflowDefinition("qnap", "QNAP", "qnap", ("qnap",)),
    WorkflowDefinition("iosafe", "ioSafe", "iosafe", ("iosafe",), setup_page=False, dashboard=False),
    WorkflowDefinition("cisco_switch", "Cisco Switch", "cisco", ("cisco_switch",)),
)


_DEFINITIONS_BY_KEY = {definition.key: definition for definition in WORKFLOW_DEFINITIONS}
_ALIASES = {
    alias: definition.key
    for definition in WORKFLOW_DEFINITIONS
    for alias in definition.included_aliases
}
_ALIASES["cisco"] = "cisco_switch"


def canonical_workflow_key(key: str | None) -> str:
    value = str(key or "").strip().lower()
    return _ALIASES.get(value, value)


def workflow_definition(key: str | None) -> WorkflowDefinition | None:
    return _DEFINITIONS_BY_KEY.get(canonical_workflow_key(key))


def workflow_definitions(*, setup_only: bool = False, dashboard_only: bool = False) -> tuple[WorkflowDefinition, ...]:
    definitions = WORKFLOW_DEFINITIONS
    if setup_only:
        definitions = tuple(definition for definition in definitions if definition.setup_page)
    if dashboard_only:
        definitions = tuple(definition for definition in definitions if definition.dashboard)
    return definitions


def workflow_page_map() -> dict[str, str]:
    return {
        definition.page: definition.key
        for definition in WORKFLOW_DEFINITIONS
        if definition.setup_page and definition.page
    }


def workflow_history_scopes(key: str | None) -> tuple[str, ...]:
    definition = workflow_definition(key)
    return definition.history_scopes if definition else (str(key or ""),)


def workflow_included(cfg: dict[str, Any], key: str | None) -> bool:
    definition = workflow_definition(key)
    if definition is None:
        return False
    included = cfg.get("included", {}) or {}
    keys = (definition.key, *definition.included_aliases)
    return any(bool(included.get(item)) for item in keys)


def set_workflow_included(cfg: dict[str, Any], key: str | None, enabled: bool) -> None:
    definition = workflow_definition(key)
    if definition is None:
        return
    included = cfg.setdefault("included", {})
    included[definition.key] = bool(enabled)
    for alias in definition.included_aliases:
        included[alias] = bool(enabled)


def workflow_run_center_stage_keys() -> list[str]:
    return [definition.key for definition in WORKFLOW_DEFINITIONS if definition.run_center_stage]


def workflow_setup_order() -> list[str]:
    return [definition.key for definition in WORKFLOW_DEFINITIONS if definition.setup_page]
