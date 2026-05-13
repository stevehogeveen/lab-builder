from __future__ import annotations

from dataclasses import dataclass
import re
import shutil
import subprocess
from typing import Any


class CiscoModuleError(RuntimeError):
    pass


@dataclass
class CiscoDiscoveryResult:
    ok: bool
    target: str
    username: str
    version: str = ""
    hostname: str = ""
    model: str = ""
    platform: str = ""
    raw_excerpt: str = ""
    error: str = ""
    warnings: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "target": self.target,
            "username": self.username,
            "version": self.version,
            "hostname": self.hostname,
            "model": self.model,
            "platform": self.platform,
            "raw_excerpt": self.raw_excerpt,
            "error": self.error,
            "warnings": list(self.warnings or []),
        }


def parse_cisco_show_version(output: str) -> dict[str, str]:
    text = str(output or "")
    version_patterns = [
        r"Cisco IOS XE Software,\s+Version\s+([^\s,]+)",
        r"Cisco IOS Software.*?,\s+Version\s+([^\s,]+)",
        r"\bVersion\s+([0-9][^\s,]+)",
        r"system:\s+version\s+([^\s,]+)",
    ]
    version = ""
    for pattern in version_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            version = match.group(1).strip()
            break

    hostname = ""
    hostname_match = re.search(r"^(\S+)\s+uptime is", text, flags=re.MULTILINE)
    if hostname_match:
        hostname = hostname_match.group(1).strip()

    model = ""
    for pattern in (
        r"cisco\s+(\S+)\s+\([^)]+\)\s+processor",
        r"[Mm]odel number\s*:\s*(\S+)",
        r"[Cc]isco\s+(\S+)\s+\(.+?\)\s+with",
    ):
        match = re.search(pattern, text)
        if match:
            model = match.group(1).strip()
            break

    platform = ""
    for pattern in (
        r"Cisco IOS Software\s+\[[^\]]+\],\s+\S+\s+Software\s+\(([^)]+)\)",
        r"Cisco IOS XE Software,\s+\S+\s+Software\s+\(([^)]+)\)",
        r"[Pp]latform:\s*([^\n,]+)",
    ):
        match = re.search(pattern, text)
        if match:
            platform = match.group(1).strip()
            break

    return {
        "version": version,
        "hostname": hostname,
        "model": model,
        "platform": platform,
        "raw_excerpt": "\n".join(text.splitlines()[:18]).strip(),
    }


class CiscoModuleService:
    def _target(self, context: dict[str, Any]) -> str:
        cfg = dict(context.get("cfg") or {})
        cisco_cfg = dict(cfg.get("cisco_switch") or {})
        return str(cisco_cfg.get("ip") or cfg.get("ip_plan", {}).get("switch") or "").strip()

    def _username(self, context: dict[str, Any]) -> str:
        cfg = dict(context.get("cfg") or {})
        cisco_cfg = dict(cfg.get("cisco_switch") or {})
        return str(cisco_cfg.get("username") or "admin").strip()

    def _password(self, context: dict[str, Any]) -> str:
        cfg = dict(context.get("cfg") or {})
        cisco_cfg = dict(cfg.get("cisco_switch") or {})
        return str(cisco_cfg.get("password") or "")

    def _status_payload(self, context: dict[str, Any]) -> dict[str, Any]:
        cfg = dict(context.get("cfg") or {})
        cisco_cfg = dict(cfg.get("cisco_switch") or {})
        return {
            "target": self._target(context),
            "username": self._username(context),
            "hostname": str(cisco_cfg.get("hostname") or "").strip(),
            "last_discovered_version": str(cisco_cfg.get("last_discovered_version") or "").strip(),
            "last_discovered_at": str(cisco_cfg.get("last_discovered_at") or "").strip(),
            "last_discovery_error": str(cisco_cfg.get("last_discovery_error") or "").strip(),
            "last_show_version": str(cisco_cfg.get("last_show_version") or "").strip(),
        }

    def _run_show_version(self, context: dict[str, Any]) -> CiscoDiscoveryResult:
        target = self._target(context)
        username = self._username(context)
        password = self._password(context)
        warnings: list[str] = []
        if not target:
            raise CiscoModuleError("Cisco switch IP is not set.")
        if not username:
            raise CiscoModuleError("Cisco switch username is not set.")
        if not password:
            raise CiscoModuleError("Cisco switch password is not set.")
        if not shutil.which("sshpass"):
            raise CiscoModuleError("sshpass is required for Cisco SSH discovery.")

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
            "ConnectTimeout=10",
            f"{username}@{target}",
            "show version",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=25)
        except subprocess.TimeoutExpired as exc:
            raise CiscoModuleError(f"Cisco SSH discovery timed out for {target}.") from exc
        stdout = str(proc.stdout or "")
        stderr = str(proc.stderr or "")
        if proc.returncode != 0:
            message = (stderr or stdout or f"Cisco SSH command failed ({proc.returncode}).").splitlines()[0]
            raise CiscoModuleError(message)
        parsed = parse_cisco_show_version(stdout)
        if not parsed.get("version"):
            warnings.append("Cisco version could not be parsed from show version output.")
        return CiscoDiscoveryResult(
            ok=True,
            target=target,
            username=username,
            version=str(parsed.get("version") or ""),
            hostname=str(parsed.get("hostname") or ""),
            model=str(parsed.get("model") or ""),
            platform=str(parsed.get("platform") or ""),
            raw_excerpt=str(parsed.get("raw_excerpt") or ""),
            warnings=warnings,
        )

    def discover(self, context: dict[str, Any]) -> dict[str, Any]:
        status = self._status_payload(context)
        try:
            result = self._run_show_version(context)
            payload = result.as_dict()
            payload["module"] = "cisco"
            payload["action"] = "discover"
            payload["status"] = status
            return payload
        except CiscoModuleError as exc:
            return {
                "module": "cisco",
                "action": "discover",
                "ok": False,
                "target": status["target"],
                "username": status["username"],
                "error": str(exc),
                "warnings": [],
                "status": status,
            }

    def plan(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "cisco", "action": "plan", "ok": True, "status": self._status_payload(context)}

    def validate(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "cisco", "action": "validate", "ok": True, "status": self._status_payload(context)}

    def preview(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "cisco", "action": "preview", "ok": True, "status": self._status_payload(context)}

    def apply(self, context: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
        return {"module": "cisco", "action": "apply", "ok": True, "status": self._status_payload(context)}

    def status(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"module": "cisco", "action": "status", "ok": True, "status": self._status_payload(context)}

    def repair(self, context: dict[str, Any], issue_id: str) -> dict[str, Any]:
        return {"module": "cisco", "action": "repair", "issue_id": issue_id, "ok": True, "status": self._status_payload(context)}
