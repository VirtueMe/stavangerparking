# How the case was solved: a review

A look back at the case from 28 September to 24 October 2026: what went well, what we caught late, and what we got wrong. Each point rests on the history (an issue, a pull request, an ADR or a dated run), not on memory, and says what it cost. The checklist it led to, for the next case, is [`docs/case-checklist.md`](case-checklist.md).

By 25 October the repository had 137 commits, 75 issues, 71 pull requests, 12 ADRs and 58 releases.

## The case's questions

The brief (in Norwegian) asks for a complete case in Fabric, on the free trial: ingestion, transformation and modelling, optionally visualisation, on Stavanger kommune's open parking data. Where each question is answered:

| # | The case asks | Where it is answered |
|---|---|---|
| 1 | An overview of the data model and the data flow | README (architecture, data flow, star schema), [`docs/architecture.md`](architecture.md) |
| 2 | Suitable fact and dimension tables | [`docs/gold.md`](gold.md), [ADR 009](adr/009-silver-model.md) |
| 3 | Is the solution scalable? | [ADR 006](adr/006-scalability-assessment.md) (measured), README "How it scales" |
| 4 | How does the code handle a new parking facility? | README "A new facility", the test that runs the pipeline with a tenth facility, [ADR 004](adr/004-facility-name-as-natural-key.md) |
| 5 | Fetch the data programmatically from opencom.no | [ADR 007](adr/007-collect-outside-the-platform.md), [ADR 003](adr/003-polling-interval.md), [ADR 008](adr/008-trigger-collection-externally.md), [`docs/collector.md`](collector.md) |
| 6 | Fabric items: Lakehouse, notebooks, Data pipelines with dependencies and failure alerts | [`docs/fabric.md`](fabric.md), deployed and run on 2026-10-22; the same on Databricks, running since 2026-09-30 ([ADR 011](adr/011-one-repository-two-platforms.md)) |
| 7 | PySpark or Polars | [ADR 001](adr/001-polars-over-pyspark.md) |
| 8 | The medallion structure, bronze → silver → gold | [`docs/bronze.md`](bronze.md), [`docs/silver.md`](silver.md), [`docs/gold.md`](gold.md) |
| 9 | Potential weaknesses | [`docs/weaknesses.md`](weaknesses.md), README "Known weaknesses" |
| 10 | Optional: Power BI | The report, [`docs/report.md`](report.md) |

## The deviation from Fabric

The brief asks for Fabric on the free trial. For most of the case the solution ran on Databricks, and Fabric was written but not run. How that came about:

