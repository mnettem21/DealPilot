"""The fraud detector: one catalogue of rules, one entry point.

    screen(deal, ctx) -> {"level": "none|warning|critical", "findings": [...], "linked_deals": [...], "reason": str, "score": x}

`deal` is plain JSON-able data: {"name", "facts", "issues", "notes"}. `ctx` holds the history and everything learned from it.
warning  = tell Fraud Ops; the deal may continue but cannot enter Closed until they clear it.
critical = request a fraud hold now.
"info" findings are supporting evidence: they never raise the level by themselves.
"""
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from .fraud import Linker
from .patterns import tokens

ORDER = {"info": 0, "warning": 1, "critical": 2}
BY_ORDER = {v: k for k, v in ORDER.items()}


def _old_tricks():
    """Bank list from the flag-fraud skill. Feedback never softens these."""
    for parent in Path(__file__).resolve().parents:
        catalog = parent / ".cursor" / "skills" / "flag-fraud" / "fraud-types.json"
        if catalog.is_file():
            rows = json.loads(catalog.read_text())["types"]
            return {row["code"] for row in rows if row.get("family") == "old"}
    raise FileNotFoundError("flag-fraud fraud-types.json")


OLD_TRICKS = _old_tricks()


def finding(rule, level, evidence, deals=(), score=0, signature=None):
    f = {"rule": rule, "level": level, "evidence": evidence, "deals": sorted(deals), "score": score}
    if signature:
        f["signature"] = signature
    return f


def rule_id(signature):
    """Stable id of a learned pattern = its set of shared rare details."""
    return hashlib.sha1("|".join(sorted(signature)).encode()).hexdigest()[:10]


def readable(token):
    k, _, v = token.partition("=")
    return f"{k.replace('_', ' ')} {v.replace('_', ' ')}"


class Context:
    """Built once per screening pass: history, learned patterns, and today's deals side by side."""

    def __init__(self, hist, patterns, current, labels=(), learned=None):
        self.hist, self.patterns, self.current = hist, patterns, current
        self.learned = learned or {}                      # id -> {status: proposed|active|rejected, tokens, ...}
        self.confirmed, self.cleared_count = Counter(), Counter()
        self.cleared = set()                              # deals Fraud Ops said were fine (names and history ids)
        for lab in labels:
            for r in lab.get("rules", []):
                (self.confirmed if lab["label"] == "confirmed" else self.cleared_count)[r] += 1
            if lab["label"] == "cleared":
                self.cleared |= {lab["deal"], f"P-{lab['deal']}@{lab['run_id'][:4]}"}
        self.rings = Linker(hist).run(current)
        self.neighbours, self.history_clusters = patterns.neighbours(current)
        self.history_breaks = patterns.history_breaks()
        scored = sorted((f["style"]["score"], n) for n, f in current.items() if f.get("style"))
        self.style_rank = {n: (i + 1, len(scored)) for i, (_, n) in enumerate(scored)}


# ---------- rules: each takes (deal, ctx) and returns a list of findings ----------

def rule_old_tricks(deal, ctx):
    return [finding(i.split(":")[1], "critical", f"Known trick {i.split(':')[1]}: {deal['notes'].get(i, '')}")
            for i in deal["issues"] if i.startswith("OLD_TRICK:")]


def rule_dependency_break(deal, ctx):
    out = []
    for b in ctx.patterns.dependency_breaks(deal["facts"]):
        peers = [i for i, bs in ctx.history_breaks.items() if any(x["a"] == b["a"] and x["b"] == b["b"] for x in bs)]
        why = (f"{b['a'].replace('_', ' ')} '{b['a_value']}' was paired with {b['b'].replace('_', ' ')} '{b['expected']}' in {b['n']} of "
               f"{b['total']} past deals; this deal has '{b['seen']}'.")
        if b["money"]:
            why += " Funds would go somewhere the bank has not paid this party before."
        out.append(finding("DEPENDENCY_BREAK", "warning", why, peers))
    return out


