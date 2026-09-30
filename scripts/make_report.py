"""
Builds report/technical_report.pdf. Run from the repo root AFTER scripts/run_submission.py so that
outputs/summary.json exists and the Evaluation section carries your real numbers.

    python3 report/make_report.py
"""
import json
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

OUT = "report/technical_report.pdf"
INK, PALE = colors.HexColor("#12263A"), colors.HexColor("#EEF2F6")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], fontName="Helvetica-Bold", fontSize=14, textColor=INK,
                    spaceBefore=14, spaceAfter=6)
BODY = ParagraphStyle("B", parent=ss["Normal"], fontName="Helvetica", fontSize=10, leading=14, alignment=TA_LEFT)
CELL = ParagraphStyle("C", parent=BODY, fontSize=8.5, leading=11)
CELLB = ParagraphStyle("CB", parent=CELL, fontName="Helvetica-Bold", textColor=colors.white)
TITLE = ParagraphStyle("T", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=22, textColor=INK, alignment=TA_LEFT)
SUB = ParagraphStyle("S", parent=BODY, fontSize=11, textColor=colors.HexColor("#5B6B7B"))


def P(t, s=BODY):
    return Paragraph(t, s)


def bullets(items):
    return ListFlowable([ListItem(P(i), leftIndent=12) for i in items], bulletType="bullet", start="-", leftIndent=12)


