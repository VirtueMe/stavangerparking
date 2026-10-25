# A checklist for the next case

What we would check in the first hours of a data case, before the plan and the model, drawn from what [the review](review.md) found late. It does not depend on this repository: copy the file and tick it off. Each item says what it would have saved.

## 1. Prerequisites, before the plan

The plan should follow the access, not the other way round.

- [ ] **The platform:** can you sign in, create a workspace and run a job today? If the brief names a platform, is a trial available to your tenant, and is the account allowed to start it? Ask again if the answer is no; a refusal on day one is not always final. *Would have saved: a plan and issues written for a platform that was not there for three weeks.*
- [ ] **The alternative:** if access is uncertain, name the second platform now and keep the code platform-neutral from the start (one package, thin platform folders), so a late answer costs a deployment and not a rewrite.
- [ ] **Licences:** what the BI tool's licence can do (a trial that turns out to be a lower tier changes the design).
- [ ] **Network:** from the platform, can the jobs reach every source and the services the pipeline calls (DNS, outbound limits on a free tier)? Test with a job, not a browser.
- [ ] **Start collecting.** If the source only shows its current state, begin collection the first day, outside any platform, and keep the raw responses unchanged. Every day without it is history you cannot get back.

## 2. The source, before the model

- [ ] **Profile every field:** the distinct values of every column, the shapes the file has had over time, and how fresh it really is. *Would have saved: a value meaning "full" read as unknown for four days.*
- [ ] **Judge freshness from the data's own timestamp,** never from the metadata of the file or the portal. Compare content between fetches.
- [ ] **Check reference data against the source:** for a capacity or a limit, compare it with the highest value the source has reported. *Would have saved: two facilities whose capacity was below their highest free count, found only after a check stopped the pipeline.*
- [ ] **Decide what a source outage is.** A server error from the source is not a failure you can fix; record it, keep the run green, and make it visible as data and as a warning.
- [ ] **What changes in the source:** a new record, a renamed record, a renamed field. Decide where each is handled (and write the answer down) before it happens.

## 3. Repository and GitHub

- [ ] **Labels:** type (feature, fix, docs), layer, `status: in progress`, and `awaiting verification` for what is merged but not yet seen working.
- [ ] **Issues as the source of truth:** one issue and one pull request per change, a new issue for new scope, a checklist in the issue body, and a project board.
- [ ] **Pull requests:** a title check (conventional commits), squash merges, and a ruleset on `main` and on any data branch (no force pushes, no deletion).
- [ ] **Releases:** generated from the commit messages on every merge, so each deployment names a version.
- [ ] **ADRs** for every decision with a trade-off, written when it is made; a **weaknesses log** kept per issue.

## 4. Pipelines and Actions

- [ ] **CI (lint and tests) as a required check,** with the dependency versions locked in one file that CI and every platform install from.
- [ ] **A deploy key limited to the branch it needs,** and an **external trigger** for any schedule that matters: test that a scheduler runs as often as it says. *Would have saved: collection started with a schedule that ran 2 of about 145 expected times in its first 12 hours.*
- [ ] **Failed runs are data:** keep the history of failures where the pipeline can read it, so a lost snapshot has a reason.
- [ ] **One collector of record at a time,** with a written handover for the platform that takes over.

## 5. Operations

- [ ] **A way to rebuild** a layer after a parsing change, on every platform, and a dry run for every tool that changes a platform.
- [ ] **Refresh the report only after the publish step has ended,** never while tables are being replaced.
- [ ] **Test the entry point as the platform runs it.** A program's exit code is not always what a platform treats as success (an exit code 0 reported as an error; an inactive alert step counted as success).
- [ ] **Fakes must fail where the real tool fails.** A fake CLI that makes folders, accepts a carriage return or never times out hides the bugs that the first real run finds. After the first real run, bring the fake in line. *Would have saved: four faults found on the first real deployment, and one more a day later.*
- [ ] **Verify in the real system before closing an issue,** and write what was not seen (a warning that has not fired yet) in the closing comment.

## 6. Documents

- [ ] **No statement of the current state.** Write dated events and mechanisms ("deployed and run on 2026-10-22"), not "is waiting for", "has never run" or "is live".
- [ ] **When a fact changes, search for every sentence that states it,** in the docs, the tests' docstrings and the code comments, not only the ones you remember.
- [ ] **State what was tried and what was not,** beside every claim about a platform you could not run.
