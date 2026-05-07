from __future__ import annotations

from pydantic import BaseModel


class NetAppModuleContext(BaseModel):
    module_name: str = "netapp"

