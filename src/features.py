"""
Feature extraction stage.

Reads the normalized turn table (Stage 0 output) and produces two tables:

  - turn_features.parquet : one row per turn
        emo_<label> x7      emotion probabilities from a pretrained classifier
        neg_score           anger + disgust + fear + sadness
        is_apology          regex, meaningful on Agent turns
        is_empathy          regex, meaningful on Agent turns
        filler_count        um / uh / erm count (customer hesitation proxy)
        rep_max_sim         for longer Customer turns: highest embedding
                            similarity to an earlier Customer turn in the call
        is_repetition       rep_max_sim >= REPETITION_SIM

  - call_features.parquet : one row per call, aggregated from the turn table
        emotion trajectory of the CUSTOMER (peak, mean, slope, onset, first
        vs last three turns), repetition count, agent apology / empathy
        counts, hesitation rate, talk-time ratio, supervisor turn count.

This stage does not use outcome labels, so it can run independently of the
labeling stage. The models run on raw `text`, not `text_clean`: the clean
column strips the word "like", which turns "would like to" into "would to".

Role-conditional call features are set to NaN for calls flagged
`role_mapping_uncertain` (roles were inferred from names and the result does
not alternate cleanly), so downstream statistics never silently mix up
Agent and Customer for those calls.

NOTE for downstream stages: `n_supervisor_turns` is close to a direct
escalation signal. Do not use it as an input feature when predicting an
escalation label, or the classifier will learn the label from its own answer.

Usage:
  Full run (Colab T4):
    python3 src/features.py --device 0 --batch-size 256

  Quick local check (CPU, small random sample):
    python3 src/features.py --sample 100 --device cpu --skip-repetition

  Re-aggregate only (no models), e.g. after changing aggregation logic:
    python3 src/features.py --aggregate-only
"""

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

EMOTION_MODEL = "j-hartmann/emotion-english-distilroberta-base"
EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMOTIONS = ["anger", "disgust", "fear", "joy", "neutral", "sadness", "surprise"]
NEGATIVE_EMOTIONS = ["anger", "disgust", "fear", "sadness"]

NEG_ONSET_THRESHOLD = 0.5
REPETITION_SIM = 0.85
REPETITION_MIN_WORDS = 6

_APOLOGY = re.compile(r"\b(?:sorry|apologi[sz]e[sd]?|apologies|apology)\b", flags=re.IGNORECASE)
_EMPATHY = re.compile(
    r"\b(?:understand|appreciate your patience|frustrat\w*|inconvenience\w*|i can imagine|that must be)\b",
    flags=re.IGNORECASE,
)
_FILLER = re.compile(r"\b(?:um+|uh+|erm+)\b", flags=re.IGNORECASE)


# --------------------------------------------------------------------------
# Model steps (need transformers / sentence-transformers, ideally a GPU)
# --------------------------------------------------------------------------

def _resolve_device(device: str) -> str:
    device = str(device)
    if device == "cpu":
        return "cpu"
    return f"cuda:{device}" if device.isdigit() else device


