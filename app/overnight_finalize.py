from __future__ import annotations

import argparse
from pathlib import Path
import sys

from app.overnight_run import (
    OVERNIGHT_COMMIT_MESSAGE,
    OvernightArtifactWriter,
    create_overnight_run_dir,
    finalize_overnight_run,
)


DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent


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
