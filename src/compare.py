"""
Stage 5: statistical comparison.

Given an outcome (escalation / refund / churn) and an optional intent filter,
compare the calls WITH the outcome (treatment) against calls WITHOUT it
(control) on every call-level behavioural feature, and rank what differs.

Design choices (all reported in the technical report):
  - Treatment = outcome flagged True (high or medium confidence, `min_conf`).
  - Control   = outcome False with confidence 'none' (clear negatives only).
  - Intent matching: control is restricted to the intents present in treatment
    and then re-sampled so its intent mix mirrors the treatment mix. Without
    this, escalation (over half of it sits in one intent) would mostly measure
    intent, not behaviour.
  - Calls with untrusted speaker roles have role-conditional features = NaN and
    are dropped per-feature, never imputed.
  - Continuous features: Welch t-test + Cohen's d. Binary features: chi-square
    + odds ratio. Benjamini-Hochberg correction across all features.
  - If the treatment group has < MIN_N calls, the result says "insufficient
    data" instead of reporting a finding.
  - Features that are near-direct label proxies are excluded per outcome
    (n_supervisor_turns for escalation).

Usage:
    python3 src/compare.py --outcome escalation
    python3 src/compare.py --outcome churn --intent "Cancellation Policies"
"""
import argparse
import json

import numpy as np
import pandas as pd
from scipy import stats

MIN_N = 30
CONTROL_RATIO = 5            # control calls sampled per treatment call
RANDOM_STATE = 42
OUTCOMES = ("escalation", "refund", "churn")

CONTINUOUS = [
    "cust_neg_mean", "cust_neg_peak", "cust_neg_slope", "cust_neg_onset_frac",
    "cust_neg_first3", "cust_neg_last3", "cust_neg_delta", "cust_neg_turn_frac",
    "cust_joy_last3", "agent_apology_count", "agent_empathy_count",
    "cust_filler_rate", "cust_avg_words", "agent_avg_words", "word_ratio",
    "n_turns", "max_rep_sim",
]
# derived 0/1 features
BINARY = {
    "has_repetition": lambda d: d["n_repetitions"] > 0,
    "any_agent_apology": lambda d: d["agent_apology_count"] > 0,
    "customer_ends_positive": lambda d: d["cust_joy_last3"] > 0.5,
    "supervisor_joined": lambda d: d["n_supervisor_turns"] > 0,
}
LEAKY = {"escalation": {"supervisor_joined", "n_supervisor_turns"}}

DESCRIPTIONS = {
    "cust_neg_peak": "peak customer negative emotion",
    "cust_neg_mean": "average customer negative emotion",
    "cust_neg_slope": "trend of customer negative emotion over the call",
    "cust_neg_onset_frac": "how early strong negativity begins (0=start, 1=never)",
    "cust_neg_first3": "customer negativity in the first 3 turns",
    "cust_neg_last3": "customer negativity in the last 3 turns",
    "cust_neg_delta": "change in customer negativity, start to end",
    "cust_neg_turn_frac": "share of customer turns that are strongly negative",
    "cust_joy_last3": "customer positive emotion at the end of the call",
    "agent_apology_count": "number of agent apologies",
    "agent_empathy_count": "number of agent empathy phrases",
    "cust_filler_rate": "customer hesitation (um/uh) rate",
    "cust_avg_words": "average words per customer turn",
    "agent_avg_words": "average words per agent turn",
    "word_ratio": "agent-to-customer word ratio",
    "n_turns": "call length in turns",
    "max_rep_sim": "highest similarity between repeated customer statements",
    "has_repetition": "customer repeated themselves",
    "any_agent_apology": "agent apologised at least once",
    "customer_ends_positive": "customer ends on a positive note",
    "supervisor_joined": "a supervisor joined the call",
}


def load_frame(labeled_path, features_path, normalized_path=None):
    lab = pd.read_parquet(labeled_path)
    feat = pd.read_parquet(features_path)
    df = lab.merge(feat.drop(columns=[c for c in feat.columns if c in lab.columns and c != "transcript_id"]),
                   on="transcript_id", how="inner")
    return df


def build_groups(df, outcome, intent=None, min_conf="medium"):
    """Return (treatment, control) DataFrames with intent matching applied."""
    ok_conf = {"high"} if min_conf == "high" else {"high", "medium"}
    d = df if intent is None else df[df["intent"] == intent]
    treat = d[d[f"outcome_{outcome}"] & d[f"conf_{outcome}"].isin(ok_conf)]
    pool = d[(~d[f"outcome_{outcome}"]) & (d[f"conf_{outcome}"] == "none")]
    if len(treat) == 0:
        return treat, pool.iloc[0:0]

    # intent matching: mirror treatment's intent mix in the control sample
    rng = np.random.RandomState(RANDOM_STATE)
    parts = []
    for it, n_t in treat["intent"].value_counts().items():
        cand = pool[pool["intent"] == it]
        k = min(len(cand), n_t * CONTROL_RATIO)
        if k:
            parts.append(cand.sample(n=k, random_state=rng))
    control = pd.concat(parts) if parts else pool.iloc[0:0]
    matched = True
    if len(control) < MIN_N:
        # intent is (almost) fully determined by the outcome, so matching leaves no
        # usable controls: fall back to an unmatched sample and flag it in the result.
        base = df if intent is None else df[df["intent"] == intent]
        pool = base[(~base[f"outcome_{outcome}"]) & (base[f"conf_{outcome}"] == "none")]
        if len(pool) < MIN_N:  # intent filter itself has no negatives: use whole corpus
            pool = df[(~df[f"outcome_{outcome}"]) & (df[f"conf_{outcome}"] == "none")]
        control = pool.sample(n=min(len(pool), len(treat) * CONTROL_RATIO), random_state=RANDOM_STATE)
        matched = False
    control.attrs["intent_matched"] = matched
    return treat, control


