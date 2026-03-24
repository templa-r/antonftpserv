import os
import requests
import base64
import xml.etree.ElementTree as ET
import re
from collections import defaultdict

# ===================== НАСТРОЙКИ =====================
API_URL = "https://ka2.sibzapaska.ru:16500/API/hs/V2/GetTires"
API_USER = "API_client"
API_PASSWORD = "rWp7mFWXRKOq"

ROUND_STEP = 10
ROUND_METHOD = 'nearest'
INCLUDE_PRICE_TAG = False

SEASON_EXCLUDE_ENABLED = True
SEASON_EXCLUDE_VALUE = "зима"

EXCLUDED_BRANDS = [
    "Mazzini", "Nexen", "MAXXIS", "Predator", "Compasal", "HIFLY", "Aoteli",
    "Torero", "Viatti", "Massimo", "Firemax", "Sonix", "Prinx", "Roadmarch",
    "Kelly", "Nitto", "Кама"
]

MAX_ITEMS = 3000

BRAND_PRIORITY = {
    "MAXXIS": 1,
    "Mazzini": 1,
    "Nexen": 1,
    "Кама": 3,
}

EXCLUDED_BRANDS_FROM_EXPORT = ["Compasal"]

EXCLUDED_CATEGORY = ["Грузовая"]

EXCLUDED_ARTICLES = [
    "195/75R16C Laufenn X FIT VAN LV01",
    "АТ27x8-12 MAXXIS M961 6PR",
    "185/75R16C Ikon Autograph Snow C4",
    "33x10,50-16 NORTEC ET-500 111N TL АШК",
]

GLOBAL_COEFF = {
    "default": 0.92,
    "diameter_ranges": [
        {"min": 13, "max": 15, "coeff": 0.95},
        {"min": 16, "max": 17, "coeff": 0.94},
        {"min": 18, "max": 22, "coeff": 0.93},
    ],
}

BRAND_COEFFS = {
    "ikon": {
        "default": 0.88,
        "diameter_ranges": [
            {"min": 13, "max": 15, "coeff": 0.905},
            {"min": 17, "max": 22, "coeff": 0.88},
        ],
    },
    "laufenn": 0.905,
    "bridgestone": 0.965,
    "hankook": {
        "default": 0.915,
        "diameter_ranges": [
            {"min": 13, "max": 16, "coeff": 0.907},
            {"min": 17, "max": 22, "coeff": 0.906},
        ],
    },
}

MODEL_RULES = {
    ("autograph", "autograph ice 9 suv"): {
        "type": "add_to_field_by_diameter",
        "field": "price",
        "ranges": [
            {"min": 16, "max": 17, "value": 1300},
            {"min": 18, "max": 19, "value": 1700},
        ],
        "default": None,
    },
}

# ===================== ФУНКЦИИ =====================
def safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        return float(str(val).replace(",", ".").strip())
    except ValueError:
        return default

