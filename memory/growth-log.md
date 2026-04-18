# Growth log

One dated entry per day, appended by the `daily-log` heartbeat tick.

Format per day:

```markdown
## YYYY-MM-DD

- soil moisture: latest X, day range Y–Z
- temperature: day range Y–Z °F
- humidity: day range Y–Z %
- light hours: N (on-window delivered)
- waterings: N (HH:MM, HH:MM, …)
- photo: `<filename>` — one-line description
- observations: new detections or visible changes from yesterday, one line each
- flag: single short phrase only if something needs operator attention
```

Rules:
- append only; never rewrite prior days
- keep each entry under 10 lines
- the `daily-log` skill owns this file — operator-authored notes should go under a separate `## Notes — YYYY-MM-DD` heading to stay out of the skill's overwrite path

<!-- first entry will be appended by the daily-log tick -->
