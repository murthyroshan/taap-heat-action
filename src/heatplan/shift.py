"""Safe shift planner: fit the work a site needs into the hours where it is safe to do it."""
from __future__ import annotations

from .plan import BANDS, personalise


def plan_shift(zr: dict, hours_needed: float, earliest: int = 6, latest: int = 20, new_worker: bool = False,
               usual_start: int = 9) -> dict:
    zr = personalise(zr, new_worker)
    need = round(hours_needed * 60)
    window = [h for h in zr["hourly"] if earliest <= h["hour"] < latest]
    # Fill the safest hours first (lowest band, then earliest), then show the day in time order.
    take_by_hour, left = {}, need
    for h in sorted(window, key=lambda h: (h["band"], h["wbgt"], h["hour"])):
        cap = BANDS[h["band"]]["work"]  # heavy-work minutes allowed in this hour
        take = min(cap, left) if left > 0 else 0
        take_by_hour[h["hour"]] = take
        left -= take
    rows = []
    for h in window:
        cap, take = BANDS[h["band"]]["work"], take_by_hour.get(h["hour"], 0)
        rows.append({"hour": h["hour"], "band": h["band"], "wbgt": h["wbgt"], "work": take,
                     "rest": (60 - take) if take else 0, "cap": cap})
    capacity = sum(r["cap"] for r in rows)
    worked = [r for r in rows if r["work"]]
    # what a usual continuous shift from 9:00 would expose workers to
    usual = [h for h in zr["hourly"] if usual_start <= h["hour"] < usual_start + max(1, round(hours_needed))]
    usual_hot = sum(1 for h in usual if h["band"] >= 3)
    return {
        "hours_needed": hours_needed,
        "feasible": left <= 0,
        "short_by_min": max(0, left),
        "safe_capacity_hours": round(capacity / 60, 1),
        "start": f"{worked[0]['hour']:02d}:00" if worked else None,
        "finish": f"{worked[-1]['hour'] + 1:02d}:00" if worked else None,
        "rows": rows,
        "usual_shift": f"{usual_start:02d}:00-{usual_start + round(hours_needed):02d}:00",
        "usual_hot_hours": usual_hot,
        "planned_hot_minutes": sum(r["work"] for r in rows if r["band"] >= 3),
        "new_worker": new_worker,
    }
