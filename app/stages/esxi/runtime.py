from __future__ import annotations

import os
import socket
import ipaddress
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlparse

import requests

from app.vmware import build_vmware_plan


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


def _default_esxi_post_config_policy() -> dict[str, Any]:
    return {
        "enabled": True,
        "discovery_start_octet": 31,
        "discovery_end_octet": 33,
        "allow_discovery_range": True,
        "allow_single_mgmt_uplink_override": False,
        "allow_vm_network_recreate": False,
        "allow_role_privilege_prune": False,
        "allow_datastore_create": False,
        "configure_only_no_reboot": True,
        "reboot_confirmed": False,
        "wug_snmp_target": "10.10.10.10@162/wutvpmonitor/priv/trap",
        "wug_notraps": "tcp,udp,vmkernel,hostd,vpxa",
        "wug_account_username": "WUGMon",
        "virtual_managers_role_name": "VirtualManagers",
    }


def ensure_esxi_post_config_policy(cfg: dict[str, Any]) -> dict[str, Any]:
    esxi_cfg = cfg.setdefault("esxi", {})
    policy = esxi_cfg.setdefault("post_config_policy", {})
    defaults = _default_esxi_post_config_policy()
    for key, value in defaults.items():
        policy.setdefault(key, value)
    return policy


def ensure_esxi_post_config_secrets(cfg: dict[str, Any]) -> dict[str, Any]:
    esxi_cfg = cfg.setdefault("esxi", {})
    secrets = esxi_cfg.setdefault("post_config_secrets", {})
    secrets.setdefault("wug_password", "")
    secrets.setdefault("snmpv3_auth_password", "")
    secrets.setdefault("snmpv3_priv_password", "")
    secrets.setdefault("kit_root_password", "")
    secrets.setdefault("svmservice_password", "")
    secrets.setdefault("localtech_password", "")
    return secrets


def build_netapp_nfs_post_config_plan(cfg: dict[str, Any]) -> dict[str, Any]:
    netapp_cfg = cfg.get("netapp", {}) or {}
    protocol = str(netapp_cfg.get("storage_protocol") or "").strip().lower()
    required = bool((cfg.get("included", {}) or {}).get("netapp")) and protocol == "nfs"
    result: dict[str, Any] = {
        "required": required,
        "ready": True,
        "status_label": "Not required",
        "status_tone": "progress",
        "blocking_reason": "",
        "datastore_name": "",
        "export_path": "",
        "svm_name": "",
        "server_ips": [],
        "esxi_hosts": [],
        "nfs_version": "4.1",
        "mount_plan": [],
        "nfs_probe_reachable": False,
        "probe_ready": False,
    }
    if not required:
        return result

    vmware_plan = build_vmware_plan(cfg, storage_protocol="nfs")
    nfs_context = dict(vmware_plan.get("nfs_context") or {})
    mount_step = next(
        (step for step in list(vmware_plan.get("steps") or []) if str(step.get("name") or "") == "plan_nfs_datastore_mounts"),
        {},
    )
    mount_details = dict(mount_step.get("details") or {})
    mount_plan = [dict(item or {}) for item in list(mount_details.get("mount_plan") or [])]
    server_ips = [str(item).strip() for item in list(nfs_context.get("lif_ips") or []) if str(item).strip()]
    esxi_hosts = [str(item).strip() for item in list(vmware_plan.get("esxi_hosts") or []) if str(item).strip()]
    datastore_name = str(nfs_context.get("datastore_name") or "").strip()
    export_path = str(nfs_context.get("export_path") or "").strip()
    svm_name = str(nfs_context.get("svm_name") or "").strip()
    nfs_version = str(mount_details.get("nfs_version") or "4.1").strip() or "4.1"

    probe = dict(((netapp_cfg.get("vmware_checks") or {}).get("nfs_mount")) or {})
    nfs_probe_checks = [dict(item or {}) for item in list(probe.get("checks") or []) if str((item or {}).get("kind") or "") == "nfs_server"]
    nfs_probe_reachable = bool(nfs_probe_checks) and all(bool(item.get("reachable")) for item in nfs_probe_checks)

    missing: list[str] = []
    if not datastore_name:
        missing.append("datastore name")
    if not export_path:
        missing.append("NFS export path")
    if not server_ips:
        missing.append("NFS LIF/server IPs")
    if not esxi_hosts:
        missing.append("ESXi target host")
    if not mount_plan or not str((mount_plan[0] or {}).get("esxcli_command") or "").strip():
        missing.append("ESXi NFS mount command")

    ready = not missing
    result.update(
        {
            "ready": ready,
            "status_label": "Ready" if ready else "Blocked",
            "status_tone": "ready" if ready else "pending",
            "blocking_reason": "" if ready else f"Missing {', '.join(missing)}.",
            "datastore_name": datastore_name,
            "export_path": export_path,
            "svm_name": svm_name,
            "server_ips": server_ips,
            "esxi_hosts": esxi_hosts,
            "nfs_version": nfs_version,
            "mount_plan": mount_plan,
            "nfs_probe_reachable": nfs_probe_reachable,
            "probe_ready": bool(probe.get("ready")),
        }
    )
    return result


