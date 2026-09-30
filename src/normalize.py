"""
Speaker and turn normalization stage. Runs before labeling or feature
extraction, since both depend on clean speaker roles.

Two problems exist in the raw turns table:

  1. A small number of turns are not real dialogue at all: leaked
     meta/summary artifacts (e.g. a speaker label like "(System Note" or
     "- Key Events" with generated summary text as the "turn"). These are
     dropped entirely.
  2. A meaningful minority of calls (~6.8%) use real names instead of
     Agent/Customer role labels, or mix named labels with role labels in
     the same call. These are resolved to roles by first-occurrence order:
     the first distinct speaker in a call is mapped to Agent, the second
     distinct speaker to Customer. A literal "Supervisor" label is kept
     as its own role, never inferred.

Two flags are produced per call:

  - roles_remapped         : at least one raw speaker label was replaced by an
                             inferred role (i.e. the call used names).
  - role_mapping_uncertain : roles were remapped AND the resulting sequence does
                             not alternate cleanly between Agent and Customer
                             (or a third unmapped speaker appeared). Only these
                             calls have an untrustworthy role assignment.

Calls with literal Agent/Customer labels are never flagged, even if the same
speaker talks twice in a row: nothing was guessed, so there is nothing to doubt.
Uncertain calls stay in the corpus but should be excluded from any
Agent/Customer-conditional feature (e.g. "count agent apologies").

Output:
  - turns_normalized.parquet : one row per turn, meta turns removed,
    speaker_role column added, original raw speaker column preserved
  - calls_normalized.parquet : one row per call, with had_meta_turn and
    role_mapping_uncertain flags joined onto the existing call metadata
"""

import argparse
import re
from pathlib import Path

import pandas as pd

STANDARD_ROLES = {"Agent", "Customer", "Supervisor"}

# Meta/artifact turn detector: a speaker label starting with a bracket/dash/
# asterisk, or containing note/summary/key event wording. Checked against
# the raw speaker label, not the turn text.
_META_PATTERN = re.compile(
    r"^[\(\[\*\-]|note|summary|key event", flags=re.IGNORECASE
)


def is_meta_turn(speaker: str) -> bool:
    return bool(_META_PATTERN.match(speaker.strip()))


def resolve_roles_for_call(speakers_in_order: list) -> dict:
    """Given the distinct speaker labels of one call, in order of first
    appearance, return a mapping {raw_speaker: role}.

    Literal Agent/Customer/Supervisor labels map to themselves. Any other
    label is assigned the next unused role from [Agent, Customer], in the
    order it first appears. A third or further unmapped label (rare) maps
    to "Unknown" rather than guessing.
    """
    mapping = {}
    available_roles = ["Agent", "Customer"]

    for speaker in speakers_in_order:
        if speaker in STANDARD_ROLES:
            mapping[speaker] = speaker
            if speaker in available_roles:
                available_roles.remove(speaker)

    for speaker in speakers_in_order:
        if speaker in mapping:
            continue
        if available_roles:
            mapping[speaker] = available_roles.pop(0)
        else:
            mapping[speaker] = "Unknown"

    return mapping


def check_alternation(role_sequence: list) -> bool:
    """True if the call alternates cleanly. Only enforced strictly for the
    two-party Agent/Customer case, since a Supervisor joining mid-call is a
    legitimate three-party event, not a mapping error.
    """
    distinct_roles = set(role_sequence)

    if distinct_roles <= {"Agent", "Customer"} and len(distinct_roles) == 2:
        return all(role_sequence[i] != role_sequence[i + 1] for i in range(len(role_sequence) - 1))

    if "Unknown" in distinct_roles:
        return False

    # Supervisor present, or a single-speaker call: nothing to flag here.
    return True


