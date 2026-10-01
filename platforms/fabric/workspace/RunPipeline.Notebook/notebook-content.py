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

# # Run the pipeline
#
# Bronze, silver, gold and the quality checks, from the raw files in `Files/` into the Delta tables in
# `Tables/`, with the package's one entry point (docs/pipeline.md). Deployed from
# `platforms/fabric/` in the repository (docs/fabric.md): change it there, not here.

# CELL ********************

# The wheel deploy.sh copied into the Lakehouse: the released version on prod, this checkout on dev
%pip install /lakehouse/default/Files/wheels/{{wheel}} --quiet

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "jupyter_python"
# META }

# CELL ********************

from stavanger_parking.pipeline import run_pipeline

result = run_pipeline("/lakehouse/default/Files", "/lakehouse/default/Tables")
print(result.report())
# A critical quality check (exit code 3) has still built every table; failing here alerts all the same
if result.exit_code:
    raise RuntimeError(f"pipeline failed with exit code {result.exit_code}")

# METADATA ********************

# META {
# META   "language": "python",
# META   "language_group": "jupyter_python"
# META }
