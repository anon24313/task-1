"""
Outcome labeling, v3 (event/turn-based; replaces the reason_for_call zero-shot
+ tiered-merge approach in labeling.py).

WHY v3
  An audit of v2 against the raw data and a 50-call hand check found:
    * escalation was labeled on ~21% of calls, but only ~1% of calls contain an
      actual escalation: 161 calls (0.8%) have a Supervisor/Manager speaker turn.
      The v2 keyword scan searched the whole conversation, so agent-only phrases
      such as "this could lead to escalations" produced most of its hits.
    * zero-shot on the one-line reason_for_call cannot see the evidence: the
      calls with real churn intent have unrelated summaries; the switching
      language exists only in the customer's own turns.
  v3 therefore labels EVENTS found in the turns, with per-signal columns so every
  label can be audited and scored.

DEFINITIONS (fixed before scoring, so metrics are not tuned to the labels)
  escalation : a supervisor/manager actually joins (speaker turn), OR the agent
               announces an immediate handoff/escalation ("i'm bringing in a
               supervisor", "i'm escalating this now"). A customer DEMANDING a
               supervisor is kept as a separate column (esc_customer_demand) and
               is NOT part of the label. Conditional offers ("i can transfer you
               if needed") do not count.
  refund     : the customer REQUESTS money back (refund language in a customer
               turn that is not hypothetical/policy chatter), OR the agent
               confirms a refund is being processed. Refund policy questions and
               "what if I needed a refund" are not requests.
  churn      : a customer turn expresses intent to leave/switch/stop using the
               company. Two stages: (1) a broad, recall-first regex prefilter on
               CUSTOMER turns; (2) zero-shot NLI on the flagged turns only
               (run once on Colab with --churn-zs). Call score = max turn score.

PIPELINE
  python3 src/labeling_v3.py                        # rules + churn prefilter (fast, local ok)
  python3 src/labeling_v3.py --churn-zs --device 0  # Colab: scores flagged customer turns
  python3 src/labeling_v3.py                        # re-run: merges churn scores -> final labels
  python3 src/eval_labels.py ...                    # per-outcome precision/recall + disagreements

KNOWN LIMITATIONS (state these in the report)
  * Roles come from Stage 0. Some calls tag a customer utterance as Agent (the
    symptom, back-to-back same-speaker turns, appears in ~0.75% of turns; some
    are legitimate), so role-conditioned signals can miss or misfire.
  * Refund hypothetical/policy filter was designed after inspecting false
    positives in the same 50 calls used to check it, so those numbers are
    optimistic; validate on a fresh sample.
  * The churn threshold is NOT pre-set from evidence: calibrate it with
    eval_labels.py's threshold sweep before trusting outcome_churn.
"""

import argparse
import re
from pathlib import Path

import pandas as pd

OUTCOMES = ["escalation", "refund", "churn"]
SUPERVISOR_ROLES = {"supervisor", "manager"}

# ---- escalation -----------------------------------------------------------
_ANNOUNCED_HANDOFF = re.compile(
    r"i(?:’|')?m (?:now )?(?:bringing in|connecting you (?:with|to)|getting|escalating|"
    r"transferring you to|looping in|putting you through to)[a-z ]{0,20}(?:a |my |the )?(?:supervisor|manager)"
    r"|i(?:’|')?m (?:now )?escalating (?:this|your|the)"
    r"|(?:supervisor|manager) (?:is )?(?:on the line|joining (?:us|the call)|now on)"
    r"|i have (?:a |my |the )?(?:supervisor|manager) (?:on the line|here|with me)",
    re.IGNORECASE,
)
_CUSTOMER_DEMAND = re.compile(
    r"supervisor|manager|escalat|higher up|(?:speak|talk) (?:to|with) (?:someone|a human|a person)",
    re.IGNORECASE,
)

