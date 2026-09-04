import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def normalize_date(value: str | date | None, meeting_date: date) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for pattern in (r"^(\d{4})-(\d{1,2})-(\d{1,2})$", r"^(\d{4})年(\d{1,2})月(\d{1,2})日?$"):
        match = re.match(pattern, text)
        if match:
            return date(*map(int, match.groups()))
    match = re.match(r"^(\d{1,2})月(\d{1,2})日?$", text)
    if match:
        return date(meeting_date.year, int(match.group(1)), int(match.group(2)))
    match = re.match(r"^(\d{2})(\d{2})$", text)
    if match:
        return date(meeting_date.year, int(match.group(1)), int(match.group(2)))
    raise ValueError(f"无法识别日期：{value}")


class ParsedTask(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(min_length=1, max_length=240)
    project_name: str = ""
    assignee_name: str = ""
    description: str = ""
    planned_start_date: str | None = None
    due_date: str | None = None
    acceptance_date: str | None = None
    status: Literal["not_started", "in_progress", "acceptance", "done", "delayed", "blocked"] = "not_started"
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    progress: int = Field(default=0, ge=0, le=100)
    current_note: str = ""
    completed_work: str = ""
    next_step: str = ""

    @field_validator("project_name", "assignee_name", "description", "current_note", "completed_work", "next_step", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        return "" if value is None else value

    @field_validator("title")
    @classmethod
    def clean_title(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("任务标题不能为空")
        return value

    @model_validator(mode="after")
    def keep_status_and_progress_consistent(self):
        if self.status == "done" and self.progress < 100:
            self.progress = 100
        return self


class ParsedRisk(BaseModel):
    model_config = ConfigDict(extra="ignore")
    project_name: str = ""
    task_title: str = ""
    risk_type: Literal["risk", "blocker", "decision", "warning", "incident"] = "risk"
    content: str = Field(min_length=1)
    owner_name: str = ""
    due_date: str | None = None
    status: Literal["open", "tracking", "resolved", "closed"] = "open"

    @field_validator("project_name", "task_title", "owner_name", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        return "" if value is None else value


class ParsedMilestone(BaseModel):
    model_config = ConfigDict(extra="ignore")
    project_name: str = ""
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    target_date: str | None = None
    status: Literal["not_started", "in_progress", "done", "delayed"] = "not_started"

    @field_validator("project_name", "description", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        return "" if value is None else value


class ParsedMeeting(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary: str = ""
    tasks: list[ParsedTask] = Field(default_factory=list)
    risks: list[ParsedRisk] = Field(default_factory=list)
    milestones: list[ParsedMilestone] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_optional_text(cls, value):
        return "" if value is None else value
