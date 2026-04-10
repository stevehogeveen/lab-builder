#!/usr/bin/env python3
from pathlib import Path
from app.esxi.models import EsxiBuildSpec
from app.esxi.builder import build_custom_iso

spec = EsxiBuildSpec(
    kit_name="manual-test",
    base_iso_path=Path("media/esxi/base/VMware-ESXi.iso"),
    output_name="custom-esxi",
    hostname="esxi01.lab.local",
    management_ip="192.168.1.50",
    subnet_mask="255.255.255.0",
    gateway="192.168.1.1",
    dns_servers=["1.1.1.1", "8.8.8.8"],
    root_password="ChangeMe123!",
    vlan_id="",
    ntp_server="pool.ntp.org",
    enable_ssh=True,
    disable_ipv6=True,
)

iso_path = build_custom_iso(spec)
print(f"Built ISO: {iso_path}")
