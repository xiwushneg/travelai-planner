"""离线端到端演示：创建 v1 -> 局部修改生成 v2 -> 比较版本。

使用临时目录中的 SQLite 数据库，进程结束后自动删除，不污染 ./data。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.models import TripSpec  # noqa: E402
from app.data.seed import DEMO_GUIDES, DEMO_SPOTS  # noqa: E402
from app.rag.local import LocalLexicalRetriever  # noqa: E402
from app.repository import LEGACY_USER_ID, Repository  # noqa: E402
from app.service import PlanningService  # noqa: E402


def day_view(day):
    return {
        "day_number": day.day_number,
        "date": day.date.isoformat(),
        "items": [
            {
                "kind": item.kind,
                "title": item.title,
                "start": item.start_time.isoformat(),
                "end": item.end_time.isoformat(),
            }
            for item in day.items
        ],
        "travel_km_estimate": day.total_travel_km,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="可选：把演示摘要写入 JSON 文件")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="travelai-demo-") as temp_dir:
        repository = Repository(str(Path(temp_dir) / "demo.db"))
        repository.replace_spots(DEMO_SPOTS)
        service = PlanningService(repository, LocalLexicalRetriever(DEMO_GUIDES))
        spec = TripSpec(
            destination="东京",
            start_date=date(2027, 4, 1),
            end_date=date(2027, 4, 2),
            selected_spot_ids=[
                "tokyo-sensoji",
                "tokyo-ueno-park",
                "tokyo-national-museum",
                "tokyo-meiji-jingu",
            ],
            must_include_spot_ids=["tokyo-sensoji", "tokyo-ueno-park"],
            max_spots_per_day=2,
            include_lunch=False,
        )
        created, create_run_id = service.create_trip(spec)
        if created.status != "success":
            raise SystemExit(json.dumps(created.model_dump(mode="json"), ensure_ascii=False))
        before = created.plan
        modified, modify_run_id, difference = service.modify_day(
            trip_id=before.trip_id,
            expected_version=1,
            day_number=2,
            desired_spot_ids=["tokyo-shibuya-sky"],
            lock_other_days=True,
        )
        if modified.status != "success":
            raise SystemExit(json.dumps(modified.model_dump(mode="json"), ensure_ascii=False))
        after = modified.plan
        summary = {
            "notice": "全部为软件演示数据，票价和开放时间未实时核验",
            "trip_id": before.trip_id,
            "versions": repository.list_versions(
                before.trip_id, owner_id=LEGACY_USER_ID
            ),
            "runs": {
                "create": repository.get_run(
                    create_run_id, owner_id=LEGACY_USER_ID
                )["status"],
                "modify": repository.get_run(
                    modify_run_id, owner_id=LEGACY_USER_ID
                )["status"],
            },
            "v1_days": [day_view(day) for day in before.days],
            "v2_days": [day_view(day) for day in after.days],
            "diff": difference.model_dump(mode="json"),
            "cost_scope": after.spec.budget_scope,
            "cost": after.cost.model_dump(mode="json"),
            "warnings": after.warnings,
            "evidence_count": len(after.evidence),
        }

    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"演示摘要已写入：{args.output}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
