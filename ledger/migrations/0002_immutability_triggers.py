"""Database-level protection for the append-only tables (PostgreSQL only).

The application layer already refuses updates and deletes; these triggers make the database
refuse them too, so a bug, a console session or a stray SQL script cannot rewrite history.
NOTE: written but not yet exercised against a live PostgreSQL instance in this first build.
"""
from django.db import migrations

TABLES = ("ledger_journal", "ledger_entry", "core_auditlog")

FUNC = """
CREATE OR REPLACE FUNCTION forbid_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '% on % is not allowed: append-only table', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
"""


def add_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(FUNC, params=None)
    for table in TABLES:
        schema_editor.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION forbid_mutation();",
            params=None,
        )


def drop_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for table in TABLES:
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table};", params=None)
    schema_editor.execute("DROP FUNCTION IF EXISTS forbid_mutation();", params=None)


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial"), ("core", "0001_initial")]
    operations = [migrations.RunPython(add_triggers, drop_triggers)]
