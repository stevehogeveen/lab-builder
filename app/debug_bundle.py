from __future__ import annotations

import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

REDACT_RE = re.compile(r"(password|passwd|secret|token|key|authorization|x-auth-token|session|cookie)", re.IGNORECASE)


def _run_text(cmd: list[str], cwd: Path) -> str:
    try:
        proc = subprocess.run(cmd, cwd=str(cwd), check=False, capture_output=True, text=True)
        text = (proc.stdout or proc.stderr or "").strip()
        return text
    except Exception as exc:  # pragma: no cover - defensive
        return f"(unavailable: {exc})"


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if REDACT_RE.search(key_text):
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = redact_value(item)
        return redacted
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        line = value
        if "://" in line and ("@" in line):
            line = re.sub(r"//[^:@/\s]+:[^@/\s]+@", "//[REDACTED]:[REDACTED]@", line)
        line = re.sub(r"(?i)(authorization|x-auth-token|cookie)\s*[:=].*$", r"\1=[REDACTED]", line)
        return line
    return value


def redact_job_logs(logs: list[Any], max_lines: int = 100) -> list[str]:
    tail = [str(line) for line in list(logs or [])[-max_lines:]]
    return [str(redact_value(line)) for line in tail]


def create_debug_bundle(
    *,
    base_dir: Path,
    artifacts_dir: Path,
    config_dir: Path,
    jobs_dir: Path,
    runs_dir: Path,
    generated_dir: Path,
    exports_dir: Path,
    kit_name: str,
    failure_context: dict[str, Any],
) -> Path:
    bundles_dir = artifacts_dir / "debug-bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    latest_path = bundles_dir / "latest-failure.txt"
    stamped_path = bundles_dir / f"debug-{stamp}.txt"

    cwd = base_dir
    git_branch = _run_text(["git", "branch", "--show-current"], cwd)
    git_commit = _run_text(["git", "rev-parse", "HEAD"], cwd)
    git_status = _run_text(["git", "status", "--short"], cwd)

    safe_context = redact_value(dict(failure_context or {}))
    safe_kit = redact_value(safe_context.get("kit_config") or {})
    safe_logs = redact_job_logs(safe_context.get("job_logs") or [])
    error_message = str(safe_context.get("error_message") or "").strip()
    diagnosis = redact_value(safe_context.get("diagnosis") or {})
    if not isinstance(diagnosis, dict):
        diagnosis = {"status": str(diagnosis)}

    payload = {
        "debug_bundle_version": 1,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git": {
            "branch": git_branch,
            "latest_commit": git_commit,
            "status_short": git_status,
        },
        "job": {
            "kit_name": str(kit_name or ""),
            "status": str(safe_context.get("job_status") or ""),
            "scope": str(safe_context.get("job_scope") or ""),
            "current_stage": str(safe_context.get("current_stage") or ""),
            "error_message": str(redact_value(error_message)),
        },
        "diagnosis": {
            "summary": str(diagnosis.get("selected_action") or diagnosis.get("status") or ""),
            "failed_stage": str(safe_context.get("current_stage") or ""),
            "desired_intent": diagnosis.get("desired_state") or {},
            "discovered_state": diagnosis.get("discovered_state") or {},
            "available_actions_options": diagnosis.get("options_discovered") or {},
            "attempted_corrections": diagnosis.get("safe_corrections_attempted") or [],
            "rejection_reasons": diagnosis.get("rejection_reasons") or [],
            "recommended_next_steps": str(diagnosis.get("recommended_fix") or ""),
            "user_action_required": bool(diagnosis.get("user_action_required")),
        },
        "environment": {
            "python_version": sys.version.replace("\n", " "),
            "platform": platform.platform(),
        },
        "paths": {
            "base_dir": str(base_dir),
            "config_dir": str(config_dir),
            "jobs_dir": str(jobs_dir),
            "runs_dir": str(runs_dir),
            "generated_dir": str(generated_dir),
            "exports_dir": str(exports_dir),
            "artifacts_dir": str(artifacts_dir),
        },
        "last_100_job_log_lines": safe_logs,
        "redacted_kit_config": safe_kit,
    }

    text = yaml.safe_dump(payload, sort_keys=False)
    latest_path.write_text(text, encoding="utf-8")
    stamped_path.write_text(text, encoding="utf-8")
    return stamped_path
