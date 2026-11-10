"""Parse supplier price-list sections with independent column mappings."""
import re
import math
import unicodedata
from decimal import Decimal

PRICE_COLS = {
    'price_wholesale': ('ціна гурт',),
    'price_drop1': ('ціна дроп 1',),
    'price_drop2': ('ціна дроп 2',),
    'price_drop3': ('ціна дроп 3',),
    'price_retail_min': ('ціна роздріб',),
}
INFO_COLS = {
    'article': ('article', 'артикул'),
    'height': ('height', 'висота'),
    'size': ('size',),
    'diameter': ('diameter',),
    'weight': ('weight', 'вага'),
    'box': ('size box', 'розмір коробки'),
    'volume': ('packaging volume', "об'ємна вага", 'об’ємна вага', "об'єм упаковки", 'об’єм упаковки'),
    'parts': ('number of parts', 'кількість частин'),
    'stock': ('наявність',),
}

def _norm(s):
    s = unicodedata.normalize('NFKC', str(s or ''))
    return re.sub(r'\s+', ' ', s).strip()

def _num(s):
    s = _norm(s).replace('\xa0', ' ')
    s = re.sub(r'[^\d,.\-]', '', s.replace(' ', ''))
    if not s or s in {'-', '.', ','}:
        return None
    s = s.replace(',', '.')
    if s.count('.') > 1:
        parts = s.split('.')
        s = parts[0] + '.' + ''.join(parts[1:])
    try:
        return float(s)
    except ValueError:
        return None

def _header_map(cells):
    lowered = [_norm(c).lower() for c in cells]
    if not any(c in ('article', 'артикул') for c in lowered):
        return None
    m = {}
    for field, keys in {**INFO_COLS, **PRICE_COLS}.items():
        for i, c in enumerate(lowered):
            if field == 'size' and 'box' in c:
                continue
            if any(c.startswith(k) for k in keys) and field not in m:
                m[field] = i
    if 'article' not in m or 'price_drop2' not in m:
        return None
    return m

_MODEL_RE = re.compile(r'^[^|/]{2,60}/[^|/]{2,60}$')

def _clean(raw):
    c = _norm(raw).replace('\\[merged\\]', '').replace('[merged]', '').strip()
    return c.lstrip('\\').strip()

def _find_model_name(cells, colmap):
    art_i = colmap['article']
    best = None
    for i, raw in enumerate(cells[:art_i + 1]):
        if i == art_i:
            continue
        c = _clean(raw)
        if (_MODEL_RE.match(c) and 'грн' not in c and not c.startswith('http')
                and len(re.findall(r'\d', c)) <= 1):
            best = c
    return best

def _ua_name(full):
    parts = [p.strip() for p in full.split('/')]
    for p in reversed(parts):
        if re.search(r'[А-Яа-яІіЇїЄєҐґ]', p):
            return tidy_name(p)
    return tidy_name(full)

_LATIN_TWINS = str.maketrans({'C': 'С', 'c': 'с', 'P': 'Р', 'p': 'р', 'A': 'А',
    'a': 'а', 'E': 'Е', 'e': 'е', 'T': 'Т', 'O': 'О', 'o': 'о', 'I': 'І',
    'i': 'і', 'K': 'К', 'M': 'М', 'H': 'Н', 'B': 'В', 'X': 'Х', 'x': 'х', 'y': 'у'})
_FIXES = {'гірдянда': 'Гірлянда', 'гірлянда': 'Гірлянда', 'вінок': 'Віночок'}

def _fix_latin(word):
    has_cyr = bool(re.search(r'[А-Яа-яІіЇїЄєҐґ]', word))
    has_lat = bool(re.search(r'[A-Za-z]', word))
    if has_cyr and has_lat and not re.fullmatch(r'[A-Za-z]+', word):
        return word.translate(_LATIN_TWINS)
    return word

