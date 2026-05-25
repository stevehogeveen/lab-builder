from __future__ import annotations

import copy
import ipaddress
import json
import os
import re
import shlex
import shutil
import socket
import ssl
import subprocess
import time
from pathlib import Path
from typing import Any


VCENTER_DEFAULT_OFFSET = 50
VCSA_SPEC_VERSION = "2.13.0"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _safe_name(value: str, fallback: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", _clean(value))
    name = re.sub(r"-{2,}", "-", name).strip(".-")
    return (name or fallback)[:80]


def _subnet(cfg: dict[str, Any]) -> str:
    return _clean(_as_dict(cfg.get("shared_network")).get("subnet") or _as_dict(cfg.get("ip_plan")).get("subnet") or "10.10.8.0/24")


def _ip_at_offset(subnet_cidr: str, offset: int) -> str:
    network = ipaddress.ip_network(subnet_cidr, strict=False)
    candidate = network.network_address + int(offset)
    if candidate not in network or candidate == network.network_address or candidate == network.broadcast_address:
        raise ValueError(f"Offset {offset} is not usable inside {subnet_cidr}")
    return str(candidate)


def default_vcenter_ip(cfg: dict[str, Any]) -> str:
    return _ip_at_offset(_subnet(cfg), VCENTER_DEFAULT_OFFSET)


def find_vcsa_iso_choices(media_dir: Path) -> list[dict[str, str]]:
    root = Path(media_dir)
    if not root.exists():
        return []
    choices: list[dict[str, str]] = []
    for path in sorted(root.glob("**/*.iso")):
        name = path.name
        if "vcsa" not in name.lower() and "vcenter" not in name.lower():
            continue
        choices.append({"name": name, "path": str(path), "size": str(path.stat().st_size)})
    return choices


def _first_vcsa_iso(media_dir: Path) -> str:
    choices = find_vcsa_iso_choices(media_dir)
    return choices[0]["path"] if choices else ""


def _prefixlen(cfg: dict[str, Any]) -> str:
    plan = _as_dict(cfg.get("ip_plan"))
    if plan.get("prefixlen") not in (None, ""):
        return str(plan.get("prefixlen"))
    return str(ipaddress.ip_network(_subnet(cfg), strict=False).prefixlen)


def _dns_servers(cfg: dict[str, Any], install: dict[str, Any]) -> list[str]:
    explicit = install.get("dns_servers")
    raw_values: list[str] = []
    if isinstance(explicit, str):
        raw_values.extend(item.strip() for item in explicit.replace(";", ",").split(","))
    else:
        for item in list(explicit or []):
            raw_values.extend(part.strip() for part in str(item).replace(";", ",").split(","))
    if not any(raw_values):
        for item in list(_as_dict(cfg.get("shared_network")).get("dns_servers") or []):
            raw_values.extend(part.strip() for part in str(item).replace(";", ",").split(","))
    if not any(raw_values):
        raw_values = [_clean(_as_dict(cfg.get("ip_plan")).get("gateway"))]

    servers: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        if not item:
            continue
        try:
            ipaddress.ip_address(item)
        except ValueError:
            continue
        if item in seen:
            continue
        servers.append(item)
        seen.add(item)
        if len(servers) >= 2:
            break
    if not servers:
        gateway = _clean(_as_dict(cfg.get("ip_plan")).get("gateway"))
        if gateway:
            servers.append(gateway)
    return servers


def _ntp_servers(cfg: dict[str, Any], install: dict[str, Any]) -> str:
    explicit = _clean(install.get("ntp_servers"))
    if explicit:
        return explicit
    gateway = _clean(_as_dict(cfg.get("ip_plan")).get("gateway"))
    return gateway or "time.nist.gov"


def _default_datastore(cfg: dict[str, Any]) -> str:
    vmware = _as_dict(cfg.get("vmware"))
    netapp = _as_dict(cfg.get("netapp"))
    desired_nfs = _as_dict(_as_dict(netapp.get("desired")).get("nfs"))
    netapp_nfs = _as_dict(netapp.get("nfs"))
    return _clean(
        _as_dict(vmware.get("nfs")).get("datastore_name")
        or desired_nfs.get("datastore_name")
        or netapp_nfs.get("datastore_name")
        or _as_dict(cfg.get("windows")).get("vsphere_datastore")
        or "datastore1"
    )


def build_vcenter_install_context(
    cfg: dict[str, Any],
    *,
    media_dir: Path,
    generated_dir: Path,
    artifacts_dir: Path,
) -> dict[str, Any]:
    vmware = _as_dict(cfg.get("vmware"))
    install = _as_dict(vmware.get("vcenter_install"))
    esxi = _as_dict(cfg.get("esxi"))
    ip_plan = _as_dict(cfg.get("ip_plan"))
    site_name = _clean(_as_dict(cfg.get("site")).get("name")) or "Kit-01"
    target_ip = _clean(install.get("target_ip") or vmware.get("vcenter_ip") or default_vcenter_ip(cfg))
    iso_path = _clean(install.get("iso_path") or _first_vcsa_iso(media_dir))
    iso_stem = _safe_name(Path(iso_path).stem if iso_path else "vcsa-installer", "vcsa-installer")
    vm_name = _safe_name(install.get("vm_name") or f"SVCNTR-{site_name}", "SVCNTR")
    sso_domain = _clean(install.get("sso_domain") or "vsphere.local")
    deployment_option = _clean(install.get("deployment_option") or "tiny")
    if deployment_option not in {"tiny", "small", "medium", "large", "xlarge"}:
        deployment_option = "tiny"
    context = {
        "site_name": site_name,
        "datacenter_name": _clean(vmware.get("datacenter_name") or site_name),
        "cluster_name": _clean(vmware.get("cluster_name") or f"{site_name}-Cluster"),
        "media_choices": find_vcsa_iso_choices(media_dir),
        "target_ip": target_ip,
        "system_name": _clean(install.get("system_name") or target_ip),
        "vm_name": vm_name,
        "iso_path": iso_path,
        "esxi_host": _clean(install.get("esxi_host") or esxi.get("management_ip") or ip_plan.get("esxi")),
        "esxi_username": _clean(install.get("esxi_username") or vmware.get("esxi_root_user") or "root"),
        "esxi_password": str(install.get("esxi_password") or vmware.get("esxi_root_password") or esxi.get("root_password") or ""),
        "datastore": _clean(install.get("datastore") or _default_datastore(cfg)),
        "deployment_network": _clean(install.get("deployment_network") or "VM Network"),
        "deployment_option": deployment_option,
        "thin_disk_mode": _truthy(install.get("thin_disk_mode"), True),
        "root_password": str(install.get("root_password") or vmware.get("password") or esxi.get("root_password") or ""),
        "sso_domain": sso_domain,
        "sso_password": str(install.get("sso_password") or vmware.get("password") or esxi.get("root_password") or ""),
        "sso_username": f"administrator@{sso_domain}",
        "prefix": _prefixlen(cfg),
        "gateway": _clean(ip_plan.get("gateway")),
        "dns_servers": _dns_servers(cfg, install),
        "ntp_servers": _ntp_servers(cfg, install),
        "ssh_enable": _truthy(install.get("ssh_enable"), True),
        "activity": _as_dict(install.get("activity")),
        "work_dir": str(Path(generated_dir) / "vcenter" / _safe_name(site_name, "Kit-01")),
        "installer_extract_dir": str(Path("/tmp/lab-builder-vcenter-installer") / iso_stem),
    }
    context["blockers"] = validate_vcenter_install_context(context, require_passwords=True)
    context["warnings"] = validate_vcenter_install_context(context, require_passwords=False)
    context["ready"] = not context["blockers"]
    return context


def password_policy_errors(password: str, label: str) -> list[str]:
    value = str(password or "")
    errors: list[str] = []
    if not value:
        return [f"{label} is required."]
    if len(value) < 8:
        errors.append(f"{label} must be at least 8 characters.")
    if any(ch.isspace() for ch in value):
        errors.append(f"{label} cannot contain spaces.")
    classes = 0
    classes += 1 if any(ch.islower() for ch in value) else 0
    classes += 1 if any(ch.isupper() for ch in value) else 0
    classes += 1 if any(ch.isdigit() for ch in value) else 0
    classes += 1 if any(not ch.isalnum() for ch in value) else 0
    if classes < 4:
        errors.append(f"{label} should include upper, lower, number, and special characters for the VCSA installer.")
    return errors


def validate_vcenter_install_context(context: dict[str, Any], *, require_passwords: bool) -> list[str]:
    issues: list[str] = []
    iso_path = Path(_clean(context.get("iso_path")))
    if not context.get("iso_path"):
        issues.append("Select a VMware VCSA ISO from the media folder.")
    elif not iso_path.exists():
        issues.append(f"VCSA ISO not found: {iso_path}")
    if context.get("target_ip"):
        try:
            ipaddress.ip_address(_clean(context.get("target_ip")))
        except ValueError:
            issues.append("vCenter appliance IP must be a valid IP address.")
    else:
        issues.append("vCenter appliance IP is required.")
    for key, label in (
        ("esxi_host", "Target ESXi host"),
        ("esxi_username", "ESXi username"),
        ("datastore", "Datastore"),
        ("deployment_network", "Deployment network"),
        ("gateway", "Gateway"),
    ):
        if not _clean(context.get(key)):
            issues.append(f"{label} is required.")
    if require_passwords:
        if not context.get("esxi_password"):
            issues.append("ESXi root password is required to deploy the VCSA VM.")
        issues.extend(password_policy_errors(str(context.get("root_password") or ""), "VCSA root password"))
        issues.extend(password_policy_errors(str(context.get("sso_password") or ""), "SSO administrator password"))
    if not context.get("dns_servers"):
        issues.append("At least one DNS server is required for VCSA networking.")
    return issues


def build_vcenter_install_spec(context: dict[str, Any], *, redact: bool = False) -> dict[str, Any]:
    esxi_password = "********" if redact and context.get("esxi_password") else str(context.get("esxi_password") or "")
    root_password = "********" if redact and context.get("root_password") else str(context.get("root_password") or "")
    sso_password = "********" if redact and context.get("sso_password") else str(context.get("sso_password") or "")
    return {
        "__version": VCSA_SPEC_VERSION,
        "new_vcsa": {
            "esxi": {
                "hostname": _clean(context.get("esxi_host")),
                "username": _clean(context.get("esxi_username")) or "root",
                "password": esxi_password,
                "deployment_network": _clean(context.get("deployment_network")) or "VM Network",
                "datastore": _clean(context.get("datastore")),
            },
            "appliance": {
                "thin_disk_mode": bool(context.get("thin_disk_mode", True)),
                "deployment_option": _clean(context.get("deployment_option")) or "tiny",
                "name": _clean(context.get("vm_name")) or "SVCNTR",
            },
            "network": {
                "ip_family": "ipv4",
                "mode": "static",
                "system_name": _clean(context.get("system_name")) or _clean(context.get("target_ip")),
                "ip": _clean(context.get("target_ip")),
                "prefix": str(context.get("prefix") or "24"),
                "gateway": _clean(context.get("gateway")),
                "dns_servers": list(context.get("dns_servers") or []),
            },
            "os": {
                "password": root_password,
                "ntp_servers": _clean(context.get("ntp_servers")) or "time.nist.gov",
                "ssh_enable": bool(context.get("ssh_enable", True)),
            },
            "sso": {
                "password": sso_password,
                "domain_name": _clean(context.get("sso_domain")) or "vsphere.local",
            },
        },
        "ceip": {"settings": {"ceip_enabled": False}},
    }


def write_vcenter_install_specs(context: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    spec_path = output_dir / "vcsa-install.json"
    redacted_path = output_dir / "vcsa-install.redacted.json"
    spec_path.write_text(json.dumps(build_vcenter_install_spec(context), indent=2) + "\n", encoding="utf-8")
    redacted_path.write_text(json.dumps(build_vcenter_install_spec(context, redact=True), indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(spec_path, 0o600)
        os.chmod(redacted_path, 0o644)
    except OSError:
        pass
    return {"spec_path": str(spec_path), "redacted_spec_path": str(redacted_path)}


def record_vcenter_install_event(
    cfg: dict[str, Any],
    *,
    phase: str,
    message: str,
    status: str | None = None,
    progress: int | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    vmware = cfg.setdefault("vmware", {})
    install = vmware.setdefault("vcenter_install", {})
    activity = install.setdefault("activity", {})
    events = [event for event in list(activity.get("events") or []) if isinstance(event, dict)]
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    event = {
        "timestamp": timestamp,
        "phase": _clean(phase) or "event",
        "message": _clean(message) or "Event recorded.",
    }
    if progress is not None:
        event["progress_percent"] = max(0, min(100, int(progress)))
    if details:
        event["details"] = copy.deepcopy(details)
    events.append(event)
    activity.update(
        {
            "status": status or activity.get("status") or "running",
            "phase": event["phase"],
            "message": event["message"],
            "updated_at": timestamp,
            "events": events[-120:],
        }
    )
    if progress is not None:
        activity["progress_percent"] = max(0, min(100, int(progress)))
    if status == "running" and not activity.get("started_at"):
        activity["started_at"] = timestamp
    if status in {"completed", "failed", "blocked"}:
        activity["finished_at"] = timestamp
    return activity


def _display_time(value: Any) -> str:
    text = _clean(value)
    if len(text) >= 19 and text[10:11] == " ":
        return text[11:19]
    if len(text) >= 19 and "T" in text:
        return text[11:19]
    return text or "--:--:--"


def _status_label(status: str) -> str:
    labels = {
        "not_started": "Not Started",
        "running": "Running",
        "waiting": "Waiting",
        "blocked": "Blocked",
        "failed": "Failed",
        "completed": "Completed",
        "warning": "Warning",
    }
    return labels.get(status, status.replace("_", " ").title())


def _event_rows(activity: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for event in list(activity.get("events") or [])[-28:]:
        if not isinstance(event, dict):
            continue
        phase = _clean(event.get("phase") or "event")
        message = _clean(event.get("message") or "Event recorded.")
        severity = "error" if phase in {"failed", "blocked"} or "failed" in message.lower() else "ready" if phase == "complete" else "info"
        rows.append({"time": _display_time(event.get("timestamp")), "stage": phase, "severity": severity, "message": message})
    return list(reversed(rows))


def _raw_log(context: dict[str, Any], activity: dict[str, Any]) -> str:
    log_path = Path(_clean(activity.get("log_path")))
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            return "\n".join(lines[-240:])
        except OSError:
            pass
    events = []
    for event in reversed(list(activity.get("events") or [])[-80:]):
        if isinstance(event, dict):
            events.append(f"{_clean(event.get('timestamp'))} [{_clean(event.get('phase'))}] {_clean(event.get('message'))}".strip())
    if events:
        return "\n".join(events)
    return json.dumps(build_vcenter_install_spec(context, redact=True), indent=2)


def build_vcenter_install_panel(cfg: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    activity = _as_dict(context.get("activity") or _as_dict(_as_dict(_as_dict(cfg.get("vmware")).get("vcenter_install")).get("activity")))
    status = _clean(activity.get("status"))
    if status not in {"running", "waiting", "blocked", "failed", "completed", "warning"}:
        status = "blocked" if context.get("blockers") else "not_started"
    progress = int(activity.get("progress_percent") or (100 if status == "completed" else 0))
    latest = _clean(activity.get("message"))
    if not latest:
        blockers = list(context.get("blockers") or [])
        latest = blockers[0] if blockers else "Ready to generate a VCSA installer spec and deploy to ESXi."
    phase = _clean(activity.get("phase")) or ("Ready" if not context.get("blockers") else "Needs input")
    return {
        "id": "vcenter-install-activity",
        "module": "vcenter",
        "title": "vCenter appliance install",
        "status": status,
        "status_label": _status_label(status),
        "progress": max(0, min(100, progress)),
        "polling": status in {"running", "waiting"},
        "poll_url": "/vcenter/install-panel",
        "poll_interval": "5s",
        "latest_message": latest,
        "focus_title": phase,
        "focus_message": _clean(activity.get("focus_message") or latest),
        "job_id": _clean(activity.get("run_id")),
        "job_label": "Run",
        "events": _event_rows(activity),
        "raw_label": "VCSA installer output",
        "raw_output": _raw_log(context, activity),
        "metrics": [
            {"label": "Appliance IP", "value": context.get("target_ip") or "Not set"},
            {"label": "Target ESXi", "value": context.get("esxi_host") or "Not set"},
            {"label": "Datastore", "value": context.get("datastore") or "Not set"},
            {"label": "Network", "value": context.get("deployment_network") or "Not set"},
            {"label": "Deployment size", "value": context.get("deployment_option") or "tiny"},
            {"label": "Media", "value": Path(_clean(context.get("iso_path"))).name if context.get("iso_path") else "Not selected"},
        ],
    }


def vcenter_extract_command(context: dict[str, Any]) -> list[str]:
    return [
        "7z",
        "x",
        "-y",
        f"-o{_clean(context.get('installer_extract_dir'))}",
        _clean(context.get("iso_path")),
        "vcsa-cli-installer/*",
        "vcsa/*",
    ]


def vcenter_deploy_command(context: dict[str, Any], spec_path: str) -> list[str]:
    deploy = Path(_clean(context.get("installer_extract_dir"))) / "vcsa-cli-installer" / "lin64" / "vcsa-deploy"
    return [
        str(deploy),
        "install",
        str(spec_path),
        "--accept-eula",
        "--acknowledge-ceip",
        "--no-ssl-certificate-verification",
    ]


def ensure_esxi_standard_portgroup(context: dict[str, Any], *, vswitch_name: str = "vSwitch0", vlan_id: int = 0) -> dict[str, Any]:
    network_name = _clean(context.get("deployment_network")) or "VM Network"
    host = _clean(context.get("esxi_host"))
    username = _clean(context.get("esxi_username")) or "root"
    password = str(context.get("esxi_password") or "")
    if not host or not username or not password:
        raise RuntimeError("ESXi host, username, and password are required to verify the deployment port group.")
    if not shutil.which("sshpass"):
        raise RuntimeError("sshpass is required to verify the ESXi deployment port group.")

    network_q = shlex.quote(network_name)
    vswitch_q = shlex.quote(vswitch_name)
    command = (
        f"if ! esxcli network vswitch standard portgroup list | grep -F -- {network_q} >/dev/null; then "
        f"esxcli network vswitch standard portgroup add -p {network_q} -v {vswitch_q}; "
        f"fi; "
        f"esxcli network vswitch standard portgroup set -p {network_q} --vlan-id {int(vlan_id)}; "
        f"esxcli network vswitch standard portgroup list | grep -F -- {network_q}"
    )
    completed = subprocess.run(
        [
            "sshpass",
            "-p",
            password,
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=15",
            f"{username}@{host}",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "Could not verify ESXi deployment port group.").strip()
        raise RuntimeError(message.splitlines()[-1] if message else "Could not verify ESXi deployment port group.")
    return {
        "ok": True,
        "network": network_name,
        "vswitch": vswitch_name,
        "vlan_id": int(vlan_id),
        "output": completed.stdout.strip(),
    }


def _wait_for_task(task: Any, *, timeout_seconds: int = 600) -> None:
    try:
        from pyVmomi import vim
    except Exception as exc:
        raise RuntimeError("pyvmomi is required for vCenter inventory setup.") from exc
    deadline = time.monotonic() + timeout_seconds
    while task.info.state in {vim.TaskInfo.State.queued, vim.TaskInfo.State.running}:
        if time.monotonic() > deadline:
            raise RuntimeError("Timed out waiting for vCenter inventory task completion.")
        time.sleep(2)
    if task.info.state == vim.TaskInfo.State.error:
        error = getattr(task.info, "error", None)
        message = getattr(error, "msg", None) or str(error or "vCenter task failed.")
        raise RuntimeError(message)


def _esxi_ssl_thumbprint(host: str, port: int = 443) -> str:
    pem = ssl.get_server_certificate((host, port))
    der = ssl.PEM_cert_to_DER_cert(pem)
    import hashlib

    digest = hashlib.sha1(der).hexdigest().upper()
    return ":".join(digest[index : index + 2] for index in range(0, len(digest), 2))


def _find_datacenter(content: Any, name: str) -> Any | None:
    try:
        from pyVmomi import vim
    except Exception as exc:
        raise RuntimeError("pyvmomi is required for vCenter inventory setup.") from exc
    for child in list(getattr(content.rootFolder, "childEntity", []) or []):
        if isinstance(child, vim.Datacenter) and getattr(child, "name", "") == name:
            return child
    return None


def _find_cluster(datacenter: Any, name: str) -> Any | None:
    try:
        from pyVmomi import vim
    except Exception as exc:
        raise RuntimeError("pyvmomi is required for vCenter inventory setup.") from exc
    for child in list(getattr(datacenter.hostFolder, "childEntity", []) or []):
        if isinstance(child, vim.ClusterComputeResource) and getattr(child, "name", "") == name:
            return child
    return None


def _cluster_hosts(cluster: Any) -> list[Any]:
    hosts: list[Any] = []
    for host in list(getattr(cluster, "host", []) or []):
        hosts.append(host)
    return hosts


def configure_vcenter_inventory(context: dict[str, Any], *, timeout_seconds: int = 1800) -> dict[str, Any]:
    try:
        from pyVim.connect import Disconnect, SmartConnect
        from pyVmomi import vim
    except Exception as exc:
        raise RuntimeError("pyvmomi is required for vCenter inventory setup.") from exc

    vcenter_host = _clean(context.get("target_ip"))
    username = _clean(context.get("sso_username")) or f"administrator@{_clean(context.get('sso_domain')) or 'vsphere.local'}"
    password = str(context.get("sso_password") or "")
    datacenter_name = _clean(context.get("datacenter_name")) or "Datacenter"
    cluster_name = _clean(context.get("cluster_name")) or "Cluster"
    esxi_host = _clean(context.get("esxi_host"))
    esxi_username = _clean(context.get("esxi_username")) or "root"
    esxi_password = str(context.get("esxi_password") or "")
    if not vcenter_host or not username or not password:
        raise RuntimeError("vCenter host and SSO credentials are required for inventory setup.")
    if not esxi_host or not esxi_username or not esxi_password:
        raise RuntimeError("ESXi host credentials are required for vCenter inventory setup.")

    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    si = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((vcenter_host, 443), timeout=10):
                pass
            si = SmartConnect(
                host=vcenter_host,
                user=username,
                pwd=password,
                port=443,
                sslContext=ssl._create_unverified_context(),
            )
            break
        except Exception as exc:
            last_error = str(exc).splitlines()[0]
            time.sleep(15)
    if si is None:
        raise RuntimeError(f"vCenter API did not become reachable: {last_error}")

    try:
        content = si.RetrieveContent()
        datacenter = _find_datacenter(content, datacenter_name)
        changed = False
        if datacenter is None:
            datacenter = content.rootFolder.CreateDatacenter(datacenter_name)
            changed = True
        cluster = _find_cluster(datacenter, cluster_name)
        if cluster is None:
            cluster = datacenter.hostFolder.CreateClusterEx(cluster_name, vim.cluster.ConfigSpecEx())
            changed = True
        existing_names = {str(getattr(host, "name", "")).lower() for host in _cluster_hosts(cluster)}
        if esxi_host.lower() not in existing_names:
            connect_spec = vim.host.ConnectSpec()
            connect_spec.hostName = esxi_host
            connect_spec.userName = esxi_username
            connect_spec.password = esxi_password
            connect_spec.force = True
            try:
                connect_spec.sslThumbprint = _esxi_ssl_thumbprint(esxi_host)
            except Exception:
                pass
            task = cluster.AddHost(connect_spec, asConnected=True)
            _wait_for_task(task, timeout_seconds=900)
            changed = True
        return {
            "ok": True,
            "changed": changed,
            "vcenter": vcenter_host,
            "datacenter": datacenter_name,
            "cluster": cluster_name,
            "esxi_host": esxi_host,
        }
    finally:
        try:
            Disconnect(si)
        except Exception:
            pass