# ---- refund ---------------------------------------------------------------
_REFUND_MENTION = re.compile(r"refund|money back|reimburs|charge ?back|credit(?:ed)? back", re.IGNORECASE)
_REFUND_HYPOTHETICAL = re.compile(
    r"\bwhat if\b|\bif i (?:need|want|were|had|decide|have to|end up)\b|\bpolic(?:y|ies)\b"
    r"|\bis (?:that|it) an option\b|\bwhat about (?:a )?(?:um |uh )?refund\b|\bin case\b"
    r"|\bwould i (?:get|be)\b|\bdo you (?:offer|give|provide)\b|\bhow (?:does|do) (?:the |your |a )?refund",
    re.IGNORECASE,
)
_REFUND_AGENT_CONFIRM = re.compile(
    r"(?:processed|initiated|issued|submitted|approved|credited|started|processing)[a-z ,]{0,30}refund"
    r"|refund[a-z ,]{0,40}(?:will be|has been|is being|been|was) (?:credited|processed|issued|approved|sent|initiated)"
    r"|full refund",
    re.IGNORECASE,
)
_REFUND_DENIAL = re.compile(
    r"not (?:eligible|possible|available)|unable|can(?:not|'t|’t)|no refund|non-?refundable|denied|not able",
    re.IGNORECASE,
)

# ---- churn prefilter (recall-first; stage 2 zero-shot does the discrimination) --
_CHURN_PREFILTER = re.compile(
    r"\bswitch(?:ing)?\b|\bconsider(?:ing)?\b"
    r"|thinking (?:about|of) (?:switch|leav|cancel|mov|going|using|trying|other)|thinking about \w+ more"
    r"|elsewhere|other (?:provider|option|compan|airline|hotel|bank|carrier|insurer)s?|competitor"
    r"|stay(?:ing)? loyal|remain loyal|not sure if i (?:should|will|want)"
    r"|(?:won.t|will not|not going to|never|might not|may not) (?:\w+ ){0,3}"
    r"(?:again|come back|return|renew|stay with|use|order|book|shop|fly)"
    r"|cancel(?:l?ing)? my (?:account|subscription|plan|service|membership|policy|card)"
    r"|leave (?:you|this|the company)|go with (?:another|someone|a different)|take my business",
    re.IGNORECASE,
)
CHURN_HYPOTHESIS = "The customer says they might leave, cancel, or switch to another company."


def _clip(text: str, n: int = 200) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()[:n]


def _has(rx: "re.Pattern", s: pd.Series) -> pd.Series:
    return s.map(lambda t: bool(rx.search(t)))


def prepare_turns(turns_df: pd.DataFrame) -> pd.DataFrame:
    need = {"transcript_id", "turn_id", "text"}
    missing = need - set(turns_df.columns)
    if missing or not ({"speaker_role", "speaker"} & set(turns_df.columns)):
        raise ValueError(f"turns table needs {need} plus speaker_role or speaker; missing {missing}")
    df = turns_df.copy()
    role_col = "speaker_role" if "speaker_role" in df.columns else "speaker"
    df["_role"] = df[role_col].astype(str).str.strip().str.lower()
    raw_col = "speaker" if "speaker" in df.columns else role_col
    df["_raw"] = df[raw_col].astype(str).str.strip().str.lower()
    df["text"] = df["text"].fillna("").astype(str)
    return df.sort_values(["transcript_id", "turn_id"]).reset_index(drop=True)


def _first_text(df: pd.DataFrame, mask: pd.Series) -> pd.Series:
    """transcript_id -> first matching turn text (clipped)."""
    return df.loc[mask].groupby("transcript_id")["text"].first().map(_clip)


