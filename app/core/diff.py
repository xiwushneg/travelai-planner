"""行程版本差异：按景点身份比较，不把普通渲染变化算作修改。"""
from app.core.models import ItineraryPlan, ItemChange, PlanDiff


def diff_plans(before: ItineraryPlan, after: ItineraryPlan) -> PlanDiff:
    def flatten(plan):
        result = {}
        for day in plan.days:
            for item in day.items:
                if item.spot_id:
                    result[item.spot_id] = (day.day_number, item.title, item.start_time, item.end_time)
        return result

    old, new = flatten(before), flatten(after)
    added, removed, changed = [], [], []
    for spot_id in sorted(new.keys() - old.keys()):
        day, title, start, end = new[spot_id]
        added.append(ItemChange(day_number=day, spot_id=spot_id, title=title,
                                after=f"{start.isoformat()}-{end.isoformat()}"))
    for spot_id in sorted(old.keys() - new.keys()):
        day, title, start, end = old[spot_id]
        removed.append(ItemChange(day_number=day, spot_id=spot_id, title=title,
                                  before=f"{start.isoformat()}-{end.isoformat()}"))
    for spot_id in sorted(old.keys() & new.keys()):
        if old[spot_id] != new[spot_id]:
            old_day, title, old_start, old_end = old[spot_id]
            new_day, _, new_start, new_end = new[spot_id]
            changed.append(ItemChange(
                day_number=new_day,
                spot_id=spot_id,
                title=title,
                before=f"Day {old_day} {old_start.isoformat()}-{old_end.isoformat()}",
                after=f"Day {new_day} {new_start.isoformat()}-{new_end.isoformat()}",
            ))
    unchanged = []
    old_days = {day.day_number: day for day in before.days}
    for day in after.days:
        old_day = old_days.get(day.day_number)
        if old_day and old_day.model_dump(mode="json") == day.model_dump(mode="json"):
            unchanged.append(day.day_number)
    return PlanDiff(
        from_version=before.version,
        to_version=after.version,
        added=added,
        removed=removed,
        changed=changed,
        unchanged_days=unchanged,
    )
