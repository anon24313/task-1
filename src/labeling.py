"""
Outcome labeling stage.

The raw dataset has no ground-truth outcome column, so calls are silver-labeled
for three target outcomes (escalation, refund, churn; not mutually exclusive)
using three independent signals:

  1. intent_prior   - a fixed heuristic mapping from the `intent` field
  2. zero_shot       - batched zero-shot classification on `reason_for_call`
  3. keyword_hit     - regex scan over the full conversation text

Confidence-tiered merge rule, per outcome:
  - zero-shot score >= 0.75                                -> confirmed alone
  - zero-shot score in [0.5, 0.75) AND (intent OR keyword)  -> confirmed
  - intent AND keyword both fire (regardless of zs score)   -> confirmed,
    UNLESS zero-shot gave a confident "no" (score < 0.2)
  - otherwise, if any signal fired at all                   -> weak
  - otherwise                                                -> none

FIXES applied after manual review of 50 hand-labeled calls, PLUS a new fix
found after inspecting the full 19,618-call run's confirmed-churn sample:

  1. (earlier) merge_outcome no longer lets "intent_hit and keyword_hit"
     override a confident zero-shot "no" (score < ZS_CONFIDENT_NO_THRESHOLD).
  2. (earlier) Removed "Cross-Brand Mentions" and "Competitor comparison"
     from the churn INTENT_PRIOR.
  3. (NEW) Removed "Cancellation Policies" from the churn INTENT_PRIOR.
     At full-corpus scale, the confirmed-churn sample was dominated by
     hotel/flight BOOKING cancellations ("cancellation fees for my
     upcoming stay") getting scored as customer CHURN (leaving the
     company) -- these are semantically unrelated but "Cancellation
     Policies" was pushing the intent_hit signal toward churn regardless.
  4. (NEW, two iterations) Rewrote the churn candidate label.
     bart-large-mnli's original label -- "customer is considering
     cancelling or switching providers" -- has enough lexical overlap
     with "cancel a hotel booking" and "compare plans/pricing" that it
     was scoring pure booking-cancellation and comparison-shopping calls
     at 0.85-0.99 confidence with zero real churn signal. Manual spot
     check of the confirmed-churn sample found roughly 80% were false
     positives of exactly this kind. A v1 fix tried a compound label
     explicitly contrasting "stop being a customer" against "cancelling a
     single booking" -- confirmed empirically to backfire: NLI models
     score entailment against one hypothesis and handle a contrastive
     clause badly, collapsing score_zero_shot_churn to a median of 0.0022
     across a 200-call sample (near-total false negatives instead of
     false positives). The current (v2) label is short and single-concept,
     leaving the booking/comparison disambiguation to the INTENT_PRIOR
     removals (fix #2, #3) instead. Raising the threshold alone would not
     have fixed the original v0 problem, since those calls were already
     scoring high with confidence, not sitting near the boundary.

KNOWN LIMITATION (documented, not fixed): the churn keyword patterns below
had ZERO matches across all 50 hand-reviewed calls, including all 5 calls
manually confirmed as genuine churn. Real churn signals in this corpus are
phrased with actual company names ("considering AT&T fiber", "might switch
to QuickBuy") rather than generic terms ("a competitor", "cancel my
subscription"), so keyword corroboration for churn has near-zero recall on
this dataset. Churn labeling therefore leans almost entirely on the
zero-shot score for this outcome specifically.

IMPORTANT: because the candidate label text changed (fix #4), zero-shot
scores for churn are NOT comparable between the old run and a re-run with
this file. The full corpus must be re-run from scratch for churn numbers to
be trustworthy -- escalation and refund labels are unaffected and don't
strictly need a re-run, but re-running everything together keeps one
consistent calls_labeled.parquet rather than a patchwork of two runs.

Usage:
  Local correctness check (CPU, small sample, fast):
    python3 src/labeling.py --sample 200 --device cpu

  Full run (Colab T4):
    python3 src/labeling.py --device 0 --batch-size 32

  Offline-only (no model download, no zero-shot signal; intent+keyword
  symmetric case is the only way to confirm an outcome in this mode):
    python3 src/labeling.py --offline-only
"""

import argparse
import re
from pathlib import Path

import pandas as pd

OUTCOMES = ["escalation", "refund", "churn"]

ZS_CONFIRM_THRESHOLD = 0.75
ZS_WEAK_THRESHOLD = 0.5
ZS_CONFIDENT_NO_THRESHOLD = 0.2

INTENT_PRIOR = {
    "Replacement vs Refund": "refund",
    "Product Returns": "refund",
    "Refund Policy": "refund",
    "Refund Delays": "refund",
    "Claims & refunds": "refund",
    "Fee Complaints": "refund",
    "Churn Prediction": "churn",
    # "Cross-Brand Mentions", "Competitor comparison", and "Cancellation
    # Policies" intentionally excluded from churn -- see FIX #2 and #3 in
    # the module docstring. All three intents commonly co-occur with calls
    # that are booking cancellations / comparison-shopping / staying put,
    # not genuine leave-intent.
    "Service Complaints": "escalation",
    "Fraud Alerts": "escalation",
    "Urgency & Stress": "escalation",
    "Customer trust": "escalation",
}