def _subnet_host_from_offset(subnet_cidr: str, offset: int) -> str:
    network = ipaddress.ip_network(str(subnet_cidr), strict=False)
    host_int = int(network.network_address) + int(offset)
    return str(ipaddress.ip_address(host_int))


def build_esxi_post_config_preview(cfg: dict[str, Any]) -> dict[str, Any]:
    policy = ensure_esxi_post_config_policy(cfg)
    secrets = ensure_esxi_post_config_secrets(cfg)
    esxi_cfg = cfg.get("esxi", {}) or {}
    inv = dict(esxi_cfg.get("post_config_inventory") or {})
    kit_id = str((cfg.get("site") or {}).get("name") or "KIT").strip().replace(" ", "-")
    support_unit = str((cfg.get("site") or {}).get("support_unit") or "SUPPORT").strip().replace(" ", "-")
    host_bay = str((cfg.get("site") or {}).get("host_bay") or "1").strip()
    mgmt_ip = str(esxi_cfg.get("management_ip") or cfg.get("ip_plan", {}).get("esxi") or "").strip()
    subnet = str((cfg.get("shared_network") or {}).get("subnet") or "").strip()
    target_hosts: list[str] = []
    if mgmt_ip:
        target_hosts.append(mgmt_ip)
    if policy.get("allow_discovery_range") and subnet:
        start = int(policy.get("discovery_start_octet") or 31)
        end = int(policy.get("discovery_end_octet") or 33)
        lo, hi = (start, end) if start <= end else (end, start)
        for octet in range(lo, hi + 1):
            try:
                host = _subnet_host_from_offset(subnet, octet)
                if host not in target_hosts:
                    target_hosts.append(host)
            except Exception:
                continue

    hostname = f"{support_unit}-{kit_id}-VP0000{host_bay}"
    domain = f"{kit_id}.forces.mil.ca"
    dns1 = _subnet_host_from_offset(subnet, 61) if subnet else ""
    dns2 = str((cfg.get("ip_plan") or {}).get("domestic_dc_ip") or "")

    datastores = list(inv.get("datastores") or [])
    disks = list(inv.get("scsi_disks") or [])
    nics = list(inv.get("physical_nics") or [])
    one_gig_nics = [item for item in nics if str(item.get("speed_mbps") or "") in {"1000", "1000.0"}]
    nic_uplinks = [str(item.get("name") or "") for item in one_gig_nics if str(item.get("name") or "").strip()]
    if len(nic_uplinks) < 2:
        nic_uplinks = [str(item.get("name") or "") for item in nics if str(item.get("name") or "").strip()][:2]

    large_unused_disks = [
        item
        for item in disks
        if float(item.get("size_gb") or 0) > 1500 and not bool(item.get("in_use"))
    ]
    local_small_ds = next(
        (
            item
            for item in datastores
            if float(item.get("capacity_gb") or 0) < 500 and str(item.get("name") or "").strip()
        ),
        {},
    )
    rename_target = f"LOCAL-S{host_bay}"
    create_local_s2_allowed = bool(policy.get("allow_datastore_create")) and len(datastores) <= 1 and bool(large_unused_disks)
    netapp_nfs = build_netapp_nfs_post_config_plan(cfg)

    plan = {
        "connection_targets": target_hosts,
        "ceip_opt_in_value": 2,
        "datastore_plan": {
            "existing_datastores": datastores,
            "scsi_disks": disks,
            "rename_local_datastore_from": str(local_small_ds.get("name") or ""),
            "rename_local_datastore_to": rename_target,
            "create_local_s2_allowed": create_local_s2_allowed,
            "create_local_s2_reason": (
                "Enabled and one eligible >1500GB unused disk found."
                if create_local_s2_allowed
                else "Requires explicit allow_datastore_create with a single-datastore baseline and eligible disk."
            ),
            "create_local_s2_disk": (large_unused_disks[0] if large_unused_disks else {}),
        },
        "netapp_nfs": netapp_nfs,
        "network_plan": {
            "detected_nics": nics,
            "preferred_mgmt_uplinks": nic_uplinks,
            "min_mgmt_uplinks_required": 2,
            "single_uplink_override_enabled": bool(policy.get("allow_single_mgmt_uplink_override")),
            "vm_network_recreate_enabled": bool(policy.get("allow_vm_network_recreate")),
        },
        "advanced_settings": {
            "UserVars.HostClientCEIPOptIn": 2,
            "Config.HostAgent.plugins.hostsvc.esxAdminsGroup": f"{kit_id}VirtualAdmins",
            "Syslog.global.logDir": f"[{rename_target}] /SystemLog",
            "UserVars.HostClientWelcomeMessage": "Access to this computer system is restricted to AUTHORIZED PERSONNEL. Acces a ce systeme d'ordinateurs est limite au PERSONNEL AUTORISE.",
        },
        "ntp": {
            "server": str(esxi_cfg.get("ntp_server") or cfg.get("ip_plan", {}).get("gateway") or ""),
            "service": "ntpd",
            "enable": True,
            "start": True,
            "current": dict(inv.get("ntp") or {}),
        },
        "identity": {
            "hostname": str(esxi_cfg.get("post_config_hostname_override") or hostname),
            "domain": str(esxi_cfg.get("post_config_domain_override") or domain),
            "dns_servers": [
                str(esxi_cfg.get("post_config_dns1_override") or dns1),
                str(esxi_cfg.get("post_config_dns2_override") or dns2),
            ],
        },
        "snmp_wug": {
            "account_username": str(esxi_cfg.get("wug_account_username") or policy.get("wug_account_username") or "WUGMon"),
            "account_password_present": bool(secrets.get("wug_password")),
            "snmpv3": {
                "loglevel": "warning",
                "auth_protocol": "SHA1",
                "priv_protocol": "AES128",
                "target": str(esxi_cfg.get("wug_snmp_target") or policy.get("wug_snmp_target") or ""),
                "syslocation": kit_id,
                "notraps": str(esxi_cfg.get("wug_notraps") or policy.get("wug_notraps") or ""),
                "enable": True,
                "auth_password_present": bool(secrets.get("snmpv3_auth_password")),
                "priv_password_present": bool(secrets.get("snmpv3_priv_password")),
            },
        },
        "roles_accounts": {
            "role_name": str(policy.get("virtual_managers_role_name") or "VirtualManagers"),
            "accounts": [
                {"username": f"{kit_id}root", "role": "Admin", "password_present": bool(secrets.get("kit_root_password"))},
                {"username": "S-VMSERVICE", "role": "Admin", "password_present": bool(secrets.get("svmservice_password"))},
                {"username": "LocalTech", "role": str(policy.get("virtual_managers_role_name") or "VirtualManagers"), "password_present": bool(secrets.get("localtech_password"))},
            ],
            "safe_privilege_prune_enabled": bool(policy.get("allow_role_privilege_prune")),
        },
        "reboot": {
            "required_after_apply": True,
            "configure_only_no_reboot": bool(policy.get("configure_only_no_reboot")),
            "reboot_confirmed": bool(policy.get("reboot_confirmed")),
        },
    }
    warnings: list[str] = []
    if len(nic_uplinks) < 2 and not bool(policy.get("allow_single_mgmt_uplink_override")):
        warnings.append("Less than two management uplinks were selected; set override only when hardware constraints require it.")
    if len(datastores) > 1:
        warnings.append("Multiple datastores already exist; automatic LOCAL-S2 creation remains blocked.")
    if not mgmt_ip:
        warnings.append("Management IP is not saved yet; discovery range fallback will be used.")
    if netapp_nfs.get("required") and netapp_nfs.get("ready") and not netapp_nfs.get("nfs_probe_reachable"):
        warnings.append("NetApp NFS datastore plan is saved, but the latest NFS reachability probe has not confirmed all NFS server IPs.")
    return {
        "enabled": bool(policy.get("enabled")),
        "policy": dict(policy),
        "inventory": inv,
        "plan": plan,
        "warnings": warnings,
    }


