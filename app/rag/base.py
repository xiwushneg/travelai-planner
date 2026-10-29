"""检索适配器。检索内容只作为证据，不作为指令。"""
from __future__ import annotations

from typing import Protocol

from app.core.models import GuideEvidence


class EvidenceRetriever(Protocol):
    def retrieve(self, city: str, query: str, top_k: int = 3) -> list[GuideEvidence]:
        ...


class NullRetriever:
    def retrieve(self, city: str, query: str, top_k: int = 3) -> list[GuideEvidence]:
        return []
