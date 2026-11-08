"""The two small ML pieces, both trained on data/history at startup (no files to maintain).

StyleModel: IsolationForest over size-independent deposit "style" (round amounts, deposits per month, biggest-deposit
share, month-to-month wobble). Supporting evidence only: a real but unusual borrower (James) also scores high, so it never
holds a deal by itself.

Forecast: history showed breaches are NOT predictable from entry-time facts (cross-validated AUC about 0.5) but every
breach had a named blocker. So we learn how long stages take with and without a blocker and project finish times.
"""
import csv
import datetime as dt
import statistics as st
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.ensemble import IsolationForest

STAGES = ["Intake", "Credit", "Committee", "Product", "Committed", "Closed"]


def style_features(months):
    """months: [{total, count, round, largest}] -> [round share, deposits/mo, largest share, wobble]"""
    t = np.array([m["total"] for m in months])
    return [float(np.mean([m["round"] for m in months])), float(np.mean([m["count"] for m in months])),
            float(np.mean(np.array([m["largest"] for m in months]) / t)), float(t.std() / t.mean())]


class StyleModel:
    def __init__(self, data_dir):
        rows = defaultdict(list)
        for r in csv.DictReader(open(Path(data_dir) / "history" / "monthly_deposits.csv")):
            rows[r["deal_id"]].append({"total": float(r["total_deposits"]), "count": int(r["deposit_count"]),
                                       "round": float(r["pct_deposits_multiple_of_100"]), "largest": float(r["largest_deposit"])})
        self.model = IsolationForest(n_estimators=300, random_state=0).fit(np.array([style_features(m) for m in rows.values()]))

    def score(self, months):
        """-> {'score': lower = more unusual, 'round_share': x} or None. Only meaningful as a RANK among deals seen together:
        this week's files sit further from history than history sits from itself, so absolute percentiles would mislead."""
        if not months:
            return None
        f = style_features(months)
        return {"score": round(float(self.model.score_samples([f])[0]), 3), "round_share": round(f[0], 2)}


class Forecast:
    def __init__(self, data_dir):
        ev = list(csv.DictReader(open(Path(data_dir) / "history" / "stage_events.csv")))
        free, blocked = defaultdict(list), []
        for e in ev:
            ratio = float(e["hours_in_stage"]) / float(e["sla_hours"])
            (blocked if e["waiting_on"] else free[e["stage"]]).append(ratio)
        self.free = {s: st.median(v) for s, v in free.items()}
        q = np.percentile(blocked, [50, 90])
        self.blocked_p50, self.blocked_p90 = float(q[0]), float(q[1])

    def project(self, stage, entered, sla, blocked, later_slas, now):
        """-> (p50 finish, p90 finish) datetimes for the deal reaching Closed."""
        el = (now - entered).total_seconds() / 3600
        r50, r90 = (self.blocked_p50, self.blocked_p90) if blocked else (self.free.get(stage, 0.5),) * 2
        rest = sum(self.free.get(s, 0.5) * h for s, h in later_slas)
        return (now + dt.timedelta(hours=max(r50 * sla - el, 0) + rest),
                now + dt.timedelta(hours=max(r90 * sla - el, 0) + rest * 1.5))
