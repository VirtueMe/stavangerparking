# ADR 002: Use the JSON resource, not the CSV

- **Status:** Accepted
- **Date:** 2026-09-28
- **Issue:** #5

## Context

The CKAN package `stavanger-parkering` offers the same data as two resources: `parking.json` and `parkering.csv`. On 2026-09-28 both contained exactly the same rows and values: 9 records with the fields `Dato`, `Klokkeslett`, `Sted`, `Latitude`, `Longitude` and `Antall_ledige_plasser`, all as strings.

- The CSV is served as `text/csv` without a charset. Today all facility names are ASCII, but a future facility with `æ`, `ø` or `å` in its name would make the encoding a guess.
- JSON is UTF-8 by definition (RFC 8259) and names every field in every record.

## Decision

Ingest the **JSON** resource. The CKAN resolver (#4) selects it by format, and fails if the package has no JSON resource or more than one.

## Alternatives considered

| Alternative | Why not |
|---|---|
| CSV | Same content, but the encoding is unspecified, and parsing depends on delimiter, quoting and column order |
| Both, compared against each other | Doubles collection for no information gain; the formats carry identical data |

## Consequences

- Parsing is independent of column order, and a renamed or added field is visible by name, which the schema drift checks (#21) rely on.
- All values are still strings (`"285"`, `"Open"`): typing happens in silver (#8), and the raw JSON is kept unchanged in bronze.
- If the JSON resource disappears, ingestion stops with a clear error rather than silently switching to CSV. Switching format is then a one-field change in `config/sources.json`.