def _bh(pvals):
    p = np.asarray(pvals, dtype=float)
    n = len(p)
    order = np.argsort(p)
    q = np.empty(n)
    prev = 1.0
    for rank, idx in enumerate(order[::-1]):
        i = n - rank
        prev = min(prev, p[idx] * n / i)
        q[idx] = prev
    return q


def compare(df, outcome, intent=None, min_conf="medium", top_k=8):
    assert outcome in OUTCOMES, f"outcome must be one of {OUTCOMES}"
    treat, control = build_groups(df, outcome, intent, min_conf)
    result = {"outcome": outcome, "intent": intent,
              "n_treatment": int(len(treat)), "n_control": int(len(control))}

    if len(treat) < MIN_N:
        result["status"] = "insufficient_data"
        result["message"] = (f"Only {len(treat)} calls match this filter (minimum {MIN_N}); "
                             "no statistical claim is made.")
        result["findings"] = []
        return result

    leaky = LEAKY.get(outcome, set())
    rows = []

    for f in CONTINUOUS:
        if f in leaky or f not in df.columns:
            continue
        a, b = treat[f].dropna(), control[f].dropna()
        if len(a) < MIN_N or len(b) < MIN_N:
            continue
        t, p = stats.ttest_ind(a, b, equal_var=False)
        pooled = np.sqrt(((len(a) - 1) * a.var() + (len(b) - 1) * b.var()) / (len(a) + len(b) - 2))
        d_eff = (a.mean() - b.mean()) / pooled if pooled > 0 else 0.0
        rows.append(dict(feature=f, test="welch_t", treat_value=float(a.mean()), control_value=float(b.mean()),
                         effect=float(d_eff), effect_name="cohens_d", p=float(p), n_treat=len(a), n_control=len(b)))

    for f, fn in BINARY.items():
        if f in leaky:
            continue
        mask_a = treat["roles_trusted"] if "roles_trusted" in treat else pd.Series(True, index=treat.index)
        mask_b = control["roles_trusted"] if "roles_trusted" in control else pd.Series(True, index=control.index)
        a, b = fn(treat[mask_a]).astype(int), fn(control[mask_b]).astype(int)
        table = np.array([[a.sum(), len(a) - a.sum()], [b.sum(), len(b) - b.sum()]])

        # Constant feature (an empty row or column): nothing to test, skip it
        if table.sum() == 0 or (table.sum(axis=0) == 0).any() or (table.sum(axis=1) == 0).any():
            continue

        try:
            chi2, p, _, _ = stats.chi2_contingency(table, correction=True)
        except ValueError:
            continue
        n = table.sum()
        cramers = float(np.sqrt(chi2 / n))
        signed = cramers if a.mean() >= b.mean() else -cramers
        rows.append(dict(feature=f, test="chi_square", treat_value=float(a.mean()), control_value=float(b.mean()),
                         effect=signed, effect_name="cramers_v", p=float(p), n_treat=len(a), n_control=len(b)))

    if not rows:
        result["status"] = "insufficient_data"
        result["message"] = "No testable features for this filter; no statistical claim is made."
        result["findings"] = []
        return result

    res = pd.DataFrame(rows)
    res["q"] = _bh(res["p"].values)
    res["significant"] = res["q"] < 0.05
    res["abs_effect"] = res["effect"].abs()
    res = res.sort_values(["significant", "abs_effect"], ascending=[False, False])

    findings = []
    for _, r in res.head(top_k).iterrows():
        findings.append({
            "feature": r["feature"],
            "description": DESCRIPTIONS.get(r["feature"], r["feature"]),
            "direction": "higher" if r["effect"] > 0 else "lower",
            "treatment_value": round(r["treat_value"], 3),
            "control_value": round(r["control_value"], 3),
            "effect_size": round(r["effect"], 3),
            "effect_name": r["effect_name"],
            "test": r["test"],
            "p_adj": float(f"{r['q']:.2e}"),
            "significant": bool(r["significant"]),
        })

    result["status"] = "ok"
    result["intent_matched"] = bool(control.attrs.get("intent_matched", True))
    result["n_significant"] = int(res["significant"].sum())
    result["findings"] = findings
    result["intent_mix_treatment"] = treat["intent"].value_counts().head(5).to_dict()
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--outcome", required=True, choices=OUTCOMES)
    ap.add_argument("--intent", default=None)
    ap.add_argument("--min-conf", default="medium", choices=["high", "medium"])
    ap.add_argument("--labeled", default="data/processed/calls_labeled_v3.parquet")
    ap.add_argument("--features", default="data/processed/call_features.parquet")
    args = ap.parse_args()
    frame = load_frame(args.labeled, args.features)
    print(json.dumps(compare(frame, args.outcome, args.intent, args.min_conf), indent=2))
