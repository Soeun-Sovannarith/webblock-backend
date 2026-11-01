from uuid import UUID
from enum import Enum
import re
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class BusinessType(str, Enum):
    EVERYDAY_STORE = "everyday_store"
    HANDMADE = "handmade"
    RETAIL = "retail"
    RESTAURANT = "restaurant"
    CAFE = "cafe"
    SERVICES = "services"
    PORTFOLIO = "portfolio"
    AGENCY = "agency"


class DaySchedule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    day: str = Field(..., description="Day name, e.g. Monday, Tuesday, etc.")
    is_open: bool = Field(default=True, description="Whether the business is open on this day")
    opens: str = Field(default="9:00 AM", description="Opening time, e.g. 9:00 AM or 09:00")
    closes: str = Field(default="6:00 PM", description="Closing time, e.g. 6:00 PM or 18:00")

    @field_validator("day")
    @classmethod
    def validate_day(cls, v: str) -> str:
        cleaned = v.strip().capitalize()
        valid_days = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
        if cleaned not in valid_days:
            # Also allow 3-letter abbreviation
            abbr_map = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday",
                        "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday"}
            if cleaned in abbr_map:
                return abbr_map[cleaned]
            raise ValueError(f"Invalid day name: '{v}'. Must be one of Monday-Sunday.")
        return cleaned


EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class BusinessOnboardingRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Step 1: Business details
    business_name: str = Field(..., min_length=1, max_length=100, description="Name of the business, e.g. Petal & Stem")
    business_type: str = Field(default="everyday_store", max_length=60, description="Business category or type")
    business_description: str = Field(
        ...,
        min_length=5,
        max_length=2000,
        description="One or two sentences telling visitors what you do",
    )
    logo_url: str | None = Field(default=None, max_length=1000, description="Optional logo image URL")

    # Step 2: Contact & hours
    phone_number: str | None = Field(default=None, max_length=50, description="Contact phone number")
    email: str | None = Field(default=None, description="Contact email address")
    address: str | None = Field(default=None, max_length=255, description="Physical address or area, e.g. Prek Eng")
    opening_hours: list[DaySchedule] = Field(
        default_factory=list,
        description="Daily opening hours for Monday through Sunday",
    )

    # Optional Theme & Visual Style
    theme: Literal["warm", "light", "dark"] = Field(default="warm", description="Puck root theme")
    accent: str | None = Field(default="#a4532b", description="Hex color accent, e.g. #a4532b")
    font: Literal["sans", "serif", "mono"] = Field(default="sans", description="Primary font family")

    # Generation options
    codegen_mode: Literal["hybrid", "llm"] | None = Field(default=None, description="Override CODEGEN_MODE ('hybrid' or 'llm').")
    instructions: str | None = Field(default=None, max_length=2000, description="Optional extra instructions for AI codegen")

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        cleaned = v.strip().lower()
        if not EMAIL_REGEX.match(cleaned):
            raise ValueError(f"Invalid email address: '{v}'")
        return cleaned

    @field_validator("business_name", "business_type", "business_description", "address")
    @classmethod
    def sanitize_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        # Strip control characters except newline and tab; a lone carriage return becomes a newline
        sanitized = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", v.replace("\r\n", "\n").replace("\r", "\n")).strip()
        return sanitized

    @field_validator("accent")
    @classmethod
    def validate_accent(cls, v: str | None) -> str | None:
        if not v:
            return None
        if not re.match(r"^#[0-9a-fA-F]{6}$", v):
            raise ValueError("Accent must be a 6-digit hex color like '#a4532b'")
        return v


class BusinessOnboardingResponse(BaseModel):
    """202 answer of both onboarding endpoints. Poll GET /v1/generation-runs/{run_id} for progress, the repository_url and
    commit_sha of the GitHub push, and the generated files. run_id is null when nothing had to be generated."""
    model_config = ConfigDict(extra="ignore")

    project_id: UUID
    project_name: str | None = Field(default=None, description="Only from POST /v1/onboardings, which creates the project")
    generation_request_id: UUID
    run_id: UUID | None = None
    status: str = Field(..., description="QUEUED for a new run, or the existing request's status")
    drizzle_schema_preview: str
    seed_sql_preview: str
    seed_source: Literal["glm", "template"]
    puck_blocks: list[dict[str, Any]]
    message: str
