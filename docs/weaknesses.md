# Known weaknesses

A running log of trade-offs and known limitations, recorded when they appear rather than reconstructed at the end. Every issue that makes a trade-off adds an entry here before it is closed.

| Weakness | Consequence | Mitigation | Related |
|---|---|---|---|
| The source feed has not updated since 23.09.2026 19:16 (checked on 28.09.2026), although the resource files are re-uploaded regularly (CKAN `last_modified` is current) | A fresh file does not mean fresh data: collected snapshots may repeat the same values, and gaps in history cannot be recovered afterwards | Staleness detection in silver compares the source timestamp in the data, not file or metadata dates; raise it with the data owner | #10 |
| The source only exposes the current state, not history | Any period without collection is lost for good | Start collecting early, outside the data platform | #6 |
| Resolving the download URL through the CKAN API makes it a second dependency, called without retries (10 s timeout) | An API hiccup costs one snapshot, even if the file itself is reachable | The next scheduled run tries again, and a failed run is visible; add retries if failures turn out to be frequent | #4 |
| More than one JSON resource in the package stops ingestion instead of picking one | Ingestion halts until someone decides which resource is right | Deliberate: a wrong guess would silently land the wrong data. The config can name the resource if this happens | #4, #3 |
| Collection runs on GitHub Actions schedules while platform access is pending | Runs can be delayed or skipped, minimum interval is 5 minutes, and schedules stop after 60 days without repository activity | Raw files are the source of truth, so bronze can be rebuilt from them; move ingestion into the platform when access is available | #6, #18 |
