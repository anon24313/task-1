# session_1_escalation

## Turn 1
**User:** Why are escalations happening?

*Filter after turn:* `{"outcome": "escalation", "intent": null}`  |  *n treatment:* 206  |  *recomputed stages 4-7*

[no ANTHROPIC_API_KEY set - evidence bundle that would be sent to Claude]
{
 "question": "Why are escalations happening?",
 "statistics": {
  "outcome": "escalation",
  "intent": null,
  "n_treatment": 206,
  "n_control": 875,
  "status": "ok",
  "intent_matched": true,
  "n_significant": 11,
  "findings": [
   {
    "feature": "word_ratio",
    "description": "agent-to-customer word ratio",
    "direction": "lower",
    "treatment_value": 1.346,
    "control_value": 1.495,
    "effect_size": -0.497,
    "effect_name": "cohens_d",
    "test": "welch_t",
    "p_adj": 4.37e-06,
    "significant": true
   },
   {
    "feature": "cust_neg_mean",
    "description": "average customer negative emotion",
    "direction": "higher",
    "treatment_value": 0.319,
    "control_value": 0.277,
    "effect_size": 0.361,
    "effect_name": "cohens_d",
    "test": "welch_t",
    "p_adj": 3.57e-05,
    "significant": true
   },
   {
    "feature": "agent_empathy_count",
    "description": "number of agent empathy phrases",
    "direction": "lower",
    "treatment_value": 2.323,
    "control_value": 2.874,
    "effect_size": -0.34,
    "effect_name": "cohens_d",
    "test": "welch_t",
    "p_adj": 4.37e-06,
    "significant": true
   },
   {
    "feature": "cust_avg_words",
    "description": "average words per customer turn",
    "direction": "lower",
    "treatment_value": 12.427,
    "control_value": 13.25,
    "effect_size": -0.332,
    "effect_name": "cohens_d",
    "test": "welch_t",
    "p_adj": 8.77e-05,
    "significant": true
   },
   {
    "feature": "cust_neg_turn_frac",
    "description": "share of customer turns that are strongly negative",
    "direction": "higher",
    "treatment_value": 0.289,
    "control_value": 0.241,
    "effect_size": 0.332,
    "effect_name": "cohens_d",
    "test": "welch_t",
    "p_adj": 0.000117,
    "significant": true
   },
   {
    "feature": "agent_avg_words",
    "description": "average words per agent turn",
    "direction": "lower",
    "treatment_value": 17.637,
    "control_value": 18.446,
    "effect_size": -0.328,
    "effect_name": "cohens_d",
    "test": "welch_t",
    "p_adj": 8.99e-05,
    "significant": true
   },
   {
    "feature": "cust_neg_first3",
    "description": "customer negativity in the first 3 turns",
    "direction": "higher",
    "treatment_value": 0.378,
    "control_value": 0.323,
    "effect_size": 0.295,
    "effect_name": "cohens_d",
    "test": "welch_t",
    "p_adj": 0.000379,
    "significant": true
   },
   {
    "feature": "cust_neg_last3",
    "description": "customer negativity in the last 3 turns",
    "direction": "higher",
    "treatment_value": 0.239,
    "control_value": 0.187,
    "effect_size": 0.278,
    "effect_name": "cohens_d",
    "test": "welch_t",
    "p_adj": 0.00278,
    "significant": true
   }
  ],
  "intent_mix_treatment": {
   "Refund Policy": 116,
   "Booking Errors": 16,
   "Claims & refunds": 14,
   "Refund Delays": 7,
   "Cancellation Policies": 6
  }
 },
 "attribution_validation": {
  "calls_ablated": 12,
  "mean_top_turn_drop": 0.1334,
  "mean_random_turn_drop": 0.0058,
  "top_beats_random_p": 0.000244
 },
 "cited_turns": [
  {
   "transcript_id": "82f0ce77-035c-4e08-a393-a62ad7d78e13",
   "turn_id": 1,
   "speaker": "Customer",
   "prob_drop": 0.2241,
   "call_prob": 0.726,
   "text": "hi james this is um addison roberts i\u2019m calling about a refund for my flight delay from sfo to jfk"
  },
  {
   "transcript_id": "61e2a5a6-1ade-4704-bdf0-aaa064105491",
   "turn_id": 3,
   "speaker": "Customer",
   "prob_drop": 0.2158,
   "call_prob": 0.559,
   "text": "about a refund for my canceled flight from seattle to"
  },
  {
   "transcript_id": "6857a5e4-aa98-488a-9dd6-c8c96c7f90ea",
   "turn_id": 1,
   "speaker": "Customer",
   "prob_drop": 0.2055,
   "call_prob": 0.822,
   "text": "hi michael this is john rogers i\u2019m uh calling about a refund for my flight from lax to ord"
  },
  {
   "transcript_id": "dd00cd30-b224-4f88-8083-62ccb3a3fa40",
   "turn_id": 27,
   "speaker": "Customer",
   "prob_drop": 0.1929,
   "call_prob": 0.673,
   "text": "so there\u2019s nothing you can do that\u2019s incredibly frustrating"
  },
  {
   "transcript_id": "e16a70b3-7791-42a1-a589-1557352e922f",
   "turn_id": 5,
   "speaker": "Customer",
   "prob_drop": 0.1834,
   "call_prob": 0.521,
   "text": "yeah it was a flight from lax uh to ord it got delayed"
  },
  {
   "transcript_id": "2ca157fa-cf01-472c-9a57-ba0fba054c4b",
   "turn_id": 3,
   "speaker": "Customer",
   "prob_drop": 0.1776,
   "call_prob": 0.615,
   "text": "it's pnr a1b2c3 i've been looking into the refund policy and it seems really confusing"
  }
 ]
}

