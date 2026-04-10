import os
import requests
import base64
import xml.etree.ElementTree as ET
import re
import json
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

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
IMAGE_BASE_URL = "https://s3.ru1.storage.beget.cloud/fa5a823588a1-adromavito/images"
IMAGE_CACHE_FILE = "image_cache.json"
IMAGE_CACHE_REFRESH = os.getenv("IMAGE_CACHE_REFRESH", "false").lower() == "true"

MAX_WORKERS = 1000
HEAD_TIMEOUT = 1

# ===================== ФИЛЬТРЫ =====================
SEASON_EXCLUDE_ENABLED = False
SEASON_EXCLUDE_VALUE = "зима"

EXCLUDED_BRANDS = [
    "Mazzini", "Nexen", "MAXXIS", "Predator", "Compasal", "HIFLY", "Aoteli",
    "Torero", "Viatti", "Massimo", "Firemax", "Sonix", "Prinx", "Roadmarch",
    "Kelly", "Nitto", "Кама"
]

EXCLUDED_BRANDS_FROM_EXPORT = ["Compasal", "Aoteli"]

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

IGNORE_COEFF_BRANDS = ["Mazzini", "Nexen", "MAXXIS", "Sonix"]
MIN_MARGIN = 500

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

def clean_name(s):
    return re.sub(r'[^\w\-]', '_', s)

def get_base_image_urls(item):
    """Возвращает список возможных URL для основного изображения (приоритет: исходное из API, затем короткое имя)."""
    original_img = item.get("img", "").strip()
    if original_img:
        return [original_img]
    # Если исходного нет, используем короткое имя
    brand = item.get("brand", "").strip()
    model = item.get("model", "").strip()
    if not brand or not model:
        return []
    brand_clean = clean_name(brand)
    model_clean = clean_name(model)
    short_filename = f"{brand_clean}_{model_clean}.jpg"
    return [f"{IMAGE_BASE_URL}/{brand_clean}/{short_filename}"]

def get_additional_image_urls(item):
    """Возвращает список URL дополнительных изображений (на основе размера с суффиксами 1-4)."""
    brand = item.get("brand", "").strip()
    model = item.get("model", "").strip()
    if not brand or not model:
        return []
    brand_clean = clean_name(brand)
    model_clean = clean_name(model)

    # Пытаемся получить размер
    width = item.get("width", "")
    profile = item.get("profile", "") or item.get("height", "")
    diameter = item.get("diameter", "")
    if not (width and profile and diameter):
        # Если нет отдельных полей, пробуем из номенклатуры
        nomenclature = item.get("Номенклатура", "")
        match = re.search(r'(\d+)/(\d+)[Zz]?[Rr](\d+)', nomenclature)
        if match:
            width, profile, diameter = match.groups()
        else:
            return []  # нет размера – нет дополнительных фото

    base_filename = f"{width}_{profile}_{diameter}_{brand_clean}_{model_clean}"
    urls = []
    for i in range(1, 5):
        urls.append(f"{IMAGE_BASE_URL}/{brand_clean}/{base_filename}_{i}.jpg")
    return urls

def get_all_image_urls(item):
    """Все возможные URL для кэширования (основные + дополнительные)."""
    return get_base_image_urls(item) + get_additional_image_urls(item)

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
        response = requests.head(url, timeout=HEAD_TIMEOUT, allow_redirects=True)
        exists = response.status_code == 200
    except:
        exists = False
    cache[url] = exists
    return exists