def compute_rule_signals(turns_df: pd.DataFrame) -> pd.DataFrame:
    df = prepare_turns(turns_df)
    agent, cust = df["_role"] == "agent", df["_role"] == "customer"

    sup = df["_role"].isin(SUPERVISOR_ROLES) | df["_raw"].isin(SUPERVISOR_ROLES)
    ann = agent & _has(_ANNOUNCED_HANDOFF, df["text"])
    dem = cust & _has(_CUSTOMER_DEMAND, df["text"])

    ref_any = cust & _has(_REFUND_MENTION, df["text"])
    ref_req = ref_any & ~_has(_REFUND_HYPOTHETICAL, df["text"])
    ref_conf = agent & _has(_REFUND_AGENT_CONFIRM, df["text"]) & ~_has(_REFUND_DENIAL, df["text"])

    pre = cust & _has(_CHURN_PREFILTER, df["text"])

    sig = pd.DataFrame({"transcript_id": df["transcript_id"].unique()}).set_index("transcript_id")
    for name, mask in [
        ("esc_structural", sup), ("esc_announced", ann), ("esc_customer_demand", dem),
        ("refund_mention_any", ref_any), ("refund_request_customer", ref_req),
        ("refund_agent_confirm", ref_conf), ("churn_candidate", pre),
    ]:
        ev = _first_text(df, mask)
        sig[name] = sig.index.isin(ev.index)
        sig[f"ev_{name}"] = ev.reindex(sig.index)
    candidates = df.loc[pre, ["transcript_id", "turn_id", "text"]].reset_index(drop=True)
    return sig.reset_index(), candidates


def merge_labels(sig: pd.DataFrame, churn_scores: pd.DataFrame, churn_threshold: float) -> pd.DataFrame:
    out = sig.copy()

    # escalation: event-based
    out["outcome_escalation"] = out["esc_structural"] | out["esc_announced"]
    out["conf_escalation"] = "none"
    out.loc[out["esc_announced"], "conf_escalation"] = "medium"
    out.loc[out["esc_structural"], "conf_escalation"] = "high"
    out["evidence_escalation"] = out["ev_esc_structural"].fillna(out["ev_esc_announced"])

    # refund: customer request OR agent confirmation
    out["outcome_refund"] = out["refund_request_customer"] | out["refund_agent_confirm"]
    out["conf_refund"] = "none"
    out.loc[out["outcome_refund"], "conf_refund"] = "medium"
    out.loc[out["refund_request_customer"] & out["refund_agent_confirm"], "conf_refund"] = "high"
    out["evidence_refund"] = out["ev_refund_request_customer"].fillna(out["ev_refund_agent_confirm"])

    # churn: prefilter + zero-shot max over flagged customer turns
    if churn_scores is not None and len(churn_scores):
        best = churn_scores.sort_values("score", ascending=False).groupby("transcript_id").first()
        out["churn_zs_max"] = out["transcript_id"].map(best["score"])
        out["evidence_churn"] = out["transcript_id"].map(best["text"]).map(_clip)
        out["churn_scored"] = True
        out["outcome_churn"] = out["churn_zs_max"].fillna(0) >= churn_threshold
        out["conf_churn"] = "none"
        out.loc[out["outcome_churn"], "conf_churn"] = "medium"
        out.loc[out["churn_zs_max"].fillna(0) >= 0.95, "conf_churn"] = "high"
    else:
        out["churn_zs_max"] = float("nan")
        out["evidence_churn"] = out["ev_churn_candidate"]
        out["churn_scored"] = False
        out["outcome_churn"] = False
        out["conf_churn"] = "none"

    out["low_confidence"] = (out[[f"conf_{o}" for o in OUTCOMES]] == "medium").any(axis=1)
    return out


def sanity_checks(labeled: pd.DataFrame) -> None:
    n = len(labeled)
    n_struct = int(labeled["esc_structural"].sum())
    print(f"  structural supervisor/manager calls: {n_struct}")
    if n > 15000 and not (120 <= n_struct <= 210):
        print("  WARNING: full corpus should show ~161 structural escalations (raw data has 161 calls with a "
              "Supervisor/Manager speaker turn). Check that normalize.py preserved those roles.")
    cand = labeled["churn_candidate"].mean()
    print(f"  churn prefilter flags {100 * cand:.1f}% of calls (recall-first; zero-shot narrows this)")