def table(rows, widths, header=True):
    data = [[P(c, CELLB if (header and r == 0) else CELL) for c in row] for r, row in enumerate(rows)]
    t = Table(data, colWidths=[w * cm for w in widths], repeatRows=1 if header else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D3DD")),
          ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
          ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    if header:
        st.append(("BACKGROUND", (0, 0), (-1, 0), INK))
    t.setStyle(TableStyle(st))
    return t


def load_summary():
    try:
        with open("outputs/summary.json", encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return None


def evaluation(S):
    if not S:
        return [P("<b>Results not yet inserted.</b> Run <font face='Courier'>python3 scripts/run_submission.py</font> "
                  "and then rebuild this report so this section carries the measured numbers.")]
    rows = [["Measure", "Value"],
            ["Questions in the query set", str(S["n_queries"])],
            ["Answered with statistical evidence", str(S["n_ok"])],
            ["Insufficient-data responses (fewer than 30 calls)", str(S["n_insufficient"])],
            ["Questions declined (no outcome recognised)", str(S["n_unparsed"])],
            ["Multi-turn sessions / total turns", "%s / %s" % (S["n_sessions"], S["n_session_turns"])]]
    out = [table(rows, [10, 5]), Spacer(1, 6)]
    if S.get("mean_top_turn_drop") is not None:
        out.append(P("<b>Attribution validation.</b> Across %d distinct outcome/intent filters, masking the top-ranked "
                     "turn lowered the predicted outcome probability by %.3f on average, against %.3f for a random turn. "
                     "The top turn beat the random-turn baseline (Wilcoxon signed-rank, p &lt; 0.05) in %d of %d filters."
                     % (S["distinct_filters_validated"], S["mean_top_turn_drop"], S["mean_random_turn_drop"],
                        S["filters_top_beats_random_p05"], S["distinct_filters_validated"])))
    if not S.get("used_llm_synthesis"):
        out.append(P("<b>Note:</b> this run had no API key, so answers are evidence bundles rather than prose."))
    return out


def build():
    S = load_summary()
    doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=2*cm, rightMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm,
                            title="Causal Rationale Extraction System: Technical Report", author="", subject="")
    s = [P("Causal Rationale Extraction System", TITLE),
         P("Technical report - NLP / Agentic division hackathon", SUB), Spacer(1, 10)]

    s += [P("1. Problem and approach", H1),
          P("Given a corpus of agent-customer call transcripts with no outcome labels, the system answers natural language "
            "questions about why escalations, refunds and churn happen, backs each answer with statistics and cited "
            "dialogue turns, and supports contextual multi-turn follow-ups."),
          Spacer(1, 4),
          P("Questions such as 'why are escalations happening' are population-level analytical questions, not similarity "
            "lookups, so the system is not a retrieval chatbot. Offline stages label calls and extract behavioural "
            "features. The online stages select a subset of calls, compare it statistically with a control group, "
            "attribute the finding to specific turns by ablation, and only then give a language model the structured "
            "evidence to narrate. The language model is a writer constrained to the given evidence, not the source of the "
            "claim.")]

    s += [P("2. Data and quality findings", H1),
          P("These figures were verified directly against the dataset."),
          Spacer(1, 4),
          table([["Finding", "Detail and handling"],
                 ["Corpus size", "19,618 usable calls and 686,761 turns (mean 35 turns per call) after dropping 3 calls with fewer than 3 turns."],
                 ["Constant domain field", "The domain field has one value across all records and does not reflect call content, so it is excluded from filtering and feature engineering."],
                 ["Non-standard speaker labels", "1,332 calls (6.79 percent) use non-standard labels. Roles are resolved by first occurrence and an alternation check; unresolved calls are flagged, not guessed."],
                 ["Leaked meta turns", "A small number of calls contain a summary line that is not dialogue. These are stripped before feature extraction."]],
                [4, 13])]

    s += [P("3. Architecture", H1),
          table([["Stage", "Purpose", "Key detail"],
                 ["0 Normalize", "Fix data quality first", "Strip meta turns, resolve speaker roles, flag uncertain calls."],
                 ["1 Labeling", "Silver outcome labels", "Intent prior, zero-shot classification of reason_for_call, keyword scan. Confidence-tiered merge: zero-shot score 0.75 or above confirms alone; 0.5 to 0.75 needs one corroborating signal; otherwise low confidence."],
                 ["2 Features", "Turn and call signals", "A cheap emotion model runs on all turns; the expensive zero-shot dialogue-act model runs only on negative turns. Aggregated to call-level features such as peak frustration, frustration slope, repetition and apology counts."],
                 ["3 Indexing", "Exemplar retrieval", "reason_for_call plus first and last real turns embedded and stored in a vector database with all metadata."],
                 ["4 Query parsing", "Question to filter", "Classify the outcome; match query terms against the 35 real intent values."],
                 ["5 Comparison", "What differs", "Outcome calls versus the rest (intent-matched), tested per feature and ranked by effect size and significance."],
                 ["6 Attribution", "Which turns drive it", "Leave-one-turn-out ablation on a lightweight classifier."],
                 ["7 Synthesis", "Readable explanation", "Constrained prompt: statistics and cited turns only, 'associated with' rather than 'causes', under 200 words."]],
                [2.6, 3.4, 11])]

    s += [P("4. Statistical comparison (Stage 5)", H1),
          bullets(["Treatment group: calls with the matching outcome. Control group: the remaining calls, intent-matched where possible.",
                   "A t-test (numeric features) or chi-square test (categorical features) per feature, ranked by effect size and significance.",
                   "If the treatment group has fewer than 30 calls after filtering, the system reports insufficient data rather than a result with false confidence."])]

    s += [P("5. Attribution and validation (Stage 6)", H1),
          bullets(["A logistic regression is fitted on call-level behavioural features, outcome calls versus control. Call length and supervisor counts are excluded because they are proxies for the label, not behaviour.",
                   "For 12 representative calls, spread across the predicted-probability range rather than the extremes, each turn is removed in turn, call features are recomputed, and the drop in predicted probability ranks the turns. The top three per call are kept as evidence.",
                   "Validation: for each call, the drop from masking the top turn is compared with the expected drop from masking a random turn, using a one-sided Wilcoxon signed-rank test. Validation runs when at least 5 calls were ablated."])]

    s += [P("6. Conversational layer (Task 2)", H1),
          P("Task 2 is a thin stateful session around the same pipeline. One structured model call classifies each message "
            "and extracts the change to the filter."),
          Spacer(1, 4),
          table([["Message type", "Behaviour"],
                 ["Refine", "Merge the new constraint into the filter and re-run stages 4 to 7. Attribution is re-run only if the treatment group size changes by more than 20 percent; otherwise existing evidence is reused. Evidence is overwritten, never appended."],
                 ["Drill down", "No recomputation. Serve further cited turns from the evidence already computed."],
                 ["New topic", "Reset filter, factors and evidence. History is kept for the audit trail only."],
                 ["History and small groups", "History holds only query, one-line summary and timestamp per turn (capped). The 30-call floor also applies to narrow refinements."]],
                [3.5, 13.5])]

    s += [P("7. Evaluation", H1)] + evaluation(S)

    s += [P("8. Limitations and guardrails", H1),
          bullets(["Outcome labels are silver labels from heuristic and zero-shot signals, not ground truth. All findings are statistical associations and are worded as such.",
                   "Attribution explains a lightweight proxy classifier, so cited turns show what drives the classifier's prediction, which is evidence for, not proof of, causation.",
                   "Query parsing is keyword-based and intent matching is by substring, so unusual phrasings may not resolve to an outcome; the system then asks the user to name one.",
                   "Every claim in a generated answer must cite a call and turn, and the model receives no material beyond the evidence bundle."])]

    s += [P("9. Reproducibility", H1),
          table([["Layer", "Tools"],
                 ["Data", "pandas, pyarrow, ijson (streaming JSON, parquet tables)"],
                 ["Models", "facebook/bart-large-mnli (zero-shot), j-hartmann/emotion-english-distilroberta-base (emotion), all-MiniLM-L6-v2 (embeddings)"],
                 ["Storage and stats", "ChromaDB, scipy, scikit-learn"],
                 ["Synthesis and serving", "Anthropic API, FastAPI"],
                 ["Compute", "Local CPU for logic and light models; Google Colab T4 for the two batched HuggingFace passes (labeling, turn features), each run once"]],
                [3.5, 13.5]),
          Spacer(1, 6),
          P("Run <font face='Courier'>python3 src/explain.py</font> for the chat interface, "
            "<font face='Courier'>python3 scripts/run_submission.py</font> to regenerate the query results, and "
            "<font face='Courier'>python3 scripts/demo.py</font> for the scripted demo.")]

    doc.build(s)
    print("wrote", OUT)


if __name__ == "__main__":
    build()
