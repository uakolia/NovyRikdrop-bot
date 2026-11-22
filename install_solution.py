"""Apply the reviewed rename update during Docker build; never contacts Google."""
import ast
import datetime
import hashlib
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED = {
    'catalog.py': '87beea0bbe5b51a40bc07d6ec63d81c2b3a3f0a9',
    'catalog_parser.py': '8836fb06e819dc2f206a8f5a4e9ad5cd1d0f575b',
    'main.py': 'ae096e8a5b5a7380e31670f6c49e49eddb24a07c',
    'warehouse_sync.py': '1f69273c12003150b14a49ff6c934ccfe4c0204a',
    'handlers_admin.py': 'c688788cb2298ce165ce158847b66ab029d616b7',
}

def replace_function(text, name, replacement):
    nodes = [n for n in ast.parse(text).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    if len(nodes) != 1:
        raise RuntimeError('Expected exactly one function: ' + name)
    n = nodes[0]
    lines = text.splitlines(keepends=True)
    first = min([n.lineno] + [d.lineno for d in n.decorator_list]) - 1
    return ''.join(lines[:first]) + replacement.rstrip() + '\n\n' + ''.join(lines[n.end_lineno:])

def main():
    source = {}
    for name, want in EXPECTED.items():
        raw = (ROOT / name).read_bytes()
        got = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if got != want:
            raise RuntimeError('Source differs from reviewed version: ' + name + '; no files changed')
        source[name] = raw.decode('utf-8')
    ast.parse((ROOT / 'price_catalog_live.py').read_text(encoding='utf-8'))
    changed = dict(source)
    changed['catalog.py'] = replace_function(source['catalog.py'], 'reload_from_google',
        'async def reload_from_google():\n    from price_catalog_live import reload_from_google as reload_live\n    return await reload_live()')
    parser = source['catalog_parser.py']
    old = "'stock_sheet': _num(cell('stock')),"
    if parser.count(old) != 1:
        raise RuntimeError('Unexpected stock parser')
    parser = parser.replace(old, "'stock_text': cell('stock'),\n            'stock_sheet': __import__('price_catalog_live').stock_quantity(cell('stock')),")
    old = "'article': ('article', 'артикул'),"
    if parser.count(old) != 1:
        raise RuntimeError('Unexpected article mapping')
    parser = parser.replace(old, old + " 'model_name': ('name of model', 'назва моделі', 'name'),")
    changed['catalog_parser.py'] = replace_function(parser, '_find_model_name',
        "def _find_model_name(cells, colmap):\n"
        "    for raw in cells[:colmap['article']]:\n"
        "        value = _clean(raw)\n"
        "        if _MODEL_RE.match(value) and not value.startswith('http') and 'грн' not in value and len(re.findall(r'\\d', value)) <= 1:\n"
        "            return value\n"
        "    index = colmap.get('model_name')\n"
        "    if index is not None and index < len(cells):\n"
        "        value = _clean(cells[index])\n"
        "        if value and not value.startswith(('http', '=')):\n"
        "            return value\n"
        "    return None")
    text = source['main.py']
    old = '    wh_syncer = asyncio.create_task(warehouse_sync.run_forever(bot))'
    end = '        wh_syncer.cancel()'
    if text.count(old) != 1 or text.count(end) != 1:
        raise RuntimeError('Unexpected main task layout')
    text = text.replace(old, old + '\n    import price_catalog_live\n    price_syncer = asyncio.create_task(price_catalog_live.run_forever())')
    changed['main.py'] = text.replace(end, end + '\n        price_syncer.cancel()')
    changed['warehouse_sync.py'] = replace_function(source['warehouse_sync.py'], '_sync_blocking',
        'def _sync_blocking() -> dict:\n    raise WarehouseError("Warehouse writes are paused to prevent conflicts with supplier availability. No cells changed.")')
    changed['handlers_admin.py'] = replace_function(source['handlers_admin.py'], 'cmd_gids',
        '@router.message(Command("gids"))\n'
        'async def cmd_gids(msg: Message):\n'
        '    if not _is_admin(msg.from_user.id):\n'
        '        return\n'
        '    from price_catalog_live import GROUPS\n'
        '    body = "Price tabs use an explicit live-title allowlist. Manual /gids overrides are disabled.\\n\\n"\n'
        '    body += "\\n".join(names[0] for names in GROUPS)\n'
        '    await msg.answer(body + "\\n\\nRun /reload to validate current titles and IDs.")')
    for name, text in changed.items():
        ast.parse(text, filename=name)
    backup = ROOT / ('rename_solution_backup_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    backup.mkdir()
    for name in changed:
        shutil.copy2(ROOT / name, backup / name)
    try:
        for name, text in changed.items():
            (ROOT / name).write_text(text, encoding='utf-8')
    except BaseException:
        for name in changed:
            shutil.copy2(backup / name, ROOT / name)
        raise
    print('Generated corrected files:', ', '.join(changed))
    print('Originals backed up to:', backup.name)
    print('No Google writes or schedules. Warehouse-to-price writes are paused.')

if __name__ == '__main__':
    main()