def run_emotion(texts: list, device: str = "cpu", batch_size: int = 256, max_length: int = 96) -> np.ndarray:
    """Returns an (N, 7) float32 array of emotion probabilities in EMOTIONS
    order. Texts are length-sorted before batching to avoid wasting compute on
    padding, then restored to their original order.
    """
    import torch
    from tqdm import tqdm
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    dev = torch.device(_resolve_device(device))
    tokenizer = AutoTokenizer.from_pretrained(EMOTION_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(EMOTION_MODEL)

    label_order = [model.config.id2label[i].lower() for i in range(model.config.num_labels)]
    missing = set(EMOTIONS) - set(label_order)
    if missing:
        raise ValueError(f"Emotion model labels {label_order} do not include {sorted(missing)}")
    col_idx = [label_order.index(e) for e in EMOTIONS]

    model.to(dev).eval()
    if dev.type == "cuda":
        model.half()

    order = np.argsort([len(t) for t in texts])
    out = np.zeros((len(texts), len(EMOTIONS)), dtype=np.float32)

    with torch.no_grad():
        for start in tqdm(range(0, len(texts), batch_size), desc="emotion"):
            idx = order[start:start + batch_size]
            batch = [texts[i] for i in idx]
            enc = tokenizer(batch, padding=True, truncation=True, max_length=max_length, return_tensors="pt").to(dev)
            probs = torch.softmax(model(**enc).logits.float(), dim=-1).cpu().numpy()
            out[idx] = probs[:, col_idx]
    return out


def make_embedder(device: str = "cpu", batch_size: int = 256):
    """Returns a function texts -> (N, d) L2-normalised embeddings."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(EMBED_MODEL, device=_resolve_device(device))

    def embed(texts: list) -> np.ndarray:
        return model.encode(
            texts, batch_size=batch_size, normalize_embeddings=True,
            convert_to_numpy=True, show_progress_bar=True,
        )

    return embed


# --------------------------------------------------------------------------
# Turn-level features
# --------------------------------------------------------------------------

def add_rule_features(turns: pd.DataFrame) -> pd.DataFrame:
    text = turns["text"].fillna("")
    turns["is_apology"] = text.str.contains(_APOLOGY)
    turns["is_empathy"] = text.str.contains(_EMPATHY)
    turns["filler_count"] = text.str.count(_FILLER).astype(np.int16)
    return turns


def add_emotion_features(turns: pd.DataFrame, emotion_probs: np.ndarray) -> pd.DataFrame:
    for j, name in enumerate(EMOTIONS):
        turns[f"emo_{name}"] = emotion_probs[:, j].astype(np.float32)
    neg_cols = [EMOTIONS.index(e) for e in NEGATIVE_EMOTIONS]
    turns["neg_score"] = emotion_probs[:, neg_cols].sum(axis=1).astype(np.float32)
    return turns


def add_repetition_features(turns: pd.DataFrame, embed_fn) -> pd.DataFrame:
    """For each longer Customer turn, the max cosine similarity to any EARLIER
    longer Customer turn in the same call. Short acknowledgements ("yes okay")
    are excluded because they are always similar to each other."""
    turns["rep_max_sim"] = np.nan

    eligible = turns[(turns["speaker_role"] == "Customer") & (turns["word_count"] >= REPETITION_MIN_WORDS)]
    if eligible.empty:
        turns["is_repetition"] = False
        return turns

    embeddings = embed_fn(eligible["text"].fillna("").tolist())
    row_index = eligible.index.to_numpy()

    rep = np.full(len(turns), np.nan, dtype=np.float32)
    for _, positions in eligible.reset_index(drop=True).groupby("transcript_id", sort=False).indices.items():
        if len(positions) < 2:
            continue
        E = embeddings[positions]
        sims = E @ E.T
        for i in range(1, len(positions)):
            rep[row_index[positions[i]]] = sims[i, :i].max()

    turns["rep_max_sim"] = rep
    turns["is_repetition"] = turns["rep_max_sim"] >= REPETITION_SIM
    return turns


def build_turn_features(turns_df: pd.DataFrame, emotion_probs: np.ndarray, embed_fn=None) -> pd.DataFrame:
    turns = turns_df[["transcript_id", "turn_id", "speaker_role", "text", "word_count"]].reset_index(drop=True)
    turns = add_rule_features(turns)
    turns = add_emotion_features(turns, emotion_probs)
    if embed_fn is not None:
        turns = add_repetition_features(turns, embed_fn)
    else:
        turns["rep_max_sim"] = np.nan
        turns["is_repetition"] = False
    return turns.drop(columns=["text"])


# --------------------------------------------------------------------------
# Call-level aggregation (pure pandas/numpy, no models)
# --------------------------------------------------------------------------

def _customer_trajectory(neg: np.ndarray, joy: np.ndarray) -> dict:
    n = len(neg)
    if n == 0:
        keys = ["cust_neg_mean", "cust_neg_peak", "cust_neg_slope", "cust_neg_onset_frac",
                "cust_neg_first3", "cust_neg_last3", "cust_neg_delta", "cust_neg_turn_frac", "cust_joy_last3"]
        return {k: np.nan for k in keys}

    pos = np.linspace(0.0, 1.0, n) if n > 1 else np.array([0.0])
    above = np.where(neg >= NEG_ONSET_THRESHOLD)[0]
    first3, last3 = neg[:3].mean(), neg[-3:].mean()
    return {
        "cust_neg_mean": neg.mean(),
        "cust_neg_peak": neg.max(),
        "cust_neg_slope": np.polyfit(pos, neg, 1)[0] if n >= 3 else np.nan,
        # position (0..1) of the first strongly negative customer turn; 1.0 means never
        "cust_neg_onset_frac": pos[above[0]] if len(above) else 1.0,
        "cust_neg_first3": first3,
        "cust_neg_last3": last3,
        "cust_neg_delta": last3 - first3,
        "cust_neg_turn_frac": float((neg >= NEG_ONSET_THRESHOLD).mean()),
        "cust_joy_last3": joy[-3:].mean(),
    }


ROLE_CONDITIONAL_PREFIXES = ("cust_", "agent_", "n_repetitions", "max_rep_sim", "word_ratio")


def aggregate_calls(turn_feats: pd.DataFrame, calls_df: pd.DataFrame) -> pd.DataFrame:
    turn_feats = turn_feats.sort_values(["transcript_id", "turn_id"]).reset_index(drop=True)
    rows = []

    for tid, g in turn_feats.groupby("transcript_id", sort=False):
        cust = g[g["speaker_role"] == "Customer"]
        agent = g[g["speaker_role"] == "Agent"]

        row = {
            "transcript_id": tid,
            "n_turns": len(g),
            "n_customer_turns": len(cust),
            "n_agent_turns": len(agent),
            "n_supervisor_turns": int((g["speaker_role"] == "Supervisor").sum()),
        }
        row.update(_customer_trajectory(cust["neg_score"].to_numpy(), cust["emo_joy"].to_numpy()))

        cust_words, agent_words = cust["word_count"].sum(), agent["word_count"].sum()
        row.update({
            "n_repetitions": int(cust["is_repetition"].sum()),
            "max_rep_sim": cust["rep_max_sim"].max() if cust["rep_max_sim"].notna().any() else np.nan,
            "agent_apology_count": int(agent["is_apology"].sum()),
            "agent_empathy_count": int(agent["is_empathy"].sum()),
            "cust_filler_rate": cust["filler_count"].sum() / cust_words if cust_words else np.nan,
            "cust_avg_words": cust["word_count"].mean() if len(cust) else np.nan,
            "agent_avg_words": agent["word_count"].mean() if len(agent) else np.nan,
            "word_ratio": agent_words / cust_words if cust_words else np.nan,
        })
        rows.append(row)

    call_feats = pd.DataFrame(rows)

    # Mask role-conditional columns where the role assignment is not trustworthy.
    if "role_mapping_uncertain" in calls_df.columns:
        uncertain_ids = set(calls_df.loc[calls_df["role_mapping_uncertain"], "transcript_id"])
        mask = call_feats["transcript_id"].isin(uncertain_ids)
        role_cols = [c for c in call_feats.columns if c.startswith(ROLE_CONDITIONAL_PREFIXES)]
        call_feats.loc[mask, role_cols] = np.nan
        call_feats["roles_trusted"] = ~mask
    else:
        call_feats["roles_trusted"] = True

    return call_feats


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract turn-level and call-level features.")
    parser.add_argument("--turns", default="data/processed/turns_normalized.parquet")
    parser.add_argument("--calls", default="data/processed/calls_normalized.parquet")
    parser.add_argument("--turn-out", default="data/processed/turn_features.parquet")
    parser.add_argument("--call-out", default="data/processed/call_features.parquet")
    parser.add_argument("--device", default="cpu", help="'cpu' or a GPU index such as '0'.")
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--sample", type=int, default=None, help="Only process N random calls.")
    parser.add_argument("--skip-repetition", action="store_true", help="Skip the embedding-based repetition feature.")
    parser.add_argument("--aggregate-only", action="store_true", help="Re-aggregate from an existing turn_features.parquet.")
    args = parser.parse_args()

    calls_df = pd.read_parquet(args.calls)

    if args.aggregate_only:
        turn_feats = pd.read_parquet(args.turn_out)
    else:
        turns_df = pd.read_parquet(args.turns)
        if args.sample:
            keep = set(calls_df.sample(n=args.sample, random_state=42)["transcript_id"])
            turns_df = turns_df[turns_df["transcript_id"].isin(keep)].reset_index(drop=True)
            calls_df = calls_df[calls_df["transcript_id"].isin(keep)].reset_index(drop=True)
            print(f"Sampled {len(keep)} random calls ({len(turns_df)} turns).")

        print(f"Running emotion model on {len(turns_df)} turns...")
        emotion_probs = run_emotion(turns_df["text"].fillna("").tolist(), args.device, args.batch_size)

        embed_fn = None if args.skip_repetition else make_embedder(args.device, args.batch_size)
        turn_feats = build_turn_features(turns_df, emotion_probs, embed_fn)

        Path(args.turn_out).parent.mkdir(parents=True, exist_ok=True)
        turn_feats.to_parquet(args.turn_out, index=False)
        print(f"Wrote: {args.turn_out}")

    call_feats = aggregate_calls(turn_feats, calls_df)
    call_feats.to_parquet(args.call_out, index=False)
    print(f"Wrote: {args.call_out}  ({len(call_feats)} calls, {call_feats.shape[1]} columns)")
    print(call_feats.describe().T[["count", "mean", "min", "max"]].round(3).to_string())
