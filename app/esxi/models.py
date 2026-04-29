from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EsxiBuildSpec:
    kit_name: str
    base_iso_path: Path
    output_name: str
    hostname: str
    management_ip: str
    subnet_mask: str
    gateway: str
    dns_servers: list[str] = field(default_factory=list)
    root_password: str = ""
    vlan_id: str = ""
    ntp_server: str = ""
    enable_ssh: bool = True
    disable_ipv6: bool = True
    esxi_version: str = "7"
