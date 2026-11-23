"""SQLite directory of delivery cities and branches, replaced after a full sync."""
import asyncio
import json
import logging
import os
import sqlite3
import time
from contextlib import closing
from datetime import timedelta

import config
from order_storage import db_path

log = logging.getLogger(__name__)
sync_lock = asyncio.Lock()


def path():
    return db_path("np-directory.sqlite3")


def cities(query, page, limit):
    if not path().exists():
        return None
    q = query.casefold()
    with closing(sqlite3.connect(path())) as db:
        if db.execute("SELECT 1 FROM unresolved WHERE instr(search, ?)>0 LIMIT 1", (q,)).fetchone():
            return None  # searchSettlements resolves villages without a published delivery-city link
        total = db.execute("SELECT count(*) FROM cities WHERE instr(search, ?)>0", (q,)).fetchone()[0]
        if not total:
            return None
        rows = db.execute("SELECT data FROM cities WHERE instr(search, ?)>0 "
                          "ORDER BY search != ?, search, data LIMIT ? OFFSET ?",
                          (q, q, limit, (page-1)*limit))
        return dict(cities=[json.loads(r[0]) for r in rows], page=page,
                    total=total, has_next=page*limit < total)


def warehouses(ref):
    if not path().exists():
        return None
    with closing(sqlite3.connect(path())) as db:
        if not db.execute("SELECT 1 FROM cities WHERE ref=?", (ref,)).fetchone():
            return None
        return [json.loads(r[0]) for r in db.execute("SELECT data FROM offices WHERE city=?", (ref,))]


def replace_offices(db, ref, rows):
    db.execute("DELETE FROM offices WHERE city=?", (ref,))
    db.executemany("INSERT INTO offices VALUES(?,?,?)",
                   [(r["ref"], ref, json.dumps(r, ensure_ascii=False)) for r in rows])


async def refresh_city(ref):
    import novaposhta as np
    async with sync_lock:
        rows = await np.cargo_warehouses(ref, min_weight=0)
        if path().exists():
            with closing(sqlite3.connect(path())) as db, db:
                replace_offices(db, ref, rows)
        return rows


async def pages(method):
    import novaposhta as np
    seen = set()
    for page in range(1, 2001):
        for attempt in range(5):
            try:
                rows = await np._call("Address", method, {"Page": str(page), "Limit": "500"})
                break
            except np.NPError as exc:
                if "too many requests" not in str(exc).lower() or attempt == 4:
                    raise
                await asyncio.sleep(5 * 2**attempt)
        if not rows:
            return
        fresh = [r for r in rows if r["Ref"] not in seen]
        if not fresh:
            raise ValueError(f"{method} repeated page {page}")
        seen.update(r["Ref"] for r in fresh)
        yield fresh
        await asyncio.sleep(1)
    raise ValueError(f"{method} exceeded pagination limit")


async def sync():
    import novaposhta as np
    target = path()
    tmp = target.with_suffix(".new")
    tmp.unlink(missing_ok=True)
    try:
        with closing(sqlite3.connect(tmp)) as db:
            db.executescript("CREATE TABLE cities(ref TEXT, search TEXT, data TEXT);"
                             "CREATE INDEX delivery_city ON cities(ref);"
                             "CREATE TABLE offices(ref TEXT PRIMARY KEY, city TEXT, data TEXT);"
                             "CREATE INDEX office_city ON offices(city);")
            db.execute("CREATE TABLE unresolved(search TEXT PRIMARY KEY)")
            linked = {}
            async for rows in pages("getCities"):
                for r in rows:
                    name = r["Description"]
                    area, region = r.get("AreaDescription") or "", r.get("RegionsDescription") or ""
                    label = ", ".join(filter(None, [name, region, area]))
                    c = dict(ref=r["Ref"], name=name, area=area, region=region, label=label,
                             settlement_ref=r.get("SettlementRef") or "")
                    if c["settlement_ref"]:
                        linked[c["settlement_ref"]] = c["ref"]
                    db.execute("INSERT INTO cities VALUES(?,?,?)",
                               (c["ref"], name.casefold(), json.dumps(c, ensure_ascii=False)))
            async for rows in pages("getSettlements"):
                for r in rows:
                    name = r["Description"]
                    ref = linked.get(r["Ref"]) or r.get("DeliveryCity")
                    if not ref:
                        db.execute("INSERT OR IGNORE INTO unresolved VALUES(?)", (name.casefold(),))
                        continue
                    area = r.get("AreaDescription") or ""
                    region = r.get("RegionsDescription") or ""
                    label = ", ".join(filter(None, [r.get("SettlementTypeDescription"), name, region, area]))
                    city = dict(ref=ref, name=name, area=area, region=region,
                                settlement_ref=r["Ref"], label=label)
                    # A delivery city may serve several settlements. Preserve each identity.
                    db.execute("DELETE FROM cities WHERE ref=? AND search=? AND json_extract(data, '$.settlement_ref')=?",
                               (ref, name.casefold(), r["Ref"]))
                    db.execute("INSERT INTO cities VALUES(?,?,?)",
                               (ref, name.casefold(), json.dumps(city, ensure_ascii=False)))
            async for rows in pages("getWarehouses"):
                for r in rows:
                    office = np.format_office(r, 0)
                    if office:
                        db.execute("INSERT INTO offices VALUES(?,?,?)",
                                   (office["ref"], r["CityRef"], json.dumps(office, ensure_ascii=False)))
            counts = [db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("cities","offices")]
            if not all(counts):
                raise ValueError("empty Nova Poshta directory")
            db.commit()
        async with sync_lock:
            os.replace(tmp, target)
        log.info("NP directory ready: %s cities, %s offices", *counts)
    finally:
        tmp.unlink(missing_ok=True)


async def run():
    first = True
    while True:
        try:
            if not first or not path().exists() or time.time() - path().stat().st_mtime > 86400:
                await sync()
            first = False
            now = config.now()
            morning = now.replace(hour=3, minute=0, second=0, microsecond=0)
            if morning <= now:
                morning += timedelta(days=1)
            await asyncio.sleep((morning-now).total_seconds())
        except Exception:
            log.exception("NP directory sync failed")
            first = False
            await asyncio.sleep(3600)
