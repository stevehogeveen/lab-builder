from __future__ import annotations

from typing import Any


DIAGNOSTIC_FIELDS = (
    "status",
    "desired_state",
    "discovered_state",
    "differences",
    "safe_corrections_attempted",
    "options_discovered",
    "selected_action",
    "rejection_reasons",
    "recommended_fix",
    "user_action_required",
)


def diagnostic_result(
    *,
    status: str,
    desired_state: Any | None = None,
    discovered_state: Any | None = None,
    differences: list[str] | None = None,
    safe_corrections_attempted: list[str] | None = None,
    options_discovered: Any | None = None,
    selected_action: str = "",
    rejection_reasons: list[str] | None = None,
    recommended_fix: str = "",
    user_action_required: bool = False,
) -> dict[str, Any]:
    return {
        "status": str(status or "").strip() or "failed",
        "desired_state": desired_state or {},
        "discovered_state": discovered_state or {},
        "differences": list(differences or []),
        "safe_corrections_attempted": list(safe_corrections_attempted or []),
        "options_discovered": options_discovered or {},
        "selected_action": str(selected_action or ""),
        "rejection_reasons": list(rejection_reasons or []),
        "recommended_fix": str(recommended_fix or ""),
        "user_action_required": bool(user_action_required),
    }


def diagnostic_summary(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "unknown")
    selected = str(result.get("selected_action") or "").strip()
    rejected = "; ".join(str(item) for item in result.get("rejection_reasons") or [] if str(item).strip())
    recommended = str(result.get("recommended_fix") or "").strip()
    parts = [f"status={status}"]
    if selected:
        parts.append(f"selected_action={selected}")
    if rejected:
        parts.append(f"rejection={rejected}")
    if recommended:
        parts.append(f"recommended_fix={recommended}")
    return " | ".join(parts)


def _format_list(items: Any, *, empty: str = "none") -> str:
    if not items:
        return empty
    if isinstance(items, (list, tuple, set)):
        return "; ".join(str(item) for item in items if str(item).strip()) or empty
    return str(items)


def diagnostic_log_lines(area: str, result: dict[str, Any]) -> list[str]:
    label = str(area or "Preflight").strip()
    lines = []
    options = result.get("options_discovered") or {}
    if options:
        lines.append(f"[DISCOVER] {label} options discovered: {options}")
    desired = result.get("desired_state") or {}
    discovered = result.get("discovered_state") or {}
    if desired or discovered:
        lines.append(f"[COMPARE] {label} desired={desired} discovered={discovered}")
    differences = _format_list(result.get("differences") or [])
    if differences != "none":
        lines.append(f"[COMPARE] {label} differences: {differences}")
    corrections = _format_list(result.get("safe_corrections_attempted") or [])
    if corrections != "none":
        lines.append(f"[REMAP] {label} safe corrections attempted: {corrections}")
    rejections = _format_list(result.get("rejection_reasons") or [])
    status = str(result.get("status") or "").lower()
    if status in {"blocked", "failed"} or rejections != "none":
        lines.append(f"[BLOCKED] {label} rejected: {rejections}")
        recommended = str(result.get("recommended_fix") or "").strip()
        if recommended:
            lines.append(f"[BLOCKED] {label} recommended fix: {recommended}")
    else:
        selected = str(result.get("selected_action") or "").strip()
        if selected:
            lines.append(f"[DECISION] {label} selected: {selected}")
    return lines
