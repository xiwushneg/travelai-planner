"""应用服务：规划、局部修改、版本差异、运行记录与检索编排。"""
from __future__ import annotations

from typing import Optional

from app.core.diff import diff_plans
from app.core.models import ItineraryPlan, PlanDiff, PlanningResult, TripSpec
from app.core.scheduler import plan_trip
from app.maps.factory import build_map_provider
from app.maps.routing import RouteEstimator
from app.rag.base import EvidenceRetriever, NullRetriever
from app.rag.local import LocalLexicalRetriever
from app.repository import LEGACY_USER_ID, Repository, VersionConflictError


class ModificationError(ValueError):
    pass


class PlanningService:
    def __init__(
        self,
        repository: Repository,
        retriever: Optional[EvidenceRetriever] = None,
        settings=None,
        map_provider=None,
    ):
        self.repository = repository
        self.retriever = retriever or NullRetriever()
        if settings is None:
            from app.config import settings as app_settings
            settings = app_settings
        self.settings = settings
        if map_provider is None:
            self.map_provider, self.map_mode = build_map_provider(settings)
        else:
            self.map_provider = map_provider
            self.map_mode = f"injected:{map_provider.name}"
        self.map_max_calls = getattr(settings, "map_max_calls_per_plan", 40)

    def _router(self) -> RouteEstimator:
        # 每次规划创建独立估算器，确保单次调用上限不会跨行程累计。
        return RouteEstimator(self.map_provider, self.map_max_calls)

    def _refresh_documents(self) -> None:
        if isinstance(self.retriever, LocalLexicalRetriever):
            # 保留调用方提供的演示/自定义文档，再按 ID 叠加数据库中的导入攻略。
            # 每次检索刷新数据库部分；历史行程中的证据快照不变。
            documents = {doc["id"]: doc for doc in self.retriever.documents}
            documents.update({
                guide.id: guide.model_dump(mode="json")
                for guide in self.repository.list_guides()
            })
            self.retriever = LocalLexicalRetriever(list(documents.values()))

    def search_evidence(self, city: str, query: str, top_k: int = 3):
        """独立证据检索：每次从数据库刷新攻略索引，保证导入后即时可见。"""
        self._refresh_documents()
        return self.retriever.retrieve(city, query, top_k)

    def _evidence(self, spec: TripSpec):
        self._refresh_documents()
        terms = [spec.destination, spec.pace.value, *spec.preferred_categories]
        return self.retriever.retrieve(spec.destination, " ".join(terms), top_k=3)

    def create_trip(
        self, spec: TripSpec, include_evidence: bool = True,
        owner_id: str = LEGACY_USER_ID,
    ) -> tuple[PlanningResult, str]:
        run = self.repository.create_run(
            "create_trip", {"spec": spec.model_dump(mode="json")}, owner_id=owner_id,
        )
        self.repository.update_run(run.id, "running")
        try:
            result = plan_trip(spec, self.repository.list_spots(), route_estimator=self._router())
            if result.status == "infeasible":
                self.repository.update_run(
                    run.id, "failed", error="PLANNING_INFEASIBLE",
                    result=result.model_dump(mode="json"),
                )
                return result, run.id
            plan = result.plan
            if include_evidence:
                plan.evidence = self._evidence(spec)
            plan = self.repository.save_new_plan(plan, owner_id=owner_id)
            result.plan = plan
            self.repository.update_run(
                run.id, "succeeded", trip_id=plan.trip_id, version=plan.version,
                result={"status": "success", "trip_id": plan.trip_id, "version": plan.version},
            )
            return result, run.id
        except Exception as exc:
            self.repository.update_run(run.id, "failed", error=str(exc))
            raise

    # 保留早期内部调用名称，公共 API 仍使用 create_trip。
    def create(self, spec: TripSpec, include_evidence: bool = True):
        return self.create_trip(spec, include_evidence)

    def modify_day(
        self,
        trip_id: str,
        expected_version: int,
        day_number: int,
        desired_spot_ids: list[str],
        lock_other_days: bool = True,
        include_evidence: bool = True,
        owner_id: str = LEGACY_USER_ID,
    ) -> tuple[PlanningResult, str, Optional[PlanDiff]]:
        request = {
            "trip_id": trip_id,
            "expected_version": expected_version,
            "day_number": day_number,
            "desired_spot_ids": desired_spot_ids,
            "lock_other_days": lock_other_days,
        }
        run = self.repository.create_run(
            "modify_day", request, trip_id=trip_id, owner_id=owner_id,
        )
        self.repository.update_run(run.id, "running", trip_id=trip_id)
        try:
            before = self.repository.get_plan(trip_id, owner_id=owner_id)
            if before.version != expected_version:
                raise VersionConflictError(expected_version, before.version)
            if len(set(desired_spot_ids)) != len(desired_spot_ids):
                raise ModificationError("desired_spot_ids 不能重复")
            target = next((day for day in before.days if day.day_number == day_number), None)
            if target is None:
                raise ModificationError(f"Day {day_number} 不存在")

            target_old = {item.spot_id for item in target.items if item.spot_id}
            all_old = {
                item.spot_id
                for day in before.days
                for item in day.items
                if item.spot_id
            }
            unaffected = all_old - target_old
            duplicated = sorted(unaffected & set(desired_spot_ids))
            if duplicated:
                raise ModificationError(f"目标日景点已存在于其他日期：{', '.join(duplicated)}")

            spec = before.spec.model_copy(deep=True)
            spec.selected_spot_ids = sorted(unaffected | set(desired_spot_ids))
            # 用户显式给出的目标日完整集合作为本轮硬约束，不能被静默丢弃。
            spec.must_include_spot_ids = sorted(
                ({spot for spot in before.spec.must_include_spot_ids if spot in unaffected})
                | set(desired_spot_ids)
            )
            locked_days = (
                {day.day_number: day for day in before.days if day.day_number != day_number}
                if lock_other_days else {}
            )
            spec.locked_days = sorted(locked_days)
            result = plan_trip(
                spec,
                self.repository.list_spots(),
                locked_days=locked_days,
                fixed_day_spot_ids={day_number: list(desired_spot_ids)},
                route_estimator=self._router(),
            )
            if result.status == "infeasible":
                self.repository.update_run(
                    run.id, "failed", trip_id=trip_id, version=before.version,
                    error="PLANNING_INFEASIBLE", result=result.model_dump(mode="json"),
                )
                return result, run.id, None

            after = result.plan
            after.evidence = self._evidence(spec) if include_evidence else list(before.evidence)
            after = self.repository.save_next_plan(
                trip_id, expected_version, after, owner_id=owner_id,
            )
            result.plan = after
            difference = diff_plans(before, after)
            self.repository.update_run(
                run.id, "succeeded", trip_id=trip_id, version=after.version,
                result={"status": "success", "diff": difference.model_dump(mode="json")},
            )
            return result, run.id, difference
        except Exception as exc:
            self.repository.update_run(run.id, "failed", trip_id=trip_id, error=str(exc))
            raise

    def compare(
        self, trip_id: str, from_version: int, to_version: int,
        owner_id: str = LEGACY_USER_ID,
    ) -> PlanDiff:
        before = self.repository.get_plan(trip_id, from_version, owner_id=owner_id)
        after = self.repository.get_plan(trip_id, to_version, owner_id=owner_id)
        return diff_plans(before, after)
