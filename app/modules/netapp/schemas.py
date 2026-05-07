from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class NetAppModuleContext(BaseModel):
    module_name: str = "netapp"
    payload: dict[str, Any] = Field(default_factory=dict)
    cfg: dict[str, Any] = Field(default_factory=dict)


class NetAppActionRequest(BaseModel):
    job: dict[str, Any] = Field(default_factory=dict)


class NetAppRepairRequest(BaseModel):
    issue_id: str
