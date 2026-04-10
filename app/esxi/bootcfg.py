from pathlib import Path


def patch_boot_cfg(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "ks=cdrom:/KS.CFG" in text:
        return

    lines = []
    for line in text.splitlines():
        if line.startswith("kernelopt="):
            if line.strip() == "kernelopt=":
                line = "kernelopt=ks=cdrom:/KS.CFG"
            else:
                line = f"{line} ks=cdrom:/KS.CFG"
        lines.append(line)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
