from __future__ import annotations

import os
import socket
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
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


def build_esxi_runtime_status(
    cfg: dict[str, Any],
    review: dict[str, Any],
    *,
    sanitize_kit_name: Callable[[str], str],
    load_job: Callable[[str], dict[str, Any]],
    probe_tcp_port_fn: Callable[[str, int], dict[str, Any]],
    client_factory: Callable[[str, str, str], Any],
) -> dict[str, Any]:
    enabled = os.getenv("LAB_BUILDER_LIVE_RUN_CENTER_CHECKS", "1").strip().lower() not in {"0", "false", "no", "skip", "disabled"}
    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    job = load_job(kit_name)
    target_ip = str(review.get("management_ip") or "").strip()
    current_media_url = str(review.get("virtual_media_url") or "")
    last_media_url = str(job.get("esxi_iso_url") or "")
    current_host, current_port = url_host_port(current_media_url)
    last_host, last_port = url_host_port(last_media_url)
    stale_media_host = bool(last_host and current_host and (last_host, last_port) != (current_host, current_port))
    result: dict[str, Any] = {
        "enabled": enabled,
        "target_ip": target_ip,
        "management_port": 443,
        "management_reachable": False,
        "management_probe": {},
        "ilo_power_state": "",
        "ilo_post_state": "",
        "ilo_boot_progress": "",
        "virtual_media_inserted": "",
        "virtual_media_image": "",
        "last_job_status": str(job.get("status") or ""),
        "last_job_stage": str(job.get("current_stage") or ""),
        "last_management_result": dict(job.get("esxi_management_network") or {}),
        "last_media_url": last_media_url,
        "current_media_url": current_media_url,
        "stale_media_host": stale_media_host,
        "summary": "Live runtime checks are disabled.",
        "recommended_action": "Set LAB_BUILDER_LIVE_RUN_CENTER_CHECKS=1 to show live ESXi reachability in Run Center.",
    }
    if not enabled:
        return result
    probe = probe_tcp_port_fn(target_ip, 443)
    result["management_probe"] = probe
    result["management_reachable"] = bool(probe.get("reachable"))
    if probe.get("reachable"):
        result["summary"] = f"ESXi management is currently reachable at {target_ip}:443."
        result["recommended_action"] = "No action required for reachability."
        return result
    try:
        ilo_cfg = cfg.get("ilo", {}) or {}
        host = str(ilo_cfg.get("current_ip") or ilo_cfg.get("host") or "").strip()
        username = str(ilo_cfg.get("username") or "").strip()
        password = str(ilo_cfg.get("password") or "")
        if host and username and password:
            client = client_factory(host, username, password)
            system_path = client.get_system_path() if hasattr(client, "get_system_path") else "/redfish/v1/Systems/1"
            system = client.get_system(system_path) if hasattr(client, "get_system") else client._get(system_path)
            result["ilo_power_state"] = str(system.get("PowerState") or "")
            result["ilo_post_state"] = str((((system.get("Oem") or {}).get("Hpe") or {}).get("PostState")) or "")
            result["ilo_boot_progress"] = str((system.get("BootProgress") or {}).get("LastState") or "")
            media = client.get_virtual_media() if hasattr(client, "get_virtual_media") else []
            mounted = next((item for item in media if item.get("Inserted")), {})
            if mounted:
                result["virtual_media_inserted"] = "yes"
                result["virtual_media_image"] = str(mounted.get("Image") or "")
            else:
                result["virtual_media_inserted"] = "no"
    except Exception as exc:
        result["ilo_error"] = str(exc).splitlines()[0]
    if result.get("ilo_power_state", "").lower() == "off":
        result["summary"] = f"ESXi management is not reachable because the server is currently Off in iLO."
        result["recommended_action"] = "Power the server On or start an ESXi run when you are ready."
    elif result.get("ilo_power_state"):
        result["summary"] = (
            f"ESXi management is not reachable. iLO reports PowerState={result.get('ilo_power_state')} "
            f"PostState={result.get('ilo_post_state') or 'unknown'}."
        )
        result["recommended_action"] = "Check iLO console, management NIC mapping, and ESXi network settings."
    else:
        result["summary"] = f"ESXi management is not reachable at {target_ip}:443."
        result["recommended_action"] = "Check power state, cabling/VLAN, and ESXi management IP settings."
    if stale_media_host:
        result["recommended_action"] += f" Last run used media host {last_host}:{last_port or 'default'}; next run will use {current_host}:{current_port or 'default'}."
    return result


