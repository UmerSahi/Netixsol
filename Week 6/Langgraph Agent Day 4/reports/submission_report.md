# AFL LangGraph Agent — Submission Report

_Generated 2026-08-22 20:25_

---

# Model Training Report

_Generated 2026-08-22 20:25_

All models are split strictly chronologically by season (no shuffling).

| Task | Model | Train seasons | Val season | Test seasons | Val metric | Test metric |
|---|---|---|---|---|---|---|
| Match winner (classification) | `logistic_regression` | <= 2022 | 2023 | [2024, 2025] | roc_auc=0.7023 | roc_auc=0.7143 |
| Player top-disposals (classification) | `random_forest` | <= 2022 | 2023 | [2024, 2025] | roc_auc=0.8967 | roc_auc=0.8953 |
| Player expected disposals (regression) | `hist_gradient_boosting` | <= 2022 | 2023 | [2024, 2025] | mae=3.9737 | mae=3.8943 |

### Match winner (classification)

- **Target column:** `home_win`
- **Feature count:** 64
- **Notes:** val_acc=0.6636, test_acc=0.6589. Draws dropped from target (undefined for binary classification).

### Player top-disposals (classification)

- **Target column:** `is_match_top_disposals`
- **Feature count:** 52
- **Notes:** Binary target = top disposal-getter within player's own team for that match.

### Player expected disposals (regression)

- **Target column:** `disposals`
- **Feature count:** 52
- **Notes:** Regression output is a predicted disposal COUNT, not a probability.


---

# Router Evaluation

_Generated 2026-08-22 20:24_

**Routing accuracy: 100.00% (21/21)**

| Query | Expected Intent | Predicted Intent | Correct |
|---|---|---|---|
| Who did Geelong play in Round 5 of 2020? | `retrieval` | `retrieval` | ✅ |
| What was the score? | `retrieval` | `retrieval` | ✅ |
| What was Patrick Dangerfield's season average in 2020? | `retrieval` | `retrieval` | ✅ |
| Who had the most disposals for Geelong in Round 5 2020? | `retrieval` | `retrieval` | ✅ |
| Who will win Cats vs Pies? | `prediction_match` | `prediction_match` | ✅ |
| Will the Pies beat the Cats? | `prediction_match` | `prediction_match` | ✅ |
| Predict Geelong vs Sydney. | `prediction_match` | `prediction_match` | ✅ |
| Who will win AFL in 2030? | `prediction_premiership` | `prediction_premiership` | ✅ |
| Who is most likely to lead Geelong in disposals? | `prediction_player` | `prediction_player` | ✅ |
| Who will top-score for Geelong? | `prediction_player` | `prediction_player` | ✅ |
| Predict the top player for Collingwood. | `prediction_player` | `prediction_player` | ✅ |
| What is holding the ball? | `direct_afl` | `direct_afl` | ✅ |
| What is a contested mark? | `direct_afl` | `direct_afl` | ✅ |
| What is the offside rule in soccer? | `off_topic` | `off_topic` | ✅ |
| Who is the greatest basketball player? | `off_topic` | `off_topic` | ✅ |
| Explain quantum physics. | `off_topic` | `off_topic` | ✅ |
| Give me a pasta recipe. | `off_topic` | `off_topic` | ✅ |
| What about last round? | `ambiguous` | `ambiguous` | ✅ |
| Who will win? | `ambiguous` | `ambiguous` | ✅ |
| Tell me about the Cats. | `ambiguous` | `ambiguous` | ✅ |
| What's the best sport? | `ambiguous` | `ambiguous` | ✅ |

## Misroutes

None — all queries routed correctly.


---

# End-to-End Conversations

_Generated 2026-08-22 20:25_

13 conversations, run through the real compiled LangGraph (no hand-written responses).

## 1. Exact match retrieval
_Covers: Exact match retrieval_

**User:** Who did Geelong play in Round 5 of 2020?
**Assistant:** According to the structured AFL dataset, Geelong Cats played Gold Coast Suns in round 5 of the 2020 season at GMHBA Stadium. Final score: Geelong Cats 89 - 52 Gold Coast Suns (win).
> intent: `retrieval` · validation: `success`

## 2. Player statistics
_Covers: Player statistics_

**User:** What was Patrick Dangerfield's average disposals in 2020?
**Assistant:** According to the structured AFL dataset, Patrick Dangerfield played 11 games in 2020, averaging 21.55 disposals and 0.73 goals per game (totals: 237 disposals, 8 goals).
> intent: `retrieval` · validation: `success`

## 3. Head-to-head retrieval
_Covers: Head-to-head retrieval_