- **2026-09-28:** a Fabric trial could not be activated in the own tenant, so collection started outside any platform, because the source only shows its current state and every day without collection is history that cannot be recovered ([ADR 007](adr/007-collect-outside-the-platform.md)).
- **2026-09-29:** the Power BI trial turned out to be Pro only. Databricks was offered as an equal alternative.
- **2026-09-30:** with the access still open and the presentation fixed, the decision was one repository for two platforms: shared code and tests, thin platform folders ([ADR 011](adr/011-one-repository-two-platforms.md)). Databricks ran the same day (#71).
- **2026-10-01:** the Fabric items and scripts were written without access to a workspace (#98).
- **2026-10-22:** a renewed application for the Fabric trial was granted. The same day the Lakehouse, the notebooks and the pipeline were deployed and run, and every table was built ([`docs/fabric.md`](fabric.md)). On 2026-10-24 the Lakehouse's tables were compared with a local build from the same raw files and matched on every row ([`docs/fabric.md`](fabric.md#compared-with-a-local-build)).

**Was it the right call?** Yes, for the case as a whole. The alternative was to wait for an answer that came three weeks later, with nothing to show. One repository for two platforms meant that when Fabric arrived, deploying took a day and not a rewrite. The cost is real, though: the plan, the issues and the first days of work were written for Fabric, and the two days spent waiting before the decision were avoidable. And the Fabric code was written blind, so its first real run on 2026-10-22 found faults that tests against a fake CLI could not (#129, #130, #132).

**What we would do differently with access on day one:** check the prerequisites before writing the plan (below), and apply for the trial again, and again, early. The one that finally worked cost a few minutes.

## What went well

- **Collection started on day one, outside any platform** ([ADR 007](adr/007-collect-outside-the-platform.md)). The history from 28 September exists, including the feed's freeze from 23 September to 1 October and its recovery, which would otherwise be lost.
- **Freshness is judged from the data's own timestamp, never from metadata.** The feed froze while every "updated" signal kept moving; only comparing the content showed it (#45, [`docs/weaknesses.md`](weaknesses.md)).
- **The quality checks did their job.** Forum reported 292 free spaces against a registered 289, which stopped every run, and the report showed a negative occupancy instead of hiding it. That led to finding the 26 reserved spaces the register leaves out (#91).
- **One repository, a platform-neutral package and thin platform folders** ([ADR 011](adr/011-one-repository-two-platforms.md)). A boundary test keeps platform code out of the package (`tests/test_platform_boundary.py`), and the same wheel runs on both platforms.
- **Decisions and trade-offs were written down as they were made:** 12 ADRs, a weaknesses log kept per issue, one issue and one pull request per change, a release per merge.
- **Measured, not assumed:** scalability ([ADR 006](adr/006-scalability-assessment.md)), the glitch thresholds from the collected data (#113), and fixes verified in production before their issue was closed (#124 and #141 are the latest).
- **A source outage is not a failed run** (#124). When the parking register answered 500 from 2026-10-07, every five-minute run went red for a fault nothing here could fix, which buried the failures that could be. The collector now records the outage, the pipeline loads it into bronze, and a quality warning shows it.

## What we caught late

- **The plan came before the access check.** The access problem was known on 2026-09-28, but the backlog and the issues were already written for Fabric. The plan only became one repository for two platforms on 2026-09-30 ([ADR 011](adr/011-one-repository-two-platforms.md)). Checking the prerequisites first, then planning, would have saved the rework.
- **The source's vocabulary.** The feed says `Fullt` ("full") for a car park with no free spaces. Silver quarantined it as unknown until #113, four days into collection, so a full car park showed as missing data at the hour occupancy matters most. Profiling every distinct value of the source before modelling would have found it.
- **What the register's capacity counts.** It counts public parking only, while the feed counts reserved spaces too (Forum in #91, Jernbanen in #112). Comparing each facility's highest free count with its capacity over the history would have flagged both on day one. #112 and #133 are still open: they are findings about the source's data, not faults in the pipeline.
- **A platform's idea of failure is not the program's.** On Databricks a run that succeeded was marked as a failed task, because the wheel's entry point ended with `sys.exit(0)` and a serverless task reports that as an error (#107, 2026-10-01). The same pattern on Fabric: a failed notebook ended the run as Completed, because the inactive alert activity counts as succeeded (#130).
- **The scheduler we trusted.** Collection started on GitHub Actions' own `schedule` trigger, every five minutes. In the first 12 hours it started 2 runs where about 145 were due, and every missed run is history lost for good. An external trigger replaced it ([ADR 008](adr/008-trigger-collection-externally.md), #49). The schedule's real behaviour should have been measured before relying on it.
- **Stale data in the report.** During a freeze, gold carried the last reading forward hour after hour, and the occupancy visuals averaged those hours: Forum showed a flat −1.0 % for 186 hours (#111).
- **Operations.** A parsing change had no way to reach production's history (#114); a report refresh during `publish` read half-replaced tables (#109); a failed push lost a snapshot with no trace in the data (#119).
- **Fakes that were kinder than the real tool.** The tests of the Fabric scripts ran against a fake CLI, and the first real deployment found what the fake allowed: a carriage return in the CLI's output broke the pipeline's JSON (#129), `fab cp` does not make folders (#132), and delta-rs could not commit on the Lakehouse mount (#130). The same happened after the fact on Databricks: the fake `fs cp` made the folders itself, so a backfill that fails on the first run of a new day passed its tests (#141, from #126). A fake must fail where the real tool fails; the first real run is the test.
- **Statements that promised a state.** The walkthrough promised checks that fail or pass depending on the hour (#109), and kept "Databricks cannot reach the sources" after it could (#121). After Fabric ran, several places still said it was waiting for a capacity, found by a search only after the first pass (#137, #144). When a fact changes, search for every sentence that states it.

## What we got wrong

- **The first fix for Forum's capacity.** It took paid, free of charge, charging and accessible spaces together (310). The operator's sign plan showed 26 reserved spaces on top, so the capacity is 315. The first pull request (#94) was closed and the fix redone (#97, merged 2026-10-22, for #91).
- **The first design of failed-run records (#119).** It stored them on the `data` branch; the collector's run history is a source of its own, and the design was replaced inside the same pull request (#120).

## A question from outside the brief

In the meeting a question came up about what to do if a field changes name in the data source. It is not one of the case's questions: the brief asks how the code handles a new parking facility (question 4), and the two were mixed up. The answer given was to map the old field to the new one and rebuild. That is right, and it is how the repository would handle it:

- **Detecting it:** the `schema_drift` quality check is critical. A missing expected field or a new one stops the run, after the tables are built, with the old field read as null ([`docs/quality.md`](quality.md)).
- **Fixing it:** bronze keeps the raw data, and new fields become new columns, so nothing is lost. The mapping belongs where the source's field names are read, in silver's parsing (`SOURCE_FIELDS`), taking the new name where the old is missing. Silver is then rebuilt from bronze (`tools/rebuild`, which sets `rebuild_silver`), and gold follows from silver. Gold itself does not know the source's field names.
- **What is not written down:** the repository documents a renamed facility ([ADR 004](adr/004-facility-name-as-natural-key.md)) but not a renamed field. The steps above are an answer, not a tested procedure.

## The questions for the review

**Was building on Databricks, with Fabric written but not run, the right answer?** Yes (see above), with the cost of a plan written for the wrong platform and a first Fabric run that found its faults late.

**Which checks, done in the first hours, would have caught the late findings?** Platform access and trial eligibility, before the plan. A profile of the source's values and history, before the model. Capacity against the highest free count, per facility. What the platform's network can reach. They are in [`docs/case-checklist.md`](case-checklist.md).

**Where did the process help, and where did it cost more than it gave?** Small issues and pull requests, ADRs, the weaknesses log and verification in production helped most, because they made each change checkable and the history readable. The cost was mostly in writing: the docs promised states (the hour-dependent checks, the waiting Fabric), and keeping them true after the facts changed took several passes.

## In the author's words

The exercise was fun, and nothing in it was really hard.
