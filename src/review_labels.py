"""
Interactive hand-validation reviewer. Shows one call at a time with its full
conversation, takes a quick keypress per outcome (y/n/s to skip), writes
results directly into the CSV as you go. Faster than opening a spreadsheet
and typing True/False by hand for 50 rows.

Resumable: rows already filled in are skipped on the next run, so you can
stop partway and continue later.

Usage:
  python3 src/review_labels.py --csv data/processed/hand_validation_sample.csv
"""

import argparse

import pandas as pd

OUTCOMES = ["escalation", "refund", "churn"]


def prompt_bool(question: str) -> str:
    while True:
        ans = input(f"{question} [y/n/s=skip/q=quit]: ").strip().lower()
        if ans in ("y", "n", "s", "q"):
            return ans
        print("Please enter y, n, s, or q.")


def review(csv_path: str, turns_path: str) -> None:
    df = pd.read_csv(csv_path)
    turns_df = pd.read_parquet(turns_path)
    speaker_col = "speaker_role" if "speaker_role" in turns_df.columns else "speaker"

    for outcome in OUTCOMES:
        col = f"manual_{outcome}"
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].astype(object)

    remaining = df[df[[f"manual_{o}" for o in OUTCOMES]].isna().any(axis=1) |
                    (df[[f"manual_{o}" for o in OUTCOMES]] == "").any(axis=1)]

    print(f"{len(remaining)} of {len(df)} calls still need review.\n")

    for idx, row in remaining.iterrows():
        print("=" * 80)
        print(f"intent: {row['intent']}")
        print(f"reason_for_call: {row['reason_for_call']}")
        print("-" * 80)

        call_turns = turns_df[turns_df["transcript_id"] == row["transcript_id"]].sort_values("turn_id")
        for t in call_turns.itertuples():
            print(f"[{getattr(t, speaker_col)}] {t.text}")
        print("=" * 80)

        for outcome in OUTCOMES:
            ans = prompt_bool(f"  {outcome}?")
            if ans == "q":
                df.to_csv(csv_path, index=False)
                print(f"\nSaved progress to {csv_path}. Resume anytime by re-running this script.")
                return
            if ans == "s":
                continue
            df.at[idx, f"manual_{outcome}"] = (ans == "y")

        df.to_csv(csv_path, index=False)  # save after every call, not just at the end
        print()

    print("All calls reviewed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Interactively review hand-validation labels.")
    parser.add_argument("--csv", default="data/processed/hand_validation_sample.csv")
    parser.add_argument("--turns", default="data/processed/turns_normalized.parquet")
    args = parser.parse_args()

    review(args.csv, args.turns)