# ===================== ДОБАВЛЕНИЕ ТОВАРА В XML =====================
def add_product_to_root(root, item, diameter, replace_images=True, image_cache=None):
    product = ET.SubElement(root, "Product")

    # Обработка всех полей, кроме img (img обработаем отдельно)
    for key, value in item.items():
        if key == "Оптовая_Цена":
            continue
        if key == "price" and not INCLUDE_PRICE_TAG:
            continue
        if key == "img":
            continue  # пропускаем исходный img, чтобы не дублировать

        element = ET.SubElement(product, key)

        if key.lower() == "retail":
            brand = item.get("brand", "").strip().lower()
            model = item.get("model", "").strip().lower()
            category = item.get("category", "")
            is_excluded = (brand in [b.lower() for b in EXCLUDED_BRANDS] or
                           category in EXCLUDED_CATEGORY)

            original_retail = safe_float(value)

            apply_coeff = True
            if brand in [b.lower() for b in IGNORE_COEFF_BRANDS]:
                apply_coeff = False

            final_price = None
            try:
                if is_excluded or not apply_coeff:
                    final_price = original_retail
                else:
                    # Вычисляем скорректированную розничную цену
                    rule = MODEL_RULES.get((brand, model))
                    if rule:
                        rule_type = rule["type"]
                        if rule_type == "fixed":
                            adjusted_retail = safe_float(rule["value"])
                        elif rule_type == "add_to_field":
                            base_val = safe_float(item.get(rule["field"], "0"))
                            adjusted_retail = base_val + rule["value"]
                        elif rule_type == "add_to_field_by_diameter":
                            if diameter is not None:
                                add_value = None
                                for r in rule["ranges"]:
                                    if r["min"] <= diameter <= r["max"]:
                                        add_value = r["value"]
                                        break
                                if add_value is not None:
                                    base_val = safe_float(item.get(rule["field"], "0"))
                                    adjusted_retail = base_val + add_value
                                else:
                                    if rule.get("default") is not None:
                                        base_val = safe_float(item.get(rule["field"], "0"))
                                        adjusted_retail = base_val + rule["default"]
                                    else:
                                        adjusted_retail = original_retail
                            else:
                                adjusted_retail = original_retail
                        else:
                            adjusted_retail = original_retail
                    else:
                        brand_settings = BRAND_COEFFS.get(brand)
                        if brand_settings is not None:
                            coeff, _, _ = get_coeff_from_settings(brand_settings, diameter)
                        else:
                            coeff, _, _ = get_coeff_from_settings(GLOBAL_COEFF, diameter)
                        adjusted_retail = original_retail * coeff

                    price_val = safe_float(item.get("price", 0))
                    if price_val > 0:
                        margin = adjusted_retail - price_val
                        if margin < MIN_MARGIN:
                            final_price = original_retail
                        else:
                            final_price = adjusted_retail
                    else:
                        final_price = adjusted_retail
            except Exception:
                final_price = original_retail

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

    # --- НОВАЯ ЛОГИКА ФОРМИРОВАНИЯ IMAGES (заменяет исходный img) ---
    if replace_images and IMAGE_REPLACE_ENABLED:
        # 1) Определяем основное фото
        base_urls = get_base_image_urls(item)
        main_url = None
        if IMAGE_CHECK_ENABLED and image_cache is not None:
            for url in base_urls:
                if image_cache.get(url, False):
                    main_url = url
                    break
        else:
            main_url = base_urls[0] if base_urls else None

        if main_url:
            # Определяем общий базовый путь (PhotoDir)
            last_slash = main_url.rfind('/')
            if last_slash != -1:
                photo_dir = main_url[:last_slash+1]   # включая слеш
                main_filename = main_url[last_slash+1:]
            else:
                photo_dir = ""
                main_filename = main_url

            # Собираем все имена файлов (основное + дополнительные)
            filenames = [main_filename]

            # 2) Дополнительные фото (суффиксные)
            additional_urls = get_additional_image_urls(item)
            if IMAGE_CHECK_ENABLED and image_cache is not None:
                for url in additional_urls:
                    if image_cache.get(url, False):
                        if '/' in url:
                            filename = url[url.rfind('/')+1:]
                        else:
                            filename = url
                        filenames.append(filename)

            # Создаём элемент Images
            images_elem = ET.SubElement(product, "Images")
            images_elem.set("PhotoDir", photo_dir)
            # Добавляем все собранные имена как дочерние Image
            for fname in filenames:
                img_elem = ET.SubElement(images_elem, "Image")
                img_elem.set("name", fname)

    # Дополнительные теги
    inSet_elem = ET.SubElement(product, "inSet")
    inSet_elem.text = "1"

    if item.get("model") == "Nortec LT 610":
        studded_elem = ET.SubElement(product, "studded")
        studded_elem.text = "Нет"

    nomenclature = item.get("Номенклатура", "")
    if re.match(r'^(1[2-9]|2[0-4])\s', nomenclature):
        product.tag = "disk"
    else:
        product.tag = "tyres""
# ===================== ОСНОВНАЯ ЛОГИКА =====================
auth = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
response = requests.get(API_URL, headers={"Authorization": f"Basic {auth}"})
response.raise_for_status()
data = response.json()

print("🔄 Загрузка данных завершена. Обработка...")

# --- Первый проход: фильтрация и сбор уникальных URL ---
all_items = []
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
    all_items.append((item, diameter))

    if IMAGE_REPLACE_ENABLED:
        for url in get_all_image_urls(item):
            unique_urls.add(url)

print(f"🔍 Всего товаров после фильтров: {total_products}")
print(f"🔍 Уникальных URL для проверки: {len(unique_urls)}")

# --- Загрузка / обновление кэша изображений ---
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
                response = requests.head(url, timeout=HEAD_TIMEOUT, allow_redirects=True)
                return url, response.status_code == 200
            except:
                return url, False

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
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

# --- Статистика по брендам ---
brand_diameter_stats = defaultdict(lambda: defaultdict(lambda: {'sum': 0, 'count': 0}))
stats_count = 0

for item, diameter in all_items:
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

# --- Создание итогового XML ---
root = ET.Element("Products")
for item, diameter in all_items:
    add_product_to_root(root, item, diameter,
                        replace_images=True,
                        image_cache=image_cache if IMAGE_CHECK_ENABLED else None)

tree = ET.ElementTree(root)
with open("aztyre_full.xml", "wb") as file:
    tree.write(file, encoding="utf-8", xml_declaration=True)

print(f"✅ Итоговый XML файл создан: aztyre.xml, всего товаров: {total_products}")

print(f"\n✅ XML файл успешно создан.")
print(f"   - Пропущено (ЗБ): {excluded_zb}")
print(f"   - Исключено по артикулу: {excluded_article}")
if SEASON_EXCLUDE_ENABLED:
    print(f"   - Исключено по сезону ({SEASON_EXCLUDE_VALUE}): {excluded_season}")
print(f"   - Всего товаров в выгрузке: {total_products}")

if IMAGE_REPLACE_ENABLED and IMAGE_CHECK_ENABLED:
    print(f"   - Проверено URL: {len(unique_urls)} (из них новых: {len(new_urls) if 'new_urls' in locals() else 0}) за {check_time:.2f} сек")
else:
    print(f"   - Проверка URL отключена (IMAGE_CHECK_ENABLED=False)")