def round_price(price, step=None, method=None):
    if step is None:
        step = ROUND_STEP
    if method is None:
        method = ROUND_METHOD
    if method == 'down':
        return (price // step) * step
    elif method == 'up':
        return ((price + step - 1) // step) * step
    else:
        return int(round(price / step) * step)

def get_coeff_from_settings(settings, diameter):
    if not isinstance(settings, dict):
        settings = {"coeff": settings}
    coeff = None
    if diameter is not None and "diameter_ranges" in settings:
        for r in settings["diameter_ranges"]:
            if r["min"] <= diameter <= r["max"]:
                coeff = r["coeff"]
                break
        if coeff is None and "default" in settings:
            coeff = settings["default"]
    if coeff is None:
        coeff = settings.get("coeff", 0.92)
    round_step = settings.get("round_step")
    round_method = settings.get("round_method")
    return coeff, round_step, round_method

def add_product_to_root(root, item, diameter):
    product = ET.SubElement(root, "Product")
    for key, value in item.items():
        if key == "Оптовая_Цена":
            continue
        if key == "price" and not INCLUDE_PRICE_TAG:
            continue

        element = ET.SubElement(product, key)

        if key.lower() == "retail":
            brand = item.get("brand", "").strip().lower()
            model = item.get("model", "").strip().lower()
            category = item.get("category", "")

            is_excluded = (brand in [b.lower() for b in EXCLUDED_BRANDS] or
                           category in EXCLUDED_CATEGORY)

            final_price = None
            try:
                if is_excluded:
                    final_price = safe_float(value)
                else:
                    rule = MODEL_RULES.get((brand, model))
                    if rule:
                        rule_type = rule["type"]
                        if rule_type == "fixed":
                            final_price = safe_float(rule["value"])
                        elif rule_type == "add_to_field":
                            base_val = safe_float(item.get(rule["field"], "0"))
                            final_price = base_val + rule["value"]
                        elif rule_type == "add_to_field_by_diameter":
                            if diameter is not None:
                                add_value = None
                                for r in rule["ranges"]:
                                    if r["min"] <= diameter <= r["max"]:
                                        add_value = r["value"]
                                        break
                                if add_value is not None:
                                    base_val = safe_float(item.get(rule["field"], "0"))
                                    final_price = base_val + add_value
                                else:
                                    if rule.get("default") is not None:
                                        base_val = safe_float(item.get(rule["field"], "0"))
                                        final_price = base_val + rule["default"]
                                    else:
                                        final_price = safe_float(value)
                            else:
                                final_price = safe_float(value)
                        else:
                            final_price = safe_float(value)
                    else:
                        brand_settings = BRAND_COEFFS.get(brand)
                        if brand_settings is not None:
                            coeff, _, _ = get_coeff_from_settings(brand_settings, diameter)
                        else:
                            coeff, _, _ = get_coeff_from_settings(GLOBAL_COEFF, diameter)
                        orig_val = safe_float(value)
                        final_price = orig_val * coeff
            except Exception:
                final_price = safe_float(value)

            step = ROUND_STEP
            method = ROUND_METHOD
            rule = MODEL_RULES.get((brand, model))
            if rule:
                step = rule.get("round_step", step)
                method = rule.get("round_method", method)

            try:
                rounded = round_price(final_price, step, method)
                element.text = str(int(rounded))
            except:
                element.text = str(int(final_price))
        else:
            element.text = str(value)

    inSet_elem = ET.SubElement(product, "inSet")
    inSet_elem.text = "1"

    if item.get("model") == "Nortec LT 610":
        studded_elem = ET.SubElement(product, "studded")
        studded_elem.text = "Нет"

    nomenclature = item.get("Номенклатура", "")
    if re.match(r'^(1[2-9]|2[0-4])\s', nomenclature):
        product.tag = "disk"
    else:
        product.tag = "tyres"

# ===================== ОСНОВНАЯ ЛОГИКА =====================
auth = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
response = requests.get(API_URL, headers={"Authorization": f"Basic {auth}"})
response.raise_for_status()
data = response.json()

# --- Фильтрация и сбор товаров ---
valid_items = []
total_products = 0
excluded_zb = 0
excluded_article = 0
excluded_season = 0

for item in data:
    name = item.get("name", "")
    if name.startswith("ЗБ"):
        excluded_zb += 1
        continue

    if item.get("brand") == "Ikon (Nokian Tyres)":
        item["brand"] = "Ikon"

    if "name" in item and "(Nokian Tyres)" in item["name"]:
        item["name"] = item["name"].replace("(Nokian Tyres)", "").strip()
        item["name"] = re.sub(r'\s+', ' ', item["name"])
        name = item["name"]

    brand = item.get("brand", "")
    if brand in EXCLUDED_BRANDS_FROM_EXPORT:
        continue

    model_replacements = {
        "Blu Earth V906": ("BluEarth Winter V906", "BluEarth Winter V906"),
        "VS-EV": ("Victra Sport EV", "Victra Sport EV"),
        "Ecsta PS72": ("Ecsta Sport PS72", "Ecsta Sport PS72"),
    }
    current_model = item.get("model", "")
    if current_model in model_replacements:
        new_model, new_name_part = model_replacements[current_model]
        item["model"] = new_model
        if "name" in item:
            item["name"] = item["name"].replace(current_model, new_name_part)
            name = item["name"]

    diameter = safe_float(item.get("diameter"), default=None)
    if diameter is None:
        nomenclature = item.get("Номенклатура", "")
        match = re.search(r'[Rr](\d{2})', nomenclature)
        if match:
            diameter = float(match.group(1))

    article = item.get("article", "")
    if any(phrase in article for phrase in EXCLUDED_ARTICLES):
        excluded_article += 1
        continue

    if SEASON_EXCLUDE_ENABLED:
        season = item.get("season", "")
        if season == SEASON_EXCLUDE_VALUE:
            excluded_season += 1
            continue

    total_products += 1
    valid_items.append((item, diameter))

print(f"🔍 Всего товаров после фильтров: {total_products}")

# --- Статистика по брендам ---
from collections import defaultdict
import traceback

brand_diameter_stats = defaultdict(lambda: defaultdict(lambda: {'sum': 0, 'count': 0}))
stats_count = 0

for item, diameter in valid_items:
    brand = item.get("brand", "").strip()
    if not brand:
        continue
    price = safe_float(item.get("price", 0))
    retail = safe_float(item.get("retail", 0))
    margin = retail - price if price else retail
    d_key = int(diameter) if diameter is not None else 'unknown'
    brand_diameter_stats[brand][d_key]['sum'] += margin
    brand_diameter_stats[brand][d_key]['count'] += 1
    stats_count += 1

print(f"📊 Обработано записей для статистики: {stats_count}")

try:
    with open("brand_statistics.txt", "w", encoding="utf-8") as f:
        f.write("Статистика по брендам (средняя маржинальность на диаметр)\n")
        f.write("="*60 + "\n")
        for brand in sorted(brand_diameter_stats.keys()):
            f.write(f"\nБренд: {brand}\n")
            f.write("-"*40 + "\n")
            for diam in sorted(brand_diameter_stats[brand].keys(), key=lambda x: (x != 'unknown', x)):
                stats = brand_diameter_stats[brand][diam]
                avg = stats['sum'] / stats['count'] if stats['count'] > 0 else 0
                f.write(f"  Диаметр {diam}: {stats['count']} шт., средняя маржинальность = {avg:.2f} руб.\n")
            f.write("\n")
    print("✅ Статистика по брендам сохранена в файл brand_statistics.txt")
except Exception as e:
    print(f"❌ Ошибка при записи статистики: {e}")
    traceback.print_exc()

# --- Сортировка и ограничение ---
main_candidates = []      # для основного файла (диаметр >=16)
extra_candidates = []     # для дополнительных (диаметр <16) – теперь не используются, но оставим для логики

for item, diameter in valid_items:
    price = safe_float(item.get("price", 0))
    retail = safe_float(item.get("retail", 0))
    margin = retail - price if price else retail
    brand = item.get("brand", "").strip()
    priority = BRAND_PRIORITY.get(brand, 999)
    if diameter is not None and diameter >= 16:
        main_candidates.append((priority, -margin, item, diameter))
    else:
        extra_candidates.append((priority, -margin, item, diameter))

# Сортируем основную группу
main_candidates.sort(key=lambda x: (x[0], x[1]))
main_selected = [(item, diameter) for (_, _, item, diameter) in main_candidates[:MAX_ITEMS]]

print(f"📦 Отобрано для основного файла: {len(main_selected)} товаров (ограничение {MAX_ITEMS})")

# --- Запись основного XML ---
root = ET.Element("Products")
main_file_count = 0
diameter_count = {}

for item, diameter in main_selected:
    if diameter is not None:
        d_int = int(diameter)
        diameter_count[d_int] = diameter_count.get(d_int, 0) + 1
    else:
        diameter_count['unknown'] = diameter_count.get('unknown', 0) + 1
    add_product_to_root(root, item, diameter)
    main_file_count += 1

tree = ET.ElementTree(root)
with open("aztyre.xml", "wb") as file:
    tree.write(file, encoding="utf-8", xml_declaration=True)

# --- Вывод статистики ---
print(f"\n✅ XML файл успешно создан.")
print(f"   - Пропущено (ЗБ): {excluded_zb}")
print(f"   - Исключено по артикулу: {excluded_article}")
if SEASON_EXCLUDE_ENABLED:
    print(f"   - Исключено по сезону ({SEASON_EXCLUDE_VALUE}): {excluded_season}")
print(f"   - Всего товаров, прошедших фильтры: {total_products}")
print(f"   - Отобрано для основного файла (ограничение {MAX_ITEMS}): {len(main_selected)}")
print(f"   - Из них в основном файле aztyre.xml: {main_file_count} (диаметры >=16)")
print(f"\n📊 Статистика по диаметрам (в основном файле):")
if diameter_count:
    for d in sorted([k for k in diameter_count if k != 'unknown']):
        print(f"   - {d}\" : {diameter_count[d]} шт.")
    if 'unknown' in diameter_count:
        print(f"   - диаметр не определён : {diameter_count['unknown']} шт.")
else:
    print("   (нет данных)")
