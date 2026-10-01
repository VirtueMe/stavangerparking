# Fabric notebook source

# METADATA ********************

# META {
# META   "kernel_info": {
# META     "name": "jupyter",
# META     "jupyter_kernel_name": "python3.11"
# META   },
# META   "dependencies": {
# META     "lakehouse": {
# META       "default_lakehouse": "11111111-1111-1111-1111-111111111111",
# META       "default_lakehouse_name": "StavangerParking",
# META       "default_lakehouse_workspace_id": "00000000-0000-0000-0000-000000000000",
# META       "known_lakehouses": [
# META         {
# META           "id": "11111111-1111-1111-1111-111111111111"
# META         }
# META       ]
# META     }
# META   }
# META }

# MARKDOWN ********************

# # Collect
#
# Fetches a snapshot of every source that is due (adaptive polling, ADR 003) into the Lakehouse's
# `Files/`. It is not scheduled: GitHub Actions is the collector of record, and Fabric only takes over
# collection with the handover in ADR 011 (docs/fabric.md).

# CELL ********************

# The wheel deploy.sh copied into the Lakehouse: the released version on prod, this checkout on dev
%pip install /lakehouse/default/Files/wheels/{{wheel}} --quiet

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "jupyter_python"
# META }

# CELL ********************

from datetime import UTC, datetime

from stavanger_parking.bronze.collect import main

run_id = f"fabric-{datetime.now(UTC):%Y%m%dT%H%M%S}"
code = main(["run", "--storage", "/lakehouse/default/Files", "--run-id", run_id])
if code:
    raise RuntimeError(f"collection failed with exit code {code}")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "jupyter_python"
# META }