def normalize(turns_df: pd.DataFrame, calls_df: pd.DataFrame) -> tuple:
    turns_df = turns_df.copy()
    turns_df["is_meta"] = turns_df["speaker"].apply(is_meta_turn)

    had_meta = turns_df.groupby("transcript_id")["is_meta"].any().rename("had_meta_turn")

    clean_turns = turns_df[~turns_df["is_meta"]].sort_values(["transcript_id", "turn_id"])

    speaker_role_col = pd.Series(index=clean_turns.index, dtype=object)
    uncertain_flags = {}
    remapped_flags = {}

    for tid, group in clean_turns.groupby("transcript_id", sort=False):
        speakers_seq = group["speaker"].tolist()
        seen = []
        for s in speakers_seq:
            if s not in seen:
                seen.append(s)

        mapping = resolve_roles_for_call(seen)
        roles_seq = [mapping[s] for s in speakers_seq]

        speaker_role_col.loc[group.index] = roles_seq
        remapped = any(raw != role for raw, role in zip(speakers_seq, roles_seq))
        remapped_flags[tid] = remapped
        uncertain_flags[tid] = remapped and not check_alternation(roles_seq)

    clean_turns["speaker_role"] = speaker_role_col
    # Renumber turn_id sequentially per call after meta-turn removal, so every
    # downstream table (turn_features, ablation indexing) stays aligned. A
    # stripped meta turn mid-conversation would otherwise leave a gap in the
    # original turn_id sequence for that call.
    clean_turns["turn_id"] = clean_turns.groupby("transcript_id").cumcount()
    uncertain_series = pd.Series(uncertain_flags, name="role_mapping_uncertain")
    uncertain_series.index.name = "transcript_id"
    remapped_series = pd.Series(remapped_flags, name="roles_remapped")
    remapped_series.index.name = "transcript_id"

    calls_normalized = (
        calls_df.merge(had_meta, on="transcript_id", how="left")
        .merge(remapped_series, on="transcript_id", how="left")
        .merge(uncertain_series, on="transcript_id", how="left")
    )
    calls_normalized["had_meta_turn"] = calls_normalized["had_meta_turn"].fillna(False)
    calls_normalized["roles_remapped"] = calls_normalized["roles_remapped"].fillna(False)
    calls_normalized["role_mapping_uncertain"] = calls_normalized["role_mapping_uncertain"].fillna(False)

    turns_out_cols = ["transcript_id", "turn_id", "speaker", "speaker_role", "text", "text_clean", "word_count"]
    turns_normalized = clean_turns[turns_out_cols].reset_index(drop=True)

    return calls_normalized, turns_normalized


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Normalize speaker roles and strip meta turns.")
    parser.add_argument("--meta", default="data/processed/calls_meta.parquet")
    parser.add_argument("--turns", default="data/processed/turns_raw.parquet")
    parser.add_argument("--calls-out", default="data/processed/calls_normalized.parquet")
    parser.add_argument("--turns-out", default="data/processed/turns_normalized.parquet")
    args = parser.parse_args()

    calls_df = pd.read_parquet(args.meta)
    turns_df = pd.read_parquet(args.turns)

    calls_normalized, turns_normalized = normalize(turns_df, calls_df)

    Path(args.calls_out).parent.mkdir(parents=True, exist_ok=True)
    calls_normalized.to_parquet(args.calls_out, index=False)
    turns_normalized.to_parquet(args.turns_out, index=False)

    n_calls = len(calls_normalized)
    n_meta = calls_normalized["had_meta_turn"].sum()
    n_uncertain = calls_normalized["role_mapping_uncertain"].sum()
    n_remapped = calls_normalized["roles_remapped"].sum()
    dropped_turns = len(turns_df) - len(turns_normalized)

    print(f"Calls: {n_calls}")
    print(f"Calls with at least one stripped meta turn: {n_meta}")
    print(f"Meta turns dropped: {dropped_turns}")
    print(f"Calls with remapped roles (names -> Agent/Customer): {n_remapped}")
    print(f"Calls flagged role_mapping_uncertain: {n_uncertain} ({100 * n_uncertain / n_calls:.2f}%)")
    print(f"Wrote: {args.calls_out}")
    print(f"Wrote: {args.turns_out}")
