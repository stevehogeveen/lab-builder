from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class OperatorHomeAction:
    Label: str
    Href: str
    SupportingMessage: str


@dataclass(frozen=True)
class OperatorHomeProgress:
    Completed: int
    Total: int
    Percent: int
    Label: str


@dataclass(frozen=True)
class OperatorHomeDevice:
    Name: str
    DisplayState: str
    Summary: str
    Href: str


@dataclass(frozen=True)
class OperatorHomeDeviceSummary:
    Found: int
    Healthy: int
    NeedsAttention: int
    Summary: str
    Items: tuple[OperatorHomeDevice, ...]


@dataclass(frozen=True)
class OperatorHomeAttentionItem:
    Title: str
    Explanation: str
    Resolution: str
    Href: str


@dataclass(frozen=True)
class OperatorHomeState:
    KitName: str
    CurrentPhase: str
    DisplayState: str
    Headline: str
    SupportingMessage: str
    DeviceSummary: OperatorHomeDeviceSummary
    AttentionItems: tuple[OperatorHomeAttentionItem, ...]
    NextAction: OperatorHomeAction
    Progress: OperatorHomeProgress


_OPERATOR_TERM_REPLACEMENTS = (
    (re.compile(r"\bPROVIDER_MODE\s*=\s*[^\s,;]+", re.IGNORECASE), "the saved connection mode"),
    (re.compile(r"\bprovider\b", re.IGNORECASE), "connection method"),
    (re.compile(r"\bRedfish\b", re.IGNORECASE), "hardware interface"),
    (re.compile(r"\bAPI payload\b", re.IGNORECASE), "device response"),
    (re.compile(r"\bAPI\b", re.IGNORECASE), "device connection"),
    (re.compile(r"\bdependency(?:-node)?\b", re.IGNORECASE), "required step"),
    (re.compile(r"\bcapability key\b", re.IGNORECASE), "device feature"),
    (re.compile(r"\benvironment variable\b", re.IGNORECASE), "saved setting"),
    (re.compile(r"\braw error\b", re.IGNORECASE), "technical error"),
)


def _plain_operator_text(value: Any, fallback: str) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return fallback
    for pattern, replacement in _OPERATOR_TERM_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def _is_run_active(job: dict[str, Any]) -> bool:
    status = str(job.get("status") or "").strip().lower()
    terminal = {
        "",
        "idle",
        "complete",
        "completed",
        "preview complete",
        "failed",
        "error",
        "cancelled",
        "canceled",
        "stopped",
    }
    return status not in terminal


def build_operator_home_state(
    *,
    kit_name: str,
    setup_summary: dict[str, Any],
    recommended_next_step: dict[str, str],
    job: dict[str, Any],
) -> OperatorHomeState:
    cards = list(setup_summary.get("items") or [])
    devices: list[OperatorHomeDevice] = []
    attention_items: list[OperatorHomeAttentionItem] = []
    ready_checks = 0
    total_checks = 0
    healthy_count = 0

    for card in cards:
        name = _plain_operator_text(card.get("name"), "Setup item")
        href = str(card.get("href") or "/dashboard")
        checks_ready = int(card.get("checks_ready") or 0)
        checks_total = int(card.get("total_checks") or 0)
        issue_count = int(card.get("blockers") or 0)
        ready_checks += checks_ready
        total_checks += checks_total

        blocker = card.get("next_blocker") if isinstance(card.get("next_blocker"), dict) else {}
        explanation = _plain_operator_text(
            blocker.get("details") or blocker.get("label"),
            f"{name} needs review before the build can continue.",
        )
        resolution = _plain_operator_text(
            blocker.get("fix"),
            f"Open {name} setup and complete the missing information.",
        )

        if issue_count:
            devices.append(
                OperatorHomeDevice(
                    Name=name,
                    DisplayState="needs_attention",
                    Summary=explanation,
                    Href=href,
                )
            )
            attention_items.append(
                OperatorHomeAttentionItem(
                    Title=f"{name} needs attention",
                    Explanation=explanation,
                    Resolution=resolution,
                    Href=str(blocker.get("href") or href),
                )
            )
        else:
            healthy_count += 1
            devices.append(
                OperatorHomeDevice(
                    Name=name,
                    DisplayState="ready",
                    Summary=(
                        f"{checks_ready} of {checks_total} checks complete."
                        if checks_total
                        else "No setup checks are required."
                    ),
                    Href=href,
                )
            )

    attention_count = len(attention_items)
    if attention_count:
        device_summary_text = (
            f"{healthy_count} setup area{'s' if healthy_count != 1 else ''} ready. "
            f"{attention_count} need{'s' if attention_count == 1 else ''} attention."
        )
    elif cards:
        device_summary_text = f"All {len(cards)} setup areas are ready."
    else:
        device_summary_text = "No setup areas are included yet."

    running = _is_run_active(job)
    if running:
        current_phase = "Build in progress"
        display_state = "running"
        headline = "A lab build is in progress"
        supporting_message = _plain_operator_text(
            job.get("current_stage"),
            "The current build is still running. Open it to see the latest step.",
        )
        next_action = OperatorHomeAction(
            Label="Open current run",
            Href="/execution",
            SupportingMessage="See the current step and any action that needs you.",
        )
    elif attention_count:
        current_phase = "Prepare kit"
        display_state = "needs_attention"
        headline = (
            "One setup area needs attention"
            if attention_count == 1
            else f"{attention_count} setup areas need attention"
        )
        supporting_message = "Resolve the items below, then Lab Builder can move to the run review."
        next_action = OperatorHomeAction(
            Label=_plain_operator_text(recommended_next_step.get("title"), "Review the next setup step"),
            Href=str(recommended_next_step.get("href") or "/dashboard"),
            SupportingMessage=_plain_operator_text(
                recommended_next_step.get("summary"),
                "Complete the next safe setup step.",
            ),
        )
    elif cards:
        current_phase = "Review and run"
        display_state = "ready"
        headline = "This kit is ready for review"
        supporting_message = "Review the saved plan before starting any work on the lab."
        next_action = OperatorHomeAction(
            Label=_plain_operator_text(recommended_next_step.get("title"), "Review the run"),
            Href=str(recommended_next_step.get("href") or "/execution"),
            SupportingMessage=_plain_operator_text(
                recommended_next_step.get("summary"),
                "Review the saved plan and safety checks.",
            ),
        )
    else:
        current_phase = "Choose setup"
        display_state = "not_started"
        headline = "Choose what this kit should include"
        supporting_message = "Start with the shared kit settings and select the equipment you are preparing."
        next_action = OperatorHomeAction(
            Label="Choose kit setup",
            Href="/global-settings",
            SupportingMessage="Set the network and equipment included in this kit.",
        )

    percent = int(round((ready_checks / total_checks) * 100)) if total_checks else 0
    progress_label = (
        f"{ready_checks} of {total_checks} checks complete"
        if total_checks
        else "No checks are available yet"
    )

    return OperatorHomeState(
        KitName=kit_name or "Current kit",
        CurrentPhase=current_phase,
        DisplayState=display_state,
        Headline=headline,
        SupportingMessage=supporting_message,
        DeviceSummary=OperatorHomeDeviceSummary(
            Found=len(cards),
            Healthy=healthy_count,
            NeedsAttention=attention_count,
            Summary=device_summary_text,
            Items=tuple(devices),
        ),
        AttentionItems=tuple(attention_items),
        NextAction=next_action,
        Progress=OperatorHomeProgress(
            Completed=ready_checks,
            Total=total_checks,
            Percent=percent,
            Label=progress_label,
        ),
    )
