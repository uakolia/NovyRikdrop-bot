"""Explicit price-tab loader. No spreadsheet writes. Installed by install_solution.py."""
import asyncio
import csv
import html
import io
import json
import logging
import os
import tempfile

log = logging.getLogger(__name__)
PRICE_ID = '18OkfzTnujb_VFTX0UuDlkEwCBNiR1pFy5OeafHugYFg'
GROUPS = [
 ('Ялинки на крабах', 'PE Umbrella System'),
 ('НОВИНКА: Ялинки на крабах 2026 Колекція', 'NEW 2026 PE Umbrella System'),
 ('Кольорові', 'Кольорові PE Umbrella System'),
 ('В горщику', 'Christmas tree in a pot'),
 ('Віночки', 'Wreaths'), ('Гірлянди', 'Garlands'), ('Ікебани', 'Ikebana'),
 ('НОВИНКА: Літаюча Ялинка', 'New Fly Tree'),
 ('НОВИНКА: Ялинка на Стіну', 'NEW Wall Tree'), ('Міні Ялинка', 'Mini Tree'),
]
_lock = None

def key(v):
    return ' '.join(str(v).split()).casefold()

def choose_tabs(properties):
    selected = []
    for names in GROUPS:
        found = [p for p in properties if key(p['title']) in {key(n) for n in names}]
        if len(found) != 1:
            raise ValueError(f'{names[0]}: expected one live tab, found {len(found)}')
        selected.append(found[0])
    return selected

def stock_quantity(v):
    import re
    s = key(v)
    if re.fullmatch(r'\d+', s):
        return int(s)
    if s in {'немає', 'нема', 'не наявні', 'не наявний', 'не в наявності',
             'відсутні', 'відсутній', 'out of stock', 'ні'}:
        return 0
    return None

def read_google():
    import config
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    sid = (config.PRICELIST_SHEET_ID or '').strip()
    if sid != PRICE_ID:
        raise ValueError('PRICELIST_SHEET_ID is not the intended destination price workbook; refused')
    raw = (config.GOOGLE_SERVICE_ACCOUNT_JSON or '').strip()
    if not raw:
        raise ValueError('GOOGLE_SERVICE_ACCOUNT_JSON is required for live title/ID verification')
    if raw.startswith('{'):
        info = json.loads(raw)
    else:
        with open(raw, encoding='utf-8') as f:
            info = json.load(f)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
    api = build('sheets', 'v4', credentials=creds, cache_discovery=False)
    meta = api.spreadsheets().get(spreadsheetId=sid,
        fields='sheets(properties(sheetId,title))').execute()
    tabs = choose_tabs([s['properties'] for s in meta.get('sheets', [])])
    ranges = ["'" + t['title'].replace("'", "''") + "'" for t in tabs]
    resp = api.spreadsheets().values().batchGet(spreadsheetId=sid,
        ranges=ranges, valueRenderOption='FORMATTED_VALUE').execute()
    values = resp.get('valueRanges', [])
    if len(values) != len(tabs):
        raise ValueError('Incomplete Google response; catalog unchanged')
    return [(t, v.get('values', [])) for t, v in zip(tabs, values)]

def assemble(sections, parser):
    merged, report = {}, []
    for tab, rows in sections:
        parsed = parser(rows)
        if not parsed:
            raise ValueError(f"{tab['title']}: zero parsed products; catalog unchanged")
        report.append(f"{html.escape(tab['title'])} [gid {tab['sheetId']}]: {len(parsed)}")
        for item in parsed:
            art = item['article']
            if art in merged:
                raise ValueError(f'Duplicate exact article across price tabs: {art}; catalog unchanged')
            merged[art] = item
    return merged, report

async def reload_from_google():
    global _lock
    import catalog
    from catalog_parser import parse_rows
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        try:
            sections = await asyncio.to_thread(read_google)
            merged, report = assemble(sections, parse_rows)
            old = len(catalog.items_or_empty())
            if len(merged) < 10 or (old and len(merged) < old * .8):
                raise ValueError(f'Catalog loss guard: old={old}, proposed={len(merged)}')
            payload = dict(catalog._catalog)
            payload['items'] = list(merged.values())
            dest = catalog.CATALOG_PATH
            fd, temp = tempfile.mkstemp(prefix='.catalog-', dir=os.path.dirname(dest), text=True)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(payload, f, ensure_ascii=False, indent=1)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp, dest)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
            catalog._catalog.clear()
            catalog._catalog.update(payload)
            catalog._last_report[:] = report
            return len(merged), None
        except Exception as exc:
            log.exception('Price reload refused')
            return 0, html.escape(str(exc))

async def run_forever():
    await asyncio.sleep(30)
    while True:
        n, err = await reload_from_google()
        if err:
            log.error('Price refresh failed, previous catalog retained: %s', err)
        else:
            log.info('Price catalog refreshed: %d', n)
        await asyncio.sleep(600)
