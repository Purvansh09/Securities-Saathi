from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class Profile(BaseModel):
    id: UUID
    full_name: str | None
    preferences: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ProfileUpdate(BaseModel):
    full_name: str | None = None
    preferences: dict[str, Any] | None = None
