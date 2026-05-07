from __future__ import annotations

from pydantic import BaseModel


class QnapModuleContext(BaseModel):
    module_name: str = "qnap"

