from __future__ import annotations

from pydantic import BaseModel, Field


class OvfTemplateRegistration(BaseModel):
    directory: str
    name: str = ""
    os_family: str = ""
    descriptor_name: str = ""


class OvfTemplateRecord(BaseModel):
    id: str
    name: str
    os_family: str = ""
    descriptor_path: str
    files: list[dict] = Field(default_factory=list)
