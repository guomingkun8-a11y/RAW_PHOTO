from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


class MessageInput(BaseModel):
    client_id: str = Field(min_length=8, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    content: str = Field(default="", max_length=8000)
    attachment_ids: list[Annotated[str, Field(min_length=1, max_length=36)]] = Field(default_factory=list, max_length=6)
    internal: bool = False


class ThreadInput(MessageInput):
    title: str = Field(default="", max_length=160)
    category: Literal["bug", "suggestion", "question"] = "bug"
    source_page: str = Field(default="", max_length=512)
    task_id: str = Field(default="", max_length=191)
    app_version: str = Field(default="", max_length=64)

    @field_validator("source_page")
    @classmethod
    def local_page(cls, value):
        if value and (not value.startswith("/") or value.startswith("//")):
            raise ValueError("来源页面必须是站内路径")
        # Avoid persisting credentials or prompts from arbitrary query strings.
        return value.split("?", 1)[0].split("#", 1)[0]


class ThreadUpdate(BaseModel):
    status: Literal["pending", "processing", "waiting_user", "resolved"] | None = None
    claim: bool | None = None
    assigned_to: str | None = Field(default=None, max_length=191)


class ReadInput(BaseModel):
    seq: int = Field(ge=0)


class SettingsInput(BaseModel):
    notify_enabled: bool
    site_url: str = Field(default="", max_length=512)

    @field_validator("site_url")
    @classmethod
    def safe_url(cls, value):
        value = value.strip().rstrip("/")
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("请填写系统的 HTTP/HTTPS 访问地址")
        return value
