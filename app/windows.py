from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
import ssl
import subprocess
import threading
import time
from typing import Any
from urllib.parse import unquote, urlparse

from app.ovf import inspect_ovf_source


class WindowsInterfaceError(Exception):
    pass


def build_deployment_preview(plan: dict[str, Any], source_summary: dict[str, Any]) -> dict[str, Any]:
    source_files = list(source_summary.get("files") or [])
    sidecar_files = [item for item in source_files if item.get("role") == "referenced"]
    vm_name = str(plan.get("vm_name") or source_summary.get("vm_name") or "").strip()
    target_network = str(plan.get("network") or "").strip()
    ovf_networks = [str(item) for item in source_summary.get("network_names") or [] if str(item).strip()]
    warnings: list[str] = []
    if target_network and ovf_networks and target_network not in ovf_networks:
        warnings.append(f"Saved VM network '{target_network}' does not match OVF network(s): {', '.join(ovf_networks)}.")
    preview = {
        "action": "Deploy OVF/OVA template",
        "mode": "dry_run",
        "creates_vm": False,
        "source": {
            "name": source_summary.get("name") or Path(str(plan.get("image_path") or "")).name,
            "path": source_summary.get("path") or plan.get("image_path") or "",
            "kind": source_summary.get("kind") or plan.get("image_kind") or "",
            "total_size_display": source_summary.get("total_size_display") or "",
            "file_count": len(source_files),
            "sidecar_count": len(sidecar_files),
        },
        "template": {
            "vm_name": source_summary.get("vm_name") or "",
            "os_description": source_summary.get("os_description") or "",
            "hardware_version": source_summary.get("hardware_version") or "",
            "cpu_count": source_summary.get("cpu_count") or "",
            "memory_mb": source_summary.get("memory_mb") or "",
            "disk_capacity": source_summary.get("disk_capacity") or "",
            "network_names": ovf_networks,
        },
        "target": {
            "vm_name": vm_name,
            "vsphere_host": str(plan.get("vsphere_host") or ""),
            "datacenter": str(plan.get("datacenter") or ""),
            "datastore": str(plan.get("datastore") or ""),
            "network": target_network,
            "folder": str(plan.get("folder") or ""),
            "resource_pool": str(plan.get("resource_pool") or ""),
            "guest_ip": str(plan.get("target_ip") or ""),
            "gateway": str(plan.get("gateway") or ""),
            "dns_servers": list(plan.get("dns_servers") or []),
        },
        "steps": [
            "Validate OVA/OVF source and sidecar files.",
            "Connect to the saved vSphere or standalone ESXi endpoint.",
            "Map the OVF network to the saved VM network.",
            "Import the VM to the selected datastore and inventory location.",
            "Leave the VM powered off until an explicit apply flow is added.",
        ],
        "warnings": warnings,
    }
    return preview


@dataclass
class VsphereConfig:
    host: str
    username: str
    password: str
    port: int = 443
    verify_tls: bool = False


@dataclass
class WinRMConfig:
    host: str
    username: str
    password: str
    port: int = 5986
    use_https: bool = True
    transport: str = "ntlm"
    server_cert_validation: str = "ignore"


