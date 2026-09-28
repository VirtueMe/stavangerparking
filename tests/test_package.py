import polars as pl
from polars.testing import assert_frame_equal

import stavanger_parking


def test_package_imports():
    assert stavanger_parking.__doc__


def test_polars_delta_round_trip(tmp_path):
    # The same Polars + delta-rs stack runs locally and in the platform's Python notebooks
    df = pl.DataFrame({"facility": ["Jernbanen", "Valberget"], "available_spaces": [285, 12]})
    df.write_delta(tmp_path / "table")
    assert_frame_equal(pl.read_delta(str(tmp_path / "table")), df)
