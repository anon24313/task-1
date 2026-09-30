"""
Preprocessing stage.

Reads the raw transcript JSON (streamed, since the file is large) and splits
it into two normalized tables:

  - calls_meta.parquet   : one row per call, lightweight metadata only
  - turns_raw.parquet    : one row per turn, raw + model-ready text

Kept separate from labeling/feature extraction on purpose: this stage only
does structural normalization and text cleanup, no model calls, so it stays
fast and re-runnable independent of anything downstream.
"""

import argparse
import re
from pathlib import Path

import ijson
import pandas as pd

# Disfluencies stripped only for the "clean" text column used as model input.
# The original "text" column is preserved untouched for quoting as evidence.
_DISFLUENCY_PATTERN = re.compile(
    r"\b(um+|uh+|erm+|like|you know|i mean)\b", flags=re.IGNORECASE
)
_WHITESPACE_PATTERN = re.compile(r"\s+")


def clean_text(text: str) -> str:
    """Strip filler words / disfluencies for model input. Not used for display."""
    cleaned = _DISFLUENCY_PATTERN.sub("", text)
    cleaned = _WHITESPACE_PATTERN.sub(" ", cleaned).strip()
    return cleaned


def preprocess(raw_path: str, meta_out: str, turns_out: str, min_turns: int = 3) -> None:
    call_rows = []
    turn_rows = []
    dropped_short = 0

    with open(raw_path, "rb") as f:
        for record in ijson.items(f, "item"):
            conversation = record.get("conversation", [])

            if len(conversation) < min_turns:
                dropped_short += 1
                continue

            transcript_id = record["transcript_id"]

            call_rows.append(
                {
                    "transcript_id": transcript_id,
                    "time_of_interaction": record.get("time_of_interaction"),
                    "intent": record.get("intent"),
                    "reason_for_call": record.get("reason_for_call"),
                    "turn_count": len(conversation),
                }
            )

            for turn_id, turn in enumerate(conversation):
                raw_text = turn.get("text", "")
                turn_rows.append(
                    {
                        "transcript_id": transcript_id,
                        "turn_id": turn_id,
                        "speaker": turn.get("speaker"),
                        "text": raw_text,
                        "text_clean": clean_text(raw_text),
                        "word_count": len(raw_text.split()),
                    }
                )

    calls_df = pd.DataFrame(call_rows)
    turns_df = pd.DataFrame(turn_rows)

    Path(meta_out).parent.mkdir(parents=True, exist_ok=True)
    calls_df.to_parquet(meta_out, index=False)
    turns_df.to_parquet(turns_out, index=False)

    print(f"Calls kept: {len(calls_df)}  |  dropped (<{min_turns} turns): {dropped_short}")
    print(f"Total turns: {len(turns_df)}")
    print(f"Wrote: {meta_out}")
    print(f"Wrote: {turns_out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Preprocess raw transcript JSON into parquet tables.")
    parser.add_argument("--raw", default="data/raw/final_transcripts.json")
    parser.add_argument("--meta-out", default="data/processed/calls_meta.parquet")
    parser.add_argument("--turns-out", default="data/processed/turns_raw.parquet")
    parser.add_argument("--min-turns", type=int, default=3)
    args = parser.parse_args()

    preprocess(args.raw, args.meta_out, args.turns_out, args.min_turns)
