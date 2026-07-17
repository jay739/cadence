from __future__ import annotations

from pydantic import BaseModel


class IntegrationStatus(BaseModel):
    configured: bool
    reachable: bool | None = None


class HealthOut(BaseModel):
    version: str
    mode: str
    database_ok: bool
    integrations: dict[str, IntegrationStatus]
