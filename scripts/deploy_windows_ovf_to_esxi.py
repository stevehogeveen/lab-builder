#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import main
from app.windows import VsphereClient, VsphereConfig, WindowsInterfaceError


DEFAULT_OVF = Path(
    "media/OVF_Templates/DepOps_W2K22_Template_VMware7.0_Feb2025-1.0/"
    "DepOps_W2K22_Template_VMware7.0_Feb2025-v1.0.ovf"
)


def main_cli() -> int:
    cfg = main.load_kit_config()
    esxi = cfg.get("esxi") or {}
    windows = cfg.get("windows") or {}
    host = str(esxi.get("management_ip") or "").strip()
    password = str(esxi.get("root_password") or "")
    if not host or not password:
        raise WindowsInterfaceError("Current kit needs ESXi management IP and root password before OVF deployment.")

    ovf_path = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DEFAULT_OVF
    vm_name = str(windows.get("vm_name") or "win2022-01").strip()
    datastore = str(windows.get("vsphere_datastore") or "datastore1").strip()
    network = str(windows.get("vsphere_network") or "VM Network").strip()

    client = VsphereClient(VsphereConfig(host=host, username="root", password=password, verify_tls=False))
    portgroup = client.ensure_standard_portgroup(name=network, vswitch_name="vSwitch0", vlan_id=0)
    print(f"portgroup {network}: {'created' if portgroup.get('changed') else 'already present'}")

    def progress(event: dict) -> None:
        percent = int(event.get("percent") or 0)
        message = str(event.get("message") or "").strip()
        print(f"deploy {percent:3d}% {message}".rstrip(), flush=True)

    try:
        result = client.deploy_ovf(
            ovf_path=ovf_path,
            vm_name=vm_name,
            datastore_name=datastore,
            network_name=network,
            ovf_network_name="VM Network",
            disk_provisioning="thin",
            power_on=False,
            progress=progress,
        )
    except WindowsInterfaceError as exc:
        print(f"pyVmomi NFC import failed; falling back to standalone ESXi SSH deploy: {str(exc).splitlines()[0]}", flush=True)
        result = client.deploy_ovf_via_esxi_ssh(
            ovf_path=ovf_path,
            vm_name=vm_name,
            datastore_name=datastore,
            network_name=network,
            power_on=False,
            progress=progress,
        )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
