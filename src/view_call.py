"""
Look up and print the full conversation for one or more transcript IDs.

Usage:
  python3 src/view_call.py --id 26111c5c-822c-48cd-a7dd-3bbd5e308477
  python3 src/view_call.py --id <id1> --id <id2>
  python3 src/view_call.py --from-csv data/processed/hand_validation_sample.csv
"""

import argparse

import pandas as pd


def print_call(tid: str, calls_df: pd.DataFrame, turns_df: pd.DataFrame, labeled_df: pd.DataFrame = None) -> None:
    call_row = calls_df[calls_df["transcript_id"] == tid]
    if call_row.empty:
        print(f"transcript_id {tid} not found.")
        return
    call_row = call_row.iloc[0]

    print("=" * 80)
    print(f"transcript_id: {tid}")
    print(f"intent: {call_row['intent']}")
    print(f"reason_for_call: {call_row['reason_for_call']}")
    if "had_meta_turn" in call_row:
        print(f"had_meta_turn: {call_row['had_meta_turn']}   role_mapping_uncertain: {call_row['role_mapping_uncertain']}")

    if labeled_df is not None:
        label_row = labeled_df[labeled_df["transcript_id"] == tid]
        if not label_row.empty:
            label_row = label_row.iloc[0]
            print(
                "silver labels: "
                f"escalation={label_row.get('outcome_escalation')} "
                f"refund={label_row.get('outcome_refund')} "
                f"churn={label_row.get('outcome_churn')} "
                f"low_confidence={label_row.get('low_confidence')}"
            )

    print("-" * 80)
    call_turns = turns_df[turns_df["transcript_id"] == tid].sort_values("turn_id")
    speaker_col = "speaker_role" if "speaker_role" in call_turns.columns else "speaker"
    for row in call_turns.itertuples():
        print(f"[{getattr(row, speaker_col)}] {row.text}")
    print("=" * 80)
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="View full conversation(s) by transcript_id.")
    parser.add_argument("--calls", default="data/processed/calls_normalized.parquet")
    parser.add_argument("--turns", default="data/processed/turns_normalized.parquet")
    parser.add_argument("--labeled", default="data/processed/calls_labeled.parquet")
    parser.add_argument("--id", action="append", default=[], help="transcript_id to view, repeatable")
    parser.add_argument("--from-csv", help="CSV with a transcript_id column, e.g. the hand-validation sample")
    args = parser.parse_args()

    calls_df = pd.read_parquet(args.calls)
    turns_df = pd.read_parquet(args.turns)
    try:
        labeled_df = pd.read_parquet(args.labeled)
    except FileNotFoundError:
        labeled_df = None

    ids = list(args.id)
    if args.from_csv:
        ids.extend(pd.read_csv(args.from_csv)["transcript_id"].tolist())

    if not ids:
        parser.error("Pass --id <transcript_id> (repeatable) or --from-csv <path>")

    for tid in ids:
        print_call(tid, calls_df, turns_df, labeled_df)
