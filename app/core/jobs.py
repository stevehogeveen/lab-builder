from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class JobStepRunner:
    kit_name: str
    job: dict[str, Any]
    save_job: Callable[[str, dict[str, Any]], None]
    ensure_run_bundle: Callable[[str, dict[str, Any]], None] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def step(self, *, status: str, stage: str, completed: int, total: int, log: str, progress_percent: int | None = None) -> None:
        if self.ensure_run_bundle is not None:
            self.ensure_run_bundle(self.kit_name, self.job)
        self.job["status"] = status
        self.job["current_stage"] = stage
        self.job["completed_steps"] = completed
        self.job["total_steps"] = total
        self.job["progress_percent"] = progress_percent if progress_percent is not None else (int((completed / total) * 100) if total else 0)
        self.job.setdefault("logs", []).append(log)
        event = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "stage": stage,
            "completed_steps": completed,
            "total_steps": total,
            "progress_percent": self.job["progress_percent"],
            "log": log,
        }
        self.job.setdefault("trace_events", []).append(event)
        self.events.append(event)
        self.save_job(self.kit_name, self.job)
