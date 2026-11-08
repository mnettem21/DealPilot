"""Critical results wait at Committed. Warnings are a separate table and do not."""

from services.common.fraud_queue import committed_block, queue_for
from services.rules.__main__ import consider
from services.stages import load


class PB:
    def __init__(self, actions, critical):
        self.actions = actions
        self.critical = critical
        self.updates = []

    def list_records(self, name, expr="", sort=""):
        rows = self.actions if name == "actions" else self.critical if name == "fraud_critical" else []
        if "folder='" in expr:
            folder = expr.split("folder='", 1)[1].split("'", 1)[0]
            rows = [row for row in rows if row.get("folder") == folder]
        if "stage='" in expr:
            stage = expr.split("stage='", 1)[1].split("'", 1)[0]
            rows = [row for row in rows if row.get("stage") == stage]
        if "kind='decision'" in expr:
            rows = [row for row in rows if row.get("kind") == "decision"]
        return rows

    def update_record(self, name, record_id, fields):
        self.updates.append((name, record_id, fields))


def test_agent_results_land_in_different_tables():
    warning = queue_for({"level": "warning", "folder": "Ridgeway Freight Logistics", "reason": "lookalike"}, "t")
    critical = queue_for({"level": "critical", "folder": "Granite Bay Building Supply", "reason": "tamper"}, "t")
    assert warning[0] == "fraud_warnings"
    assert "label" not in warning[1]
    assert critical[0] == "fraud_critical"
    assert critical[1]["label"] == "fraud critical"
    assert queue_for({"level": "none", "folder": "Oakline Auto"}, "t") is None
    assert committed_block("open") == "wait"
    assert committed_block("rejected") == "decline"
    assert committed_block("released") is None
    assert committed_block(None) is None


def test_committed_sits_until_the_fraud_team_decides():
    stage = load("committed")
    deal = {"id": "1", "folder": "Granite Bay Building Supply", "status": "open", "extracted": {}}
    approved = [{"folder": deal["folder"], "stage": "committed", "kind": "decision", "actor": "rm", "decision": "approve"}]
    waiting = PB(approved, [{"folder": deal["folder"], "status": "open", "label": "fraud critical"}])
    consider(waiting, stage, deal)
    assert waiting.updates[-1][2] == {"status": "waiting_human", "waiting_on": "fraud critical"}

    rejected = PB(approved, [{"folder": deal["folder"], "status": "rejected", "label": "fraud critical"}])
    consider(rejected, stage, dict(deal))
    assert rejected.updates[-1][2]["status"] == "declined"
    assert rejected.updates[-1][2]["waiting_on"] == "fraud critical"
