import os
import requests
import base64
import xml.etree.ElementTree as ET
import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# ===================== НАСТРОЙКИ =====================
API_URL = "https://ka2.sibzapaska.ru:16500/API/hs/V2/GetTires"
API_USER = "API_client"
API_PASSWORD = "rWp7mFWXRKOq"

ROUND_STEP = 10
ROUND_METHOD = 'nearest'
INCLUDE_PRICE_TAG = False

# ===================== ЗАМЕНА ИЗОБРАЖЕНИЙ =====================
IMAGE_REPLACE_ENABLED = True
IMAGE_CHECK_ENABLED = True
IMAGE_BASE_URL = "https://s3.ru1.storage.beget.cloud/fa5a823588a1-adromavito/images/"
IMAGE_CACHE_FILE = "image_cache.json"

# Чтение переменной окружения для принудительного обновления кэша
IMAGE_CACHE_REFRESH = os.getenv("IMAGE_CACHE_REFRESH", "false").lower() == "true"

# ===================== ФИЛЬТРЫ =====================
SEASON_EXCLUDE_ENABLED = True
SEASON_EXCLUDE_VALUE = "зима"

EXCLUDED_BRANDS = [
    "Mazzini", "Nexen", "MAXXIS", "Predator", "Compasal", "HIFLY", "Aoteli",
    "Torero", "Viatti", "Massimo", "Firemax", "Sonix", "Prinx", "Roadmarch",
    "Kelly", "Nitto", "Кама"
]
EXCLUDED_CATEGORY = ["Грузовая"]

EXCLUDED_ARTICLES = [
    "195/75R16C Laufenn X FIT VAN LV01",
    "АТ27x8-12 MAXXIS M961 6PR",
    "185/75R16C Ikon Autograph Snow C4",
    "33x10,50-16 NORTEC ET-500 111N TL АШК",
]

# ===================== КОЭФФИЦИЕНТЫ ЦЕН =====================
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

# ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====================
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

def get_new_image_url(item):
    """
    Возвращает список возможных URL для изображения.
    Первый элемент — с размерами (если удалось получить), второй — короткий (бренд_модель).
    Если размеров нет, список содержит только короткий.
    """
    brand = item.get("brand", "").strip()
    model = item.get("model", "").strip()
    if not brand or not model:
        return []

    def clean(s):
        return re.sub(r'[^\w\-]', '_', s)

    urls = []

    # 1) Попытка с размерами
    width = item.get("width", "")
    profile = item.get("profile", "") or item.get("height", "")
    diameter = item.get("diameter", "")
    if width and profile and diameter:
        filename = f"{width}_{profile}_{diameter}_{clean(brand)}_{clean(model)}.jpg"
        urls.append(IMAGE_BASE_URL + filename)

    # 2) Попытка извлечь размер из Номенклатура
    nomenclature = item.get("Номенклатура", "")
    match = re.search(r'(\d+)/(\d+)[Zz]?[Rr](\d+)', nomenclature)
    if match:
        width, profile, diameter = match.groups()
        filename = f"{width}_{profile}_{diameter}_{clean(brand)}_{clean(model)}.jpg"
        # Добавляем только если ещё не добавлен (избегаем дублирования)
        if not urls or urls[0] != IMAGE_BASE_URL + filename:
            urls.append(IMAGE_BASE_URL + filename)

    # 3) Короткое имя (бренд_модель) — всегда добавляем
    short_filename = f"{clean(brand)}_{clean(model)}.jpg"
    urls.append(IMAGE_BASE_URL + short_filename)

    return urls

# ===================== КЭШ =====================
def load_image_cache():
    if os.path.exists(IMAGE_CACHE_FILE):
        try:
            with open(IMAGE_CACHE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_image_cache(cache):
    try:
        with open(IMAGE_CACHE_FILE, 'w') as f:
            json.dump(cache, f)
    except:
        pass

def check_image_exists(url, cache):
    if url in cache:
        return cache[url]
    try:
        response = requests.head(url, timeout=2, allow_redirects=True)
        exists = response.status_code == 200
    except:
        exists = False
    cache[url] = exists
    return exists

# ===================== ОСНОВНАЯ ЛОГИКА =====================
auth = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
response = requests.get(API_URL, headers={"Authorization": f"Basic {auth}"})
response.raise_for_status()
data = response.json()

print("🔄 Загрузка данных завершена. Обработка...")

# --- Первый проход: фильтрация и сбор уникальных URL ---
valid_items = []
unique_urls = set()
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

    if IMAGE_REPLACE_ENABLED:
        new_url = get_new_image_url(item)
        if new_url:
            unique_urls.add(new_url)

# --- Загрузка / обновление кэша ---
image_cache = {}
check_time = 0
if IMAGE_REPLACE_ENABLED and IMAGE_CHECK_ENABLED:
    if IMAGE_CACHE_REFRESH and os.path.exists(IMAGE_CACHE_FILE):
        os.remove(IMAGE_CACHE_FILE)
        print("🔁 Кэш принудительно удалён (IMAGE_CACHE_REFRESH=True).")
    image_cache = load_image_cache()
    print(f"🔍 Загружено {len(image_cache)} записей из кэша.")
    new_urls = [url for url in unique_urls if url not in image_cache]
    if new_urls:
        print(f"🔍 Требуется проверить {len(new_urls)} новых URL...")
        check_start = time.time()

        def check_url(url):
            try:
                response = requests.head(url, timeout=2, allow_redirects=True)
                return url, response.status_code == 200
            except:
                return url, False

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(check_url, url) for url in new_urls]
            for future in as_completed(futures):
                url, exists = future.result()
                image_cache[url] = exists

        check_time = time.time() - check_start
        print(f"✅ Проверка завершена за {check_time:.2f} сек.")
        save_image_cache(image_cache)
    else:
        print("✅ Все URL уже есть в кэше, проверка не требуется.")