_KEYWORD_PATTERNS = {
    "escalation": [
        r"supervisor", r"manager", r"escalat", r"speak to (someone|a human|a person)",
        r"higher up", r"file a complaint",
    ],
    "refund": [
        r"refund", r"money back", r"reimburse", r"chargeback",
    ],
    "churn": [
        r"cancel(l?ing)? my (account|subscription|policy|plan|service)",
        r"switch(ing)? to (a competitor|another)", r"close my account",
        r"take my business elsewhere", r"might as well cancel",
        r"thinking about cancel", r"consider(ing)? cancel",
    ],
}
_COMPILED_PATTERNS = {
    outcome: [re.compile(p, flags=re.IGNORECASE) for p in patterns]
    for outcome, patterns in _KEYWORD_PATTERNS.items()
}

_ZERO_SHOT_LABELS = {
    "customer escalated to a supervisor or manager": "escalation",
    "customer requested a refund or money back": "refund",
    # FIX #4 (v1, REVERTED): a compound "X, as opposed to Y" label was tried
    # to explicitly rule out booking cancellations, but zero-shot NLI models
    # score entailment against a single hypothesis and handle contrastive/
    # negated clauses badly -- confirmed empirically: this collapsed
    # score_zero_shot_churn to a median of 0.0022 (mean 0.029) across a
    # 200-call random sample, i.e. it stopped firing almost entirely rather
    # than firing more precisely.
    # FIX #4 (v2, current): short, single-concept, no contrastive clause.
    # The booking/comparison disambiguation is left to the INTENT_PRIOR
    # removals (fix #2, #3) instead of being folded into this one sentence.
    "customer wants to stop being a customer or switch to a different company": "churn",
}
_CANDIDATE_LABELS = list(_ZERO_SHOT_LABELS.keys())


def keyword_signal(conversation_text: str) -> dict:
    text_lower = conversation_text.lower()
    return {
        outcome: any(p.search(text_lower) for p in patterns)
        for outcome, patterns in _COMPILED_PATTERNS.items()
    }


def intent_signal(intent: str) -> dict:
    prior = INTENT_PRIOR.get(intent)
    return {outcome: (outcome == prior) for outcome in OUTCOMES}


def zero_shot_signals_batch(texts: list, classifier) -> list:
    """Runs the whole list through the pipeline in one call (batched via the
    pipeline's own batch_size, not a per-row loop).

    Returns a list of {outcome: raw_score} dicts, same order as `texts`.
    Thresholding happens later in merge_outcome, since the tiered rule needs
    the raw score, not just a boolean.
    """
    results = classifier(texts, candidate_labels=_CANDIDATE_LABELS, multi_label=True)
    if isinstance(results, dict):
        results = [results]

    raw_scores = []
    for result in results:
        scores = dict(zip(result["labels"], result["scores"]))
        raw_scores.append({_ZERO_SHOT_LABELS[label]: score for label, score in scores.items()})
    return raw_scores


def merge_outcome(zs_score, intent_hit: bool, keyword_hit: bool) -> str:
    """Confidence-tiered merge for a single outcome on a single call.
    Returns "confirmed", "weak", or "none".

    zs_score may be None (offline-only mode, no zero-shot signal available).
    """
    if zs_score is not None:
        if zs_score >= ZS_CONFIRM_THRESHOLD:
            return "confirmed"
        if zs_score >= ZS_WEAK_THRESHOLD and (intent_hit or keyword_hit):
            return "confirmed"
        if zs_score < ZS_CONFIDENT_NO_THRESHOLD:
            return "none"

    if intent_hit and keyword_hit:
        return "confirmed"

    has_any_evidence = (
        (zs_score is not None and zs_score >= ZS_WEAK_THRESHOLD)
        or intent_hit
        or keyword_hit
    )
    return "weak" if has_any_evidence else "none"


def merge_call(intent_sig: dict, keyword_sig: dict, zs_scores: dict = None) -> dict:
    """intent_sig, keyword_sig: {outcome: bool}. zs_scores: {outcome: float} or None."""
    merged = {}
    any_confirmed = False
    any_weak = False

    for outcome in OUTCOMES:
        zs_score = zs_scores.get(outcome) if zs_scores else None
        status = merge_outcome(zs_score, intent_sig.get(outcome, False), keyword_sig.get(outcome, False))

        merged[f"outcome_{outcome}"] = status == "confirmed"
        merged[f"status_{outcome}"] = status
        merged[f"sig_intent_{outcome}"] = intent_sig.get(outcome, False)
        merged[f"sig_keyword_{outcome}"] = keyword_sig.get(outcome, False)
        if zs_scores is not None:
            merged[f"score_zero_shot_{outcome}"] = zs_score

        any_confirmed = any_confirmed or (status == "confirmed")
        any_weak = any_weak or (status == "weak")

    merged["low_confidence"] = (not any_confirmed) and any_weak
    return merged