**User:** What is Geelong's head to head record against Collingwood?
**Assistant:** According to the structured AFL dataset, Collingwood Magpies and Geelong Cats have played 67 times. Collingwood Magpies have won 31, Geelong Cats have won 36, with 0 draws.
> intent: `retrieval` · validation: `success`

## 4. Match winner prediction
_Covers: Match winner prediction_

**User:** Who will win Cats vs Pies?
**Assistant:** Based on the trained model (logistic_regression) and available historical data, Geelong Cats have an estimated 75.9% probability of winning (Geelong Cats 75.9% vs Collingwood Magpies 24.1%). The prediction is mainly supported by scoring average difference. This is a model estimate, not a guaranteed result. I can make a matchup prediction for these two teams using the latest available historical data, but I cannot verify from the local dataset that they are actually scheduled to play at this time.
> intent: `prediction_match` · validation: `success`

## 5. Player prediction
_Covers: Player prediction_

**User:** Who is most likely to lead Geelong in disposals?
**Assistant:** Based on the model, Bailey Smith is currently the highest-ranked prediction for Geelong Cats to record the most disposals, with an estimated probability of 85.6%. This is probabilistic, not certain. Other contenders: Bailey Smith (85.6%); Max Holmes (83.6%); Gryan Miers (72.4%); Tom Stewart (42.9%); Tom Atkins (39.8%).
> intent: `prediction_player` · validation: `success`

## 6. Direct AFL explanation
_Covers: Direct AFL explanation_

**User:** What is holding the ball?
**Assistant:** A free kick paid against a tackled player who had prior opportunity to dispose of the ball correctly (by hand or foot) but failed to do so before or during the tackle.
> intent: `direct_afl` · validation: `success`

## 7. Off-topic refusal
_Covers: Off-topic refusal_

**User:** What is the offside rule in soccer?
**Assistant:** I can only help with AFL-related questions. You can ask me about AFL matches, player statistics, teams, rules, or supported predictions.
> intent: `off_topic` · validation: `n/a`

## 8. Ambiguous team
_Covers: Ambiguous team/player_

**User:** What was the score for Coast in round 1 2020?
**Assistant:** That could refer to multiple teams: Gold Coast Suns, West Coast Eagles. Which one did you mean?
> intent: `retrieval` · validation: `ambiguous`

## 8b. Ambiguous player
_Covers: Ambiguous team/player_

**User:** How many disposals did Smith have in round 1 2022?
**Assistant:** I found multiple players matching 'Smith': Archie Smith, Ashley Smith, Bailey Smith, Brodie Smith, Clay Smith, Clem Smith, Derek Eggmolesse-Smith, Devon Smith, Dylan Smith, George Horlin-Smith (+15 more). Which player do you mean?
> intent: `retrieval` · validation: `ambiguous`

## 9. Unsupported prediction
_Covers: Unsupported prediction_

**User:** Predict which player will be best defender for Geelong.
**Assistant:** I can predict the supported player outcomes in my model (expected_disposals, top_disposals), but I do not have a trained model for 'defender'.
> intent: `prediction_player` · validation: `failed`

## 10. Multi-turn follow-up
_Covers: Multi-turn follow-up_

**User:** Which team did Geelong Cats play in Round 5 of the 2020 AFL season?
**Assistant:** According to the structured AFL dataset, Geelong Cats played Gold Coast Suns in round 5 of the 2020 season at GMHBA Stadium. Final score: Geelong Cats 89 - 52 Gold Coast Suns (win).
> intent: `retrieval` · validation: `success`

**User:** Who had the most disposals for Geelong in that match?
**Assistant:** According to the structured AFL dataset, Mitch Duncan led Geelong Cats with 25 disposals in round 5 of 2020 against Gold Coast Suns.
> intent: `retrieval` · validation: `success`

**User:** What were his disposals and goals?
**Assistant:** According to the structured AFL dataset, Mitch Duncan had 25 disposals (15 kicks, 10 handballs) and 0 goals in round 5 of 2020 against Gold Coast Suns.
> intent: `retrieval` · validation: `success`

## 11. Mixed AFL + off-topic
_Covers: Mixed request handled AFL-only_

**User:** Can you tell me Geelong's head to head record against Collingwood, and also write me a haiku about tacos?
**Assistant:** According to the structured AFL dataset, Collingwood Magpies and Geelong Cats have played 67 times. Collingwood Magpies have won 31, Geelong Cats have won 36, with 0 draws.
> intent: `retrieval` · validation: `success`

## 12. Season/premiership prediction
_Covers: Season-wide (non-matchup) prediction_

