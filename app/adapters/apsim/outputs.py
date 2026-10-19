"""Read APSIM output .db (SQLite) report tables: catalog (variables, units, row counts) and queries."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DATE_COL = "Clock.Today"
SKIP_COLS = {"CheckpointID", "SimulationID", "Zone"}


def _ro(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def catalog(db: Path) -> list[dict]:
    c = _ro(db)
    try:
        tables = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like '\\_%' "
                                          "escape '\\' order by name")]
        units = {}
        if c.execute("select 1 from sqlite_master where name='_Units'").fetchone():
            units = {(r[0], r[1]): r[2] for r in c.execute("select TableName, ColumnHeading, Units from _Units")}
        out = []
        for t in tables:
            cols = c.execute(f'pragma table_info("{t}")').fetchall()
            n = c.execute(f'select count(*) from "{t}"').fetchone()[0]
            names = [x[1] for x in cols]
            first = last = None
            if DATE_COL in names and n:
                first, last = c.execute(f'select min("{DATE_COL}"), max("{DATE_COL}") from "{t}"').fetchone()
            out.append({"table_name": t, "row_count": n,
                        "first_date": first[:10] if first else None, "last_date": last[:10] if last else None,
                        "variables": [{"name": x[1], "dtype": x[2] or "", "unit": units.get((t, x[1]))}
                                      for x in cols if x[1] not in SKIP_COLS]})
        return out
    finally:
        c.close()


def messages(db: Path, limit: int = 200) -> list[dict]:
    c = _ro(db)
    try:
        if not c.execute("select 1 from sqlite_master where name='_Messages'").fetchone():
            return []
        rows = c.execute("select Date, ComponentName, Message, MessageType from _Messages "
                         "where MessageType <> 2 order by rowid limit ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def query(db: Path, table: str, variables: list[str] | None, start: str | None, end: str | None,
          limit: int, offset: int) -> tuple[list[str], list[list], int]:
    c = _ro(db)
    try:
        names = [x[1] for x in c.execute(f'pragma table_info("{table}")')]
        if not names:
            raise KeyError(table)
        cols = [v for v in (variables or [n for n in names if n not in SKIP_COLS]) if v]
        bad = [v for v in cols if v not in names]
        if bad:
            raise ValueError(bad)
        if DATE_COL in names and DATE_COL not in cols:
            cols = [DATE_COL, *cols]
        where, args = [], []
        if start and DATE_COL in names:
            where.append(f'"{DATE_COL}" >= ?')
            args.append(start)
        if end and DATE_COL in names:
            where.append(f'"{DATE_COL}" <= ?')
            args.append(end + " 23:59:59")
        w = (" where " + " and ".join(where)) if where else ""
        total = c.execute(f'select count(*) from "{table}"{w}', args).fetchone()[0]
        order = f' order by "{DATE_COL}"' if DATE_COL in names else ""
        sel = ", ".join(f'"{x}"' for x in cols)
        rows = c.execute(f'select {sel} from "{table}"{w}{order} limit ? offset ?', [*args, limit, offset]).fetchall()
        data = [[(v[:10] if (k == DATE_COL and isinstance(v, str)) else v) for k, v in zip(cols, r, strict=True)]
                for r in rows]
        return cols, data, total
    finally:
        c.close()
