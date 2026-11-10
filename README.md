# SimpleMedKit — каталог препаратів

Довідник препаратів для застосунку **SimpleMedKit**, зібраний з
**Державного реєстру лікарських засобів України** (МОЗ України,
[drlz.moz.gov.ua](https://drlz.moz.gov.ua/register/)).

| Файл | Що це |
|---|---|
| `catalog.json` | Каталог: `id`, `name`, `activeIngredient`, `category`, `releaseForm` |
| `version.json` | Версія каталогу (дата знімка реєстру), кількість позицій, SHA-256 |
| `seeds.json` | Ручні позиції застосунку — мають пріоритет над реєстром |
| `build_catalog.py` | Перетворення CSV реєстру на каталог |

## Як оновлюється

GitHub Action `Оновлення каталогу` щонеділі завантажує
[експорт реєстру](https://drlz.moz.gov.ua/export-csv), перебудовує каталог і
комітить зміни. Запустити вручну: вкладка **Actions → Оновлення каталогу →
Run workflow**.

Застосунок завантажує файли за посиланнями:

- `https://raw.githubusercontent.com/Gudvin2000/simplemedkit-catalog/main/version.json`
- `https://raw.githubusercontent.com/Gudvin2000/simplemedkit-catalog/main/catalog.json`

## Джерело даних

Відомості Державного реєстру лікарських засобів України (МОЗ України).
Каталог має довідковий характер і може відставати від реєстру; актуальна
інформація — в офіційному реєстрі.
