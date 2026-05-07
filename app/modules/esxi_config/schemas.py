from __future__ import annotations

from pydantic import BaseModel


class EsxiConfigModuleContext(BaseModel):
    module_name: str = "esxi_config"

