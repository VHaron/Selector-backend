from typing import Any, Dict, List
from pydantic import BaseModel, Field
from .config import ROOMS

class BaseAuthReq(BaseModel):
    session_token: str


class SmartGridSettings(BaseModel):
    budget_usd: float = Field(gt=0, le=100000)
    max_tables: int = Field(ge=1, le=24)
    session_start: str
    session_end: str
    usd_per_eur: float = Field(default=1.08, gt=0, le=100)
    usd_per_rub: float = Field(default=0.011, gt=0, le=10)


class SmartGridRequest(BaseAuthReq):
    tournaments: List[Dict[str, Any]] = Field(default_factory=list, max_length=800)
    locked: List[Dict[str, Any]] = Field(default_factory=list, max_length=300)
    settings: SmartGridSettings


class LoginRequest(BaseModel):
    username: str
    password: str
    site: str = "auto"  # auto | globusgo | globusmvp


class SiteSelectionRequest(BaseModel):
    selection_id: str
    site: str


class MFARequest(BaseAuthReq):
    otp: str


class StructsFilter(BaseModel):
    isFreezeout: str = '1'
    isPko: str = '1'
    isTurbo: str = '1'
    isHyperTurbo: str = '1'
    is6MaxTurboPko: str = '1'
    isSeries: str = '1'


class PrioritiesFilter(BaseModel):
    priority1: str = '1'
    priority2: str = '1'
    priority3: str = '1'


class FiltersRequest(BaseAuthReq):
    date_from: str = ''
    date_to: str = ''
    # Frontend sends canonical GlobusGo room IDs; they are translated to each site's own IDs.
    rooms: list[str] = Field(default_factory=lambda: list(ROOMS.values()))
    structs: StructsFilter = Field(default_factory=StructsFilter)
    priorities: PrioritiesFilter = Field(default_factory=PrioritiesFilter)


class PresetData(BaseModel):
    name: str
    items: List[Dict[str, Any]]


class SavePresetsRequest(BaseAuthReq):
    presets: List[PresetData]


class SaveSettingsRequest(BaseAuthReq):
    selected_rooms: list[str]
    active_formats: dict = Field(default_factory=lambda: {"isPko": True, "isFreezeout": True, "isTurbo": True})
    is_compact: bool = False
    sort_order: str = "time"


class SaveNotesRequest(BaseAuthReq):
    notes: dict


class SessionData(BaseModel):
    id: str
    date: str
    total_buyin: float
    total_cashes: float
    profit: float
    tournaments_count: int


class SaveSessionHistoryRequest(BaseAuthReq):
    session_data: SessionData


class ClearSessionHistoryRequest(BaseAuthReq):
    pass


class SaveGridRequest(BaseAuthReq):
    grid: List[Dict[str, Any]]


class SaveNamedGridRequest(BaseAuthReq):
    name: str = Field(min_length=1, max_length=80)
    grid: List[Dict[str, Any]]


class SaveBalancesRequest(BaseAuthReq):
    balances: Dict[str, float]