else:
    print("🔍 Проверка существования файлов отключена (IMAGE_CHECK_ENABLED=False)")

# --- Второй проход: создание XML ---
root = ET.Element("Products")
extra_roots = {
    '15': ET.Element("Products"),
    '16': ET.Element("Products"),
    '17': ET.Element("Products"),
    '18': ET.Element("Products"),
    '19_20': ET.Element("Products"),
    '21_24': ET.Element("Products"),
}

main_file_count = 0
diameter_count = {}

def add_product_to_root(root, item, diameter):
    product = ET.SubElement(root, "Product")
    for key, value in item.items():
        if key == "Оптовая_Цена":
            continue
        if key == "price" and not INCLUDE_PRICE_TAG:
            continue

        if key == "img" and IMAGE_REPLACE_ENABLED:
            possible_urls = get_new_image_url(item)
            new_url = None
            for url in possible_urls:
                if IMAGE_CHECK_ENABLED:
                    # Проверяем существование по кэшу
                    if image_cache.get(url, False):
                        new_url = url
                        break
                else:
                    # Если проверка отключена, берём первый (с размерами) если есть, иначе короткий
                    # Но логика: если есть список, берём первый, который не None
                    new_url = url
                    break
            if new_url:
                value = new_url

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

for item, diameter in valid_items:
    if diameter is not None:
        d_int = int(diameter)
        diameter_count[d_int] = diameter_count.get(d_int, 0) + 1
    else:
        diameter_count['unknown'] = diameter_count.get('unknown', 0) + 1

    if diameter not in (12, 13, 14):
        add_product_to_root(root, item, diameter)
        main_file_count += 1

    if diameter is not None:
        if diameter == 15:
            add_product_to_root(extra_roots['15'], item, diameter)
        elif diameter == 16:
            add_product_to_root(extra_roots['16'], item, diameter)
        elif diameter == 17:
            add_product_to_root(extra_roots['17'], item, diameter)
        elif diameter == 18:
            add_product_to_root(extra_roots['18'], item, diameter)
        elif 19 <= diameter <= 20:
            add_product_to_root(extra_roots['19_20'], item, diameter)
        elif 21 <= diameter <= 24:
            add_product_to_root(extra_roots['21_24'], item, diameter)

tree = ET.ElementTree(root)
with open("aztyre2.xml", "wb") as file:
    tree.write(file, encoding="utf-8", xml_declaration=True)

file_names = {
    '15': "ztyrer15.xml",
    '16': "ztyrer16.xml",
    '17': "ztyrer17.xml",
    '18': "ztyrer18.xml",
    '19_20': "ztyrer1920.xml",
    '21_24': "ztyrer2024.xml",
}
for key, xml_root in extra_roots.items():
    if len(xml_root) > 0:
        tree = ET.ElementTree(xml_root)
        with open(file_names[key], "wb") as file:
            tree.write(file, encoding="utf-8", xml_declaration=True)

print(f"\n✅ XML файлы успешно созданы.")
print(f"   - Пропущено (ЗБ): {excluded_zb}")
print(f"   - Исключено по артикулу: {excluded_article}")
if SEASON_EXCLUDE_ENABLED:
    print(f"   - Исключено по сезону ({SEASON_EXCLUDE_VALUE}): {excluded_season}")
print(f"   - Всего товаров, прошедших фильтры: {total_products}")
print(f"   - Из них в основном файле aztyre2.xml: {main_file_count} (исключены диаметры 12, 13, 14)")
if IMAGE_REPLACE_ENABLED and IMAGE_CHECK_ENABLED:
    print(f"   - Проверено URL: {len(unique_urls)} (из них новых: {len(new_urls) if 'new_urls' in locals() else 0}) за {check_time:.2f} сек")
else:
    print(f"   - Проверка URL отключена (IMAGE_CHECK_ENABLED=False)")
print(f"\n📊 Статистика по диаметрам:")
if diameter_count:
    for d in sorted([k for k in diameter_count if k != 'unknown']):
        print(f"   - {d}\" : {diameter_count[d]} шт.")
    if 'unknown' in diameter_count:
        print(f"   - диаметр не определён : {diameter_count['unknown']} шт.")
else:
    print("   (нет данных)")
