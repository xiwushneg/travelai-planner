"""可离线运行的检索基线：城市硬过滤 + 标签/词法评分 + 来源打包。

它不是向量检索，也不伪装成向量检索。生产可在相同 Protocol 后替换为
PostgreSQL/pgvector 或 Chroma 实现，排程内核无需改变。
"""
from __future__ import annotations

import re

from app.core.models import GuideEvidence, SourceInfo


class LocalLexicalRetriever:
    def __init__(self, documents: list[dict]):
        self.documents = documents

    @staticmethod
    def _terms(text: str) -> set[str]:
        latin = re.findall(r"[a-zA-Z0-9]+", text.lower())
        chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", text)
        terms = set(latin)
        for run in chinese_runs:
            terms.add(run)
            terms.update(run[i:i + 2] for i in range(len(run) - 1))
        return terms

    def retrieve(self, city: str, query: str, top_k: int = 3) -> list[GuideEvidence]:
        query_terms = self._terms(query)
        ranked = []
        for doc in self.documents:
            if doc["city"] != city:  # 硬过滤，不靠相似度猜测城市
                continue
            title = doc["title"]
            content = doc["content"]
            tags = doc.get("tags", [])
            haystack = " ".join([title, content, *tags])
            terms = self._terms(haystack)
            overlap = query_terms & terms
            if not overlap:
                continue
            score = len(overlap)
            score += sum(2 for tag in tags if tag in query)
            if city in query:
                score += 1
            ranked.append((score, doc))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        results = []
        for score, doc in ranked[:top_k]:
            results.append(GuideEvidence(
                id=doc["id"],
                title=doc["title"],
                city=doc["city"],
                snippet=doc["content"][:280],
                source=SourceInfo.model_validate(doc["source"]),
                score=float(score),
            ))
        return results
