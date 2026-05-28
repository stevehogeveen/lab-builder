from __future__ import annotations

import argparse
from datetime import datetime
import os
from pathlib import Path
import sys
import time
from typing import Any

import yaml

from app.core.config import sanitize_kit_name
from app.overnight_run import (
    OVERNIGHT_COMMIT_MESSAGE,
    OvernightArtifactWriter,
    create_overnight_run_dir,
    finalize_overnight_run,
)


DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent


def _read_yaml_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_yaml_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    os.replace(tmp_path, path)


def _resolve_kit_name_for_run(repo_root: Path, run_dir: Path) -> str:
    snapshot = _read_yaml_dict(run_dir / "config-snapshot.yml")
    kit_config = snapshot.get("kit_config") if isinstance(snapshot.get("kit_config"), dict) else {}
    site = kit_config.get("site") if isinstance(kit_config.get("site"), dict) else {}
    kit_name = str(site.get("name") or "").strip()
    if kit_name:
        return sanitize_kit_name(kit_name)

    current_kit_path = repo_root / "config" / "current_kit.txt"
    try:
        current_kit = current_kit_path.read_text(encoding="utf-8").strip()
    except OSError:
        current_kit = ""
    if current_kit:
        return sanitize_kit_name(current_kit)

    kits_dir = repo_root / "config" / "kits"
    try:
        first_kit = next(iter(sorted(kits_dir.glob("*.yml"))), None)
    except OSError:
        first_kit = None
    return sanitize_kit_name(first_kit.stem if first_kit else "")


def _same_path(left: str, right: Path) -> bool:
    if not left:
        return False
    try:
        return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
    except OSError:
        return str(left) == str(right)


def _job_matches_run(job: dict[str, Any], run_dir: Path) -> bool:
    run_id = str(job.get("run_id") or "").strip()
    run_bundle_dir = str(job.get("run_bundle_dir") or "").strip()
    return (run_id == run_dir.name) or _same_path(run_bundle_dir, run_dir)


def _finalized_job_status(status_label: str) -> str:
    return "Completed" if status_label == "Ready for review" else "Needs attention"


def _finalization_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    secret_findings = list(result.get("secret_findings") or [])
    return {
        "status_label": str(result.get("status_label") or ""),
        "test_result": str(result.get("test_result") or ""),
        "compile_result": str(result.get("compile_result") or ""),
        "push_result": str(result.get("push_result") or ""),
        "secret_scan_result": str(result.get("secret_scan_result") or ""),
        "secret_findings_count": len(secret_findings),
        "finalization_completed_at": str(result.get("finalization_completed_at") or ""),
        "finalization_deadline": str(result.get("finalization_deadline") or ""),
        "finalization_timing": str(result.get("finalization_timing") or ""),
        "needs_attention_reasons": [str(item) for item in list(result.get("needs_attention_reasons") or [])],
    }


def _write_job_state_snapshot(
    *,
    run_dir: Path,
    kit_name: str,
    job_path: Path,
    job: dict[str, Any],
    result: dict[str, Any],
) -> None:
    snapshot = {
        "kit_name": sanitize_kit_name(kit_name),
        "run_id": str(job.get("run_id") or run_dir.name),
        "status": str(job.get("status") or ""),
        "current_stage": str(job.get("current_stage") or ""),
        "progress_percent": int(job.get("progress_percent") or 0),
        "updated_at": str(job.get("updated_at") or ""),
        "finalization": _finalization_snapshot(result),
        "paths": {
            "job_yaml": str(job_path),
            "run_bundle": str(run_dir),
            "morning_ready": str(run_dir / "MORNING_READY.md"),
            "summary": str(run_dir / "summary.yml"),
            "trace": str(run_dir / "trace.yml"),
        },
        "logs": [str(line) for line in list(job.get("logs") or [])],
        "events": list(job.get("trace_events") or []),
    }
    _write_yaml_atomic(run_dir / "job-state.yml", snapshot)


