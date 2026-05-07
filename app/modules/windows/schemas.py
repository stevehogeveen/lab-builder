from __future__ import annotations

from pydantic import BaseModel


class WindowsModuleContext(BaseModel):
    module_name: str = "windows"

