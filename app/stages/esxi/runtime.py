from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests


def normalize_esxi_version(value: Any) -> str:
    text = str(value or "7").strip()
    if text in {"7", "8"}:
        return text
    raise ValueError(f"Unsupported ESXi version: {text or '(empty)'}")


def infer_esxi_version_from_iso_path(path: Path) -> str:
    text = str(path).lower()
    if "esxi8" in text or "esxi-8" in text or "esxi_8" in text or "vmware-vmvisor-installer-8" in text:
        return "8"
    return "7"


def discover_esxi_base_isos(base_dir: Path, version: str | None = None) -> list[dict[str, Any]]:
    requested = normalize_esxi_version(version) if version else ""
    search_dirs: list[tuple[str, Path]] = [
        ("", base_dir),
        ("7", base_dir / "esxi7"),
        ("8", base_dir / "esxi8"),
    ]
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for discovered_version, directory in search_dirs:
        if requested and discovered_version and discovered_version != requested:
            continue
        for path in sorted(list(directory.glob("*.iso")) + list(directory.glob("*.ISO"))):
            resolved = str(path)
            if resolved in seen:
                continue
            seen.add(resolved)
            inferred_version = discovered_version or infer_esxi_version_from_iso_path(path)
            if requested and inferred_version != requested:
                continue
            results.append(
                {
                    "path": str(path),
                    "name": path.name,
                    "version": inferred_version,
                    "exists": path.exists(),
                    "readable": os.access(path, os.R_OK),
                }
            )
    return results


def resolve_esxi_base_iso_path(cfg: dict[str, Any], *, media_base_dir: Path) -> Path:
    version = normalize_esxi_version((cfg.get("esxi", {}) or {}).get("version"))
    configured = str((cfg.get("esxi", {}) or {}).get("base_iso_path") or "").strip()
    if configured:
        path = Path(configured)
        if path.exists() and path.suffix.lower() == ".iso":
            return path
        if path.exists():
            raise ValueError(f"Configured ESXi base ISO is not an .iso file: {path}")
        raise FileNotFoundError(f"Configured ESXi base ISO was not found: {path}")
    candidates = [Path(item["path"]) for item in discover_esxi_base_isos(media_base_dir, version=version)]
    if not candidates:
        raise FileNotFoundError(f"No ESXi {version} base ISO was found under {media_base_dir}")
    return candidates[0]


def validate_esxi_base_iso(path: Path, version: str) -> None:
    normalize_esxi_version(version)
    if not path.exists():
        raise FileNotFoundError(f"Selected ESXi {version} base ISO was not found: {path}")
    if path.suffix.lower() != ".iso":
        raise ValueError(f"Selected ESXi {version} base ISO must be an .iso file: {path}")
    if not path.is_file():
        raise ValueError(f"Selected ESXi {version} base ISO is not a file: {path}")
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except Exception as exc:
        raise OSError(f"Selected ESXi {version} base ISO could not be read: {path}") from exc


def detect_public_base_url_details(
    target_host: str = "",
    runtime_public_base_url: str = "",
    *,
    env_public_base_url: str | None = None,
    env_lab_builder_port: str | None = None,
    env_port: str | None = None,
) -> dict[str, str]:
    configured = str(env_public_base_url if env_public_base_url is not None else os.getenv("LAB_BUILDER_PUBLIC_BASE_URL", "")).strip().rstrip("/")
    if configured:
        return {
            "url": configured,
            "source": "LAB_BUILDER_PUBLIC_BASE_URL",
            "host": configured.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0],
            "port": configured.rsplit(":", 1)[-1] if ":" in configured.split("://", 1)[-1].split("/", 1)[0] else "",
            "probe_target": str(target_host or ""),
        }
    runtime_configured = str(runtime_public_base_url or "").strip().rstrip("/")
    if runtime_configured:
        parsed_runtime = urlparse(runtime_configured)
        return {
            "url": runtime_configured,
            "source": "current Run Center request URL",
            "host": parsed_runtime.hostname or "",
            "port": str(parsed_runtime.port or ""),
            "probe_target": str(target_host or ""),
        }
    host = "127.0.0.1"
    probe_target = (target_host or "8.8.8.8").strip()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((probe_target, 443))
            host = sock.getsockname()[0] or host
    except Exception:
        pass
    port = str(env_lab_builder_port if env_lab_builder_port is not None else os.getenv("LAB_BUILDER_PORT", "")).strip() or str(env_port if env_port is not None else os.getenv("PORT", "")).strip() or "8000"
    source = "auto-detected route to iLO"
    if str(env_lab_builder_port if env_lab_builder_port is not None else os.getenv("LAB_BUILDER_PORT", "")).strip():
        source += " + LAB_BUILDER_PORT"
    elif str(env_port if env_port is not None else os.getenv("PORT", "")).strip():
        source += " + PORT"
    else:
        source += " + default port 8000"
    return {
        "url": f"http://{host}:{port}",
        "source": source,
        "host": host,
        "port": port,
        "probe_target": probe_target,
    }


def detect_public_base_url(target_host: str = "", runtime_public_base_url: str = "") -> str:
    return detect_public_base_url_details(target_host, runtime_public_base_url=runtime_public_base_url).get("url", "")


