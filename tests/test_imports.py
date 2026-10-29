import json
from datetime import date
from pathlib import Path

import pytest

from app.core.models import Guide, TravelParty, TripSpec
from app.data.seed import DEMO_GUIDES, DEMO_SPOTS
from app.imports.cli import execute_import
from app.imports.io import load_records
from app.rag.local import LocalLexicalRetriever
from app.repository import Repository
from app.service import PlanningService


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "data" / "templates"


@pytest.mark.parametrize(
    ("kind", "suffix"),
    [
        ("spots", "jsonl"),
        ("spots", "csv"),
        ("guides", "jsonl"),
        ("guides", "csv"),
    ],
)
def test_templates_dry_run_and_idempotent_import(tmp_path, kind, suffix):
    source = TEMPLATES / f"{kind}.example.{suffix}"
    database = tmp_path / f"{kind}-{suffix}.db"

    dry_run = execute_import(kind, source, database, dry_run=True)
    assert dry_run["dry_run"] is True
    assert dry_run["committed"] is False
    assert dry_run["valid_records"] == 1
    assert dry_run["errors"] == []
    assert not database.exists()

    first = execute_import(kind, source, database)
    assert first["committed"] is True
    assert first["inserted"] == 1
    assert first["updated"] == first["unchanged"] == 0

    second = execute_import(kind, source, database)
    assert second["inserted"] == second["updated"] == 0
    assert second["unchanged"] == 1

    repository = Repository(database)
    stored = repository.list_spots() if kind == "spots" else repository.list_guides()
    assert len(stored) == 1
    assert stored[0].id == f"example-city-{'central-park' if kind == 'spots' else 'guide'}"


def test_import_rejects_whole_file_without_creating_database(tmp_path):
    import os
    import subprocess
    import sys

    source = tmp_path / "spots.invalid.jsonl"
    valid_line = (TEMPLATES / "spots.example.jsonl").read_text(encoding="utf-8").strip()
    source.write_text(valid_line + '\n{"id":"broken","name":"缺少必填字段"}\n', encoding="utf-8")
    database = tmp_path / "must-not-exist.db"

    report = execute_import("spots", source, database)

    assert report["committed"] is False
    assert report["total_records"] == 2
    assert report["valid_records"] == 1
    # rejected_records 表示整批未提交的记录数，不只是格式错误的行数。
    assert report["rejected_records"] == 2
    assert report["errors"][0]["line"] == 2
    assert not database.exists()

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "scripts/import_data.py", "spots", str(source), "--db", str(database)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert json.loads(completed.stdout)["errors"][0]["line"] == 2
    assert not database.exists()


def test_verified_source_requires_url_and_verification_date(tmp_path):
    source = tmp_path / "verified.jsonl"
    raw = json.loads((TEMPLATES / "spots.example.jsonl").read_text(encoding="utf-8"))
    raw["source"]["status"] = "verified"
    source.write_text(json.dumps(raw, ensure_ascii=False) + "\n", encoding="utf-8")

    records, errors, total = load_records(source, "spots")
    assert records == []
    assert total == 1
    assert len(errors) == 1
    assert "last_verified_at" in errors[0]["message"]

    raw["source"]["url"] = "https://example.com/official-not-real"
    raw["source"]["last_verified_at"] = "2026-10-29"
    source.write_text(json.dumps(raw, ensure_ascii=False) + "\n", encoding="utf-8")
    records, errors, total = load_records(source, "spots")
    assert errors == []
    assert total == 1
    assert len(records) == 1
    assert records[0].source.status.value == "verified"


def test_upsert_updates_only_changed_records(tmp_path):
    repository = Repository(tmp_path / "catalog.db")
    spots, errors, _ = load_records(TEMPLATES / "spots.example.jsonl", "spots")
    assert errors == []
    assert repository.import_catalog("spots", spots)["inserted"] == 1

    changed = spots[0].model_copy(update={"name": "更新后的虚构景点"})
    assert repository.import_catalog("spots", [changed]) == {
        "inserted": 0,
        "updated": 1,
        "unchanged": 0,
    }
    assert repository.list_spots()[0].name == "更新后的虚构景点"
    assert repository.import_catalog("spots", [changed])["unchanged"] == 1


def test_imported_spot_and_guide_participate_in_planning(tmp_path):
    repository = Repository(tmp_path / "planning.db")
    repository.replace_spots(DEMO_SPOTS)
    imported_spot = DEMO_SPOTS[0].model_copy(
        deep=True,
        update={
            "id": "tokyo-imported-spot",
            "name": "东京导入专用亲子公园",
            "tags": ["park", "family", "亲子"],
            "visit_duration_minutes": 90,
        },
    )
    imported_guide = Guide(
        id="tokyo-imported-guide",
        title="东京亲子公园轻松旅行导入攻略",
        city="东京",
        tags=["family", "park", "亲子"],
        content="东京亲子公园轻松旅行，建议预留交通与休息时间。",
        source={"status": "unverified", "name": "本地导入测试"},
    )
    assert repository.import_catalog("spots", [imported_spot])["inserted"] == 1
    assert repository.import_catalog("guides", [imported_guide])["inserted"] == 1

    service = PlanningService(repository, LocalLexicalRetriever(DEMO_GUIDES))
    spec = TripSpec(
        destination="东京",
        start_date=date(2027, 4, 1),
        end_date=date(2027, 4, 3),
        party=TravelParty(adults=2, children=1),
        pace="relaxed",
        budget_cny=8000,
        selected_spot_ids=["tokyo-imported-spot", "tokyo-ueno-park"],
        must_include_spot_ids=["tokyo-imported-spot"],
        preferred_categories=["亲子"],
        include_lunch=False,
    )
    result, _ = service.create_trip(spec)

    assert result.status == "success", result.model_dump(mode="json")
    planned_ids = {
        item.spot_id
        for day in result.plan.days
        for item in day.items
        if item.spot_id
    }
    assert "tokyo-imported-spot" in planned_ids
    assert "tokyo-imported-guide" in {item.id for item in result.plan.evidence}
    document_ids = {document["id"] for document in service.retriever.documents}
    assert {document["id"] for document in DEMO_GUIDES} <= document_ids
    assert "tokyo-imported-guide" in document_ids
