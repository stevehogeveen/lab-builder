from app.windows import VsphereClient


def test_build_standalone_vmx_uses_esxi_registerable_defaults():
    vmx = VsphereClient._build_standalone_vmx(
        vm_name="win2022-01",
        disk_name="win2022-01.vmdk",
        nvram_name="win2022-01.nvram",
        network_name="VM Network",
    )

    assert 'displayName = "win2022-01"' in vmx
    assert 'firmware = "efi"' in vmx
    assert 'efi.secureBoot.enabled = "TRUE"' in vmx
    assert 'pciBridge4.virtualDev = "pcieRootPort"' in vmx
    assert 'scsi0:0.fileName = "win2022-01.vmdk"' in vmx
    assert 'ethernet0.networkName = "VM Network"' in vmx
    assert "scsi0.pciSlotNumber" not in vmx
    assert "ethernet0.pciSlotNumber" not in vmx