def rule_ring(deal, ctx):
    r = ctx.rings.get(deal["name"])
    if not r:
        return []
    ev = f"Same hidden pattern as {len(r['linked'])} other deal(s): " + " | ".join(r["evidence"])
    if r["identity"]:
        return [finding("IDENTITY_REUSE", "warning", "The same person identifiers (driver's license or SSN+birthdate) appear under a different business. " + ev, r["linked"], r["score"])]
    return [finding("RING_LINK", "warning", ev, r["linked"], r["score"])]


def rule_neighbour(deal, ctx):
    n = ctx.neighbours.get(deal["name"])
    if not n:
        return []
    rej = ctx.learned.get(rule_id(n["signature"])) if n["signature"] else None
    if rej and rej["status"] == "rejected":
        return [finding("PATTERN_NEIGHBOUR", "info", "Looks like a pattern Fraud Ops rejected as harmless.", n["cluster"], n["sim"])]
    shared = "; ".join(f"{readable(t)} (in {k} deals)" for t, k in n["shared"][:4])
    ev = (f"Unusually similar to {', '.join(n['cluster'])} (closest score {n['sim']} vs a normal limit of {n['threshold']}). "
          f"Shared rare details: {shared}.")
    return [finding("PATTERN_NEIGHBOUR", "warning", ev, n["cluster"], n["sim"], n["signature"])]


def rule_learned(deal, ctx):
    """Patterns Fraud Ops approved: any deal carrying most of the pattern's details is critical, even with no neighbour yet."""
    have = set(tokens(deal["facts"]))
    out = []
    for r in ctx.learned.values():
        if r["status"] == "active" and r["tokens"]:
            need = max(3, math.ceil(0.6 * len(r["tokens"])))
            hit = [t for t in r["tokens"] if t in have]
            if len(hit) >= need:
                out.append(finding("LEARNED_RULE", "critical", f"Matches pattern {r['id']} approved by Fraud Operations: {'; '.join(readable(t) for t in hit[:5])}."))
    return out


def rule_style(deal, ctx):
    rank, f = ctx.style_rank.get(deal["name"]), deal["facts"]
    if rank and rank[0] <= 3 and f["style"]["round_share"] >= 0.8:
        return [finding("STYLE_ANOMALY", "info", f"Deposit style is #{rank[0]} most unusual of {rank[1]} deals ({f['style']['round_share']:.0%} of deposits are round hundreds).")]
    return []


def rule_forensics(deal, ctx):
    if deal["facts"].get("stmt_created_early"):
        return [finding("DOC_FORENSICS", "info", f"{deal['facts']['stmt_created_early']} bank statement file(s) were created before the last transaction they list.")]
    return []


RULES = [rule_old_tricks, rule_dependency_break, rule_ring, rule_neighbour, rule_learned, rule_style, rule_forensics]


def adjust(f, ctx):
    """Feedback from Fraud Ops: patterns only linked to cleared deals fade; rules that keep turning out right or wrong move one step."""
    if f["rule"] in OLD_TRICKS or f["level"] == "info":
        return f
    c, k = ctx.confirmed[f["rule"]], ctx.cleared_count[f["rule"]]
    trust = (c + 1) / (c + k + 2)
    level, note = f["level"], ""
    if f["deals"] and all(d in ctx.cleared for d in f["deals"]):
        level, note = "info", " (only linked to deals Fraud Operations cleared)"
    elif c + k >= 3 and trust < 0.35:
        level, note = BY_ORDER[ORDER[level] - 1], f" (this rule was cleared as a false alarm {k} of {c + k} times)"
    elif c >= 2 and trust >= 0.8 and level == "warning":
        level, note = "critical", f" (this rule was confirmed as fraud {c} of {c + k} times)"
    return f if level == f["level"] else dict(f, level=level, evidence=f["evidence"] + note)


def screen(deal, ctx):
    findings = [adjust(f, ctx) for rule in RULES for f in rule(deal, ctx)]
    top = max((ORDER[f["level"]] for f in findings), default=0)
    level = {2: "critical", 1: "warning"}.get(top, "none")
    linked = sorted({d for f in findings for d in f["deals"]})
    return {"level": level, "findings": findings, "linked_deals": linked, "score": max((f["score"] for f in findings), default=0),
            "reason": " ".join(f"[{f['rule']}] {f['evidence']}" for f in findings)}