def sync_finalized_job_state(
    *,
    repo_root: Path,
    artifacts_root: Path,
    run_dir: Path,
    result: dict[str, Any],
) -> Path | None:
    kit_name = _resolve_kit_name_for_run(repo_root, run_dir)
    job_path = Path(artifacts_root) / "jobs" / f"{sanitize_kit_name(kit_name)}_job.yml"
    job = _read_yaml_dict(job_path)
    if not job or not _job_matches_run(job, run_dir):
        return None

    status_label = str(result.get("status_label") or "").strip() or "Needs attention"
    job_status = _finalized_job_status(status_label)
    event_status = "completed" if job_status == "Completed" else "needs_attention"
    message = f"Finalization result: {status_label}."
    logs = [str(line) for line in list(job.get("logs") or [])]
    log_line = f"[OVERNIGHT] finalization: {event_status} - {message}"
    if log_line not in logs:
        logs.append(log_line)

    events = list(job.get("trace_events") or [])
    if not any(isinstance(event, dict) and event.get("stage") == "finalization" and event.get("message") == message for event in events):
        events.append(
            {
                "timestamp": datetime.now().astimezone().isoformat(),
                "stage": "finalization",
                "status": event_status,
                "progress": 100,
                "message": message,
                "source": "overnight_finalize_cli",
            }
        )

    job["status"] = job_status
    job["execution_mode"] = "overnight_hardware"
    job["execution_mode_label"] = "Overnight hardware run"
    job["scope"] = "overnight_hardware"
    job["root_scope"] = "overnight_hardware"
    job["current_stage"] = "Finalization complete"
    job["progress_percent"] = 100
    job["completed_steps"] = 100
    job["total_steps"] = 100
    job["logs"] = logs
    job["trace_events"] = events
    job["overnight_finalization"] = _finalization_snapshot(result)
    job["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_yaml_atomic(job_path, job)
    _write_job_state_snapshot(run_dir=run_dir, kit_name=kit_name, job_path=job_path, job=job, result=result)
    return job_path


def latest_overnight_run_dir(artifacts_root: Path) -> Path | None:
    root = Path(artifacts_root) / "runs" / "overnight"
    if not root.exists():
        return None
    runs = [item for item in root.iterdir() if item.is_dir()]
    if not runs:
        return None
    return max(runs, key=lambda item: item.stat().st_mtime)


def resolve_run_dir(repo_root: Path, artifacts_root: Path, requested: str | None, *, create_if_missing: bool) -> Path:
    if requested:
        path = Path(requested).expanduser()
        return path if path.is_absolute() else repo_root / path

    latest = latest_overnight_run_dir(artifacts_root)
    if latest is not None:
        return latest
    if create_if_missing:
        return create_overnight_run_dir(artifacts_root)
    raise FileNotFoundError(f"No overnight run folder exists under {artifacts_root / 'runs' / 'overnight'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Finalize the Overnight Hardware Run without starting hardware work.",
    )
    parser.add_argument("--repo-root", default=str(DEFAULT_REPO_ROOT), help="Repository root. Defaults to this checkout.")
    parser.add_argument("--artifacts-root", default="", help="Artifacts root. Defaults to <repo-root>/artifacts.")
    parser.add_argument("--run-dir", default="", help="Specific overnight run folder. Defaults to latest, or creates one if none exists.")
    parser.add_argument("--no-create-if-missing", action="store_true", help="Fail instead of creating a finalization run folder when none exists.")
    parser.add_argument("--no-git", action="store_true", help="Write MORNING_READY.md and run checks without committing or pushing.")
    parser.add_argument("--no-tests", action="store_true", help="Skip pytest and compileall. Intended only for command smoke tests.")
    parser.add_argument("--commit-message", default=OVERNIGHT_COMMIT_MESSAGE, help="Commit message for safe auto-commit.")
    parser.add_argument(
        "--commit-path",
        action="append",
        default=[],
        help="Path to include in the auto-commit. May be repeated. Defaults to the overnight scheduler path list.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve()
    artifacts_root = Path(args.artifacts_root).expanduser() if args.artifacts_root else repo_root / "artifacts"
    if not artifacts_root.is_absolute():
        artifacts_root = repo_root / artifacts_root

    run_dir = resolve_run_dir(
        repo_root,
        artifacts_root,
        args.run_dir or None,
        create_if_missing=not args.no_create_if_missing,
    )
    writer = OvernightArtifactWriter(run_dir)
    writer.initialize_placeholders()
    result = finalize_overnight_run(
        writer,
        repo_root=repo_root,
        run_tests=not args.no_tests,
        allow_git=not args.no_git,
        commit_paths=list(args.commit_path) or None,
        commit_message=str(args.commit_message or OVERNIGHT_COMMIT_MESSAGE),
    )
    sync_finalized_job_state(repo_root=repo_root, artifacts_root=artifacts_root, run_dir=run_dir, result=result)

    print(f"status: {result.get('status_label')}")
    print(f"artifact_folder: {result.get('artifact_folder')}")
    print(f"branch: {result.get('branch') or 'unknown'}")
    print(f"commit_sha: {result.get('commit_sha') or 'not created'}")
    print(f"push_result: {result.get('push_result')}")
    print(f"test_result: {result.get('test_result')}")
    print(f"compile_result: {result.get('compile_result')}")
    return 0 if result.get("status_label") == "Ready for review" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
