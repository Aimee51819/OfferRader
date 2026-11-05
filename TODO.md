# AppTracker — TODO / future work

## Research agent (enrich applications from the web)

Today the inbox pipeline only creates/updates applications from email text.
Most confirmation emails do **not** include the full job description, location,
salary, skills, or listing URL — so those fields stay empty unless entered by hand.

**Desired follow-on process (separate from email classification):**

1. Email agent (existing): detect new application or status change → create/update row + review queue.
2. Research agent (new): for applications with thin data (Unknown title, empty description/url):
   - Search the company careers page / LinkedIn / Greenhouse / Lever / Workday listing
   - Scrape or fetch the posting when a URL is known or discoverable
   - Fill title, location, salary, skills, description, listing URL
   - Prefer a suggestion/preview step before overwriting user edits
3. Keep research off the critical email path so a slow scrape never blocks inbox sync.
4. Rate-limit and cache aggressively; never re-scrape rows the user already completed.

## Other backlog

- ~~Interview calendar (.ics) export~~ (done: Import / export → Deadlines .ics)
- Resume-tailoring suggestions per listing
- Slack/Discord notifications
- Optional auth for hosted deployments (required before any remote access; see README security notes)
- Measure Qwen3-4B accuracy on the synthetic corpus (`make eval`) and grow the fixture set from real (anonymized) formats
- Business-day calendars per employer (today: always human review)
