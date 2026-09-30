import pandas as pd

df = pd.read_parquet("data/processed/calls_labeled.parquet")
lc = df[df["low_confidence"]]

print(f"low_confidence calls: {len(lc)} / {len(df)}")
print()
print("Vote value counts (0, 1, 2 possible):")
print(lc[["votes_escalation", "votes_refund", "votes_churn"]].apply(pd.Series.value_counts))
