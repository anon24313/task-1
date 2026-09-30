// Builds deck/presentation.pptx. Run from the repo root AFTER scripts/run_submission.py so slide 7 shows real numbers:
//   node deck/make_deck.js
const pptxgen = require("pptxgenjs");
const fs = require("fs");

let S = null;
try { S = JSON.parse(fs.readFileSync("outputs/summary.json", "utf8")); } catch (e) {}

const INK = "12263A", PAPER = "F4F6F8", AMBER = "F2A541", SLATE = "5B6B7B", WHITE = "FFFFFF", MIST = "C9D6E2";
const HEAD = "Cambria", BODY = "Calibri";

const pres = new pptxgen();
pres.layout = "LAYOUT_16x9";
pres.title = "Causal Rationale Extraction System";
pres.author = "Anonymous";
pres.company = "";

const shadow = () => ({ type: "outer", color: "000000", blur: 6, offset: 2, angle: 90, opacity: 0.12 });

function base(title, notes) {
  const s = pres.addSlide();
  s.background = { color: PAPER };
  s.addText(title, { x: 0.5, y: 0.3, w: 9, h: 0.7, fontFace: HEAD, fontSize: 32, bold: true, color: INK, margin: 0, isTextBox: true });
  s.addNotes(notes);
  return s;
}
function badge(s, x, y, label, d = 0.36) {
  s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: AMBER }, line: { color: AMBER } });
  s.addText(String(label), { x, y, w: d, h: d, align: "center", valign: "middle", fontFace: BODY, fontSize: 13, bold: true, color: INK, margin: 0, isTextBox: true });
}
function card(s, x, y, w, h) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.08, fill: { color: WHITE }, line: { color: "DDE3E9", width: 0.75 }, shadow: shadow() });
}
const bl = (arr) => arr.map((t, i) => ({ text: t, options: { bullet: true, breakLine: i < arr.length - 1, paraSpaceAfter: 6 } }));

// 1. Title
{
  const s = pres.addSlide();
  s.background = { color: INK };
  s.addText("Causal Rationale Extraction System", { x: 0.7, y: 1.35, w: 8.6, h: 1.4, fontFace: HEAD, fontSize: 40, bold: true, color: WHITE, margin: 0, valign: "top", isTextBox: true });
  s.addText("Why do escalations, refunds and churn happen? Evidence-backed answers from call transcripts.", { x: 0.7, y: 2.85, w: 8, h: 0.8, fontFace: BODY, fontSize: 18, color: MIST, margin: 0, valign: "top", isTextBox: true });
  ["Escalation", "Refund", "Churn"].forEach((t, i) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 0.7 + i * 1.65, y: 3.95, w: 1.5, h: 0.42, rectRadius: 0.2, fill: { color: INK }, line: { color: AMBER, width: 1.25 } });
    s.addText(t, { x: 0.7 + i * 1.65, y: 3.95, w: 1.5, h: 0.42, align: "center", valign: "middle", fontFace: BODY, fontSize: 13, color: AMBER, bold: true, margin: 0, isTextBox: true });
  });
  s.addText("NLP / Agentic division hackathon", { x: 0.7, y: 4.95, w: 6, h: 0.3, fontFace: BODY, fontSize: 12, color: MIST, margin: 0, isTextBox: true });
  s.addNotes("Introduce the task: no outcome labels, so the system must explain why escalations, refunds and churn happen, with evidence.");
}

// 2. Not retrieval
{
  const s = base("Not retrieval. Analysis.", "A question like 'why are escalations happening' is about a population of calls. Similar-call lookup gives anecdotes; we compare groups, attribute to turns, then let the LLM narrate.");
  card(s, 0.5, 1.3, 4.2, 2.6);
  s.addText("A retrieval chatbot", { x: 0.75, y: 1.45, w: 3.7, h: 0.4, fontFace: HEAD, fontSize: 20, bold: true, color: SLATE, margin: 0, isTextBox: true });
  s.addText(bl(["Finds similar calls", "Gives anecdotes, not patterns", "Lets the model invent the cause"]), { x: 0.75, y: 2.0, w: 3.7, h: 1.7, fontFace: BODY, fontSize: 15, color: SLATE, margin: 0, valign: "top", isTextBox: true });
  card(s, 5.3, 1.3, 4.2, 2.6);
  s.addText("Our system", { x: 5.55, y: 1.45, w: 3.7, h: 0.4, fontFace: HEAD, fontSize: 20, bold: true, color: INK, margin: 0, isTextBox: true });
  s.addText(bl(["Selects a population of calls", "Compares it with a control group", "Attributes the finding to turns", "Narrates only that evidence"]), { x: 5.55, y: 2.0, w: 3.7, h: 1.7, fontFace: BODY, fontSize: 15, color: INK, margin: 0, valign: "top", isTextBox: true });
  s.addText("The language model is a writer, not the source of the causal claim.", { x: 0.5, y: 4.4, w: 9, h: 0.6, fontFace: HEAD, fontSize: 20, italic: true, color: INK, margin: 0, isTextBox: true });
}

