# -*- coding: utf-8 -*-
"""
Database-side integrity for PostgreSQL/PostGIS (optional, Phase 3).

Generates SQL that makes the database itself enforce what the plugin does
inside QGIS, so edits from other clients (field apps, scripts, other GIS)
stay consistent:

* a UNIQUE constraint on the master StreetID,
* foreign keys from centerlines / address points to the master,
* a trigger on the master that cascades name-part changes to linked rows,
* a trigger on linked tables that copies the name parts when a StreetID is set.
"""

from typing import List, Sequence, Tuple

from .names import NAME_PARTS


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _t(schema: str, table: str) -> str:
    return f"{_q(schema)}.{_q(table)}" if schema else _q(table)


def build_sql(master: Tuple[str, str, str, List[str]], linked: Sequence[Tuple[str, str, str, List[str]]],
              prefix: str = "nena") -> str:
    """
    :param master: (schema, table, StreetID column, [name-part columns in NAME_PARTS order or ''])
    :param linked: same tuples for the centerline / address tables
    """
    ms, mt, mid, mparts = master
    mtab = _t(ms, mt)
    sql = [f"-- NENA Addressing and RCL Tools: database integrity for {mtab}",
           "BEGIN;",
           f"ALTER TABLE {mtab} ADD CONSTRAINT {_q(prefix + '_' + mt + '_streetid_uq')} UNIQUE ({_q(mid)});"]
    cascade_updates = []
    for i, (s, t, sid_col, parts) in enumerate(linked):
        tab = _t(s, t)
        sql.append(f"ALTER TABLE {tab} ADD CONSTRAINT {_q(prefix + '_' + t + '_streetid_fk')} FOREIGN KEY "
                   f"({_q(sid_col)}) REFERENCES {mtab} ({_q(mid)}) ON UPDATE CASCADE ON DELETE RESTRICT "
                   "DEFERRABLE INITIALLY DEFERRED;")
        pairs = [(lp, mp) for lp, mp in zip(parts, mparts) if lp and mp]
        sets = ", ".join(f"{_q(lp)} = NEW.{_q(mp)}" for lp, mp in pairs)
        cascade_updates.append(f"  UPDATE {tab} SET {sets} WHERE {_q(sid_col)} = NEW.{_q(mid)};")  # nosec B608 - identifiers quoted by _q
        fn = _q(f"{prefix}_{t}_copy_street")
        selects = ", ".join(_q(mp) for _lp, mp in pairs)
        intos = ", ".join(f"NEW.{_q(lp)}" for lp, _mp in pairs)
        sql += [
            f"CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger LANGUAGE plpgsql AS $$",
            "BEGIN",
            f"  IF NEW.{_q(sid_col)} IS NOT NULL THEN",
            f"    SELECT {selects} INTO {intos} FROM {mtab} WHERE {_q(mid)} = NEW.{_q(sid_col)};",  # nosec B608
            "  END IF;",
            "  RETURN NEW;",
            "END $$;",
            f"DROP TRIGGER IF EXISTS {_q(prefix + '_' + t + '_copy_street')} ON {tab};",
            f"CREATE TRIGGER {_q(prefix + '_' + t + '_copy_street')} BEFORE INSERT OR UPDATE OF {_q(sid_col)} "
            f"ON {tab} FOR EACH ROW EXECUTE FUNCTION {fn}();",
        ]
    watched = ", ".join(_q(c) for c in mparts if c)
    fn = _q(f"{prefix}_{mt}_cascade_names")
    sql += [f"CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger LANGUAGE plpgsql AS $$", "BEGIN"]
    sql += cascade_updates
    sql += ["  RETURN NEW;", "END $$;",
            f"DROP TRIGGER IF EXISTS {_q(prefix + '_' + mt + '_cascade_names')} ON {mtab};",
            f"CREATE TRIGGER {_q(prefix + '_' + mt + '_cascade_names')} AFTER UPDATE OF {watched} ON {mtab} "
            f"FOR EACH ROW EXECUTE FUNCTION {fn}();",
            "COMMIT;"]
    return "\n".join(sql)


def layer_table(layer) -> Tuple[str, str]:
    """(schema, table) of a PostgreSQL layer."""
    from qgis.core import QgsDataSourceUri
    uri = QgsDataSourceUri(layer.source())
    return uri.schema(), uri.table()


def part_columns(layer, mapping=None) -> List[str]:
    """Physical name-part columns of a layer in NAME_PARTS order ('' where missing)."""
    cols = []
    for p in NAME_PARTS:
        name = (mapping or {}).get(p) or p
        i = layer.fields().lookupField(name)
        cols.append(layer.fields().at(i).name() if i >= 0 else "")
    return cols


def run_sql(layer, sql: str) -> None:
    """Execute through a connection built from the layer's own data source."""
    from qgis.core import QgsProviderRegistry
    md = QgsProviderRegistry.instance().providerMetadata("postgres")
    conn = md.createConnection(layer.source(), {})
    conn.executeSql(sql)
