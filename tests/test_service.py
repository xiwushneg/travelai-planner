from datetime import date

import pytest

from app.core.models import TripSpec
from app.data.seed import DEMO_GUIDES, DEMO_SPOTS
from app.rag.local import LocalLexicalRetriever
from app.repository import LEGACY_USER_ID, Repository, VersionConflictError
from app.service import ModificationError, PlanningService


def service(tmp_path):
    repo = Repository(str(tmp_path / "test.db"))
    repo.replace_spots(DEMO_SPOTS)
    return PlanningService(repo, LocalLexicalRetriever(DEMO_GUIDES))


def test_repository_requires_explicit_owner():
    """归属参数必须显式传入，避免漏传时静默回落到 legacy 数据。"""
    import inspect

    for name in (
        "create_run", "list_trips", "get_run", "save_new_plan",
        "save_next_plan", "get_plan", "list_versions",
    ):
        parameter = inspect.signature(getattr(Repository, name)).parameters["owner_id"]
        assert parameter.default is inspect.Parameter.empty, name


def spec():
    return TripSpec(
        destination="东京",
        start_date=date(2027, 4, 1),
        end_date=date(2027, 4, 2),
        selected_spot_ids=[
            "tokyo-sensoji", "tokyo-ueno-park",
            "tokyo-national-museum", "tokyo-meiji-jingu",
        ],
        must_include_spot_ids=[
            "tokyo-sensoji", "tokyo-ueno-park",
            "tokyo-national-museum", "tokyo-meiji-jingu",
        ],
        max_spots_per_day=2,
        include_lunch=False,
    )


def test_modify_day_preserves_other_days_and_creates_diff(tmp_path):
    svc = service(tmp_path)
    result, run_id = svc.create_trip(spec())
    assert result.status == "success", result.model_dump(mode="json")
    before = result.plan
    assert before.version == 1
    assert svc.repository.get_run(run_id, owner_id=LEGACY_USER_ID)["status"] == "succeeded"

    changed, modify_run, difference = svc.modify_day(
        before.trip_id, 1, 2, ["tokyo-shibuya-sky"], lock_other_days=True
    )
    assert changed.status == "success", changed.model_dump(mode="json")
    after = changed.plan
    assert after.version == 2
    assert after.days[0] == before.days[0]
    day2_spots = {item.spot_id for item in after.days[1].items if item.spot_id}
    assert day2_spots == {"tokyo-shibuya-sky"}
    assert 1 in difference.unchanged_days
    assert {change.spot_id for change in difference.added} == {"tokyo-shibuya-sky"}
    assert svc.repository.get_run(modify_run, owner_id=LEGACY_USER_ID)["status"] == "succeeded"

    with pytest.raises(VersionConflictError):
        svc.modify_day(before.trip_id, 1, 2, ["tokyo-hamarikyu"])
    # 失败尝试不能覆盖 v2。
    assert svc.repository.get_plan(before.trip_id, owner_id=LEGACY_USER_ID).version == 2


def test_retriever_uses_city_hard_filter(tmp_path):
    svc = service(tmp_path)
    evidence = svc.retriever.retrieve("东京", "东京 亲子 文化", top_k=10)
    assert evidence
    assert all(item.city == "东京" for item in evidence)
    assert all(item.id != "guide-osaka-other" for item in evidence)


def test_infeasible_modification_does_not_create_version(tmp_path):
    svc = service(tmp_path)
    created, _ = svc.create_trip(spec())
    trip = created.plan
    result, run_id, difference = svc.modify_day(
        trip.trip_id,
        1,
        2,
        ["tokyo-shibuya-sky", "tokyo-hamarikyu", "tokyo-odaiba"],
        lock_other_days=True,
    )
    assert result.status == "infeasible"
    assert result.conflicts[0].code == "capacity_exceeded"
    assert difference is None
    assert svc.repository.get_run(run_id, owner_id=LEGACY_USER_ID)["status"] == "failed"
    assert svc.repository.get_plan(trip.trip_id, owner_id=LEGACY_USER_ID).version == 1


def test_duplicate_spot_on_locked_day_is_rejected_without_version_change(tmp_path):
    svc = service(tmp_path)
    created, _ = svc.create_trip(spec())
    trip = created.plan
    with pytest.raises(ModificationError, match="已存在于其他日期"):
        svc.modify_day(
            trip.trip_id,
            1,
            2,
            ["tokyo-sensoji", "tokyo-ueno-park", "tokyo-national-museum"],
            lock_other_days=True,
        )
    assert svc.repository.get_plan(trip.trip_id, owner_id=LEGACY_USER_ID).version == 1


def test_unlocked_global_replan_still_keeps_target_day_exact(tmp_path):
    svc = service(tmp_path)
    created, _ = svc.create_trip(spec())
    before = created.plan
    changed, _, _ = svc.modify_day(
        before.trip_id,
        1,
        1,
        ["tokyo-shibuya-sky"],
        lock_other_days=False,
    )
    assert changed.status == "success", changed.model_dump(mode="json")
    target = next(day for day in changed.plan.days if day.day_number == 1)
    assert {item.spot_id for item in target.items if item.spot_id} == {
        "tokyo-shibuya-sky"
    }
    scheduled = {
        item.spot_id for day in changed.plan.days for item in day.items if item.spot_id
    }
    assert len(scheduled) == 3
    assert "tokyo-shibuya-sky" in scheduled
