"""
Hand-validation for Stage 1 silver labels.

Two modes:

  --make-sample   Pull N random calls (default 50) with their reason_for_call
                  and first few turns, write a CSV with empty manual_* columns
                  for a human to fill in by hand (True/False per outcome).

  --score         Read a filled-in CSV back, compare manual_* columns against
                  the silver outcome_* columns already in calls_labeled.parquet,
                  and report agreement rate overall and per outcome.

This is the number that defends the 0.75 / 0.5 confidence-tier thresholds in
the report, rather than leaving them as unvalidated constants.
"""

import argparse

import pandas as pd

OUTCOMES = ["escalation", "refund", "churn"]


def make_sample(calls_path: str, turns_path: str, out_csv: str, n: int, seed: int = 42) -> None:
    calls_df = pd.read_parquet(calls_path)
    turns_df = pd.read_parquet(turns_path)

    sample = calls_df.sample(n=min(n, len(calls_df)), random_state=seed).copy()

    def first_turns_preview(tid, k=4):
        rows = turns_df[turns_df["transcript_id"] == tid].sort_values("turn_id").head(k)
        return " | ".join(f"[{r.speaker}] {r.text}" for r in rows.itertuples())

    sample["conversation_preview"] = sample["transcript_id"].apply(first_turns_preview)

    out_cols = ["transcript_id", "intent", "reason_for_call", "conversation_preview"]
    for outcome in OUTCOMES:
        sample[f"manual_{outcome}"] = ""  # fill with True/False by hand

    sample[out_cols + [f"manual_{o}" for o in OUTCOMES]].to_csv(out_csv, index=False)
    print(f"Wrote {len(sample)} calls to {out_csv}")
    print("Fill in the manual_escalation / manual_refund / manual_churn columns with True or False,")
    print("then run this script again with --score.")


def score(labeled_path: str, filled_csv: str) -> None:
    labeled_df = pd.read_parquet(labeled_path)
    manual_df = pd.read_csv(filled_csv)

    missing = manual_df[[f"manual_{o}" for o in OUTCOMES]].isna().any(axis=1)
    if missing.any():
        print(f"Warning: {missing.sum()} rows have an unfilled manual_ column, skipping those.")
        manual_df = manual_df[~missing]

    merged = manual_df.merge(labeled_df[["transcript_id"] + [f"outcome_{o}" for o in OUTCOMES]], on="transcript_id")

    print(f"Scored against {len(merged)} hand-labeled calls\n")

    total_agree = 0
    total_compared = 0
    for outcome in OUTCOMES:
        manual_col = merged[f"manual_{outcome}"].astype(str).str.strip().str.lower().map(
            {"true": True, "false": False, "1": True, "0": False}
        )
        silver_col = merged[f"outcome_{outcome}"]

        valid = manual_col.notna()
        agree = (manual_col[valid] == silver_col[valid]).sum()
        n = valid.sum()

        total_agree += agree
        total_compared += n

        print(f"  {outcome}: {agree}/{n} agree ({100 * agree / n:.1f}%)" if n else f"  {outcome}: no valid rows")

    if total_compared:
        print(f"\nOverall agreement: {total_agree}/{total_compared} ({100 * total_agree / total_compared:.1f}%)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hand-validate Stage 1 silver labels.")
    parser.add_argument("--calls", default="data/processed/calls_normalized.parquet")
    parser.add_argument("--turns", default="data/processed/turns_normalized.parquet")
    parser.add_argument("--labeled", default="data/processed/calls_labeled.parquet")
    parser.add_argument("--sample-csv", default="data/processed/hand_validation_sample.csv")
    parser.add_argument("--make-sample", action="store_true")
    parser.add_argument("--score", action="store_true")
    parser.add_argument("-n", type=int, default=50)
    args = parser.parse_args()

    if args.make_sample:
        make_sample(args.calls, args.turns, args.sample_csv, args.n)
    elif args.score:
        score(args.labeled, args.sample_csv)
    else:
        parser.error("Pass --make-sample or --score")
