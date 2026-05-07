from __future__ import annotations

from pydantic import BaseModel


class StorageModuleContext(BaseModel):
    module_name: str = "storage"

