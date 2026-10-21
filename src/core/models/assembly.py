from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from core.models.catalog import HardwarePart


class AssemblyMode(str, Enum):
    IDLE_MONITORING = "IDLE_MONITORING"
    COUNTING = "COUNTING"
    INSPECTING_PART = "INSPECTING_PART"
    INSPECTING_ORIENTATION = "INSPECTING_ORIENTATION"


class AssemblyState(BaseModel):
    productId: str
    currentStep: int = Field(ge=1)
    requiredHardware: list[HardwarePart] = Field(default_factory=list)
    stepCompleted: bool = False
    activeMode: AssemblyMode = AssemblyMode.IDLE_MONITORING


class SessionCreateRequest(BaseModel):
    productId: str


class SessionUpdateRequest(BaseModel):
    currentStep: int | None = Field(default=None, ge=1)
    stepCompleted: bool | None = None
    activeMode: AssemblyMode | None = None


class SessionResponse(BaseModel):
    sessionId: str
    state: AssemblyState