def get_esxi_effective_values(
    cfg: dict[str, Any],
    *,
    validate_esxi_hostname_fn: Callable[[str], list[str]],
    build_esxi_password_policy_check_fn: Callable[[str], dict[str, Any]],
    normalize_esxi_version_fn: Callable[[Any], str],
) -> dict[str, Any]:
    esxi_cfg = cfg.get("esxi", {}) or {}
    try:
        version = normalize_esxi_version_fn(esxi_cfg.get("version"))
    except ValueError:
        version = str(esxi_cfg.get("version") or "").strip()
    values = {
        "version": version,
        "base_iso_path": str(esxi_cfg.get("base_iso_path") or "").strip(),
        "hostname": str(esxi_cfg.get("hostname") or "").strip(),
        "management_ip": str(esxi_cfg.get("management_ip") or cfg.get("ip_plan", {}).get("esxi") or "").strip(),
        "subnet_mask": str(esxi_cfg.get("subnet_mask") or "").strip(),
        "gateway": str(esxi_cfg.get("gateway") or cfg.get("ip_plan", {}).get("gateway") or "").strip(),
        "dns_servers": [x.strip() for x in (esxi_cfg.get("dns_servers") or cfg.get("shared_network", {}).get("dns_servers") or []) if x and str(x).strip()],
        "root_password": str(esxi_cfg.get("root_password") or ""),
        "vlan_id": str(esxi_cfg.get("vlan_id") or "").strip(),
        "ntp_server": str(esxi_cfg.get("ntp_server") or "").strip(),
        "enable_ssh": bool(esxi_cfg.get("enable_ssh", True)),
        "disable_ipv6": bool(esxi_cfg.get("disable_ipv6", True)),
        "debug_no_reboot": bool(esxi_cfg.get("debug_no_reboot", False)),
    }
    missing: list[str] = []
    if not values["hostname"]:
        missing.append("hostname")
    if not values["management_ip"]:
        missing.append("management IP")
    if not values["subnet_mask"]:
        missing.append("subnet mask")
    if not values["gateway"]:
        missing.append("gateway")
    if not values["root_password"]:
        missing.append("root password")
    version_errors = []
    try:
        normalize_esxi_version_fn(values["version"])
    except ValueError as exc:
        version_errors.append(str(exc))
    hostname_errors = [] if not values["hostname"] else validate_esxi_hostname_fn(values["hostname"])
    password_check = build_esxi_password_policy_check_fn(values["root_password"]) if values["root_password"] else {
        "valid": False,
        "errors": [],
        "notes": [],
        "class_count": 0,
        "length": 0,
    }
    values["missing_fields"] = missing
    values["hostname_valid"] = not hostname_errors
    values["hostname_errors"] = hostname_errors
    values["hostname_warnings"] = (
        ["If you later join this host to Active Directory, keep the short name under 15 characters to avoid NetBIOS name changes."]
        if values["hostname"] and len(values["hostname"].split(".", 1)[0]) >= 15
        else []
    )
    values["root_password_policy_valid"] = bool(password_check.get("valid"))
    values["root_password_errors"] = list(password_check.get("errors") or [])
    values["root_password_notes"] = list(password_check.get("notes") or [])
    values["root_password_class_count"] = int(password_check.get("class_count") or 0)
    values["root_password_length"] = int(password_check.get("length") or 0)
    values["validation_errors"] = list(version_errors) + list(hostname_errors) + list(password_check.get("errors") or [])
    values["validation_notes"] = list(values["hostname_warnings"]) + list(password_check.get("notes") or [])
    return values


