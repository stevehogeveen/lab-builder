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


def run_checked(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=True,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
    )


def extract_iso_file(base_iso: Path, iso_path: str, dest_path: Path) -> bool:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["7zz", "e", "-y", str(base_iso), iso_path, f"-o{dest_path.parent}"],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return False
    extracted = dest_path.parent / Path(iso_path).name
    if not extracted.exists():
        return False
    extracted.replace(dest_path)
    return True


def write_boot_report(iso_path: Path, report_path: Path) -> None:
    result = run_checked(
        ["xorriso", "-indev", str(iso_path), "-report_el_torito", "plain"]
    )
    system_area = run_checked(
        ["xorriso", "-indev", str(iso_path), "-report_system_area", "plain"]
    )
    report_path.write_text(
        "# El Torito boot report\n"
        + result.stdout
        + "\n# System area report\n"
        + system_area.stdout,
        encoding="utf-8",
    )


def build_custom_iso(spec: EsxiBuildSpec) -> Path:
    require_tool("7zz")
    require_tool("xorriso")

    run_dir = Path("artifacts/exports/esxi-isos") / spec.kit_name / spec.output_name
    run_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        stage_dir = Path(tmp) / "patch"
        stage_dir.mkdir(parents=True, exist_ok=True)

        ks_path = stage_dir / "KS.CFG"
        ks_path.write_text(build_kickstart(spec), encoding="utf-8")

        boot_cfg = stage_dir / "BOOT.CFG"
        efi_boot_cfg = stage_dir / "EFI" / "BOOT" / "BOOT.CFG"

        if not extract_iso_file(spec.base_iso_path, "BOOT.CFG", boot_cfg):
            raise RuntimeError("BOOT.CFG was not found in the base ESXi ISO.")
        patch_boot_cfg(boot_cfg)

        efi_present = extract_iso_file(spec.base_iso_path, "EFI/BOOT/BOOT.CFG", efi_boot_cfg)
        if efi_present:
            patch_boot_cfg(efi_boot_cfg)

        output_iso = run_dir / f"{spec.output_name}.iso"
        if output_iso.exists():
            output_iso.unlink()

        base_boot_report = run_dir / "base-boot-report.txt"
        output_boot_report = run_dir / "output-boot-report.txt"
        write_boot_report(spec.base_iso_path, base_boot_report)

        #
        # Preserve the original vendor boot structure by replaying the boot
        # metadata from the source ISO instead of rebuilding a fresh generic
        # El Torito layout from an extracted directory tree.
        #
        xorriso_cmd = [
            "xorriso",
            "-indev",
            str(spec.base_iso_path),
            "-outdev",
            str(output_iso),
            "-boot_image",
            "any",
            "replay",
            "-map",
            str(ks_path),
            "/KS.CFG",
            "-map",
            str(boot_cfg),
            "/BOOT.CFG",
        ]
        if efi_present:
            xorriso_cmd.extend(
                [
                    "-map",
                    str(efi_boot_cfg),
                    "/EFI/BOOT/BOOT.CFG",
                ]
            )
        xorriso_cmd.extend(["-commit", "-end"])
        run_checked(xorriso_cmd)

        write_boot_report(output_iso, output_boot_report)

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
            "patched_files": [
                "/KS.CFG",
                "/BOOT.CFG",
                *([] if not efi_present else ["/EFI/BOOT/BOOT.CFG"]),
            ],
            "boot_reports": {
                "base": str(base_boot_report),
                "output": str(output_boot_report),
            },
        }
        (run_dir / "build-summary.yml").write_text(
            yaml.safe_dump(summary, sort_keys=False),
            encoding="utf-8",
        )

    return output_iso
