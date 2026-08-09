# Stage 01 — pull the schedule

## Inputs
- (parameter) `community_slug` — ask if not given
- (parameter) how far ahead — default: this month; "next month too" → second
  call with `cal_date` set to a timestamp inside that month

## Process
1. `get_calendar(community_slug)` (+ future months if asked).
2. Write events in date order: **Event name** — weekday, date, time
   (mention the timezone Skool reports), one line what it is, link if any.
3. Nothing scheduled? Say exactly that in one friendly line.

## Outputs
- `latest.md` → `output/`

## Completion
Done when every event has a date a human can put in their own calendar
without converting anything. No further stages.