class VsphereClient:
    def __init__(self, config: VsphereConfig) -> None:
        self.config = config
        self._service_instance: Any = None

    def connect(self) -> Any:
        try:
            from pyVim.connect import SmartConnect, SmartConnectNoSSL
        except Exception as exc:
            raise WindowsInterfaceError("pyvmomi is not installed in this environment.") from exc
        connector = SmartConnect if self.config.verify_tls else SmartConnectNoSSL
        try:
            self._service_instance = connector(
                host=self.config.host,
                user=self.config.username,
                pwd=self.config.password,
                port=self.config.port,
            )
            return self._service_instance
        except Exception as exc:
            raise WindowsInterfaceError(f"vSphere connection failed: {str(exc).splitlines()[0]}") from exc

    def disconnect(self) -> None:
        if not self._service_instance:
            return
        try:
            from pyVim.connect import Disconnect

            Disconnect(self._service_instance)
        except Exception:
            pass
        finally:
            self._service_instance = None

    def inventory_summary(self) -> dict[str, Any]:
        si = self.connect()
        try:
            content = si.RetrieveContent()
            about = getattr(content, "about", None)
            root = getattr(content, "rootFolder", None)
            datacenters = [child for child in getattr(root, "childEntity", []) or [] if getattr(child, "name", "")]
            return {
                "connected": True,
                "product": getattr(about, "fullName", "") if about else "",
                "api_version": getattr(about, "apiVersion", "") if about else "",
                "datacenters": [getattr(item, "name", "") for item in datacenters],
            }
        finally:
            self.disconnect()

    @staticmethod
    def _wait_for_lease_ready(lease: Any, *, timeout_seconds: int = 300) -> None:
        try:
            from pyVmomi import vim
        except Exception as exc:
            raise WindowsInterfaceError("pyvmomi is not installed in this environment.") from exc
        deadline = time.monotonic() + timeout_seconds
        while lease.state == vim.HttpNfcLease.State.initializing:
            if time.monotonic() > deadline:
                raise WindowsInterfaceError("Timed out waiting for the OVF upload lease to become ready.")
            time.sleep(1)
        if lease.state == vim.HttpNfcLease.State.error:
            error = getattr(lease, "error", None)
            message = getattr(error, "msg", None) or str(error or "OVF upload lease failed.")
            raise WindowsInterfaceError(message)
        if lease.state != vim.HttpNfcLease.State.ready:
            raise WindowsInterfaceError(f"OVF upload lease entered unexpected state: {lease.state}")

    @staticmethod
    def _wait_for_task(task: Any, *, timeout_seconds: int = 120) -> None:
        try:
            from pyVmomi import vim
        except Exception as exc:
            raise WindowsInterfaceError("pyvmomi is not installed in this environment.") from exc
        deadline = time.monotonic() + timeout_seconds
        while task.info.state in {vim.TaskInfo.State.queued, vim.TaskInfo.State.running}:
            if time.monotonic() > deadline:
                raise WindowsInterfaceError("Timed out waiting for vSphere task completion.")
            time.sleep(1)
        if task.info.state == vim.TaskInfo.State.error:
            error = getattr(task.info, "error", None)
            message = getattr(error, "msg", None) or str(error or "vSphere task failed.")
            raise WindowsInterfaceError(message)

    @staticmethod
    def _walk_inventory(root: Any, wanted_type: Any) -> list[Any]:
        found: list[Any] = []

        def walk(entity: Any) -> None:
            if isinstance(entity, wanted_type):
                found.append(entity)
            for attr in ("childEntity", "vmFolder", "hostFolder", "networkFolder", "host"):
                children = getattr(entity, attr, None)
                if children is None:
                    continue
                if not isinstance(children, list):
                    children = [children]
                for child in children:
                    walk(child)

        walk(root)
        return found

    @staticmethod
    def _find_named(items: list[Any], name: str, label: str) -> Any:
        desired = str(name or "").strip()
        if desired:
            for item in items:
                if getattr(item, "name", "") == desired:
                    return item
            raise WindowsInterfaceError(f"{label} was not found: {desired}")
        if not items:
            raise WindowsInterfaceError(f"No {label.lower()} objects were found.")
        return items[0]

    @staticmethod
    def _find_existing_vm(content: Any, vm_name: str) -> Any | None:
        try:
            from pyVmomi import vim
        except Exception as exc:
            raise WindowsInterfaceError("pyvmomi is not installed in this environment.") from exc
        view = content.viewManager.CreateContainerView(content.rootFolder, [vim.VirtualMachine], True)
        try:
            for vm in view.view:
                if getattr(vm, "name", "") == vm_name:
                    return vm
        finally:
            view.Destroy()
        return None

    def _connect_for_import(self) -> Any:
        try:
            from pyVim.connect import SmartConnect
        except Exception as exc:
            raise WindowsInterfaceError("pyvmomi is not installed in this environment.") from exc
        context = None if self.config.verify_tls else ssl._create_unverified_context()
        try:
            self._service_instance = SmartConnect(
                host=self.config.host,
                user=self.config.username,
                pwd=self.config.password,
                port=self.config.port,
                sslContext=context,
            )
            return self._service_instance
        except Exception as exc:
            raise WindowsInterfaceError(f"vSphere connection failed: {str(exc).splitlines()[0]}") from exc

    def ensure_standard_portgroup(self, *, name: str, vswitch_name: str = "vSwitch0", vlan_id: int = 0) -> dict[str, Any]:
        try:
            from pyVmomi import vim
        except Exception as exc:
            raise WindowsInterfaceError("pyvmomi is not installed in this environment.") from exc
        si = self._connect_for_import()
        try:
            content = si.RetrieveContent()
            hosts = self._walk_inventory(content.rootFolder, vim.HostSystem)
            host = self._find_named(hosts, "", "ESXi host")
            existing = [pg.spec.name for pg in host.config.network.portgroup]
            if name in existing:
                return {"changed": False, "name": name, "vswitch": vswitch_name, "vlan_id": vlan_id}
            spec = vim.host.PortGroup.Specification()
            spec.name = name
            spec.vlanId = int(vlan_id)
            spec.vswitchName = vswitch_name
            spec.policy = vim.host.NetworkPolicy()
            host.configManager.networkSystem.AddPortGroup(spec)
            return {"changed": True, "name": name, "vswitch": vswitch_name, "vlan_id": vlan_id}
        finally:
            self.disconnect()

    def _ssh_base_command(self) -> list[str]:
        if not self.config.password:
            raise WindowsInterfaceError("ESXi SSH password is required for standalone OVF deployment.")
        return [
            "sshpass",
            "-p",
            self.config.password,
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=15",
            f"{self.config.username}@{self.config.host}",
        ]

    def _scp_base_command(self) -> list[str]:
        if not self.config.password:
            raise WindowsInterfaceError("ESXi SSH password is required for standalone OVF deployment.")
        return [
            "sshpass",
            "-p",
            self.config.password,
            "scp",
            "-O",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
        ]

    def _run_esxi_ssh(self, command: str, *, timeout_seconds: int = 120) -> dict[str, Any]:
        completed = subprocess.run(
            [*self._ssh_base_command(), command],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "ok": completed.returncode == 0,
        }

    def _copy_to_esxi_datastore(self, source: Path, remote_directory: str, *, timeout_seconds: int = 7200) -> dict[str, Any]:
        completed = subprocess.run(
            [*self._scp_base_command(), str(source), f"{self.config.username}@{self.config.host}:{remote_directory}/"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "ok": completed.returncode == 0,
        }

    def _remote_esxi_file_size(self, remote_path: str) -> int | None:
        result = self._run_esxi_ssh(f"stat -c '%s' {shlex.quote(remote_path)} 2>/dev/null || true", timeout_seconds=60)
        if not result["ok"]:
            return None
        text = str(result["stdout"] or "").strip()
        if not text.isdigit():
            return None
        return int(text)

    def _ensure_standard_portgroup_ssh(self, *, name: str, vswitch_name: str = "vSwitch0", vlan_id: int = 0) -> None:
        name_q = shlex.quote(name)
        vswitch_q = shlex.quote(vswitch_name)
        command = (
            f"esxcli network vswitch standard portgroup add -p {name_q} -v {vswitch_q} 2>/tmp/lab-builder-portgroup.err || true; "
            f"esxcli network vswitch standard portgroup set -p {name_q} --vlan-id {int(vlan_id)}"
        )
        result = self._run_esxi_ssh(command, timeout_seconds=60)
        if not result["ok"]:
            raise WindowsInterfaceError(f"Could not ensure ESXi port group {name}: {(result['stderr'] or result['stdout']).strip()}")

    @staticmethod
    def _build_standalone_vmx(*, vm_name: str, disk_name: str, nvram_name: str, network_name: str) -> str:
        lines = [
            '.encoding = "UTF-8"',
            'config.version = "8"',
            'virtualHW.version = "19"',
            f'displayName = "{vm_name}"',
            'guestOS = "windows2019srvNext-64"',
            'firmware = "efi"',
            'efi.secureBoot.enabled = "TRUE"',
            f'nvram = "{nvram_name}"',
            'memSize = "4096"',
            'numvcpus = "1"',
            'cpuid.coresPerSocket = "1"',
            'scsi0.present = "TRUE"',
            'scsi0.virtualDev = "lsisas1068"',
            'scsi0.pciSlotNumber = "160"',
            'scsi0:0.present = "TRUE"',
            f'scsi0:0.fileName = "{disk_name}"',
            'scsi0:0.deviceType = "scsi-hardDisk"',
            'sata0.present = "TRUE"',
            'sata0.pciSlotNumber = "32"',
            'sata0:0.present = "TRUE"',
            'sata0:0.deviceType = "cdrom-raw"',
            'sata0:0.fileName = "emptyBackingString"',
            'sata0:0.startConnected = "FALSE"',
            'usb_xhci.present = "TRUE"',
            'usb_xhci.pciSlotNumber = "224"',
            'ethernet0.present = "TRUE"',
            'ethernet0.virtualDev = "e1000e"',
            f'ethernet0.networkName = "{network_name}"',
            'ethernet0.addressType = "generated"',
            'ethernet0.wakeOnPcktRcv = "TRUE"',
            'ethernet0.pciSlotNumber = "192"',
            'ethernet0.allowGuestConnectionControl = "TRUE"',
            'svga.present = "TRUE"',
            'svga.autodetect = "TRUE"',
            'svga.vramSize = "268435456"',
            'mks.enable3d = "FALSE"',
        ]
        return "\n".join(lines) + "\n"

    def deploy_ovf_via_esxi_ssh(
        self,
        *,
        ovf_path: str | Path,
        vm_name: str,
        datastore_name: str = "datastore1",
        network_name: str = "VM Network",
        power_on: bool = False,
        cleanup_sources: bool = True,
        progress: Any | None = None,
    ) -> dict[str, Any]:
        source = Path(str(ovf_path)).expanduser().resolve()
        summary = inspect_ovf_source(source)
        if source.suffix.lower() != ".ovf":
            raise WindowsInterfaceError("Standalone ESXi SSH deployment currently requires an OVF descriptor directory.")
        if summary.get("warnings"):
            raise WindowsInterfaceError("OVF source is not ready: " + "; ".join(str(item) for item in summary.get("warnings") or []))
        if not vm_name:
            raise WindowsInterfaceError("VM name is required for OVF deployment.")

        remote_directory = f"/vmfs/volumes/{datastore_name}/{vm_name}"
        remote_directory_q = shlex.quote(remote_directory)

        def report(percent: int, message: str) -> None:
            if progress:
                progress({"percent": max(0, min(100, int(percent))), "message": message})

        existing = self._run_esxi_ssh("vim-cmd vmsvc/getallvms", timeout_seconds=60)
        if existing["ok"] and any(line.split()[1:2] == [vm_name] for line in existing["stdout"].splitlines() if line[:1].isdigit()):
            return {"ok": True, "changed": False, "vm_name": vm_name, "message": "VM already exists.", "warnings": []}

        report(3, f"Ensuring ESXi network port group {network_name}.")
        self._ensure_standard_portgroup_ssh(name=network_name, vswitch_name="vSwitch0", vlan_id=0)

        report(5, f"Creating datastore directory {remote_directory}.")
        mkdir = self._run_esxi_ssh(f"mkdir -p {remote_directory_q}", timeout_seconds=60)
        if not mkdir["ok"]:
            raise WindowsInterfaceError(f"Could not create ESXi datastore directory: {(mkdir['stderr'] or mkdir['stdout']).strip()}")

        referenced = [Path(str(item.get("path") or "")).resolve() for item in summary.get("files", []) if item.get("role") == "referenced"]
        vmdk_files = [path for path in referenced if path.suffix.lower() == ".vmdk"]
        nvram_files = [path for path in referenced if path.suffix.lower() == ".nvram"]
        if not vmdk_files:
            raise WindowsInterfaceError("OVF descriptor does not reference a VMDK file.")
        source_vmdk = vmdk_files[0]
        source_nvram = nvram_files[0] if nvram_files else None

        files_to_copy = [source_vmdk]
        if source_nvram:
            files_to_copy.append(source_nvram)
        copied: list[str] = []
        for index, file_path in enumerate(files_to_copy, start=1):
            remote_path = f"{remote_directory}/{file_path.name}"
            existing_size = self._remote_esxi_file_size(remote_path)
            if existing_size == file_path.stat().st_size:
                report(10 + (index - 1) * 10, f"Using already uploaded {file_path.name}.")
            else:
                report(10 + (index - 1) * 10, f"Uploading {file_path.name} to ESXi datastore.")
                upload = self._copy_to_esxi_datastore(file_path, remote_directory)
                if not upload["ok"]:
                    raise WindowsInterfaceError(f"ESXi file upload failed for {file_path.name}: {(upload['stderr'] or upload['stdout']).strip()}")
            copied.append(file_path.name)

        converted_disk = f"{vm_name}.vmdk"
        source_vmdk_remote = f"{remote_directory}/{source_vmdk.name}"
        converted_vmdk_remote = f"{remote_directory}/{converted_disk}"
        report(55, "Converting source stream VMDK to ESXi thin disk.")
        convert = self._run_esxi_ssh(
            "vmkfstools -i "
            f"{shlex.quote(source_vmdk_remote)} "
            "-d thin "
            f"{shlex.quote(converted_vmdk_remote)}",
            timeout_seconds=7200,
        )
        if not convert["ok"]:
            raise WindowsInterfaceError(f"VMDK conversion failed: {(convert['stderr'] or convert['stdout']).strip()}")

        nvram_name = f"{vm_name}.nvram"
        if source_nvram:
            report(75, "Installing NVRAM sidecar.")
            copy_nvram = self._run_esxi_ssh(
                f"cp {shlex.quote(f'{remote_directory}/{source_nvram.name}')} {shlex.quote(f'{remote_directory}/{nvram_name}')}",
                timeout_seconds=60,
            )
            if not copy_nvram["ok"]:
                raise WindowsInterfaceError(f"NVRAM install failed: {(copy_nvram['stderr'] or copy_nvram['stdout']).strip()}")

        vmx_name = f"{vm_name}.vmx"
        vmx_remote = f"{remote_directory}/{vmx_name}"
        vmx_content = self._build_standalone_vmx(vm_name=vm_name, disk_name=converted_disk, nvram_name=nvram_name, network_name=network_name)
        report(82, "Writing VMX inventory definition.")
        write_vmx = self._run_esxi_ssh(f"cat > {shlex.quote(vmx_remote)} <<'EOF'\n{vmx_content}EOF", timeout_seconds=60)
        if not write_vmx["ok"]:
            raise WindowsInterfaceError(f"VMX write failed: {(write_vmx['stderr'] or write_vmx['stdout']).strip()}")

        report(90, "Registering VM in ESXi inventory.")
        register = self._run_esxi_ssh(f"vim-cmd solo/registervm {shlex.quote(vmx_remote)}", timeout_seconds=120)
        if not register["ok"]:
            raise WindowsInterfaceError(f"VM registration failed: {(register['stderr'] or register['stdout']).strip()}")
        vm_id = register["stdout"].strip().splitlines()[-1].strip() if register["stdout"].strip() else ""

        powered_on = False
        if power_on and vm_id:
            report(96, "Powering on VM.")
            power = self._run_esxi_ssh(f"vim-cmd vmsvc/power.on {shlex.quote(vm_id)}", timeout_seconds=120)
            if not power["ok"]:
                raise WindowsInterfaceError(f"VM registered but power-on failed: {(power['stderr'] or power['stdout']).strip()}")
            powered_on = True

        removed_sources: list[str] = []
        if cleanup_sources:
            removable = [f"{remote_directory}/{source_vmdk.name}"]
            if source_nvram:
                removable.append(f"{remote_directory}/{source_nvram.name}")
            cleanup = self._run_esxi_ssh("rm -f " + " ".join(shlex.quote(path) for path in removable), timeout_seconds=120)
            if cleanup["ok"]:
                removed_sources = [Path(path).name for path in removable]

        report(100, "Standalone ESXi OVF deployment complete.")
        return {
            "ok": True,
            "changed": True,
            "method": "esxi_ssh",
            "vm_id": vm_id,
            "vm_name": vm_name,
            "datastore": datastore_name,
            "network": network_name,
            "remote_directory": remote_directory,
            "vmx": vmx_remote,
            "converted_disk": converted_disk,
            "files_uploaded": copied,
            "removed_upload_sources": removed_sources,
            "powered_on": powered_on,
            "warnings": [],
        }

    def deploy_ovf(
        self,
        *,
        ovf_path: str | Path,
        vm_name: str,
        datastore_name: str = "",
        network_name: str = "VM Network",
        ovf_network_name: str = "VM Network",
        disk_provisioning: str = "thin",
        power_on: bool = False,
        progress: Any | None = None,
    ) -> dict[str, Any]:
        try:
            from pyVmomi import vim
        except Exception as exc:
            raise WindowsInterfaceError("pyvmomi is required for live OVF deployment.") from exc
        source = Path(str(ovf_path)).expanduser().resolve()
        summary = inspect_ovf_source(source)
        if summary.get("warnings"):
            raise WindowsInterfaceError("OVF source is not ready: " + "; ".join(str(item) for item in summary.get("warnings") or []))
        if not vm_name:
            raise WindowsInterfaceError("VM name is required for OVF deployment.")
        si = self._connect_for_import()
        lease = None
        keepalive_stop = threading.Event()
        try:
            content = si.RetrieveContent()
            session_cookie = str(getattr(getattr(si, "_stub", None), "cookie", "") or "").strip()
            if self._find_existing_vm(content, vm_name):
                return {"ok": True, "changed": False, "vm_name": vm_name, "message": "VM already exists.", "warnings": []}
            datacenters = self._walk_inventory(content.rootFolder, vim.Datacenter)
            datacenter = self._find_named(datacenters, "", "Datacenter")
            compute_resources = self._walk_inventory(datacenter.hostFolder, vim.ComputeResource)
            compute = self._find_named(compute_resources, "", "Compute resource")
            hosts = list(getattr(compute, "host", []) or [])
            host = self._find_named(hosts, "", "ESXi host")
            datastore = self._find_named(list(datacenter.datastore or []), datastore_name, "Datastore")
            networks = self._walk_inventory(content.rootFolder, vim.Network)
            network = self._find_named(networks, network_name, "Network")

            params = vim.OvfManager.CreateImportSpecParams()
            params.entityName = vm_name
            params.diskProvisioning = disk_provisioning or "thin"
            params.ipAllocationPolicy = "dhcpPolicy"
            params.ipProtocol = "IPv4"
            mapping = vim.OvfManager.NetworkMapping()
            mapping.name = ovf_network_name or network_name
            mapping.network = network
            params.networkMapping = [mapping]
            result = content.ovfManager.CreateImportSpec(source.read_text(encoding="utf-8"), compute.resourcePool, datastore, params)
            errors = [str(getattr(item, "msg", item)) for item in list(result.error or [])]
            warnings = [str(getattr(item, "msg", item)) for item in list(result.warning or [])]
            if errors or not result.importSpec:
                raise WindowsInterfaceError("OVF import spec failed: " + "; ".join(errors or ["No import spec was returned."]))

            file_items = {str(item.deviceId): item for item in list(result.fileItem or [])}
            lease = compute.resourcePool.ImportVApp(result.importSpec, datacenter.vmFolder, host)
            self._wait_for_lease_ready(lease)

            device_urls = list(lease.info.deviceUrl or [])
            upload_total = 0
            upload_paths: dict[str, Path] = {}
            for device in device_urls:
                file_item = file_items.get(str(device.importKey))
                relative_path = str(getattr(file_item, "path", "") or Path(unquote(urlparse(str(device.url)).path)).name)
                local_path = (source.parent / relative_path).resolve()
                upload_paths[str(device.importKey)] = local_path
                upload_total += local_path.stat().st_size
            uploaded = 0
            lock = threading.Lock()

            def report(percent: int, message: str = "") -> None:
                try:
                    lease.HttpNfcLeaseProgress(max(0, min(100, int(percent))))
                except Exception:
                    pass
                if progress:
                    progress({"percent": max(0, min(100, int(percent))), "message": message})

            def keepalive() -> None:
                while not keepalive_stop.wait(10):
                    with lock:
                        percent = int((uploaded / upload_total) * 100) if upload_total else 0
                    report(percent, "OVF upload in progress.")

            thread = threading.Thread(target=keepalive, daemon=True)
            thread.start()

            for device in device_urls:
                local_path = upload_paths[str(device.importKey)]
                upload_url = str(device.url).replace("*", self.config.host)
                content_type = "application/x-vnd.vmware-streamVmdk" if local_path.suffix.lower() == ".vmdk" else "application/octet-stream"
                report(int((uploaded / upload_total) * 100) if upload_total else 0, f"Uploading {local_path.name}")
                curl_cmd = [
                    "curl",
                    "--fail",
                    "--silent",
                    "--show-error",
                    "--http1.1",
                    "--upload-file",
                    str(local_path),
                    "--header",
                    f"Content-Type: {content_type}",
                    "--header",
                    "Expect:",
                ]
                if session_cookie:
                    curl_cmd.extend(["--cookie", session_cookie])
                if not self.config.verify_tls:
                    curl_cmd.append("--insecure")
                curl_cmd.append(upload_url)
                completed = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=7200, check=False)
                if completed.returncode != 0:
                    detail = (completed.stderr or completed.stdout or "").strip()
                    raise WindowsInterfaceError(f"OVF file upload failed for {local_path.name}: {detail or 'curl exited with error.'}")
                with lock:
                    uploaded += local_path.stat().st_size
                    percent = int((uploaded / upload_total) * 100) if upload_total else 0
                report(percent, f"Uploaded {local_path.name}")

            keepalive_stop.set()
            report(100, "Completing OVF import.")
            lease.HttpNfcLeaseComplete()
            lease = None
            imported_vm = self._find_existing_vm(content, vm_name)
            if power_on and imported_vm:
                self._wait_for_task(imported_vm.PowerOn())
            return {
                "ok": True,
                "changed": True,
                "vm_name": vm_name,
                "datastore": datastore.summary.name,
                "network": network.name,
                "warnings": warnings,
                "files_uploaded": [path.name for path in upload_paths.values()],
                "powered_on": bool(power_on),
            }
        except Exception:
            keepalive_stop.set()
            if lease is not None:
                try:
                    lease.HttpNfcLeaseAbort()
                except Exception:
                    pass
            raise
        finally:
            self.disconnect()

    @staticmethod
    def validate_ovf_inputs(plan: dict[str, Any]) -> dict[str, Any]:
        warnings: list[str] = []
        image_path = str(plan.get("image_path") or "").strip()
        image_kind = str(plan.get("image_kind") or "").strip().lower()
        source_summary: dict[str, Any] = {}
        if image_kind not in {"ova", "ovf"}:
            warnings.append("Windows source image must be OVA or OVF.")
        if not image_path or not Path(image_path).exists():
            warnings.append("Windows source image file is missing.")
        else:
            source_summary = inspect_ovf_source(image_path)
            warnings.extend([item for item in source_summary.get("warnings", []) if item not in warnings])
        labels = {
            "vsphere_host": "vSphere host",
            "vsphere_username": "vSphere username",
            "datastore": "Datastore",
            "network": "VM network",
        }
        for field, label in labels.items():
            if not str(plan.get(field) or "").strip():
                warnings.append(f"{label} is missing.")
        deployment_preview = build_deployment_preview(plan, source_summary) if source_summary else {}
        warnings.extend([item for item in deployment_preview.get("warnings", []) if item not in warnings])
        return {
            "ready": not warnings,
            "warnings": warnings,
            "source_summary": source_summary,
            "deployment_preview": deployment_preview,
        }


class WinRMClient:
    def __init__(self, config: WinRMConfig) -> None:
        self.config = config

    def _endpoint(self) -> str:
        scheme = "https" if self.config.use_https else "http"
        return f"{scheme}://{self.config.host}:{self.config.port}/wsman"

    def probe(self) -> dict[str, Any]:
        try:
            import winrm
        except Exception as exc:
            raise WindowsInterfaceError("pywinrm is not installed in this environment.") from exc
        try:
            session = winrm.Session(
                self._endpoint(),
                auth=(self.config.username, self.config.password),
                transport=self.config.transport,
                server_cert_validation=self.config.server_cert_validation,
            )
            result = session.run_cmd("hostname")
        except Exception as exc:
            raise WindowsInterfaceError(f"WinRM probe failed: {str(exc).splitlines()[0]}") from exc
        stdout = bytes(result.std_out or b"").decode("utf-8", errors="replace").strip()
        stderr = bytes(result.std_err or b"").decode("utf-8", errors="replace").strip()
        return {
            "connected": int(result.status_code) == 0,
            "status_code": int(result.status_code),
            "endpoint": self._endpoint(),
            "stdout": stdout,
            "stderr": stderr,
        }
