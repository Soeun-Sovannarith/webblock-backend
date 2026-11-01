from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
from app.services.catalog import validate_page_path
from app.services.github import parse_repo_url


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    repository_url: str | None = None
    subdomain: str | None = Field(default=None, pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

    @field_validator("repository_url")
    @classmethod
    def _github_url(cls, v: str | None) -> str | None:
        if v and not parse_repo_url(v):
            raise ValueError("repository_url must look like https://github.com/<owner>/<repo>")
        return v or None

    def repository_full_name(self) -> str | None:
        parsed = parse_repo_url(self.repository_url) if self.repository_url else None
        return "/".join(parsed) if parsed else None

class PageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    path: str
    puck_tree: dict[str, Any] | None = None
    seo_title: str | None = Field(default=None, max_length=255)
    seo_description: str | None = Field(default=None, max_length=500)
    sort_order: int | None = None

    @field_validator("path")
    @classmethod
    def _page_path(cls, v: str) -> str:
        return validate_page_path(v)

class PageUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    path: str | None = None
    puck_tree: dict[str, Any] | None = None
    seo_title: str | None = Field(default=None, max_length=255)
    seo_description: str | None = Field(default=None, max_length=500)
    sort_order: int | None = None
    base_edit_revision: int | None = Field(default=None, ge=0, description="Reject with 409 if the project has moved past it.")

    @field_validator("path")
    @classmethod
    def _page_path(cls, v: str | None) -> str | None:
        return validate_page_path(v) if v is not None else None

class GenerationRequestCreate(BaseModel):
    instructions: str | None = Field(default=None, max_length=4000, description="Code change for the AI (optional).")
    drizzle_schema: str | None = Field(default=None, description="Replaces the Drizzle schema; omit to keep the current one.")
    puck_json: dict[str, Any] | None = Field(default=None, description="Replaces the home page's Puck tree first; omit to keep it.")
    codegen_mode: Literal["hybrid", "llm"] | None = Field(default=None, description="Override CODEGEN_MODE ('hybrid' or 'llm').")

class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)

class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    page_id: UUID | None = Field(default=None, description="Page to change; defaults to the home page.")

class ChangeSetUpdate(BaseModel):
    status: Literal["APPLIED", "DISCARDED"] = Field(description="APPLIED rewrites the page; DISCARDED drops the proposal.")
