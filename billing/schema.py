"""Keep an existing database in step with the models after an app update."""

from sqlalchemy import inspect, text


def _default_sql(column, dialect):
    default = column.default
    if default is None or not default.is_scalar:
        return ""
    value = default.arg
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, str):
        value = "'" + value.replace("'", "''") + "'"
    return f" DEFAULT {value}"


def add_missing_columns(engine, metadata):
    """db.create_all() makes new tables but never changes existing ones. Add any new
    columns (all new columns are nullable or have a default), so older shop databases
    keep working after an update. Returns the columns added."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    added = []
    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in have:
                    continue
                kind = column.type.compile(dialect=engine.dialect)
                default = _default_sql(column, engine.dialect)
                null = "" if column.nullable or not default else " NOT NULL"
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {kind}{default}{null}'))
                added.append(f"{table.name}.{column.name}")
    return added