// 3. Data
{
  const s = base("The data, and what we fixed", "All figures were verified against the real dataset. The domain field is constant, so we never filter on it.");
  const stats = [["19,618", "usable calls"], ["686,761", "turns, 35 per call on average"], ["6.79%", "calls with non-standard speaker labels"], ["1", "constant 'domain' field, excluded"]];
  stats.forEach(([n, l], i) => {
    const x = 0.5 + (i % 2) * 4.6, y = 1.3 + Math.floor(i / 2) * 1.6;
    card(s, x, y, 4.4, 1.4);
    s.addText(n, { x: x + 0.3, y: y + 0.15, w: 3.8, h: 0.7, fontFace: HEAD, fontSize: 38, bold: true, color: INK, margin: 0, isTextBox: true });
    s.addText(l, { x: x + 0.3, y: y + 0.85, w: 3.8, h: 0.4, fontFace: BODY, fontSize: 14, color: SLATE, margin: 0, isTextBox: true });
  });
  s.addText("3 calls under 3 turns dropped. Leaked summary turns stripped. Unresolved speaker roles flagged, not guessed.", { x: 0.5, y: 4.6, w: 9, h: 0.5, fontFace: BODY, fontSize: 13, color: SLATE, margin: 0, isTextBox: true });
}

// 4. Pipeline
{
  const s = base("Eight stages, two halves", "Offline stages run once; the GPU is only needed for the labeling zero-shot pass and turn features. Online stages run per question.");
  const rows = [
    ["Offline: run once", [["0", "Normalize", "Strip meta turns, resolve speaker roles"], ["1", "Label", "Silver labels from three signals"], ["2", "Features", "Cheap emotion pass first, dialogue acts on negative turns only"], ["3", "Index", "Exemplar calls embedded in ChromaDB"]]],
    ["Online: per question", [["4", "Parse", "Question to outcome and intent filter"], ["5", "Compare", "Treatment vs control, tests, effect size"], ["6", "Attribute", "Leave-one-turn-out ablation"], ["7", "Synthesize", "Claude writes from the evidence only"]]],
  ];
  rows.forEach(([label, items], r) => {
    const y0 = 1.25 + r * 2.0;
    s.addText(label, { x: 0.5, y: y0, w: 5, h: 0.3, fontFace: BODY, fontSize: 12, bold: true, color: SLATE, margin: 0, isTextBox: true });
    items.forEach(([n, t, d], i) => {
      const x = 0.5 + i * 2.3, y = y0 + 0.35;
      card(s, x, y, 2.1, 1.45);
      badge(s, x + 0.12, y + 0.12, n, 0.34);
      s.addText(t, { x: x + 0.56, y: y + 0.1, w: 1.45, h: 0.38, fontFace: BODY, fontSize: 14, bold: true, color: INK, margin: 0, valign: "middle", isTextBox: true });
      s.addText(d, { x: x + 0.12, y: y + 0.58, w: 1.86, h: 0.8, fontFace: BODY, fontSize: 11, color: SLATE, margin: 0, valign: "top", isTextBox: true });
    });
  });
}