def validate_esxi_post_config_preview(preview: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = list(preview.get("warnings") or [])
    plan = dict(preview.get("plan") or {})
    targets = list(plan.get("connection_targets") or [])
    if not targets:
        errors.append("No ESXi connection target could be resolved from management IP or discovery range.")
    ntp_server = str(((plan.get("ntp") or {}).get("server") or "")).strip()
    if not ntp_server:
        errors.append("NTP server is empty. Set ESXi NTP or kit gateway.")
    dns_servers = [str(item or "").strip() for item in list((plan.get("identity") or {}).get("dns_servers") or [])]
    if not dns_servers or not dns_servers[0]:
        errors.append("Primary DNS server is empty.")
    network_plan = dict(plan.get("network_plan") or {})
    uplinks = [item for item in list(network_plan.get("preferred_mgmt_uplinks") or []) if str(item).strip()]
    if len(uplinks) < 2 and not bool(network_plan.get("single_uplink_override_enabled")):
        errors.append("At least two management uplinks are required unless the single-uplink override is enabled.")
    ds_plan = dict(plan.get("datastore_plan") or {})
    netapp_nfs = dict(plan.get("netapp_nfs") or {})
    if bool(ds_plan.get("create_local_s2_allowed")) and not ds_plan.get("create_local_s2_disk"):
        errors.append("LOCAL-S2 creation is enabled but no eligible >1500GB unused disk was found.")
    if bool(netapp_nfs.get("required")) and not bool(netapp_nfs.get("ready")):
        errors.append(f"NetApp NFS datastore setup is not ready for ESXi post-config: {netapp_nfs.get('blocking_reason') or 'missing mount inputs'}")
    snmp_wug = dict(plan.get("snmp_wug") or {})
    snmpv3 = dict(snmp_wug.get("snmpv3") or {})
    if not bool(snmp_wug.get("account_password_present")):
        warnings.append("WUGMon password is not set in ESXi post-config secrets.")
    if not bool(snmpv3.get("auth_password_present")):
        warnings.append("SNMPv3 auth password is not set in ESXi post-config secrets.")
    if not bool(snmpv3.get("priv_password_present")):
        warnings.append("SNMPv3 privacy password is not set in ESXi post-config secrets.")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def build_esxi_post_config_actions(preview: dict[str, Any]) -> list[dict[str, Any]]:
    plan = dict(preview.get("plan") or {})
    ds_plan = dict(plan.get("datastore_plan") or {})
    network_plan = dict(plan.get("network_plan") or {})
    identity = dict(plan.get("identity") or {})
    ntp = dict(plan.get("ntp") or {})
    advanced = dict(plan.get("advanced_settings") or {})
    snmp = dict((plan.get("snmp_wug") or {}).get("snmpv3") or {})
    roles_accounts = dict(plan.get("roles_accounts") or {})
    actions: list[dict[str, Any]] = [
        {"id": "discover_connection", "label": "Connect/discover ESXi host", "desired": {"targets": list(plan.get("connection_targets") or [])}, "destructive": False},
        {"id": "ceip", "label": "Disable CEIP", "desired": {"UserVars.HostClientCEIPOptIn": 2}, "destructive": False},
        {"id": "datastore_rename", "label": "Rename local datastore", "desired": {"from": ds_plan.get("rename_local_datastore_from"), "to": ds_plan.get("rename_local_datastore_to")}, "destructive": False},
        {"id": "datastore_create_local_s2", "label": "Create LOCAL-S2 datastore", "desired": {"allowed": bool(ds_plan.get("create_local_s2_allowed")), "disk": dict(ds_plan.get("create_local_s2_disk") or {})}, "destructive": True},
    ]
    netapp_nfs = dict(plan.get("netapp_nfs") or {})
    if bool(netapp_nfs.get("required")):
        actions.append({"id": "netapp_nfs_datastore_mount", "label": "Mount NetApp NFS datastore", "desired": netapp_nfs, "destructive": False})
    actions.extend(
        [
            {"id": "vswitch0_uplinks", "label": "Attach management uplinks to vSwitch0", "desired": {"uplinks": list(network_plan.get("preferred_mgmt_uplinks") or [])}, "destructive": False},
            {"id": "vm_network_pg", "label": "Ensure VM Network port group", "desired": {"recreate_allowed": bool(network_plan.get("vm_network_recreate_enabled"))}, "destructive": False},
            {"id": "identity_dns", "label": "Apply hostname/domain/DNS", "desired": {"hostname": identity.get("hostname"), "domain": identity.get("domain"), "dns_servers": list(identity.get("dns_servers") or [])}, "destructive": False},
            {"id": "ntp", "label": "Apply NTP and start ntpd", "desired": {"server": ntp.get("server"), "service": "ntpd"}, "destructive": False},
            {"id": "advanced_settings", "label": "Apply advanced settings", "desired": dict(advanced), "destructive": False},
            {"id": "snmp_wug", "label": "Apply WUG/SNMP policy", "desired": dict(snmp), "destructive": False},
            {"id": "roles_accounts", "label": "Apply roles/accounts policy", "desired": dict(roles_accounts), "destructive": False},
        ]
    )
    return actions


def execute_esxi_post_config_actions(
    cfg: dict[str, Any],
    *,
    preview: dict[str, Any],
    validation: dict[str, Any],
    run_action_fn: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    policy = dict((preview.get("policy") or {}))
    actions = build_esxi_post_config_actions(preview)
    results: list[dict[str, Any]] = []
    warnings = list(validation.get("warnings") or [])
    errors = list(validation.get("errors") or [])
    mode = "dry_run" if run_action_fn is None else str((cfg.get("esxi") or {}).get("post_config_transport") or "live")
    if not validation.get("ok"):
        return {
            "ok": False,
            "mode": mode,
            "actions": actions,
            "results": [],
            "warnings": warnings,
            "errors": errors,
            "reboot_required": False,
            "reboot_performed": False,
        }
    for action in actions:
        action_id = str(action.get("id") or "")
        if action_id == "datastore_create_local_s2" and not bool((action.get("desired") or {}).get("allowed")):
            results.append({"id": action_id, "status": "skipped", "reason": "creation_not_allowed_or_not_eligible"})
            continue
        if run_action_fn is None:
            results.append({"id": action_id, "status": "planned", "mode": "dry_run"})
            continue
        try:
            action_result = dict(run_action_fn(action_id, dict(action.get("desired") or {})) or {})
            results.append({"id": action_id, "status": str(action_result.get("status") or "applied"), "details": action_result})
        except Exception as exc:
            results.append({"id": action_id, "status": "failed", "error": str(exc).splitlines()[0]})
            errors.append(f"{action_id}: {str(exc).splitlines()[0]}")
    reboot_required = True
    reboot_performed = False
    if reboot_required:
        if bool(policy.get("configure_only_no_reboot")):
            warnings.append("Configure-only mode is enabled; reboot was intentionally skipped.")
        elif bool(policy.get("reboot_confirmed")):
            reboot_performed = True
        else:
            warnings.append("Reboot required but not confirmed; set reboot confirmation before restart.")
    return {
        "ok": not errors,
        "mode": mode,
        "actions": actions,
        "results": results,
        "warnings": warnings,
        "errors": errors,
        "reboot_required": reboot_required,
        "reboot_performed": reboot_performed,
    }


def _shell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def _ssh_failure_summary(code: int, out: str, err: str) -> str:
    ignored_prefixes = (
        "Warning: Permanently added ",
        "Warning: ",
    )
    for text in (err, out):
        for line in str(text or "").splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            if any(cleaned.startswith(prefix) for prefix in ignored_prefixes):
                continue
            return cleaned
    return f"ssh command failed ({code})"


def _parse_datastore_summary_capacity(summary: str, datastore_name: str) -> dict[str, Any]:
    if not datastore_name:
        return {}
    current_name = ""
    for raw_line in str(summary or "").splitlines():
        line = raw_line.strip()
        if line.startswith("name = "):
            current_name = line.split("=", 1)[1].strip().strip('",')
            continue
        if current_name != datastore_name:
            continue
        if line.startswith("capacity = "):
            try:
                capacity = int(line.split("=", 1)[1].strip().strip(","))
            except ValueError:
                capacity = 0
            return {"capacity_bytes": capacity}
    return {}


def build_esxi_post_config_ssh_run_action(
    cfg: dict[str, Any],
    preview: dict[str, Any],
    *,
    command_runner: Callable[[list[str]], tuple[int, str, str]] | None = None,
) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    esxi_cfg = cfg.get("esxi", {}) or {}
    secrets = ensure_esxi_post_config_secrets(cfg)
    host = str(esxi_cfg.get("management_ip") or cfg.get("ip_plan", {}).get("esxi") or "").strip()
    if not host:
        raise RuntimeError("ESXi SSH transport cannot start because management IP is empty.")
    username = "root"
    password = str(esxi_cfg.get("root_password") or "").strip()
    if not password:
        raise RuntimeError("ESXi SSH transport cannot start because root password is empty.")
    if command_runner is None:
        use_sshpass = shutil.which("sshpass")
        if not use_sshpass:
            raise RuntimeError(
                "ESXi live SSH transport requires sshpass for password-based root login. "
                "Install sshpass or configure key/agent access and provide a custom command runner."
            )

        def _default_runner(cmd: list[str]) -> tuple[int, str, str]:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            return proc.returncode, str(proc.stdout or ""), str(proc.stderr or "")

        command_runner = _default_runner

    plan = dict(preview.get("plan") or {})
    identity = dict(plan.get("identity") or {})
    ntp = dict(plan.get("ntp") or {})
    advanced = dict(plan.get("advanced_settings") or {})
    ds_plan = dict(plan.get("datastore_plan") or {})
    network_plan = dict(plan.get("network_plan") or {})
    snmp_wug = dict(plan.get("snmp_wug") or {})
    snmpv3 = dict(snmp_wug.get("snmpv3") or {})
    roles_accounts = dict(plan.get("roles_accounts") or {})

    def run_ssh_raw(remote_command: str) -> tuple[str, str]:
        cmd = [
            "sshpass",
            "-p",
            password,
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "ConnectTimeout=10",
            f"{username}@{host}",
            remote_command,
        ]
        code, out, err = command_runner(cmd)
        if code != 0:
            raise RuntimeError(_ssh_failure_summary(code, str(out or ""), str(err or "")))
        return str(out or ""), str(err or "")

    def run_ssh(remote_command: str) -> dict[str, Any]:
        out, _err = run_ssh_raw(remote_command)
        return {"status": "applied", "command": remote_command, "stdout": out.splitlines()[-1] if out else ""}

    def refresh_datastore_summary(datastore_name: str) -> dict[str, Any]:
        if not datastore_name:
            return {}
        result: dict[str, Any] = {}
        try:
            run_ssh_raw(f"vim-cmd hostsvc/datastore/refresh {_shell_quote(datastore_name)}")
            result["refreshed"] = True
        except RuntimeError as exc:
            result["refresh_warning"] = str(exc)
        try:
            summary_out, _summary_err = run_ssh_raw("vim-cmd hostsvc/datastore/listsummary")
            result["summary_stdout"] = summary_out
            result.update(_parse_datastore_summary_capacity(summary_out, datastore_name))
        except RuntimeError as exc:
            result["summary_warning"] = str(exc)
        return result

    def runner(action_id: str, desired: dict[str, Any]) -> dict[str, Any]:
        if action_id == "discover_connection":
            return run_ssh("vmware -vl")
        if action_id == "ceip":
            return run_ssh("esxcli system settings advanced set -o /UserVars/HostClientCEIPOptIn -i 2")
        if action_id == "datastore_rename":
            src = str(desired.get("from") or "").strip()
            dst = str(desired.get("to") or "").strip()
            if not src or not dst:
                return {"status": "skipped", "reason": "missing_datastore_names"}
            return run_ssh(f"esxcli storage filesystem rename -l {_shell_quote(src)} -n {_shell_quote(dst)}")
        if action_id == "datastore_create_local_s2":
            if not bool(desired.get("allowed")):
                return {"status": "skipped", "reason": "not_allowed"}
            disk = dict(desired.get("disk") or {})
            disk_name = str(disk.get('name') or "").strip()
            if not disk_name:
                return {"status": "skipped", "reason": "missing_disk_name"}
            return run_ssh(f"vmkfstools -C vmfs6 -S LOCAL-S2 {_shell_quote('/vmfs/devices/disks/' + disk_name)}")
        if action_id == "netapp_nfs_datastore_mount":
            if not bool(desired.get("ready")):
                return {"status": "skipped", "reason": desired.get("blocking_reason") or "missing_netapp_nfs_mount_inputs"}
            mount_plan = [dict(item or {}) for item in list(desired.get("mount_plan") or [])]
            if not mount_plan:
                return {"status": "skipped", "reason": "no_mount_plan"}
            target = mount_plan[0]
            datastore_name = str(target.get("datastore_name") or desired.get("datastore_name") or "").strip()
            nfs_version = str(target.get("nfs_version") or desired.get("nfs_version") or "4.1").strip()
            list_cmd = "esxcli storage nfs41 list" if nfs_version == "4.1" else "esxcli storage nfs list"
            for check_cmd, effective_version in (
                ("esxcli storage nfs41 list", "4.1"),
                ("esxcli storage nfs list", "3"),
            ):
                out, _err = run_ssh_raw(check_cmd)
                existing_line = next((line for line in out.splitlines() if datastore_name and datastore_name in line), "")
                if not existing_line:
                    continue
                if "true" in existing_line.lower():
                    datastore_summary = refresh_datastore_summary(datastore_name)
                    return {
                        "status": "skipped",
                        "reason": "datastore_already_mounted",
                        "stdout": out,
                        "requested_nfs_version": nfs_version,
                        "effective_nfs_version": effective_version,
                        "list_command": check_cmd,
                        "datastore_summary": datastore_summary,
                    }
                raise RuntimeError(
                    f"Datastore '{datastore_name}' already exists on ESXi but is not accessible or mounted. "
                    "Manual cleanup or remount review is required before retrying automation."
                )
            add_cmd = str(target.get("esxcli_command") or "").strip()
            fallback_cmd = str(target.get("esxcli_fallback_command") or "").strip()
            if not add_cmd:
                return {"status": "skipped", "reason": "missing_esxcli_mount_command"}
            fallback_used = False
            try:
                run_ssh_raw(add_cmd)
            except RuntimeError as exc:
                if not (fallback_cmd and nfs_version == "4.1"):
                    raise
                cleanup_out, _cleanup_err = run_ssh_raw("esxcli storage nfs41 list")
                cleanup_line = next((line for line in cleanup_out.splitlines() if datastore_name and datastore_name in line), "")
                if cleanup_line:
                    run_ssh_raw(f"esxcli storage nfs41 remove -v {_shell_quote(datastore_name)}")
                run_ssh_raw(fallback_cmd)
                fallback_used = True
                list_cmd = "esxcli storage nfs list"
            verify_out, _verify_err = run_ssh_raw(list_cmd)
            if datastore_name and datastore_name not in verify_out:
                raise RuntimeError(f"Datastore '{datastore_name}' was not present in ESXi NFS mounts after apply.")
            datastore_summary = refresh_datastore_summary(datastore_name)
            return {
                "status": "applied",
                "command": fallback_cmd if fallback_used else add_cmd,
                "stdout": verify_out,
                "fallback_used": fallback_used,
                "requested_nfs_version": nfs_version,
                "effective_nfs_version": "3" if fallback_used else nfs_version,
                "datastore_summary": datastore_summary,
            }
        if action_id == "vswitch0_uplinks":
            uplinks = [str(item).strip() for item in list(desired.get("uplinks") or []) if str(item).strip()]
            if not uplinks:
                return {"status": "skipped", "reason": "no_uplinks"}
            commands = [f"esxcli network vswitch standard uplink add -u {_shell_quote(u)} -v vSwitch0 || true" for u in uplinks]
            commands.append("esxcli network ip interface set -i vmk0 -m true")
            return run_ssh(" ; ".join(commands))
        if action_id == "vm_network_pg":
            return run_ssh("esxcli network vswitch standard portgroup add -p 'VM Network' -v vSwitch0 || true")
        if action_id == "identity_dns":
            hostname = str(identity.get("hostname") or "").strip()
            domain = str(identity.get("domain") or "").strip()
            dns_servers = [str(item).strip() for item in list(identity.get("dns_servers") or []) if str(item).strip()]
            cmd = []
            if hostname:
                cmd.append(f"esxcli system hostname set --host={_shell_quote(hostname)}")
            if domain:
                cmd.append(f"esxcli system hostname set --domain={_shell_quote(domain)}")
            if dns_servers:
                cmd.append(f"esxcli network ip dns server add --server={_shell_quote(dns_servers[0])} || true")
                if len(dns_servers) > 1:
                    cmd.append(f"esxcli network ip dns server add --server={_shell_quote(dns_servers[1])} || true")
            if not cmd:
                return {"status": "skipped", "reason": "no_identity_changes"}
            return run_ssh(" ; ".join(cmd))
        if action_id == "ntp":
            server = str(ntp.get("server") or "").strip()
            if not server:
                return {"status": "skipped", "reason": "no_ntp_server"}
            return run_ssh(
                f"esxcli system ntp set --server={_shell_quote(server)} ; "
                "esxcli system ntp set --enabled=true ; "
                "esxcli network firewall ruleset set -e true -r ntpClient ; "
                "esxcli system ntp get"
            )
        if action_id == "advanced_settings":
            admins_group = str(advanced.get("Config.HostAgent.plugins.hostsvc.esxAdminsGroup") or "").strip()
            log_dir = str(advanced.get("Syslog.global.logDir") or "").strip()
            welcome = str(advanced.get("UserVars.HostClientWelcomeMessage") or "").strip()
            cmd = [
                "esxcli system settings advanced set -o /UserVars/HostClientCEIPOptIn -i 2",
            ]
            if admins_group:
                cmd.append(f"esxcli system settings advanced set -o /Config/HostAgent/plugins/hostsvc/esxAdminsGroup -s {_shell_quote(admins_group)}")
            if log_dir:
                cmd.append(f"esxcli system syslog config set --logdir={_shell_quote(log_dir)}")
            if welcome:
                cmd.append(f"esxcli system settings advanced set -o /UserVars/HostClientWelcomeMessage -s {_shell_quote(welcome)}")
            return run_ssh(" ; ".join(cmd))
        if action_id == "snmp_wug":
            wug_user = str(snmp_wug.get("account_username") or "WUGMon").strip()
            wug_pwd = str(secrets.get("wug_password") or "")
            auth_pwd = str(secrets.get("snmpv3_auth_password") or "")
            priv_pwd = str(secrets.get("snmpv3_priv_password") or "")
            target = str(snmpv3.get("target") or "").strip()
            notraps = str(snmpv3.get("notraps") or "").strip()
            syslocation = str(snmpv3.get("syslocation") or "").strip()
            if not (wug_pwd and auth_pwd and priv_pwd):
                return {"status": "skipped", "reason": "missing_snmp_or_wug_secrets"}
            cmd = [
                f"esxcli system account add -i {_shell_quote(wug_user)} -p {_shell_quote(wug_pwd)} -c {_shell_quote(wug_pwd)} || true",
                f"esxcli system permission set -i {_shell_quote(wug_user)} -r ReadOnly || true",
                "esxcli system snmp set --loglevel warning --authentication SHA1 --privacy AES128",
            ]
            if target:
                cmd.append(f"esxcli system snmp set --targets {_shell_quote(target)}")
            if notraps:
                cmd.append(f"esxcli system snmp set --notraps {_shell_quote(notraps)}")
            if syslocation:
                cmd.append(f"esxcli system snmp set --syslocation {_shell_quote(syslocation)}")
            cmd.append(f"esxcli system snmp hash --auth-hash {_shell_quote(auth_pwd)} --priv-hash {_shell_quote(priv_pwd)}")
            cmd.append("esxcli system snmp set --enable true")
            cmd.append("esxcli system snmp get")
            return run_ssh(" ; ".join(cmd))
        if action_id == "roles_accounts":
            role_name = str(roles_accounts.get("role_name") or "VirtualManagers").strip()
            accounts = list(roles_accounts.get("accounts") or [])
            kit_root_pwd = str(secrets.get("kit_root_password") or "")
            svm_pwd = str(secrets.get("svmservice_password") or "")
            localtech_pwd = str(secrets.get("localtech_password") or "")
            if not role_name:
                return {"status": "skipped", "reason": "missing_role_name"}
            cmd = [f"vim-cmd vimsvc/auth/roles | grep -q {_shell_quote(role_name)} || vim-cmd vimsvc/auth/role_add {_shell_quote(role_name)}"]
            for acct in accounts:
                user = str(acct.get("username") or "").strip()
                role = str(acct.get("role") or "").strip()
                if not user or not role:
                    continue
                if user.endswith("root") and kit_root_pwd:
                    cmd.append(f"esxcli system account add -i {_shell_quote(user)} -p {_shell_quote(kit_root_pwd)} -c {_shell_quote(kit_root_pwd)} || true")
                elif user == "S-VMSERVICE" and svm_pwd:
                    cmd.append(f"esxcli system account add -i {_shell_quote(user)} -p {_shell_quote(svm_pwd)} -c {_shell_quote(svm_pwd)} || true")
                elif user == "LocalTech" and localtech_pwd:
                    cmd.append(f"esxcli system account add -i {_shell_quote(user)} -p {_shell_quote(localtech_pwd)} -c {_shell_quote(localtech_pwd)} || true")
                cmd.append(f"vim-cmd vimsvc/auth/entity_permission_add vim.Folder:ha-folder-root {_shell_quote(user)} false {_shell_quote(role)} true || true")
            return run_ssh(" ; ".join(cmd))
        raise RuntimeError(f"Unsupported ESXi post-config action: {action_id}")

    return runner
