"""Будує assets/data/medications_catalog.json з Державного реєстру ЛЗ.

Джерело: Державний реєстр лікарських засобів України. Найсвіжіший файл —
експорт https://drlz.moz.gov.ua/export-csv; також набір відкритих даних
https://data.gov.ua/dataset/reestr_likarskyh_zasobiv_moz (оновлюється рідше).
Кодування UTF-8 (drlz) або windows-1251 (data.gov.ua), роздільник «;». Відкриті дані можна вільно
використовувати з обов'язковим посиланням на джерело.

Запуск з кореня застосунку (оновлює вшитий каталог):
    python tool/build_catalog_from_registry.py шлях/до/реєстру.csv

Запуск у репозиторії simplemedkit-catalog (щотижневе оновлення):
    python build_catalog.py registry.csv --seeds seeds.json \
        --out catalog.json --version-out version.json

Що робить:
  * бере лише чинні готові ліки: без субстанцій та in bulk, без
    достроково припинених і прострочених (на дату знімка) реєстрацій;
  * одна позиція на торгову назву (назви в каталозі унікальні);
  * діюча речовина — українською, якщо її вдається впізнати в полі
    «Склад (діючі)», інакше міжнародна назва латиницею;
  * категорія — за кодом АТХ, форма випуску — за ключовими словами;
  * ручні позиції наявного каталогу (id «seed-…») лишаються першими
    й мають пріоритет над реєстром.
"""

import argparse
import csv
import datetime
import difflib
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

CATALOG = Path('assets/data/medications_catalog.json')

# Назви, довші за це, обрізаються до першої дужки або коми.
MAX_NAME = 50

# Категорії застосунку (MedicationCategories) за префіксом коду АТХ.
# Довший префікс перевіряється першим.
ATC_CATEGORIES = {
    'A02': 'Шлунок та травлення', 'A03': 'Шлунок та травлення',
    'A04': 'Шлунок та травлення', 'A05': 'Шлунок та травлення',
    'A06': 'Шлунок та травлення', 'A07': 'Шлунок та травлення',
    'A09': 'Шлунок та травлення',
    'A11': 'Вітаміни та БАДи', 'A12': 'Вітаміни та БАДи',
    'A13': 'Вітаміни та БАДи', 'B03': 'Вітаміни та БАДи',
    'B01': 'Серце та судини', 'C': 'Серце та судини',
    'D08': 'Антисептики', 'D': 'Шкіра та догляд',
    'G01': 'Інтимне здоров’я', 'G02': 'Інтимне здоров’я',
    'G03': 'Інтимне здоров’я',
    'J01': 'Антибіотики', 'J05': 'Застуда та грип',
    'M01': 'Знеболюючі', 'M02': 'М’язи та суглоби',
    'M03': 'М’язи та суглоби', 'M04': 'М’язи та суглоби',
    'M05': 'М’язи та суглоби', 'M09': 'М’язи та суглоби',
    'N02': 'Знеболюючі',
    'N03': 'Нервова система', 'N04': 'Нервова система',
    'N05': 'Нервова система', 'N06': 'Нервова система',
    'N07': 'Нервова система',
    'R01': 'Застуда та грип', 'R02': 'Застуда та грип',
    'R05': 'Застуда та грип', 'R06': 'Алергія',
    'S01': 'Очі та вуха', 'S02': 'Очі та вуха', 'S03': 'Очі та вуха',
}
OTHER = 'Інше'

# Форми випуску застосунку (AppConstants.releaseForms) за ключовими словами.
RELEASE_FORMS = [
    ('супозитор', 'супозиторії'), ('песар', 'супозиторії'),
    ('капсул', 'капсули'),
    ('таблет', 'таблетки'), ('драже', 'таблетки'),
    ('пастил', 'таблетки'), ('льодяник', 'таблетки'),
    ('сироп', 'сироп'), ('крапл', 'краплі'),
    ('гель', 'гель'), ('мазь', 'мазь'),
    ('стік', 'стіки'),
    ('порошок', 'порошок'), ('гранул', 'порошок'),
    ('ампул', 'ампули'),
]

# Службові слова, які в назвах пишемо малими («зі смаком меду та лимону»).
SMALL_WORDS = {'та', 'зі', 'із', 'з', 'до', 'на', 'для', 'у', 'в', 'і', 'й',
               'по', 'без', 'від'}

# Одиниці виміру в назвах («7,5 МГ» → «7,5 мг»).
UNITS = {'мг', 'мл', 'мкг', 'г', 'од', 'мо', 'ммоль'}

