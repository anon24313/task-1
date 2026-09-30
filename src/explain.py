"""
Stages 4, 6, 7 and the Task 2 conversational layer.

  Stage 4  parse_query      natural language -> (outcome, optional intent)
  Stage 5  compare.compare  statistical comparison (see compare.py)
  Stage 6  attribute        leave-one-turn-out ablation on a lightweight classifier
  Stage 7  synthesize       Claude writes ONLY from the structured evidence
  Task 2   Session          refine / drill_down / new_topic over the same pipeline

Usage (CLI chat):
    export ANTHROPIC_API_KEY=...       # optional; without it the evidence + prompt are printed
    python3 src/explain.py
"""
import json
import os
import re
import sys
import datetime as dt

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, os.path.dirname(__file__))
import compare as C
import features as F

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
P = "data/processed/"

# call-level features the attribution classifier may use. Call length and
# supervisor counts are excluded: they are label proxies, not behaviour.
CLASSIFIER_FEATURES = [
    "cust_neg_mean", "cust_neg_peak", "cust_neg_slope", "cust_neg_onset_frac",
    "cust_neg_first3", "cust_neg_last3", "cust_neg_delta", "cust_neg_turn_frac",
    "cust_joy_last3", "n_repetitions", "max_rep_sim", "agent_apology_count",
    "agent_empathy_count", "cust_filler_rate", "cust_avg_words", "agent_avg_words", "word_ratio",
]

OUTCOME_WORDS = {
    "escalation": ["escalat", "supervisor", "manager", "higher up"],
    "refund": ["refund", "money back", "reimburs"],
    "churn": ["churn", "cancel", "leave", "switch", "competitor", "lose customer", "leaving"],
}


# ----------------------------------------------------------------------------
# Data
# ----------------------------------------------------------------------------
class Data:
    def __init__(self, labeled=P + "calls_labeled_v3.parquet", features=P + "call_features.parquet",
                 turn_feats=P + "turn_features.parquet", turns=P + "turns_normalized.parquet"):
        self.calls = C.load_frame(labeled, features)
        self.tf = pd.read_parquet(turn_feats)
        txt = pd.read_parquet(turns, columns=["transcript_id", "turn_id", "text"])
        self.text = {(t, i): s for t, i, s in zip(txt.transcript_id, txt.turn_id, txt.text)}
        self.intents = sorted(self.calls["intent"].dropna().unique())


# ----------------------------------------------------------------------------
# Stage 4: query parsing
# ----------------------------------------------------------------------------
def parse_query(q, intents):
    ql = q.lower()
    outcome = None
    for o, words in OUTCOME_WORDS.items():
        if any(w in ql for w in words):
            outcome = o
            break
    intent = None
    for it in intents:
        stem = it.lower().rstrip("s")
        if it.lower() in ql or (len(stem) > 5 and stem in ql):
            intent = it
            break
    return outcome, intent


# ----------------------------------------------------------------------------
# Stage 6: attribution by leave-one-turn-out ablation
# ----------------------------------------------------------------------------
def fit_classifier(treat, control):
    d = pd.concat([treat.assign(_y=1), control.assign(_y=0)])
    X, y = d[CLASSIFIER_FEATURES], d["_y"]
    clf = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                        LogisticRegression(max_iter=1000, class_weight="balanced", C=0.5))
    clf.fit(X, y)
    return clf


def _call_row(g, tid):
    meta = pd.DataFrame({"transcript_id": [tid], "role_mapping_uncertain": [False]})
    return F.aggregate_calls(g, meta)[CLASSIFIER_FEATURES]


def ablate_call(clf, g, tid):
    """Drop each turn in turn, recompute call features, measure probability drop."""
    g = g.sort_values("turn_id").reset_index(drop=True)
    base = clf.predict_proba(_call_row(g, tid))[0, 1]
    rows = [_call_row(g.drop(index=i), tid) for i in range(len(g))]
    p = clf.predict_proba(pd.concat(rows, ignore_index=True))[:, 1]
    out = g[["turn_id", "speaker_role"]].copy()
    out["drop"] = base - p
    return base, out