def tidy_name(name):
    n = re.sub(r'\s+', ' ', name).strip().strip('"«»').strip()
    n = ' '.join(_fix_latin(w) for w in n.split())
    n = n.replace('"', '').replace('«', '').replace('»', '')
    words = n.split()
    if words:
        low = words[0].lower()
        if low in _FIXES:
            words[0] = _FIXES[low]
        elif words[0].islower():
            words[0] = words[0][0].upper() + words[0][1:]
        n = ' '.join(words)
    m = re.fullmatch(r'(.+?)\s+(Вінок|Віночок)', n, re.IGNORECASE)
    if m:
        n = f'Віночок {m.group(1)}'
    return n.strip()

def _box_value(mapped, cells):
    # Use the same strict dimension parser as TTN creation; never guess dimensions.
    from shipment_boxes import parse_boxes, PackagingError
    def valid(value):
        try:
            parse_boxes(value)
            return True
        except PackagingError:
            return False
    if valid(mapped):
        return mapped
    candidates = {}
    for raw in cells:
        value = _clean(raw).replace('\\', '')
        if valid(value):
            key = tuple(parse_boxes(value))
            candidates.setdefault(key, value)
    if len(candidates) == 1:
        return next(iter(candidates.values()))
    return mapped

def parse_rows(rows):
    items = []
    colmap = None
    current_model = None
    pending = None
    def _with_prices(item, drop2, drop3, cell):
        item['price_drop2'] = drop2
        item['price_drop3'] = drop3
        for field in ('price_wholesale', 'price_drop1', 'price_retail_min'):
            if not item.get(field):
                item[field] = _num(cell(field))
        return item
    for cells in rows:
        cells = [str(c) if c is not None else '' for c in cells]
        hm = _header_map(cells)
        if hm:
            colmap = hm
            current_model = None
            pending = None
            continue
        if not colmap:
            continue
        def cell(field):
            i = colmap.get(field)
            if i is None or i >= len(cells):
                return ''
            return _clean(cells[i]).replace('\\', '')
        name = _find_model_name(cells, colmap)
        if name:
            current_model = name
        article = cell('article')
        drop2 = _num(cell('price_drop2'))
        drop3 = _num(cell('price_drop3'))
        if (not article or article.lower() == 'article' or len(article) > 25
                or not re.search(r'\d', article) or 'tree' in article.lower()):
            if pending is not None and (drop2 or drop3):
                items.append(_with_prices(pending, drop2, drop3, cell))
                pending = None
            continue
        if not current_model:
            continue
        height = _num(cell('height'))
        size_txt = cell('size') or cell('height')
        weight = _num(cell('weight'))
        volume = _num(cell('volume'))
        item = {
            'model': current_model, 'model_ua': _ua_name(current_model),
            'article': article, 'height_m': height, 'size': size_txt,
            'diameter_cm': _num(cell('diameter')), 'weight_kg': weight,
            'volume_m3': volume, 'box': _box_value(cell('box'), cells),
            'parts': _num(cell('parts')),
            'price_wholesale': _num(cell('price_wholesale')),
            'price_drop1': _num(cell('price_drop1')),
            'price_drop2': drop2, 'price_drop3': drop3,
            'price_retail_min': _num(cell('price_retail_min')),
            'stock_sheet': _num(cell('stock')),
        }
        if drop2 or drop3:
            items.append(item)
            pending = None
        else:
            pending = item
    seen = {}
    for it in items:
        key = it['article']
        if key not in seen:
            seen[key] = it
        else:
            for k, v in it.items():
                if seen[key].get(k) in (None, '') and v not in (None, ''):
                    seen[key][k] = v
    result = list(seen.values())
    nouns = (('WR-', 'Віночок'), ('GR-', 'Гірлянда'), ('IK-', 'Ікебана'),
             ('WT-', 'Настінна ялинка'), ('GT-', 'Подарункова ялинка'))
    for it in result:
        art = it['article'].upper()
        for pref, noun in nouns:
            if art.startswith(pref):
                if not it['model_ua'].lower().startswith(noun.lower()):
                    it['model_ua'] = f"{noun} {it['model_ua']}"
                break
    for it in result:
        w = it.get('weight_kg')
        if w is None or not math.isfinite(w) or w <= 0:
            it['weight_kg'] = 10.0
            it['weight_estimated'] = True
    return result

def parse_csv_text(text):
    import csv
    import io
    return parse_rows(csv.reader(io.StringIO(text)))
