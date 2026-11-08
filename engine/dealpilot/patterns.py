"""Pattern recognition from history: understand what is normal, then compare each new deal to it.

1. Neighbour similarity: EVERY scalar field of a deal becomes a `field=value` token (no curated list). TF-IDF weights rare
   tokens up and popular ones down automatically; cosine finds each deal's closest deal from a DIFFERENT borrower. The alarm level is
   a robust outlier level (median + 5 MADs) of how similar history deals normally are to their closest neighbour, so it comes from the data.
   A new collected column automatically adds tokens, no code change.
2. Learned dependencies: mine "A almost always determines B" from history (vendor -> payment account, ...). A deal that breaks one
   is suspicious; the history deals that break it are suspicious too.
"""
import math
import re
from collections import Counter, defaultdict

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from .checks import building, norm_addr, norm_name

# Bookkeeping fields that say nothing about who the borrower is or how the paperwork looks.
SKIP = {"id", "ref_date", "signed_date", "signed", "has_extension", "months", "dep_months", "style", "needed_by", "bad",
        "legal_name", "ein", "guarantor_dob", "guarantor_ssn4", "guarantor_name", "id_name", "id_dob", "id_address", "id_dl",
        "letter_amount", "quote_total", "fin_cash", "fin_revenue_fy", "tax_gross_receipts", "annualized_deposits",
        "app_acct_last4", "stmt_acct_last4", "use_of_proceeds", "affiliated", "purpose"}
SUFFIX = re.compile(r"\b(inc|llc|co|corp|ltd|company|pllc|lp)\b")
ALARM_MADS = 5                # alarm when a deal is this many robust deviations more similar to a neighbour than normal
RARE_FRACTION = 0.05          # a token is "rare" when it appears in under 5% of all deals
MIN_DEP_SUPPORT, MIN_DEP_PURITY = 5, 0.85
MIN_ID_VALUES = 15            # B must look like an identifier (accounts, phones), not a category like 'product'
MONEY_FIELDS = {"remit_acct_last4", "remit_routing", "vendor"}  # where funds are sent (prior from the Product-stage rule)


def key(v):
    """Normalise a value for comparison: lowercase, no punctuation, no legal suffix (so 'X LLC' meets 'X, Inc.')."""
    s = re.sub(r"[^a-z0-9 ]", " ", str(v).lower())
    return " ".join(SUFFIX.sub("", s).split())


def tokens(f):
    out = []
    for k, v in f.items():
        if k in SKIP or v in (None, "", [], {}):
            continue
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, (dict, list, bool)) or x in (None, ""):
                continue
            if isinstance(x, (int, float)):
                x = f"~{int(round(math.log(abs(x)) * 4))}" if abs(x) >= 1000 else f"{round(float(x), 1)}"
            elif k.endswith("_date"):
                x = str(x)[:7]
            elif k == "business_address":
                x = building(x)
            elif k == "guarantor_address":
                x = norm_addr(x)
            else:
                x = key(x)
            if x:
                out.append(f"{k}={x.replace(' ', '_')}")
    return out


def same_borrower(a, b):
    return (a.get("ein") and a.get("ein") == b.get("ein")) or norm_name(a.get("legal_name")) == norm_name(b.get("legal_name"))