## Turn 2
**User:** What about Loyalty Program calls only?

*Filter after turn:* `{"outcome": "escalation", "intent": "Loyalty Program"}`  |  *n treatment:* 3  |  *recomputed stages 4-7*

Only 3 calls match this filter (minimum 30); no statistical claim is made.

## Turn 3
**User:** Show me more examples

*Filter after turn:* `{"outcome": "escalation", "intent": "Loyalty Program"}`  |  *n treatment:* 3  |  *served from cached evidence (no recompute)*

No further evidence available for this filter.

## Turn 4
**User:** Now why do customers ask for refunds?

*Filter after turn:* `{"outcome": "refund", "intent": null}`  |  *n treatment:* 3630  |  *recomputed stages 4-7*

[no ANTHROPIC_API_KEY set - evidence bundle that would be sent to Claude]
{
 "question": "Now why do customers ask for refunds?",
 "statistics": {
  "outcome": "refund",
  "intent": null,
  "n_treatment": 3630,
  "n_control": 5428,
  "status": "ok",
  "intent_matched": true,
  "n_significant": 15,
  "findings": [
   {
    "feature": "cust_joy_last3",
    "description": "customer positive emotion at the end of the call",
    "direction": "lower",
    "treatment_value": 0.322,
    "control_value": 0.361,
    "effect_size": -0.18,
    "effect_name": "cohens_d",
    "test": "welch_t",
    "p_adj": 3.89e-16,
    "significant": true
   },
   {
    "feature": "max_rep_sim",
    "description": "highest similarity between repeated customer statements",
    "direction": "higher",
    "treatment_value": 0.63,
    "control_value": 0.616,
    "effect_size": 0.135,
    "effect_name": "cohens_d",
    "test": "welch_t",
    "p_adj": 1.92e-09,
    "significant": true
   },
   {
    "feature": "cust_neg_onset_frac",
    "description": "how early strong negativity begins (0=start, 1=never)",
    "direction": "lower",
    "treatment_value": 0.178,
    "control_value": 0.214,
    "effect_size": -0.131,
    "effect_name": "cohens_d",
    "test": "welch_t",
    "p_adj": 2.75e-09,
    "significant": true
   },
   {
    "feature": "supervisor_joined",
    "description": "a supervisor joined the call",
    "direction": "higher",
    "treatment_value": 0.034,
    "control_value": 0.003,
    "effect_size": 0.12,
    "effect_name": "cramers_v",
    "test": "chi_square",
    "p_adj": 7.57e-29,
    "significant": true
   },
   {
    "feature": "cust_neg_mean",
    "description": "average customer negative emotion",
    "direction": "higher",
    "treatment_value": 0.25,
    "control_value": 0.238,
    "effect_size": 0.115,
    "effect_name": "cohens_d",
    "test": "welch_t",
    "p_adj": 3.22e-07,
    "significant": true
   },
   {
    "feature": "cust_neg_turn_frac",
    "description": "share of customer turns that are strongly negative",
    "direction": "higher",
    "treatment_value": 0.213,
    "control_value": 0.2,
    "effect_size": 0.105,
    "effect_name": "cohens_d",
    "test": "welch_t",
    "p_adj": 3.22e-06,
    "significant": true
   },
   {
    "feature": "cust_avg_words",
    "description": "average words per customer turn",
    "direction": "lower",
    "treatment_value": 12.899,
    "control_value": 13.16,
    "effect_size": -0.103,
    "effect_name": "cohens_d",
    "test": "welch_t",
    "p_adj": 2.85e-06,
    "significant": true
   },
   {
    "feature": "cust_neg_last3",
    "description": "customer negativity in the last 3 turns",
    "direction": "higher",
    "treatment_value": 0.165,
    "control_value": 0.152,
    "effect_size": 0.079,
    "effect_name": "cohens_d",
    "test": "welch_t",
    "p_adj": 0.000573,
    "significant": true
   }
  ],
  "intent_mix_treatment": {
   "Refund Policy": 539,
   "Refund Delays": 503,
   "Replacement vs Refund": 457,
   "Product Returns": 344,
   "Cross-Brand Mentions": 299
  }
 },
 "attribution_validation": {
  "calls_ablated": 12,
  "mean_top_turn_drop": 0.0666,
  "mean_random_turn_drop": 0.0042,
  "top_beats_random_p": 0.000244
 },
 "cited_turns": [
  {
   "transcript_id": "2aaabb74-73e5-486b-b96c-05b0bc86e2a6",
   "turn_id": 21,
   "speaker": "Customer",
   "prob_drop": 0.1201,
   "call_prob": 0.608,
   "text": "i appreciate um the quick response and delivery options but those seem to be failing right now"
  },
  {
   "transcript_id": "4d89c84d-edc8-487a-931f-60e4d90ab825",
   "turn_id": 5,
   "speaker": "Customer",
   "prob_drop": 0.0984,
   "call_prob": 0.543,
   "text": "they don\u2019t fit right um according to the size chart"
  },
  {
   "transcript_id": "d7d37053-79bc-4f35-925a-ccd9bed8d330",
   "turn_id": 11,
   "speaker": "Customer",
   "prob_drop": 0.0852,
   "call_prob": 0.588,
   "text": "but like it\u2019s already delayed and over the estimated time"
  },
  {
   "transcript_id": "d8c69f16-f74a-4c10-a43e-8afedf0446ca",
   "turn_id": 17,
   "speaker": "Customer",
   "prob_drop": 0.0849,
   "call_prob": 0.557,
   "text": "not really i'm boarding in like fifteen minutes ugh i really don't know now"
  },
  {
   "transcript_id": "5ce01898-498f-4cba-a957-8b665fefe875",
   "turn_id": 23,
   "speaker": "Customer",
   "prob_drop": 0.0754,
   "call_prob": 0.495,
   "text": "yeah and how long would that take"
  },
  {
   "transcript_id": "4d89c84d-edc8-487a-931f-60e4d90ab825",
   "turn_id": 23,
   "speaker": "Customer",
   "prob_drop": 0.0739,
   "call_prob": 0.543,
   "text": "that makes sense so um just to recap i\u2019m sending back jeans for not fitting and i\u2019ll get a full refund to my original payment method"
  }
 ]
}

## Turn 5
**User:** Show me more examples

*Filter after turn:* `{"outcome": "refund", "intent": null}`  |  *n treatment:* 3630  |  *served from cached evidence (no recompute)*

More cited turns for the current findings:
[8f823c2f-dd75-4195-a273-5753414124b2 turn 11, Customer, prob drop 0.072] uh uh does this take long i i really need to resolve this
[8b79fc54-99ba-4584-bba4-4643c31e630d turn 27, Customer, prob drop 0.071] absolutely i agree i’ll wait for the email
[5ce01898-498f-4cba-a957-8b665fefe875 turn 25, Customer, prob drop 0.069] well i guess that’s not too terrible but i’m honestly considering other hotel options like marriott
[d7d37053-79bc-4f35-925a-ccd9bed8d330 turn 5, Customer, prob drop 0.066] hmm i’ve noticed some delays especially compared to like amazon it’s frustrating
[d7d37053-79bc-4f35-925a-ccd9bed8d330 turn 21, Customer, prob drop 0.065] so i’m just stuck waiting
[5ce01898-498f-4cba-a957-8b665fefe875 turn 13, Customer, prob drop 0.063] besides the noise i expected uh better service overall it just wasn’t what i paid for