def build_esxi_install_review(
    cfg: dict[str, Any],
    *,
    run_stamp: str | None = None,
    include_runtime: bool = False,
    sanitize_kit_name: Callable[[str], str],
    resolve_ilo_control_host: Callable[[dict[str, Any]], str],
    get_esxi_effective_values_fn: Callable[[dict[str, Any]], dict[str, Any]],
    resolve_esxi_base_iso_path_fn: Callable[[dict[str, Any]], Path],
    validate_esxi_base_iso_fn: Callable[[Path, str], None],
    detect_public_base_url_details_fn: Callable[[str, str], dict[str, str]],
    build_esxi_iso_url_fn: Callable[[dict[str, Any], Path, str], str],
    build_esxi_install_target_review_fn: Callable[[dict[str, Any]], dict[str, Any]],
    build_esxi_runtime_status_fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    datetime_cls: Any = datetime,
    exports_dir: Path | None = None,
) -> dict[str, Any]:
    kit_name = sanitize_kit_name(cfg.get("site", {}).get("name", "Kit-01"))
    login_ip = resolve_ilo_control_host(cfg)
    stamp = (run_stamp or datetime_cls.now().strftime("%Y%m%d-%H%M%S")).strip()
    output_name = f"esxi-{stamp}"
    output_root = Path(exports_dir) if exports_dir is not None else Path("artifacts/exports")
    output_iso = output_root / "esxi-isos" / kit_name / output_name / f"{output_name}.iso"
    values = get_esxi_effective_values_fn(cfg)
    base_iso_path = resolve_esxi_base_iso_path_fn(cfg)
    validate_esxi_base_iso_fn(base_iso_path, values["version"])
    runtime_public_base_url = str((cfg.get("_runtime", {}) or {}).get("public_base_url") or "")
    try:
        public_base_url = detect_public_base_url_details_fn(login_ip, runtime_public_base_url=runtime_public_base_url)
    except TypeError as exc:
        if "runtime_public_base_url" not in str(exc):
            raise
        public_base_url = detect_public_base_url_details_fn(login_ip)
    iso_url = build_esxi_iso_url_fn(cfg, output_iso, login_ip)
    review = {
        "run_stamp": stamp,
        "source_label": "Saved kit values from the ESXi Setup page and shared defaults",
        "manual_defaults_label": "Manual test script defaults are not used by Run Center",
        "version": values["version"],
        "base_iso_path": str(base_iso_path),
        "output_iso_path": str(output_iso),
        "virtual_media_url": iso_url,
        "virtual_media_base_url": public_base_url.get("url", ""),
        "virtual_media_base_url_source": public_base_url.get("source", ""),
        "virtual_media_base_url_host": public_base_url.get("host", ""),
        "virtual_media_base_url_port": public_base_url.get("port", ""),
        "virtual_media_base_url_probe_target": public_base_url.get("probe_target", ""),
        "hostname": values["hostname"],
        "management_ip": values["management_ip"],
        "subnet_mask": values["subnet_mask"],
        "gateway": values["gateway"],
        "dns_servers": values["dns_servers"],
        "root_password_saved": bool(values["root_password"]),
        "vlan_id": values["vlan_id"],
        "ntp_server": values["ntp_server"],
        "enable_ssh": values["enable_ssh"],
        "disable_ipv6": values["disable_ipv6"],
        "debug_no_reboot": values["debug_no_reboot"],
        "install_target": build_esxi_install_target_review_fn(cfg),
        "missing_fields": list(values["missing_fields"]),
        "validation_errors": list(values["validation_errors"]),
        "validation_notes": list(values["validation_notes"]),
    }
    if include_runtime:
        review["runtime_status"] = build_esxi_runtime_status_fn(cfg, review)
    return review


def esxi_password_policy_valid(
    password: str,
    *,
    build_esxi_password_policy_check_fn: Callable[[str], dict[str, Any]],
) -> bool:
    return bool(build_esxi_password_policy_check_fn(password).get("valid"))