class Patterns:
    def __init__(self, hist):
        self.hist = hist
        self.deps = self._mine_dependencies()

    # ---------- 1. neighbour similarity ----------
    def neighbours(self, current):
        """-> ({name: finding-dict} for current deals with a suspicious neighbour, history clusters [[ids]])"""
        ids = list(self.hist.facts) + [n for n in current if n not in self.hist.facts]
        facts = [self.hist.facts[i] if i in self.hist.facts else current[i] for i in ids]
        vec = TfidfVectorizer(token_pattern=r"\S+", lowercase=False, sublinear_tf=True)
        try:
            V = vec.fit_transform([" ".join(tokens(f)) for f in facts])
        except ValueError:
            return {}, []
        n = len(ids)
        S = (V @ V.T).toarray()
        np.fill_diagonal(S, 0)
        for a in range(n):  # a returning borrower is not a ring
            for b in range(a + 1, n):
                if S[a, b] and same_borrower(facts[a], facts[b]):
                    S[a, b] = S[b, a] = 0
        nh = len(self.hist.facts)
        null = S[:nh, :nh].max(axis=1)  # history vs history only: today's deals cannot raise their own bar
        thr = float(np.median(null) + ALARM_MADS * 1.4826 * np.median(np.abs(null - np.median(null))))  # robust to a few bad deals in history
        B = (V > 0).astype(int).tocsc()
        df = np.asarray(B.sum(axis=0)).ravel()
        rare_cols = np.where(df <= max(3, RARE_FRACTION * n))[0]
        Br = B[:, rare_cols]
        R = (Br @ Br.T).toarray()                       # number of rare tokens two deals share
        edge = (S >= thr) & (R >= 2)
        feats = vec.get_feature_names_out()
        rare_names = {feats[c] for c in rare_cols}
        comp, seen = {}, set()
        for s0 in range(n):
            if s0 in seen or not edge[s0].any():
                continue
            stack, members = [s0], set()
            while stack:
                x = stack.pop()
                if x in members:
                    continue
                members.add(x)
                stack.extend(np.where(edge[x])[0])
            seen |= members
            for m in members:
                comp[m] = members
        out = {}
        for i, name in enumerate(ids):
            if name in current and i in comp:
                j = int(np.argmax(np.where(edge[i], S[i], 0)))
                shared = np.where((V[i].toarray()[0] > 0) & (V[j].toarray()[0] > 0))[0]
                shared = sorted(shared, key=lambda c: df[c])[:6]
                cluster = sorted(ids[m] for m in comp[i] if m != i)
                group = [m for m in comp[i] if ids[m] in current]
                group = group if len(group) >= 2 else list(comp[i])
                signature = sorted(set.intersection(*[set(tokens(facts[m])) for m in group]) & rare_names)
                out[name] = {"sim": round(float(S[i, j]), 2), "neighbour": ids[j], "threshold": round(thr, 2), "cluster": cluster,
                             "signature": signature,
                             "bad_neighbour": any(self.hist.facts[c]["bad"] for c in cluster if c in self.hist.facts),
                             "shared": [(feats[c], int(df[c])) for c in shared]}
        hist_clusters, done = [], set()
        for m, members in comp.items():
            if m < nh and id(members) not in done and all(x < nh for x in members):
                done.add(id(members))
                hist_clusters.append(sorted(ids[x] for x in members))
        return out, hist_clusters

    # ---------- 2. learned dependencies ----------
    def _mine_dependencies(self):
        rows = list(self.hist.facts.values())
        fields = [k for k, v in rows[0].items() if isinstance(v, str) and k not in SKIP]
        card = {k: len({key(r[k]) for r in rows if r.get(k)}) for k in fields}
        deps = []
        for a in fields:
            for b in fields:
                if a == b or card[b] < MIN_ID_VALUES or card[a] < MIN_DEP_SUPPORT:
                    continue
                groups = defaultdict(Counter)
                for r in rows:
                    if r.get(a) and r.get(b):
                        groups[key(r[a])][key(r[b])] += 1
                entries = {}
                for g, c in groups.items():
                    total = sum(c.values())
                    val, n = c.most_common(1)[0]
                    if total >= MIN_DEP_SUPPORT and n / total >= MIN_DEP_PURITY:
                        entries[g] = (val, n, total)
                if entries:
                    deps.append({"a": a, "b": b, "map": entries})
        return deps

    def dependency_breaks(self, f):
        """Facts of one deal -> list of {a,b,expected,seen,n,total,money} where it breaks a learned dependency."""
        out = []
        for d in self.deps:
            if f.get(d["a"]) and f.get(d["b"]):
                hit = d["map"].get(key(f[d["a"]]))
                if hit and key(f[d["b"]]) != hit[0]:
                    out.append({"a": d["a"], "b": d["b"], "a_value": f[d["a"]], "expected": hit[0], "seen": f[d["b"]],
                                "n": hit[1], "total": hit[2], "money": d["b"] in MONEY_FIELDS})
        return out

    def history_breaks(self):
        """History deals that break a learned dependency -> {deal_id: [break, ...]} (candidates for 'find the rest')."""
        return {i: b for i, f in self.hist.facts.items() if (b := self.dependency_breaks(f))}
