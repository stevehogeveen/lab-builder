from __future__ import annotations

from pydantic import BaseModel


class EsxiInstallModuleContext(BaseModel):
    module_name: str = "esxi_install"