**User:** Who will win AFL in 2030?
**Assistant:** Based on the trained match model (logistic_regression) applied as a full round-robin power ranking for the 2030 season, Western Bulldogs currently looks strongest, with an estimated 64.7% average win probability across all opponents (leading contenders: Western Bulldogs (64.7%); Geelong Cats (62.7%); Adelaide Crows (60.2%); Sydney Swans (59.6%); Hawthorn Hawks (56.8%)). This is a power ranking derived from the single-match model -- not a simulated ladder, finals series, or guaranteed result. This is 5 seasons beyond the most recent data available (2025). Team strength, rosters, and coaching change substantially year to year, and no fixture or roster data exists for 2030 -- treat this as a very low-confidence extrapolation from current team strength, not a real premiership forecast.
> intent: `prediction_premiership` · validation: `success`


---

# Annotated State Traces

_Generated 2026-08-22 20:25_

## Trace 1 — Retrieval example

**User query:** Who did Geelong play in Round 5 of 2020?

```
LOAD_CONTEXT: query='Who did Geelong play in Round 5 of 2020?'
ROUTER: intent=retrieval entities={'year': 2020, 'round': '5', 'team': 'Geelong Cats'} reasoning=Matched a retrieval marker (factual number/result request).
RETRIEVAL_NODE: tool=get_team_match_in_round result_ok=True
VALIDATION: success
RESPONSE_FORMATTER: grounded=True
```

**Final response:** According to the structured AFL dataset, Geelong Cats played Gold Coast Suns in round 5 of the 2020 season at GMHBA Stadium. Final score: Geelong Cats 89 - 52 Gold Coast Suns (win).

## Trace 2 — Prediction example

**User query:** Who will win Cats vs Pies?

```
LOAD_CONTEXT: query='Who will win Cats vs Pies?'
ROUTER: intent=prediction_match entities={'team': 'Geelong Cats', 'opponent': 'Collingwood Magpies'} reasoning=Matched a match-prediction marker with two teams named.
PREDICTION_MATCH_NODE: home=Geelong Cats away=Collingwood Magpies ok=True
VALIDATION: success
RESPONSE_FORMATTER: grounded=True
```

**Final response:** Based on the trained model (logistic_regression) and available historical data, Geelong Cats have an estimated 75.9% probability of winning (Geelong Cats 75.9% vs Collingwood Magpies 24.1%). The prediction is mainly supported by scoring average difference. This is a model estimate, not a guaranteed result. I can make a matchup prediction for these two teams using the latest available historical data, but I cannot verify from the local dataset that they are actually scheduled to play at this time.

## Trace 3 — Ambiguous/failed example

**User query:** How many disposals did Smith have in round 1 2022?

```
LOAD_CONTEXT: query='How many disposals did Smith have in round 1 2022?'
ROUTER: intent=retrieval entities={'year': 2022, 'round': '1', 'stat': 'disposals', 'player': 'Smith'} reasoning=Matched a retrieval marker (factual number/result request).
RETRIEVAL_NODE: tool=get_player_match_stats result_ok=False
VALIDATION: ambiguous/needs clarification
CLARIFICATION_NODE: I found multiple players matching 'Smith': Archie Smith, Ashley Smith, Bailey Smith, Brodie Smith, Clay Smith, Clem Smith, Derek Eggmolesse-Smith, Devon Smith, Dylan Smith, George Horlin-Smith (+15 more). Which player do you mean?
```

**Final response:** I found multiple players matching 'Smith': Archie Smith, Ashley Smith, Bailey Smith, Brodie Smith, Clay Smith, Clem Smith, Derek Eggmolesse-Smith, Devon Smith, Dylan Smith, George Horlin-Smith (+15 more). Which player do you mean?

## Trace 4 — Season/premiership prediction example

**User query:** Who will win AFL in 2030?

```
LOAD_CONTEXT: query='Who will win AFL in 2030?'
ROUTER: intent=prediction_premiership entities={'year': 2030} reasoning=Matched a season/league-wide prediction request (not a single named matchup).
PREDICTION_PREMIERSHIP_NODE: season=2030 ok=True
VALIDATION: success
RESPONSE_FORMATTER: grounded=True
```

**Final response:** Based on the trained match model (logistic_regression) applied as a full round-robin power ranking for the 2030 season, Western Bulldogs currently looks strongest, with an estimated 64.7% average win probability across all opponents (leading contenders: Western Bulldogs (64.7%); Geelong Cats (62.7%); Adelaide Crows (60.2%); Sydney Swans (59.6%); Hawthorn Hawks (56.8%)). This is a power ranking derived from the single-match model -- not a simulated ladder, finals series, or guaranteed result. This is 5 seasons beyond the most recent data available (2025). Team strength, rosters, and coaching change substantially year to year, and no fixture or roster data exists for 2030 -- treat this as a very low-confidence extrapolation from current team strength, not a real premiership forecast.
