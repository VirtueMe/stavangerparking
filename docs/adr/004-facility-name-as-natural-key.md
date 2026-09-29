# ADR 004: The facility name in the feed is the natural key of a facility

- **Status:** Accepted
- **Date:** 2026-09-29
- **Issue:** #15

## Context

The feed identifies each parking facility only by `Sted`, a display name such as `"Jernbanen"` or `"St Olav"`, with a latitude and longitude. It has no id. The facility dimension (#12) needs a natural key to maintain its rows with MERGE, and the facts join to it.

The same facilities are known under other names elsewhere. On 2026-09-29:

| Feed (`Sted`) | Coordinates in the feed | National parking register ([Parkeringsregisteret](https://www.vegvesen.no/fag/teknologi/apne-data/et-utvalg-apne-data/parkeringsregisteret-api/)) | Operator website ([stavanger-parkering.no](https://stavanger-parkering.no/en/parkering/p-hus/)) |
|---|---|---|---|
| Jernbanen | 58.966341, 5.732047 | `P- Jernbanen` (id 3650), 58.96616, 5.73261 | P-Jernbanen |
| Valberget | 58.9721884, 5.7299647 | `P - Valberget` (id 44894) | P-Valberghallen |
| Parketten | 58.975601, 5.721537 | `P - Parketten` (id 44934) | P-Arketten |
| St Olav | 58.966764, 5.730807 | `P- St. Olav` (id 3756) | P-St. Olav |
| Forum | 58.9523279, 5.7046637 | `P - Forum` (id 46816), 58.95443, 5.70189 | P-Forum |

The feed's coordinates differ from the register's by about 40 m (Jernbanen) to about 280 m (Forum), and are given with 6 or 7 decimal places.

## Decision

- **`Sted`, exactly as delivered, is the natural key** of a facility: `facility` in silver, `facility_name` in `dim_parking_facility`. It is not trimmed, case-folded or otherwise normalised, so the key is always the source's own value.
- The dimension gives each facility a **surrogate key**, which the facts use (#12, #13).
- Names in other sources are **mapped to the feed's name by hand**, in a mapping file (ADR 005), which also records the register id. Nothing matches names or coordinates automatically.

## Alternatives considered

| Alternative | Why not |
|---|---|
| Coordinates as key | Not an identity: they differ between sources by up to 280 m, can be corrected by the publisher at any time, and are decimals that must match exactly |
| Name and coordinates together | Inherits the instability of both: a moved pin would split a facility just as a rename would |
| The register id (Parkeringsregisteret) | The feed does not contain it, so it would have to be looked up from the name anyway; it belongs in the mapping file, not in the key |
| A normalised name (trimmed, case-folded, `P-` prefix removed) | Hides the source's actual value, and a normalisation rule is a guess about which differences are meaningful. The collected snapshots show no variation in the names, so there is nothing to normalise yet |

## Consequences

- **A renamed facility appears as a new facility.** The old name stops appearing and is marked inactive in the dimension; the new name is inserted with a new surrogate key, and history is split between the two. If it happens, the mapping file can map the old name to the new facility, and the dimension can follow that mapping; it is not built until it is needed.
- A rename is noticeable: one facility stops and another starts at nearly the same coordinates on the same day. Detecting that belongs to the quality checks (#21).
- A new facility needs no work: it is a new name, and the facility MERGE inserts it (#12).
- Two facilities with the same name in one snapshot would share a key. Silver quarantines the second as a conflicting duplicate ([ADR 009](009-silver-model.md)).
- Recorded in [`docs/weaknesses.md`](../weaknesses.md).