def attribute(data, outcome, treat, control, n_calls=12, top_k=3, seed=42):
    clf = fit_classifier(treat, control)
    probs = clf.predict_proba(treat[CLASSIFIER_FEATURES])[:, 1]
    # representative calls: spread across the probability range, not just the extremes
    order = np.argsort(probs)
    picks = order[np.linspace(len(order) // 3, len(order) - 1, min(n_calls, len(order)), dtype=int)]
    chosen = treat.iloc[picks]["transcript_id"].tolist()

    tf_idx = data.tf[data.tf["transcript_id"].isin(chosen)]
    evidence, top_drops, mean_drops = [], [], []
    for tid, g in tf_idx.groupby("transcript_id"):
        if len(g) < 4:
            continue
        base, res = ablate_call(clf, g, tid)
        res = res.sort_values("drop", ascending=False)
        top_drops.append(res["drop"].iloc[0])
        mean_drops.append(res["drop"].mean())          # expected drop of a random turn
        for _, r in res.head(top_k).iterrows():
            evidence.append({"transcript_id": tid, "turn_id": int(r["turn_id"]), "speaker": r["speaker_role"],
                             "prob_drop": round(float(r["drop"]), 4), "call_prob": round(float(base), 3),
                             "text": data.text.get((tid, int(r["turn_id"])), "")[:300]})
    evidence.sort(key=lambda e: -e["prob_drop"])

    validation = {"calls_ablated": len(top_drops)}
    if len(top_drops) >= 5:
        w = stats.wilcoxon(top_drops, mean_drops, alternative="greater")
        validation.update(mean_top_turn_drop=round(float(np.mean(top_drops)), 4),
                          mean_random_turn_drop=round(float(np.mean(mean_drops)), 4),
                          top_beats_random_p=float(f"{w.pvalue:.2e}"))
    return evidence, validation


# ----------------------------------------------------------------------------
# Stage 7: synthesis (Claude writes only from the evidence)
# ----------------------------------------------------------------------------
SYSTEM = ("You explain why a business outcome happens in customer-service calls. Use ONLY the statistical "
          "findings and cited turns provided. Do not add outside claims or causes. Say 'associated with', "
          "not 'causes'. Cite turns as [call transcript_id, turn N]. If data is insufficient, say so plainly. "
          "Keep it under 200 words.")


def build_prompt(question, stats_result, evidence, validation):
    payload = {"question": question, "statistics": stats_result, "attribution_validation": validation,
               "cited_turns": evidence[:6]}
    return json.dumps(payload, indent=1, default=str)


def synthesize(question, stats_result, evidence, validation):
    if stats_result["status"] == "insufficient_data":
        return stats_result["message"]
    prompt = build_prompt(question, stats_result, evidence, validation)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "[no ANTHROPIC_API_KEY set - evidence bundle that would be sent to Claude]\n" + prompt
    import anthropic
    msg = anthropic.Anthropic().messages.create(model=MODEL, max_tokens=700, system=SYSTEM,
                                                messages=[{"role": "user", "content": prompt}])
    return msg.content[0].text


# ----------------------------------------------------------------------------
# Task 2: session
# ----------------------------------------------------------------------------
def classify_followup(msg, intents, current):
    """Heuristic follow-up classifier (one LLM call replaces this when a key is set)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            ask = ("Classify this follow-up in a call-analytics chat. Current filter: %s. Intents: %s. "
                   "Reply ONLY JSON: {\"type\":\"refine|drill_down|new_topic\",\"outcome\":null|string,"
                   "\"intent\":null|string}. Message: %s" % (json.dumps(current), intents, msg))
            r = anthropic.Anthropic().messages.create(model=MODEL, max_tokens=120,
                                                      messages=[{"role": "user", "content": ask}])
            return json.loads(re.search(r"\{.*\}", r.content[0].text, re.S).group(0))
        except Exception:
            pass
    outcome, intent = parse_query(msg, intents)
    ml = msg.lower()
    if outcome and outcome != current.get("outcome"):
        return {"type": "new_topic", "outcome": outcome, "intent": intent}
    if intent and intent != current.get("intent"):
        return {"type": "refine", "outcome": None, "intent": intent}
    if any(w in ml for w in ["more", "example", "show", "quote", "another", "next"]):
        return {"type": "drill_down", "outcome": None, "intent": None}
    return {"type": "drill_down", "outcome": None, "intent": None}


class Session:
    def __init__(self, data):
        self.data = data
        self.filter = {}
        self.stats = None
        self.evidence = []
        self.validation = {}
        self.shown = 0
        self.history = []          # capped: query, one-line answer, timestamp

    def _run(self, question, rerank_only=False):
        f = self.filter
        new = C.compare(self.data.calls, f["outcome"], f.get("intent"))
        prev_n = self.stats["n_treatment"] if self.stats else None
        self.stats = new
        if new["status"] != "ok":
            self.evidence, self.validation, self.shown = [], {}, 0
            return
        big_change = prev_n is None or abs(new["n_treatment"] - prev_n) / max(prev_n, 1) > 0.20
        if big_change or not self.evidence:
            treat, control = C.build_groups(self.data.calls, f["outcome"], f.get("intent"))
            self.evidence, self.validation = attribute(self.data, f["outcome"], treat, control)
        self.shown = 0     # evidence is overwritten, never appended

    def ask(self, msg):
        if not self.filter:
            kind = "new_topic"
            outcome, intent = parse_query(msg, self.data.intents)
            delta = {"outcome": outcome, "intent": intent}
        else:
            delta = classify_followup(msg, self.data.intents, self.filter)
            kind = delta["type"]

        if kind == "new_topic":
            outcome = delta.get("outcome")
            if not outcome:
                return "I could not tell which outcome you mean. Try escalation, refund or churn."
            self.filter = {"outcome": outcome, "intent": delta.get("intent")}
            self.stats, self.evidence = None, []
            self._run(msg)
            answer = synthesize(msg, self.stats, self.evidence, self.validation)
        elif kind == "refine":
            if delta.get("outcome"):
                self.filter["outcome"] = delta["outcome"]
            if delta.get("intent"):
                self.filter["intent"] = delta["intent"]
            self._run(msg)
            answer = synthesize(msg, self.stats, self.evidence, self.validation)
        else:  # drill_down: no recomputation, serve more of what is already computed
            chunk = self.evidence[self.shown + 6: self.shown + 12] or self.evidence[:6]
            self.shown += 6
            lines = ["[%s turn %d, %s, prob drop %.3f] %s" % (e["transcript_id"], e["turn_id"], e["speaker"],
                                                              e["prob_drop"], e["text"]) for e in chunk]
            answer = "More cited turns for the current findings:\n" + "\n".join(lines) if lines else \
                     "No further evidence available for this filter."

        self.history.append({"query": msg, "summary": answer.split("\n")[0][:140],
                             "time": dt.datetime.now().isoformat(timespec="seconds")})
        self.history = self.history[-20:]
        return answer


if __name__ == "__main__":
    print("Loading data...")
    s = Session(Data())
    print("Ask about escalation / refund / churn. Ctrl-C to quit.")
    while True:
        try:
            print("\n" + s.ask(input("\n> ")))
        except (KeyboardInterrupt, EOFError):
            break