TRANSLIT = dict(zip(
    'абвгґдеєжзиіїйклмнопрстуфхцчшщьюя',
    ['a', 'b', 'v', 'h', 'g', 'd', 'e', 'ie', 'zh', 'z', 'y', 'i', 'i',
     'i', 'k', 'l', 'm', 'n', 'o', 'p', 'r', 's', 't', 'u', 'f', 'kh',
     'ts', 'ch', 'sh', 'shch', '', 'iu', 'ia'],
))


def translit(word):
    return ''.join(TRANSLIT.get(ch, ch) for ch in word)


def parse_date(value):
    """Дата у форматі «дд.мм.рррр» (data.gov.ua) або «рррр-мм-дд» (drlz)."""
    for fmt in ('%d.%m.%Y', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def col(row, *names):
    """Значення першої наявної колонки: формати експорту відрізняються."""
    for name in names:
        if name in row:
            return row[name] or ''
    return ''


def atc_of(row):
    # «Код АТС 1» (кирилиця, data.gov.ua) або «Код ATC 1» (латиниця, drlz).
    return col(row, 'Код АТС 1', 'Код ATC 1')


def group_of(row):
    return col(row, 'Фармакотерапевтична група', 'Фармакотерапевтична група 1')


def id_of(row):
    return col(row, 'ID', 'MPID')


def is_active(row, on_date):
    if col(row, 'Дострокове припинення') == 'Так':
        return False
    # Новий формат: дата дочасного припинення (може бути й у майбутньому).
    stop = parse_date(col(row, 'Дата дочасного припинення'))
    if stop and stop <= on_date:
        return False
    end = row['Дата закінчення']
    if end and end != 'необмежений':
        date = parse_date(end)
        if date and date < on_date:
            return False
    group = group_of(row)
    form = row['Форма випуску'].lower()
    if group.startswith('Субстанц') or 'субстанц' in form:
        return False
    return 'in bulk' not in form


def pretty_name(raw):
    """«НУРОФЕН® ЕКСПРЕС» → «Нурофен Експрес»."""
    name = re.sub(r'[®™©]', '', raw).strip()
    name = re.sub(r'\s+', ' ', name)

    def fix_part(part):
        letters = [ch for ch in part if ch.isalpha()]
        # Абревіатури («ХЛ», «СР», «Д3») лишаємо як є.
        if len(letters) <= 2 and len(part) <= 2:
            return part
        return part[:1].upper() + part[1:].lower()

    def fix(word):
        # Одиниці та дозування («мг/мл», «0,1 %») — малими літерами.
        if ('/' in word or '%' in word or word[:1].isdigit()
                or word.lower() in UNITS):
            return word.lower()
        if word.lower() in SMALL_WORDS:
            return word.lower()
        if '-' in word:
            # «НО-ШПА» → «Но-шпа»: після дефіса — малими літерами.
            head, rest = word.split('-', 1)
            return head[:1].upper() + head[1:].lower() + '-' + rest.lower()
        return fix_part(word)

    pretty = ' '.join(fix(w) for w in name.split(' '))
    # Довгі описові назви (вакцини тощо) обрізаємо до першої дужки/коми.
    if len(pretty) > MAX_NAME:
        pretty = re.split(r' \(|,', pretty, maxsplit=1)[0].strip()
    if len(pretty) > MAX_NAME:
        pretty = pretty[:MAX_NAME].rsplit(' ', 1)[0].rstrip(' /-')
    return pretty


def category_for(atc):
    atc = (atc or '').strip().upper()
    for length in (3, 1):
        hit = ATC_CATEGORIES.get(atc[:length])
        if hit:
            return hit
    return OTHER


def release_form_for(raw):
    text = raw.lower()
    # Форма — перша частина опису до «по …» / «;».
    head = re.split(r'[;(]| по ', text, maxsplit=1)[0]
    for key, form in RELEASE_FORMS:
        if key in head:
            return form
    return ''


def ukrainian_ingredient(inn, composition):
    """Шукає в «Склад (діючі)» українську назву речовини з [inn]."""
    target = re.sub(r'[^a-z]', '', inn.lower().split(',')[0].split(' ')[0])
    if len(target) < 4:
        return None
    best, best_score = None, 0.0
    for word in re.findall(r'[а-яіїєґ’\']{4,}', composition.lower()):
        latin = translit(word.replace('’', '').replace("'", ''))
        size = min(len(latin), len(target))
        score = difflib.SequenceMatcher(
            None, latin[:size], target[:size]).ratio()
        if score > best_score:
            best, best_score = word, score
    if best is None or best_score < 0.75:
        return None
    # Родовий відмінок → називний для типових закінчень.
    for suffix, repl in (('ію', 'ій'), ('ату', 'ат'), ('ину', 'ин'),
                         ('олу', 'ол'), ('у', ''), ('ю', 'й')):
        if best.endswith(suffix):
            best = best[: -len(suffix)] + repl
            break
    else:
        return None
    return best[:1].upper() + best[1:]


COMBINATION = 'Комбінований препарат'


def ingredient_for(row):
    inn = row['Міжнародне непатентоване найменування'].strip().strip('*')
    composition = row['Склад (діючі)']
    if re.search('[а-яіїєґ]', inn.lower()):
        return ukrainian_inn(inn)
    # «Paracetamol, combinations …» → речовина «Paracetamol».
    first = inn.split(',')[0].strip()
    lowered = first.lower()
    if (not first or first in ('Comb drug', 'Mono')
            or ' and ' in lowered or 'combination' in lowered):
        return COMBINATION
    return ukrainian_ingredient(first, composition) or first


def ukrainian_inn(inn):
    """МНН українською (формат drlz): «ібупрофен» → «Ібупрофен»."""
    if ';' in inn or inn.isupper():
        return COMBINATION
    first = inn.split(',')[0].strip()
    if not first or 'комбінац' in first.lower():
        return COMBINATION
    return first[:1].upper() + first[1:]


def snapshot_date(rows):
    """Дата знімка реєстру — найпізніша дата початку дії у файлі.

    Чинність перевіряємо на цю дату, а не на сьогодні: реєстрація, що
    спливла вже після знімка, найімовірніше була продовжена, а файл про
    це ще не знає.
    """
    dates = [parse_date(r['Дата початку дії']) for r in rows]
    latest = max((d for d in dates if d), default=datetime.datetime.now())
    # У свіжому експорті трапляються дати з майбутнього — не далі сьогодні.
    return min(latest, datetime.datetime.now())


def build(registry_path, seeds_path=CATALOG, out_path=CATALOG,
          version_path=None):
    raw = Path(registry_path).read_bytes()
    try:
        text = raw.decode('utf-8-sig')  # експорт drlz.moz.gov.ua
    except UnicodeDecodeError:
        text = raw.decode('cp1251', errors='replace')  # data.gov.ua
    all_rows = list(csv.DictReader(io.StringIO(text), delimiter=';'))
    snapshot = snapshot_date(all_rows)
    rows = [r for r in all_rows if is_active(r, snapshot)]
    print(f'Знімок реєстру станом на {snapshot:%d.%m.%Y}.')

    existing = json.loads(Path(seeds_path).read_text(encoding='utf-8'))
    seeds = [m for m in existing if str(m['id']).startswith('seed-')]
    taken = {m['name'].lower() for m in seeds}

    by_name = defaultdict(list)
    for row in rows:
        name = pretty_name(row['Торгівельне найменування'])
        if name:
            by_name[name].append(row)

    result = list(seeds)
    for name in sorted(by_name, key=str.lower):
        if name.lower() in taken:
            continue
        group = by_name[name]
        forms = Counter(release_form_for(r['Форма випуску']) for r in group)
        form = next((f for f, _ in forms.most_common() if f), '')
        main = next((r for r in group if atc_of(r)), group[0])
        result.append({
            'id': 'reg-' + re.sub(r'[^0-9a-z]', '', id_of(main).lower())[-16:],
            'name': name,
            'activeIngredient': ingredient_for(main),
            'category': category_for(atc_of(main)),
            'releaseForm': form,
        })
        taken.add(name.lower())

    # Унікальність id.
    seen = set()
    for item in result:
        base, n = item['id'], 1
        while item['id'] in seen:
            n += 1
            item['id'] = f'{base}-{n}'
        seen.add(item['id'])

    lines = ',\n'.join(
        '  ' + json.dumps(m, ensure_ascii=False) for m in result)
    content = '[\n' + lines + '\n]\n'
    Path(out_path).write_text(content, encoding='utf-8')
    print(f'Записано {len(result)} позицій '
          f'({len(seeds)} ручних + {len(result) - len(seeds)} з реєстру).')

    if version_path:
        # Опис версії для застосунку: він порівнює `version` зі своєю і
        # перевіряє `sha256` завантаженого файлу.
        Path(version_path).write_text(json.dumps({
            'schema': 1,
            'version': f'{snapshot:%Y-%m-%d}',
            'count': len(result),
            'sha256': hashlib.sha256(content.encode('utf-8')).hexdigest(),
        }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(
        description='Каталог препаратів із Державного реєстру ЛЗ.')
    parser.add_argument('registry', help='CSV-файл реєстру')
    parser.add_argument('--seeds', default=str(CATALOG),
                        help='каталог, з якого беруться ручні позиції seed-*')
    parser.add_argument('--out', default=str(CATALOG),
                        help='куди записати каталог')
    parser.add_argument('--version-out', default=None,
                        help='куди записати version.json (необов\'язково)')
    args = parser.parse_args()
    build(args.registry, args.seeds, args.out, args.version_out)


if __name__ == '__main__':
    main()
