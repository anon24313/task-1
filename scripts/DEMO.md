# Demo guide (3 to 4 minutes)

Run from the repo root. Use a large terminal font and a dark theme so it reads on video.

## Before recording
1. `export ANTHROPIC_API_KEY=...` so answers are prose, not raw evidence.
2. Run `python3 scripts/demo.py --auto` once to warm up and confirm nothing errors. The first question is the slow one (attribution runs once); later refines reuse it.
3. Close other windows. Record the screen with OBS, or the built-in recorder (Win+G on Windows, Cmd+Shift+5 on Mac).

## Run of show
| Step | Send | Say |
|---|---|---|
| 1 | "Why are escalations happening?" | This is a population question, not a lookup. Point at the statistics, then the cited `[call, turn]` evidence. |
| 2 | "What about <intent> calls only?" | Refine: the filter is merged and stages 4 to 7 re-run. Note the group size changed. |
| 3 | "Show me more examples" | Drill down: no recompute. Point at the "no recompute" line. |
| 4 | "Now why do customers ask for refunds?" | New topic: filter, factors and evidence are reset. |
| 5 | Optional: session 3 from `outputs/sessions/` | Narrow to a rare intent to show the insufficient-data message instead of a false claim. |

Start it with `python3 scripts/demo.py` (press Enter between steps) or `--auto` for a continuous take.

## If something breaks
- No API key or API down: the demo still runs and prints the evidence bundle Claude would receive. Say so, and show it.
- A step is slow: talk through the architecture slide while it runs, or use the saved transcripts in `outputs/sessions/`.
