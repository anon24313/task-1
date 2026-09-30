"""Tests for the model-free parts of src/features.py (aggregation, repetition
logic, masking). The emotion and embedding models are replaced by fakes."""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import features as F


def _turns(rows):
    df = pd.DataFrame(rows, columns=["transcript_id", "turn_id", "speaker_role", "text", "word_count"])
    return df


def _emotion(n, neg_values):
    """Probability rows whose negative mass (all in 'anger') equals neg_values."""
    probs = np.zeros((n, len(F.EMOTIONS)), dtype=np.float32)
    for i, v in enumerate(neg_values):
        probs[i, F.EMOTIONS.index("anger")] = v
        probs[i, F.EMOTIONS.index("neutral")] = 1.0 - v
    return probs


def test_rising_negativity_has_positive_slope_and_late_onset():
    rows = []
    neg = []
    for k in range(6):  # 6 customer turns, negativity rising from 0.05 to 0.9
        rows.append(("c1", 2 * k, "Agent", "how can i help", 4))
        neg.append(0.0)
        rows.append(("c1", 2 * k + 1, "Customer", "this is taking way too long honestly", 7))
        neg.append([0.05, 0.1, 0.2, 0.5, 0.8, 0.9][k])
    turns = _turns(rows)
    tf = F.build_turn_features(turns, _emotion(len(turns), neg))
    cf = F.aggregate_calls(tf, pd.DataFrame({"transcript_id": ["c1"], "role_mapping_uncertain": [False]}))
    r = cf.iloc[0]
    assert r["cust_neg_slope"] > 0
    assert r["cust_neg_peak"] > 0.89
    assert 0.5 < r["cust_neg_onset_frac"] < 0.8       # first >=0.5 is the 4th of 6 customer turns
    assert r["cust_neg_delta"] > 0.5                    # last3 much worse than first3


def test_no_negative_turn_gives_onset_one():
    turns = _turns([("c1", 0, "Customer", "thanks so much that is great", 6)] * 1 +
                   [("c1", 1, "Agent", "you are welcome", 3)])
    tf = F.build_turn_features(turns, _emotion(2, [0.0, 0.0]))
    cf = F.aggregate_calls(tf, pd.DataFrame({"transcript_id": ["c1"], "role_mapping_uncertain": [False]}))
    assert cf.iloc[0]["cust_neg_onset_frac"] == 1.0


def test_apology_and_empathy_counted_on_agent_turns_only():
    turns = _turns([
        ("c1", 0, "Agent", "i am so sorry about that", 6),
        ("c1", 1, "Customer", "i am sorry too but fix it", 6),
        ("c1", 2, "Agent", "i understand your frustration", 4),
    ])
    tf = F.build_turn_features(turns, _emotion(3, [0, 0, 0]))
    cf = F.aggregate_calls(tf, pd.DataFrame({"transcript_id": ["c1"], "role_mapping_uncertain": [False]}))
    assert cf.iloc[0]["agent_apology_count"] == 1
    assert cf.iloc[0]["agent_empathy_count"] == 1


def test_repetition_detected_with_fake_embedder():
    turns = _turns([
        ("c1", 0, "Customer", "my internet keeps dropping every evening", 6),
        ("c1", 1, "Agent", "let me check", 3),
        ("c1", 2, "Customer", "my internet keeps dropping every evening still", 7),   # same vector -> repeat
        ("c1", 3, "Customer", "yes okay", 2),                                           # too short, ignored
        ("c1", 4, "Customer", "totally different question about billing dates", 6),   # orthogonal vector
    ])
    vectors = {"a": np.array([1.0, 0.0]), "b": np.array([0.0, 1.0])}

    def fake_embed(texts):
        return np.stack([vectors["b"] if "billing" in t else vectors["a"] for t in texts])

    tf = F.build_turn_features(turns, _emotion(5, [0] * 5), embed_fn=fake_embed)
    assert bool(tf.loc[2, "is_repetition"]) is True
    assert bool(tf.loc[4, "is_repetition"]) is False
    assert np.isnan(tf.loc[3, "rep_max_sim"])            # short turn never scored
    cf = F.aggregate_calls(tf, pd.DataFrame({"transcript_id": ["c1"], "role_mapping_uncertain": [False]}))
    assert cf.iloc[0]["n_repetitions"] == 1


def test_uncertain_calls_have_role_features_masked():
    turns = _turns([("c1", 0, "Agent", "sorry about that", 3), ("c1", 1, "Customer", "not good at all", 4)])
    tf = F.build_turn_features(turns, _emotion(2, [0, 0.9]))
    cf = F.aggregate_calls(tf, pd.DataFrame({"transcript_id": ["c1"], "role_mapping_uncertain": [True]}))
    r = cf.iloc[0]
    assert np.isnan(r["agent_apology_count"]) and np.isnan(r["cust_neg_peak"])
    assert r["n_turns"] == 2 and bool(r["roles_trusted"]) is False   # structural counts survive


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
