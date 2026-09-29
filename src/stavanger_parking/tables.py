"""Where tables live: every layer's tables sit directly under one tables root.

The root describes the environment (a local folder, `/lakehouse/default/Tables`, or an
`abfss://…/Tables` URI), not the source, so it is a parameter of every build.
"""


def table_path(tables_root: str, name: str) -> str:
    return f"{str(tables_root).rstrip('/')}/{name}"