// 5. Evidence engine
{
  const s = base("The evidence engine", "Stage 5 finds what differs; stage 6 finds which turns drive it. The classifier deliberately excludes call length and supervisor counts, which are label proxies.");
  card(s, 0.5, 1.3, 4.4, 3.5);
  badge(s, 0.7, 1.45, 5);
  s.addText("Statistical comparison", { x: 1.15, y: 1.45, w: 3.6, h: 0.36, fontFace: HEAD, fontSize: 18, bold: true, color: INK, margin: 0, valign: "middle", isTextBox: true });
  s.addText(bl(["Outcome calls vs the rest, intent-matched", "t-test or chi-square per feature", "Ranked by effect size and significance", "Under 30 calls: reports insufficient data"]), { x: 0.7, y: 2.05, w: 4.0, h: 2.6, fontFace: BODY, fontSize: 14, color: INK, margin: 0, valign: "top", isTextBox: true });
  card(s, 5.1, 1.3, 4.4, 3.5);
  badge(s, 5.3, 1.45, 6);
  s.addText("Turn attribution", { x: 5.75, y: 1.45, w: 3.6, h: 0.36, fontFace: HEAD, fontSize: 18, bold: true, color: INK, margin: 0, valign: "middle", isTextBox: true });
  s.addText(bl(["Drop each turn, re-score the call", "12 calls across the probability range", "Call length and supervisor counts excluded as label proxies", "Top turn vs random turn: Wilcoxon test"]), { x: 5.3, y: 2.05, w: 4.0, h: 2.6, fontFace: BODY, fontSize: 14, color: INK, margin: 0, valign: "top", isTextBox: true });
}

// 6. Conversation
{
  const s = base("Follow-ups that stay grounded", "One structured LLM call classifies the message and extracts the delta. The 30-call floor also applies to narrow refinements.");
  const cols = [["1", "Refine", "Merge the new constraint and re-run stages 4 to 7. Re-attribute only if group size moves over 20%."], ["2", "Drill down", "No recompute. Serve more cited turns from the evidence already computed."], ["3", "New topic", "Reset filter, factors and evidence. History kept for the audit trail only."]];
  cols.forEach(([n, t, d], i) => {
    const x = 0.5 + i * 3.1;
    card(s, x, 1.3, 2.9, 2.7);
    badge(s, x + 0.2, 1.5, n);
    s.addText(t, { x: x + 0.2, y: 2.0, w: 2.5, h: 0.4, fontFace: HEAD, fontSize: 20, bold: true, color: INK, margin: 0, isTextBox: true });
    s.addText(d, { x: x + 0.2, y: 2.5, w: 2.5, h: 1.4, fontFace: BODY, fontSize: 14, color: SLATE, margin: 0, valign: "top", isTextBox: true });
  });
  s.addText("History is capped to query, one-line summary and timestamp per turn.", { x: 0.5, y: 4.4, w: 9, h: 0.4, fontFace: BODY, fontSize: 13, color: SLATE, margin: 0, isTextBox: true });
}

// 7. Guardrails and results
{
  const s = pres.addSlide();
  s.background = { color: INK };
  s.addText(S ? "Guardrails and results" : "Guardrails", { x: 0.5, y: 0.3, w: 9, h: 0.7, fontFace: HEAD, fontSize: 32, bold: true, color: WHITE, margin: 0, isTextBox: true });
  const items = ["Silver labels, not ground truth: findings are 'associated with', never 'causes'", "Under 30 calls, the system says so instead of guessing", "Every claim cites a call and turn; the model sees only the evidence bundle", "Attribution is checked against random-turn masking"];
  s.addText(bl(items), { x: 0.5, y: 1.3, w: S ? 4.8 : 9, h: 3.4, fontFace: BODY, fontSize: 16, color: WHITE, margin: 0, valign: "top", isTextBox: true });
  if (S) {
    const stats = [[S.n_queries, "questions run"], [S.n_ok, "answered with evidence"], [S.n_insufficient, "insufficient-data cases"]];
    if (S.mean_top_turn_drop != null) stats.push([`${S.mean_top_turn_drop} vs ${S.mean_random_turn_drop}`, "top vs random turn drop"]);
    stats.forEach(([n, l], i) => {
      const y = 1.3 + i * 0.95;
      s.addText(String(n), { x: 5.8, y, w: 3.7, h: 0.55, fontFace: HEAD, fontSize: 30, bold: true, color: AMBER, margin: 0, isTextBox: true });
      s.addText(l, { x: 5.8, y: y + 0.52, w: 3.7, h: 0.3, fontFace: BODY, fontSize: 12, color: MIST, margin: 0, isTextBox: true });
    });
  }
  s.addNotes("Close on honesty: silver labels, statistical association, insufficient-data handling, and cited evidence.");
}

pres.writeFile({ fileName: "deck/presentation.pptx" }).then(() => console.log("wrote deck/presentation.pptx"));
