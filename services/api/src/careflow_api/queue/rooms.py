"""`config/rooms.yaml`: departments, rooms and the levels each room accepts (M1-10).

Loaded and validated when the API starts, so a bad file stops the API instead of stranding a
patient: every level routes to a department that has a room accepting it.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Annotated, Final, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from careflow_api.config import REPO_ROOT

ROOMS_FILE: Final = REPO_ROOT / "config" / "rooms.yaml"
LEVELS: Final = frozenset(range(1, 6))

type Level = Annotated[int, Field(ge=1, le=5)]
type Key = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,31}$")]


class UnknownDepartment(LookupError):
    pass


class UnknownRoom(LookupError):
    pass


class Room(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Key
    #: Shown on the board and in `token.called` (at most 32 characters, as the contract allows).
    name: Annotated[str, Field(min_length=1, max_length=32)]
    accepts_levels: Annotated[frozenset[Level], Field(min_length=1)]


class Department(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Key
    name: Annotated[str, Field(min_length=1, max_length=64)]
    #: Token codes are `<prefix>-<3 digits>`.
    prefix: Annotated[str, Field(pattern=r"^[A-Z]$")]
    #: In calling order: `call_next` without a room fills the first free one.
    rooms: Annotated[tuple[Room, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _unique_rooms(self) -> Self:
        ids = [room.id for room in self.rooms]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{self.id}: room ids repeat")
        return self

    def room(self, room_id: str) -> Room:
        for room in self.rooms:
            if room.id == room_id:
                return room
        raise UnknownRoom(room_id)


class RoomsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    grace_seconds: Annotated[int, Field(gt=0, le=3600)]
    service_minutes: dict[Level, Annotated[float, Field(gt=0, le=240)]]
    routing: dict[Level, Key]
    departments: Annotated[tuple[Department, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if set(self.service_minutes) != LEVELS or set(self.routing) != LEVELS:
            raise ValueError("service_minutes and routing need every level 1-5")
        ids = [d.id for d in self.departments]
        prefixes = [d.prefix for d in self.departments]
        if len(set(ids)) != len(ids) or len(set(prefixes)) != len(prefixes):
            raise ValueError("department ids and prefixes must be unique")
        by_id = {d.id: d for d in self.departments}
        for level, department_id in self.routing.items():
            department = by_id.get(department_id)
            if department is None:
                raise ValueError(f"level {level} routes to unknown department {department_id!r}")
            if not any(level in room.accepts_levels for room in department.rooms):
                raise ValueError(f"no room in {department_id} accepts level {level}")
        return self

    def department(self, department_id: str) -> Department:
        for department in self.departments:
            if department.id == department_id:
                return department
        raise UnknownDepartment(department_id)

    def department_for(self, level: int) -> Department:
        return self.department(self.routing[level])


def load_rooms(path: Path = ROOMS_FILE) -> RoomsConfig:
    return RoomsConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


@cache
def rooms() -> RoomsConfig:
    """The validated `config/rooms.yaml`, read once."""
    return load_rooms()


__all__ = [
    "LEVELS",
    "ROOMS_FILE",
    "Department",
    "Room",
    "RoomsConfig",
    "UnknownDepartment",
    "UnknownRoom",
    "load_rooms",
    "rooms",
]
