from __future__ import annotations

from pydantic import BaseModel


class IloModuleContext(BaseModel):
    module_name: str = "ilo"

