"""
Batch-run the query set and scripted multi-turn sessions, and save everything
needed for the "query dataset + system outputs" deliverable (max 1 GB).

Run from the repo root:
    export ANTHROPIC_API_KEY=...        # needed for prose answers; without it answers are raw evidence bundles
    python3 scripts/run_submission.py
    python3 scripts/run_submission.py --limit 5 --skip-sessions     # quick smoke test

Writes to outputs/:
    query_set.txt, query_results.json, query_results.csv,
    sessions.json, sessions/*.md, summary.json
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
import explain as E  # noqa: E402

OUT = "outputs"
INTENT_PHRASE = {
    "escalation": "Why do escalations happen in {i} calls?",
    "refund": "Why do customers ask for refunds in {i} calls?",
    "churn": "Why do customers cancel in {i} calls?",
}


def cache_attribution():
    """Attribution is deterministic (fixed seed), so identical groups need only one ablation run."""
    orig, cache = E.attribute, {}

    def cached(data, outcome, treat, control, **kw):
        key = (outcome, len(treat), len(control))
        if key not in cache:
            cache[key] = orig(data, outcome, treat, control, **kw)
        return cache[key]

    E.attribute = cached


def load_base_queries(path):
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("#")]


def intent_queries(data, n_top, n_rare):
    """Generate intent-specific queries from the real intent values. Rare intents are included on purpose,
    so the set contains insufficient-data cases (fewer than 30 treatment calls)."""
    vc = data.calls["intent"].value_counts()
    picks = [(i, o) for i in vc.index[:n_top] for o in INTENT_PHRASE]
    picks += [(i, "escalation") for i in vc.index[-n_rare:]] if n_rare else []
    out = []
    for intent, outcome in picks:
        q = INTENT_PHRASE[outcome].format(i=intent)
        if E.parse_query(q, data.intents) == (outcome, intent):      # keep only queries that parse back cleanly
            out.append(q)
    return list(dict.fromkeys(out))


def run_query(data, q):
    s, t0 = E.Session(data), time.time()
    try:
        answer = s.ask(q)
    except Exception as e:
        return {"query": q, "outcome": None, "intent": None, "status": "error",
                "n_treatment": None, "n_control": None,
                "answer": f"{type(e).__name__}: {e}", "stats": {}, "validation": {},
                "evidence": [], "seconds": round(time.time() - t0, 1)}
    st = s.stats or {}
    return {"query": q, "outcome": s.filter.get("outcome"), "intent": s.filter.get("intent"),
            "status": st.get("status", "unparsed"), "n_treatment": st.get("n_treatment"),
            "n_control": st.get("n_control"), "answer": answer, "stats": st,
            "validation": s.validation, "evidence": s.evidence[:6], "seconds": round(time.time() - t0, 1)}


def session_scripts(data):
    """Three multi-turn scripts covering refine, drill down, new topic and an insufficient-data refine."""
    vc = data.calls["intent"].value_counts()
    top, second, rare = vc.index[0], vc.index[1], vc.index[-1]
    return {
        "session_1_escalation": ["Why are escalations happening?", f"What about {top} calls only?",
                                 "Show me more examples", "Now why do customers ask for refunds?",
                                 "Show me more examples"],
        "session_2_refund": ["Why do customers ask for refunds?", f"Only {second} calls please",
                             "Show me another example", "What about churn instead? Why do customers cancel?"],
        "session_3_insufficient": ["Why do customers cancel?", f"Narrow it to {rare} calls",
                                   f"OK, try {top} calls instead", "Show me more quotes"],
    }


def run_session(data, name, messages):
    s, turns = E.Session(data), []
    for msg in messages:
        prev = s.stats
        t0 = time.time()
        ans = s.ask(msg)
        recomputed = s.stats is not prev
        turns.append({"user": msg, "answer": ans, "filter": dict(s.filter),
                      "n_treatment": (s.stats or {}).get("n_treatment"),
                      "action": "recomputed stages 4-7" if recomputed else "served from cached evidence (no recompute)",
                      "seconds": round(time.time() - t0, 1)})
    return {"name": name, "turns": turns, "history": s.history}


def write_session_md(sess, path):
    lines = [f"# {sess['name']}", ""]
    for i, t in enumerate(sess["turns"], 1):
        lines += [f"## Turn {i}", f"**User:** {t['user']}", "",
                  f"*Filter after turn:* `{json.dumps(t['filter'])}`  |  *n treatment:* {t['n_treatment']}  |  "
                  f"*{t['action']}*", "", t["answer"], ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def summarize(results, sessions):
    ok = [r for r in results if r["status"] == "ok"]
    uniq = {}
    for r in ok:
        uniq[(r["outcome"], r["intent"])] = r["validation"]
    val = [v for v in uniq.values() if "mean_top_turn_drop" in v]
    return {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "used_llm_synthesis": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "n_queries": len(results),
        "n_ok": len(ok),
        "n_insufficient": sum(r["status"] == "insufficient_data" for r in results),
        "n_unparsed": sum(r["status"] == "unparsed" for r in results),
        "n_sessions": len(sessions),
        "n_session_turns": sum(len(s["turns"]) for s in sessions),
        "distinct_filters_validated": len(val),
        "mean_top_turn_drop": round(sum(v["mean_top_turn_drop"] for v in val) / len(val), 4) if val else None,
        "mean_random_turn_drop": round(sum(v["mean_random_turn_drop"] for v in val) / len(val), 4) if val else None,
        "filters_top_beats_random_p05": sum(v.get("top_beats_random_p", 1) < 0.05 for v in val),
    }


def dir_size_mb(path):
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(path) for f in fs) / 1e6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", default="queries.txt")
    ap.add_argument("--n-intent-top", type=int, default=5, help="top intents, each x 3 outcomes")
    ap.add_argument("--n-rare", type=int, default=3, help="rarest intents, escalation only (insufficient-data cases)")
    ap.add_argument("--limit", type=int, default=None, help="only run the first N queries (smoke test)")
    ap.add_argument("--skip-sessions", action="store_true")
    a = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("WARNING: ANTHROPIC_API_KEY not set. Answers will be raw evidence bundles, not prose.")
    os.makedirs(os.path.join(OUT, "sessions"), exist_ok=True)
    cache_attribution()
    data = E.Data()

    queries = load_base_queries(a.queries) + intent_queries(data, a.n_intent_top, a.n_rare)
    queries = list(dict.fromkeys(queries))[: a.limit]
    with open(os.path.join(OUT, "query_set.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(queries) + "\n")

    results = []
    for n, q in enumerate(queries, 1):
        r = run_query(data, q)
        results.append(r)
        print(f"[{n}/{len(queries)}] {r['status']:<18} n={r['n_treatment']}  {q}")
    with open(os.path.join(OUT, "query_results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1, default=str)
    with open(os.path.join(OUT, "query_results.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["query", "outcome", "intent", "status", "n_treatment", "n_control", "answer"])
        for r in results:
            w.writerow([r["query"], r["outcome"], r["intent"], r["status"], r["n_treatment"], r["n_control"],
                        r["answer"]])

    sessions = []
    if not a.skip_sessions:
        for name, msgs in session_scripts(data).items():
            sess = run_session(data, name, msgs)
            sessions.append(sess)
            write_session_md(sess, os.path.join(OUT, "sessions", name + ".md"))
            print(f"session done: {name}")
        with open(os.path.join(OUT, "sessions.json"), "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=1, default=str)

    summary = summarize(results, sessions)
    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1))
    size = dir_size_mb(OUT)
    print(f"outputs/ size: {size:.1f} MB (limit 1000 MB)" + ("  <-- TOO LARGE" if size > 1000 else ""))


if __name__ == "__main__":
    main()