def run_churn_zero_shot(cand_path: str, scores_path: str, device, batch_size: int) -> None:
    from transformers import pipeline  # imported here so the rule stage needs no torch

    cand = pd.read_parquet(cand_path)
    partial = Path(scores_path).with_suffix(".partial.parquet")
    done = pd.read_parquet(partial) if partial.exists() else cand.iloc[0:0].assign(score=[])
    start = len(done)
    print(f"{len(cand)} candidate turns; resuming from {start}")

    clf = pipeline("zero-shot-classification", model="facebook/bart-large-mnli",
                   device=device, batch_size=batch_size)
    chunk = 4000
    parts = [done]
    for i in range(start, len(cand), chunk):
        piece = cand.iloc[i:i + chunk].copy()
        res = clf(piece["text"].tolist(), candidate_labels=[CHURN_HYPOTHESIS],
                  hypothesis_template="{}", multi_label=True)
        if isinstance(res, dict):
            res = [res]
        piece["score"] = [r["scores"][0] for r in res]
        parts.append(piece)
        pd.concat(parts, ignore_index=True).to_parquet(partial, index=False)  # checkpoint (Colab can disconnect)
        print(f"  scored {min(i + chunk, len(cand))}/{len(cand)}")
    final = pd.concat(parts, ignore_index=True)
    final.to_parquet(scores_path, index=False)
    print(f"Wrote {scores_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Event/turn-based outcome labeling (v3).")
    ap.add_argument("--calls", default="data/processed/calls_normalized.parquet")
    ap.add_argument("--turns", default="data/processed/turns_normalized.parquet")
    ap.add_argument("--out", default="data/processed/calls_labeled_v3.parquet")
    ap.add_argument("--candidates", default="data/processed/churn_candidate_turns.parquet")
    ap.add_argument("--scores", default="data/processed/churn_turn_scores.parquet")
    ap.add_argument("--churn-zs", action="store_true", help="Colab stage: zero-shot score flagged customer turns.")
    ap.add_argument("--device", default="cpu", help="'cpu' or a GPU index like '0' (Colab T4).")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--churn-threshold", type=float, default=0.8,
                    help="PROVISIONAL. Calibrate with eval_labels.py's sweep before trusting outcome_churn.")
    args = ap.parse_args()

    if args.churn_zs:
        dev = int(args.device) if str(args.device).isdigit() else args.device
        run_churn_zero_shot(args.candidates, args.scores, dev, args.batch_size)
        raise SystemExit(0)

    calls = pd.read_parquet(args.calls)
    turns = pd.read_parquet(args.turns)
    sig, candidates = compute_rule_signals(turns)

    scores = pd.read_parquet(args.scores) if Path(args.scores).exists() else None
    labeled = calls.merge(merge_labels(sig, scores, args.churn_threshold), on="transcript_id", how="left")

    for col in [c for c in labeled.columns if c.startswith(("outcome_", "esc_", "refund_", "churn_cand"))]:
        if labeled[col].dtype == object:
            labeled[col] = labeled[col].fillna(False).astype(bool)
    if "role_mapping_uncertain" in labeled.columns:
        labeled["low_confidence"] = labeled["low_confidence"] | labeled["role_mapping_uncertain"].fillna(False).astype(bool)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    labeled.to_parquet(args.out, index=False)
    if scores is None:  # candidates only change on the first run; rewriting them on the merge run is needless I/O
        candidates.to_parquet(args.candidates, index=False)

    print(f"Labeled {len(labeled)} calls -> {args.out}")
    for o in OUTCOMES:
        k = int(labeled[f"outcome_{o}"].sum())
        print(f"  {o}: {k} ({100 * k / len(labeled):.1f}%)")
    print(f"  low_confidence: {int(labeled['low_confidence'].sum())}")
    sanity_checks(labeled)
    if scores is None:
        print("\nCHURN NOT YET SCORED: outcome_churn is all False. Run on Colab:\n"
              "  python3 src/labeling_v3.py --churn-zs --device 0\nthen re-run this script.")