def build_conv_text_lookup(turns_df: pd.DataFrame) -> pd.Series:
    return turns_df.groupby("transcript_id")["text"].apply(lambda texts: " ".join(texts))


def label_offline(calls_df: pd.DataFrame, turns_df: pd.DataFrame) -> pd.DataFrame:
    """intent_prior + keyword_hit only, no model call. For quick mechanics
    testing. Only the intent+keyword symmetric case can confirm an outcome
    in this mode, since the zero-shot tiers are unavailable.
    """
    conv_text_by_call = build_conv_text_lookup(turns_df)

    rows = []
    for _, call in calls_df.iterrows():
        tid = call["transcript_id"]
        conv_text = conv_text_by_call.get(tid, "")
        merged = merge_call(intent_signal(call["intent"]), keyword_signal(conv_text), zs_scores=None)
        rows.append({"transcript_id": tid, **merged})

    return calls_df.merge(pd.DataFrame(rows), on="transcript_id", how="left")


def label_with_zero_shot(calls_df: pd.DataFrame, turns_df: pd.DataFrame, classifier) -> pd.DataFrame:
    """Full 3-signal labeling, zero-shot run batched over all reason_for_call texts at once."""
    conv_text_by_call = build_conv_text_lookup(turns_df)

    reasons = calls_df["reason_for_call"].fillna("").tolist()
    zero_shot_scores = zero_shot_signals_batch(reasons, classifier)

    rows = []
    for (_, call), zs_scores in zip(calls_df.iterrows(), zero_shot_scores):
        tid = call["transcript_id"]
        conv_text = conv_text_by_call.get(tid, "")
        merged = merge_call(intent_signal(call["intent"]), keyword_signal(conv_text), zs_scores=zs_scores)
        rows.append({"transcript_id": tid, **merged})

    return calls_df.merge(pd.DataFrame(rows), on="transcript_id", how="left")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Label call outcomes.")
    parser.add_argument("--meta", default="data/processed/calls_normalized.parquet")
    parser.add_argument("--turns", default="data/processed/turns_normalized.parquet")
    parser.add_argument("--out", default="data/processed/calls_labeled.parquet")
    parser.add_argument("--offline-only", action="store_true",
                         help="Skip zero-shot signal, use only intent_prior + keyword_hit.")
    parser.add_argument("--sample", type=int, default=None,
                         help="Only label N random calls. Use for local CPU correctness checks.")
    parser.add_argument("--device", default="cpu",
                         help="'cpu', 'cuda', 'cuda:0', or a GPU index like '0'. Use '0' on Colab T4.")
    parser.add_argument("--batch-size", type=int, default=16,
                         help="Pipeline batch size. Larger is faster on GPU, irrelevant on CPU.")
    args = parser.parse_args()

    calls_df = pd.read_parquet(args.meta)
    turns_df = pd.read_parquet(args.turns)

    if args.sample:
        # NOTE: random, not .head(n) -- a plain .head(n) samples whatever
        # order calls_normalized.parquet happens to be in, which is not
        # guaranteed representative (confirmed non-representative for this
        # dataset: a .head(200) run showed 19.5% churn vs. the full
        # corpus's 31.8% before this fix). random_state fixed for
        # reproducibility.
        calls_df = calls_df.sample(n=args.sample, random_state=42).reset_index(drop=True)
        keep_ids = set(calls_df["transcript_id"])
        turns_df = turns_df[turns_df["transcript_id"].isin(keep_ids)].reset_index(drop=True)
        print(f"Sampled down to {len(calls_df)} random calls for a quick correctness check.")

    if args.offline_only:
        labeled = label_offline(calls_df, turns_df)
    else:
        from transformers import pipeline

        device = args.device
        if device not in ("cpu",):
            device = int(device) if device.isdigit() else device

        clf = pipeline(
            "zero-shot-classification",
            model="facebook/bart-large-mnli",
            device=device,
            batch_size=args.batch_size,
        )
        labeled = label_with_zero_shot(calls_df, turns_df, clf)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    labeled.to_parquet(args.out, index=False)

    print(f"Labeled {len(labeled)} calls -> {args.out}")
    for outcome in OUTCOMES:
        n = labeled[f"outcome_{outcome}"].sum()
        print(f"  {outcome}: {n} ({100 * n / len(labeled):.1f}%)")
    lc = labeled["low_confidence"].sum()
    print(f"  low_confidence: {lc} ({100 * lc / len(labeled):.1f}%)")
