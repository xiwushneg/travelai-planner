"""与当前项目模型一致的严格导入校验。"""
from __future__ import annotations

import re
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.models import SourceInfo, Spot


def _valid_id(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", value):
        raise ValueError("id 只能包含小写字母、数字、点、下划线、连字符，且以字母或数字开头")
    return value


def _validate_source(source: SourceInfo) -> None:
    if not source.name.strip():
        raise ValueError("source.name 不能为空；没有外部来源时请填写真实的数据整理说明")
    if source.status.value == "verified" and not (source.url and source.last_verified_at):
        raise ValueError("verified 记录必须同时提供 source.url 和 source.last_verified_at")


class ImportSpot(Spot):
    model_config = ConfigDict(extra="forbid")

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return _valid_id(value)

    @field_validator("name", "city", "category")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不能为空或只包含空白")
        return value

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        _validate_source(self.source)
        return self


class ImportGuide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    tags: list[str] = Field(default_factory=list)
    content: str = Field(min_length=1, max_length=10000)
    source: SourceInfo

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return _valid_id(value)

    @field_validator("title", "city", "content")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("不能为空或只包含空白")
        return value

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        _validate_source(self.source)
        return self