def build_esxi_iso_url(cfg: dict[str, Any], output_iso: Path, target_host: str = "", *, sanitize_kit_name: Any) -> str:
    runtime_public_base_url = str((cfg.get("_runtime", {}) or {}).get("public_base_url") or "")
    public_base_url = detect_public_base_url(target_host, runtime_public_base_url=runtime_public_base_url)
    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    output_name = sanitize_kit_name(output_iso.stem)
    return f"{public_base_url}/esxi-built-iso/{quote(kit_name)}/{quote(output_name)}.iso"


def verify_esxi_virtual_media_url(iso_url: str, output_iso: Path, *, timeout_seconds: int = 10) -> dict[str, Any]:
    expected_size = output_iso.stat().st_size if output_iso.exists() else 0
    result: dict[str, Any] = {
        "url": str(iso_url or ""),
        "output_iso_path": str(output_iso),
        "expected_size_bytes": expected_size,
        "status": "unknown",
        "http_status": "",
        "content_length": "",
        "bytes_read": 0,
        "recommended_fix": "",
    }
    skip_value = os.getenv("LAB_BUILDER_VALIDATE_ESXI_MEDIA_URL", "1").strip().lower()
    if skip_value in {"0", "false", "no", "skip", "disabled"}:
        result["status"] = "skipped"
        result["reason"] = "disabled_by_env"
        result["recommended_fix"] = "Unset LAB_BUILDER_VALIDATE_ESXI_MEDIA_URL or set it to 1 to enable this preflight."
        return result
    if not iso_url:
        result["status"] = "failed"
        result["error"] = "Virtual media URL is empty."
        result["recommended_fix"] = "Configure a reachable Lab Builder public base URL before running ESXi."
        return result
    if not output_iso.exists():
        result["status"] = "failed"
        result["error"] = f"Generated ESXi ISO does not exist: {output_iso}"
        result["recommended_fix"] = "Rebuild the ESXi ISO and verify the export path is writable."
        return result
    response = None
    try:
        response = requests.get(iso_url, stream=True, timeout=(3, max(timeout_seconds, 1)))
        result["http_status"] = response.status_code
        result["content_length"] = response.headers.get("content-length", "")
        if response.status_code >= 400:
            result["status"] = "failed"
            result["error"] = f"GET {iso_url} returned HTTP {response.status_code}."
            result["recommended_fix"] = (
                "Start Lab Builder on the configured public URL or set LAB_BUILDER_PUBLIC_BASE_URL "
                "to the address and port reachable by iLO."
            )
            return result
        first_chunk = b""
        for chunk in response.iter_content(chunk_size=4096):
            if chunk:
                first_chunk = chunk
                break
        result["bytes_read"] = len(first_chunk)
        if not first_chunk:
            result["status"] = "failed"
            result["error"] = f"GET {iso_url} succeeded but returned no ISO bytes."
            result["recommended_fix"] = "Verify the generated ISO route and output file before mounting virtual media."
            return result
        result["status"] = "ok"
        if result["content_length"]:
            try:
                result["content_length_matches_expected"] = int(result["content_length"]) == expected_size
            except ValueError:
                result["content_length_matches_expected"] = False
        return result
    except requests.RequestException as exc:
        result["status"] = "failed"
        result["error"] = str(exc).splitlines()[0]
        result["recommended_fix"] = (
            "Start Lab Builder on the configured public URL, or set LAB_BUILDER_PUBLIC_BASE_URL "
            "to a URL reachable from this host and iLO. The ESXi installer cannot boot from a URL "
            "that is not being served."
        )
        return result
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def esxi_virtual_media_url_check_summary(check: dict[str, Any]) -> str:
    status = str(check.get("status") or "unknown")
    if status == "ok":
        return (
            "Virtual media URL reachable from Lab Builder: "
            f"http_status={check.get('http_status') or '(unknown)'} "
            f"bytes_read={check.get('bytes_read') or 0} "
            f"content_length={check.get('content_length') or '(unknown)'} "
            f"expected_size={check.get('expected_size_bytes') or 0}"
        )
    if status == "skipped":
        return f"Virtual media URL preflight skipped: {check.get('reason') or 'not specified'}"
    return (
        "Virtual media URL check failed: "
        f"{check.get('error') or 'unknown error'} | "
        f"recommended_fix={check.get('recommended_fix') or 'Check Lab Builder public base URL.'}"
    )


def probe_tcp_port(host: str, port: int, *, timeout_seconds: float = 0.75) -> dict[str, Any]:
    start = __import__("time").monotonic()
    result: dict[str, Any] = {
        "host": str(host or ""),
        "port": int(port),
        "reachable": False,
        "latency_ms": "",
        "error": "",
    }
    if not host:
        result["error"] = "host is empty"
        return result
    try:
        with socket.create_connection((host, int(port)), timeout=max(timeout_seconds, 0.1)):
            result["reachable"] = True
            result["latency_ms"] = int((__import__("time").monotonic() - start) * 1000)
            return result
    except Exception as exc:
        result["error"] = str(exc).splitlines()[0]
        return result


def url_host_port(url: str) -> tuple[str, str]:
    parsed = urlparse(str(url or ""))
    return parsed.hostname or "", str(parsed.port or "")
