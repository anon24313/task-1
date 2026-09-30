"""
Scores labeling_v3 output against reference labels and lists disagreements.

References (any that exist):
  --manual  hand_validation_sample.csv  (manual_escalation / manual_refund / manual_churn)
  --groq    calls_labeled_groq_sample.csv (groq_outcome_* from full-conversation LLM judge)

Reports per outcome: TP/FP/FN/TN, precision, recall, F1 with Wilson 95% intervals
(n is small, so the intervals matter), a churn-threshold sweep when churn has been
scored, and writes every disagreement with the evidence text to a CSV for review.

Note on the 50-call sample: it contains almost no escalation events (base rate ~1%),
so it cannot validate the escalation rule. Use --esc-review to dump a precision
sample (predicted positives) and a recall probe (customer demanded a supervisor but
no event was labeled) for reading by hand.

Usage:
  python3 src/eval_labels.py
  python3 src/eval_labels.py --esc-review 30
  python3 src/eval_labels.py --churn-review 10          # dump turns per score bin to judge
  python3 src/eval_labels.py --churn-review-score data/processed/churn_review_sample.csv
"""

import argparse
import math
from pathlib import Path

import pandas as pd

OUTCOMES = ["escalation", "refund", "churn"]


def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def to_bool(s: pd.Series) -> pd.Series:
    m = {"true": True, "false": False, "1": True, "0": False}
    return s.map(lambda v: m.get(str(v).strip().lower(), pd.NA) if pd.notna(v) else pd.NA)


def confusion(pred: pd.Series, ref: pd.Series):
    valid = ref.notna()
    p, r = pred[valid].astype(bool), ref[valid].astype(bool)
    return int((p & r).sum()), int((p & ~r).sum()), int((~p & r).sum()), int((~p & ~r).sum())


def fmt(k, n):
    if n == 0:
        return "  n/a (0 cases)"
    lo, hi = wilson(k, n)
    return f"{100 * k / n:5.1f}% [{100 * lo:.0f}-{100 * hi:.0f}]"


def report(name: str, df: pd.DataFrame, ref_cols: dict, pred_prefix="outcome_"):
    print(f"\n=== vs {name} ===")
    rows = []
    for o in OUTCOMES:
        ref = to_bool(df[ref_cols[o]])
        tp, fp, fn, tn = confusion(df[f"{pred_prefix}{o}"], ref)
        n = tp + fp + fn + tn
        print(f"{o:10s} n={n:3d} pos_ref={tp + fn:2d} pred_pos={tp + fp:2d} | TP={tp} FP={fp} FN={fn} TN={tn}"
              f" | precision {fmt(tp, tp + fp)} | recall {fmt(tp, tp + fn)}")
        rows.append((o, tp, fp, fn))
    return rows


def disagreements(df: pd.DataFrame, ref_cols: dict, ref_name: str) -> pd.DataFrame:
    recs = []
    for o in OUTCOMES:
        ref = to_bool(df[ref_cols[o]])
        for i, row in df.iterrows():
            if pd.isna(ref[i]):
                continue
            if bool(row[f"outcome_{o}"]) != bool(ref[i]):
                recs.append({
                    "transcript_id": row["transcript_id"], "outcome": o, "intent": row.get("intent"),
                    "pipeline": bool(row[f"outcome_{o}"]), ref_name: bool(ref[i]),
                    "evidence": row.get(f"evidence_{o}"),
                    "your_verdict_pipeline_right": "",
                })
    return pd.DataFrame(recs)


def churn_sweep(df: pd.DataFrame, ref_cols: dict, ref_name: str):
    if "churn_zs_max" not in df.columns or df["churn_zs_max"].isna().all():
        print("\n(churn not scored yet: no threshold sweep)")
        return
    ref = to_bool(df[ref_cols["churn"]])
    print(f"\n=== churn threshold sweep vs {ref_name} ===")
    for t in [0.5, 0.6, 0.7, 0.8, 0.9, 0.95]:
        pred = df["churn_zs_max"].fillna(0) >= t
        tp, fp, fn, _ = confusion(pred, ref)
        print(f"  tau={t:.2f} pred_pos={tp + fp:3d} TP={tp} FP={fp} FN={fn}"
              f" | precision {fmt(tp, tp + fp)} | recall {fmt(tp, tp + fn)}")


