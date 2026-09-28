# Known weaknesses

A running log of trade-offs and known limitations, recorded when they appear rather than reconstructed at the end. Every issue that makes a trade-off adds an entry here before it is closed.

| Weakness | Consequence | Mitigation | Related |
|---|---|---|---|
| The source feed has not updated since 23.09.2026 19:16 (checked on 28.09.2026), although the dataset metadata changes | Collected snapshots may repeat the same values, and gaps in history cannot be recovered afterwards | Staleness detection in silver; raise it with the data owner | #10 |
| The source only exposes the current state, not history | Any period without collection is lost for good | Start collecting early, outside the data platform | #6 |
| Collection runs on GitHub Actions schedules while platform access is pending | Runs can be delayed or skipped, minimum interval is 5 minutes, and schedules stop after 60 days without repository activity | Raw files are the source of truth, so bronze can be rebuilt from them; move ingestion into the platform when access is available | #6, #18 |
