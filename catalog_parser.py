"""
Універсальний парсер прайс-листа "Ялинкар".

Працює з рядками таблиці (список списків клітинок) — джерелом може бути
CSV-експорт Google Таблиці або markdown-експорт. Прайс містить кілька секцій
з різними наборами колонок; парсер знаходить рядки-заголовки (де є "Article")
і будує мапу колонок для кожної секції окремо.
"""
import re
import unicodedata

PRICE_COLS = {
    "price_wholesale": ("ціна гурт",),
    "price_drop1": ("ціна дроп 1",),
    "price_drop2": ("ціна дроп 2",),
    "price_drop3": ("ціна дроп 3",),
    "price_retail_min": ("ціна роздріб",),
}

INFO_COLS = {
    "article": ("article",),
    "height": ("height",),
    "size": ("size",),          # для гірлянд/ікебан "Size" замість Height
    "diameter": ("diameter",),
    "weight": ("weight",),
    "box": ("size box",),
    "volume": ("packaging volume",),
    "parts": ("number of parts",),
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"\s+", " ", s).strip()


def _num(s):
    """'3 378 грн.' / '7,8' / '€66' -> float або None"""
    s = _norm(s).replace("\xa0", " ")
    s = re.sub(r"[^\d,.\-]", "", s.replace(" ", ""))
    if not s or s in {"-", ".", ","}:
        return None
    s = s.replace(",", ".")
    # '25.25.120' захист
    if s.count(".") > 1:
        parts = s.split(".")
        s = parts[0] + "." + "".join(parts[1:])
    try:
        return float(s)
    except ValueError:
        return None


def _header_map(cells):
    """Якщо рядок — заголовок секції, повернути мапу {поле: індекс}."""
    lowered = [_norm(c).lower() for c in cells]
    if not any(c == "article" for c in lowered):
        return None
    m = {}
    for field, keys in {**INFO_COLS, **PRICE_COLS}.items():
        for i, c in enumerate(lowered):
            if field == "size" and "box" in c:
                continue  # "Size box" — це поле box, а не size
            if any(c.startswith(k) for k in keys) and field not in m:
                m[field] = i
    # обов'язково має бути хоч одна дроп-ціна
    if "article" not in m or "price_drop2" not in m:
        return None
    return m


_MODEL_RE = re.compile(r"^[^|/]{2,60}/[^|/]{2,60}$")


def _clean(raw: str) -> str:
    c = _norm(raw).replace("\\[merged\\]", "").replace("[merged]", "").strip()
    return c.lstrip("\\").strip()


def _find_model_name(cells, colmap):
    """Назва моделі — клітинка формату 'English / Українська' поруч з Article."""
    art_i = colmap["article"]
    best = None
    for i, raw in enumerate(cells[:art_i + 1]):
        if i == art_i:
            continue  # сам артикул може містити '/'
        c = _clean(raw)
        if (_MODEL_RE.match(c) and "грн" not in c and not c.startswith("http")
                and len(re.findall(r"\d", c)) <= 1):
            best = c
    return best


def _ua_name(full: str) -> str:
    """'Grace / Грейс' -> 'Грейс' (беремо українську частину, якщо є кирилиця)."""
    parts = [p.strip() for p in full.split("/")]
    for p in reversed(parts):
        if re.search(r"[А-Яа-яІіЇїЄєҐґ]", p):
            return p
    return full.strip()


def parse_rows(rows):
    """
    rows: iterable списків клітинок (рядки таблиці зверху вниз).
    Повертає список товарів (dict).
    """
    items = []
    colmap = None
    current_model = None

    for cells in rows:
        cells = [str(c) if c is not None else "" for c in cells]
        hm = _header_map(cells)
        if hm:
            colmap = hm
            current_model = None
            continue
        if not colmap:
            continue

        def cell(field):
            i = colmap.get(field)
            if i is None or i >= len(cells):
                return ""
            return _clean(cells[i]).replace("\\", "")

        name = _find_model_name(cells, colmap)
        if name:
            current_model = name

        article = cell("article")
        if (not article or article.lower() == "article" or len(article) > 25
                or not re.search(r"\d", article) or "tree" in article.lower()):
            continue
        drop2 = _num(cell("price_drop2"))
        drop3 = _num(cell("price_drop3"))
        if not (drop2 or drop3):
            continue  # рядок без цін (немає в наявності)
        if not current_model:
            continue

        height = _num(cell("height"))
        size_txt = cell("size") or cell("height")
        weight = _num(cell("weight"))
        volume = _num(cell("volume"))
        if weight is None and volume:
            weight = round(volume * 120, 1)  # оцінка: ~120 кг/м³ для литих ялинок
        item = {
            "model": current_model,
            "model_ua": _ua_name(current_model),
            "article": article,
            "height_m": height,
            "size": size_txt,
            "diameter_cm": _num(cell("diameter")),
            "weight_kg": weight,
            "volume_m3": volume,
            "box": cell("box"),
            "parts": _num(cell("parts")),
            "price_wholesale": _num(cell("price_wholesale")),
            "price_drop1": _num(cell("price_drop1")),
            "price_drop2": drop2,
            "price_drop3": drop3,
            "price_retail_min": _num(cell("price_retail_min")),
        }
        items.append(item)

    # дедуплікація: об'єднані по вертикалі клітинки повторюють артикул у кількох
    # рядках; відсутні поля добираємо з наступних рядків-дублікатів
    seen = {}
    for it in items:
        key = it["article"]
        if key not in seen:
            seen[key] = it
        else:
            for k, v in it.items():
                if seen[key].get(k) in (None, "") and v not in (None, ""):
                    seen[key][k] = v
    result = list(seen.values())

    # добір ваги/об'єму, де їх немає в прайсі: оцінка за товарами тієї ж моделі
    # (вага ~ пропорційна висоті^2), інакше за середнім по висоті в каталозі
    by_model = {}
    for it in result:
        by_model.setdefault(it["model"], []).append(it)
    for it in result:
        if it["weight_kg"]:
            continue
        h = it["height_m"]
        est = None
        if h:
            sib = [s for s in by_model[it["model"]] if s["weight_kg"] and s["height_m"]]
            if sib:
                ref = min(sib, key=lambda s: abs(s["height_m"] - h))
                est = ref["weight_kg"] * (h / ref["height_m"]) ** 2
            else:
                any_h = [s for s in result if s["weight_kg"] and s["height_m"]]
                near = [s for s in any_h if abs(s["height_m"] - h) <= 0.31]
                if near:
                    est = sum(s["weight_kg"] for s in near) / len(near)
        it["weight_kg"] = round(est, 1) if est else 5.0
        it["weight_estimated"] = True
    return result


def parse_csv_text(text: str):
    import csv
    import io
    return parse_rows(csv.reader(io.StringIO(text)))
