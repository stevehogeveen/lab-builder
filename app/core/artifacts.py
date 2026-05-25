from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class ArtifactRoot:
    kind: str
    root: Path


def collect_artifact_entries(
    roots: list[ArtifactRoot],
    *,
    kit_name: str,
    query: str = "",
    report_type: str = "all",
    limit: int = 120,
) -> list[dict[str, str]]:
    needle = query.strip().lower()
    entries: list[dict[str, str]] = []
    for artifact_root in roots:
        kind = artifact_root.kind
        root = artifact_root.root
        if report_type != "all" and report_type != kind:
            continue
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            parent_parts = " ".join(path.parent.parts[-3:])
            text = f"{kind} {path.name} {parent_parts}".lower()
            if needle and needle not in text:
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            entries.append(
                {
                    "kind": kind,
                    "label": path.name,
                    "path": str(path),
                    "parent": str(path.parent),
                    "server": path.parent.parent.name if path.parent.parent != path.parent else "",
                    "mtime": f"{modified.year:04d}-{modified.month:02d}-{modified.day:02d} {modified.hour:02d}:{modified.minute:02d}:{modified.second:02d}",
                    "kit_match": "Yes" if kit_name in str(path) else "",
                }
            )
    entries.sort(key=lambda item: item["mtime"], reverse=True)
    return entries[:limit]