def esc_review(labeled: pd.DataFrame, n: int, path: str):
    pos = labeled[labeled["outcome_escalation"]]
    pos = pd.concat([g.sample(min(len(g), max(n // 2, 1)), random_state=7) for _, g in pos.groupby("conf_escalation")])
    miss = labeled[labeled["esc_customer_demand"] & ~labeled["outcome_escalation"]]
    miss = miss.sample(min(len(miss), n), random_state=7)
    out = pd.concat([
        pos.assign(kind="precision_check_predicted_positive")[["transcript_id", "intent", "kind", "conf_escalation", "evidence_escalation"]],
        miss.assign(kind="recall_probe_customer_demanded_but_no_event")[["transcript_id", "intent", "kind", "ev_esc_customer_demand"]]
            .rename(columns={"ev_esc_customer_demand": "evidence_escalation"}),
    ])
    out["your_verdict_is_escalation"] = ""
    out.to_csv(path, index=False)
    print(f"\nWrote escalation review sample ({len(out)} rows) -> {path}")


CHURN_BINS = [0.0, 0.5, 0.7, 0.8, 0.9, 0.95, 1.0001]
CHURN_BIN_LABELS = ["0.00-0.50", "0.50-0.70", "0.70-0.80", "0.80-0.90", "0.90-0.95", "0.95-1.00"]


def _best_turn_per_call(scores_path: str) -> pd.DataFrame:
    s = pd.read_parquet(scores_path)
    best = s.sort_values("score", ascending=False).groupby("transcript_id").first().reset_index()
    best["bin"] = pd.cut(best["score"], CHURN_BINS, right=False, labels=CHURN_BIN_LABELS)
    return best


def churn_review(scores_path: str, n_per_bin: int, path: str):
    """Sample calls by the bin of their max churn score; you judge the top-scoring turn."""
    best = _best_turn_per_call(scores_path)
    print("\ncalls per max-score bin (all flagged calls):")
    print(best["bin"].value_counts().reindex(CHURN_BIN_LABELS).to_string())
    samp = pd.concat([g.sample(min(len(g), n_per_bin), random_state=7)
                      for _, g in best.groupby("bin", observed=True)])
    samp["text"] = samp["text"].map(lambda t: " ".join(str(t).split())[:240])
    out = samp[["bin", "transcript_id", "turn_id", "score", "text"]].sort_values("bin", ascending=False)
    out["your_verdict_leaves"] = ""
    out.to_csv(path, index=False)
    print(f"\nWrote {len(out)} turns -> {path}\n"
          "Fill your_verdict_leaves with y or n: does THIS customer turn express intent to leave, "
          "cancel, or switch to another company? (a hypothetical or a mere comparison is n)")


def churn_review_score(review_path: str, scores_path: str):
    """Per-bin precision from your verdicts, then estimated precision/positives per threshold."""
    rev = pd.read_csv(review_path)
    v = rev["your_verdict_leaves"].astype(str).str.strip().str.lower().map(
        {"y": True, "yes": True, "true": True, "1": True, "n": False, "no": False, "false": False, "0": False})
    rev = rev.assign(v=v).dropna(subset=["v"])
    sizes = _best_turn_per_call(scores_path)["bin"].value_counts()
    print(f"\n=== churn precision by max-score bin ({len(rev)} judged turns) ===")
    est = {}
    for b in reversed(CHURN_BIN_LABELS):
        g = rev[rev["bin"] == b]
        n, k = len(g), int(g["v"].sum())
        size = int(sizes.get(b, 0))
        prec = k / n if n else float("nan")
        est[b] = (size, prec)
        print(f"  {b}: judged {n:2d}, yes {k:2d} | precision {fmt(k, n)} | calls in bin {size} "
              f"| est. true churn calls {'n/a' if not n else round(size * prec)}")
    print("\n=== estimated result if you cut at each threshold (uses the per-bin estimates) ===")
    for edge, first in [(0.95, "0.95-1.00"), (0.9, "0.90-0.95"), (0.8, "0.80-0.90"), (0.7, "0.70-0.80"), (0.5, "0.50-0.70")]:
        bins = CHURN_BIN_LABELS[CHURN_BIN_LABELS.index(first):]
        if any(est[b][1] != est[b][1] for b in bins):
            print(f"  tau={edge:.2f}: not enough judged turns in every bin above it")
            continue
        calls = sum(est[b][0] for b in bins)
        pos = sum(est[b][0] * est[b][1] for b in bins)
        print(f"  tau={edge:.2f}: {calls} calls labeled churn, ~{100 * pos / calls:.0f}% of them real "
              f"(~{round(pos)} true churn calls)")
    low = rev[rev["bin"] == CHURN_BIN_LABELS[0]]
    if len(low):
        print(f"\nRecall probe: {int(low['v'].sum())} of {len(low)} judged turns in the 0.00-0.50 bin were real churn "
              "(if this is above 0, real churn is hiding below the threshold).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default="data/processed/calls_labeled_v3.parquet")
    ap.add_argument("--manual", default="data/processed/hand_validation_sample.csv")
    ap.add_argument("--groq", default="data/processed/calls_labeled_groq_sample.csv")
    ap.add_argument("--out", default="data/processed/label_disagreements.csv")
    ap.add_argument("--esc-review", type=int, default=0)
    ap.add_argument("--scores", default="data/processed/churn_turn_scores.parquet")
    ap.add_argument("--churn-review", type=int, default=0, help="dump N turns per score bin for you to judge")
    ap.add_argument("--churn-review-score", default=None, help="path to the filled churn review CSV")
    args = ap.parse_args()

    if args.churn_review:
        churn_review(args.scores, args.churn_review, "data/processed/churn_review_sample.csv")
        raise SystemExit(0)
    if args.churn_review_score:
        churn_review_score(args.churn_review_score, args.scores)
        raise SystemExit(0)

    labeled = pd.read_parquet(args.labels)
    all_dis = []

    if Path(args.manual).exists():
        man = pd.read_csv(args.manual)
        m = man.merge(labeled, on="transcript_id", how="inner", suffixes=("_m", ""))
        cols = {o: f"manual_{o}" for o in OUTCOMES}
        if any(to_bool(m[c]).notna().any() for c in cols.values()):
            report("manual labels (n small: read the intervals)", m, cols)
            churn_sweep(m, cols, "manual")
            all_dis.append(disagreements(m, cols, "manual"))
        else:
            print("manual CSV has no filled labels yet")
    if Path(args.groq).exists():
        g = pd.read_csv(args.groq)
        gm = g.merge(labeled, on="transcript_id", how="inner", suffixes=("_g", ""))
        cols = {o: f"groq_outcome_{o}" for o in OUTCOMES}
        report("Groq full-conversation judge (a second opinion, not ground truth)", gm, cols)
        churn_sweep(gm, cols, "groq")
        all_dis.append(disagreements(gm, cols, "groq"))

    if all_dis:
        d = pd.concat(all_dis, ignore_index=True)
        d.to_csv(args.out, index=False)
        print(f"\nWrote {len(d)} disagreement rows with evidence -> {args.out}")
    if args.esc_review:
        esc_review(labeled, args.esc_review, "data/processed/escalation_review_sample.csv")
