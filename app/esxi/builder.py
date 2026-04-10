from pathlib import Path
import shutil
import subprocess
import tempfile
import yaml

from .models import EsxiBuildSpec
from .kickstart import build_kickstart
from .bootcfg import patch_boot_cfg


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"Required tool not found: {name}")


def build_custom_iso(spec: EsxiBuildSpec) -> Path:
    require_tool("7zz")
    require_tool("xorriso")

    run_dir = Path("artifacts/exports/esxi-isos") / spec.kit_name / spec.output_name
    run_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        stage_dir = Path(tmp) / "stage"
        stage_dir.mkdir(parents=True, exist_ok=True)

        subprocess.run(
            ["7zz", "x", str(spec.base_iso_path), f"-o{stage_dir}"],
            check=True,
        )

        ks_path = stage_dir / "KS.CFG"
        ks_path.write_text(build_kickstart(spec), encoding="utf-8")

        boot_cfg = stage_dir / "BOOT.CFG"
        efi_boot_cfg = stage_dir / "EFI" / "BOOT" / "BOOT.CFG"

        if boot_cfg.exists():
            patch_boot_cfg(boot_cfg)
        if efi_boot_cfg.exists():
            patch_boot_cfg(efi_boot_cfg)

        output_iso = run_dir / f"{spec.output_name}.iso"

        subprocess.run(
            [
                "xorriso",
                "-as",
                "mkisofs",
                "-relaxed-filenames",
                "-J",
                "-R",
                "-o",
                str(output_iso),
                "-b",
                "ISOLINUX.BIN",
                "-c",
                "BOOT.CAT",
                "-no-emul-boot",
                "-boot-load-size",
                "4",
                "-boot-info-table",
                "-eltorito-alt-boot",
                "-e",
                "EFI/BOOT/BOOTX64.EFI",
                "-no-emul-boot",
                str(stage_dir),
            ],
            check=True,
        )

        summary = {
            "kit_name": spec.kit_name,
            "base_iso": str(spec.base_iso_path),
            "output_iso": str(output_iso),
            "hostname": spec.hostname,
            "management_ip": spec.management_ip,
            "gateway": spec.gateway,
            "dns_servers": spec.dns_servers,
            "vlan_id": spec.vlan_id,
            "ntp_server": spec.ntp_server,
            "enable_ssh": spec.enable_ssh,
            "disable_ipv6": spec.disable_ipv6,
        }
        (run_dir / "build-summary.yml").write_text(
            yaml.safe_dump(summary, sort_keys=False),
            encoding="utf-8",
        )

    return output_iso
