import requests
import xml.etree.ElementTree as ET
import re
import os
import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

# ===================== НАСТРОЙКИ (из первого файла) =====================
ROUND_STEP = 10
ROUND_METHOD = 'nearest'
INCLUDE_PRICE_TAG = False

# Замена изображений
IMAGE_REPLACE_ENABLED = True
IMAGE_CHECK_ENABLED = True
IMAGE_BASE_URL = "https://s3.ru1.storage.beget.cloud/fa5a823588a1-adromavito/images"
IMAGE_CACHE_FILE = "image_cache_4tochki.json"
IMAGE_CACHE_REFRESH = os.getenv("IMAGE_CACHE_REFRESH", "false").lower() == "true"
MAX_WORKERS = 50
HEAD_TIMEOUT = 1

# Фильтры
SEASON_EXCLUDE_ENABLED = False
SEASON_EXCLUDE_VALUE = "зима"

EXCLUDED_BRANDS = [
    "Mazzini", "Nexen", "MAXXIS", "Predator", "Compasal", "HIFLY", "Aoteli",
    "Torero", "Viatti", "Massimo", "Firemax", "Sonix", "Prinx", "Roadmarch",
    "Kelly", "Nitto", "Кама"
]
EXCLUDED_BRANDS_FROM_EXPORT = ["Compasal", "Aoteli"]
EXCLUDED_CATEGORY = ["Грузовая"]
EXCLUDED_ARTICLES = []  # можно заполнить при необходимости

# Коэффициенты цен
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
    brand = item.get("brand", "").strip()
    model = item.get("model", "").strip()
    if not brand or not model:
        return []
    brand_clean = clean_name(brand)
    model_clean = clean_name(model)
    short_filename = f"{brand_clean}_{model_clean}.jpg"
    return [f"{IMAGE_BASE_URL}/{brand_clean}/{short_filename}"]

def get_additional_image_urls(item):
    brand = item.get("brand", "").strip()
    model = item.get("model", "").strip()
    if not brand or not model:
        return []
    brand_clean = clean_name(brand)
    model_clean = clean_name(model)

    width = item.get("width", "")
    profile = item.get("height", "")
    diameter = item.get("diameter", "")
    if not (width and profile and diameter):
        name = item.get("name", "")
        match = re.search(r'(\d+)/(\d+)[Zz]?[Rr](\d+)', name)
        if match:
            width, profile, diameter = match.groups()
        else:
            return []

    base_filename = f"{width}_{profile}_{diameter}_{brand_clean}_{model_clean}"
    urls = []
    for i in range(1, 5):
        urls.append(f"{IMAGE_BASE_URL}/{brand_clean}/{base_filename}_{i}.jpg")
    return urls

def get_all_image_urls(item):
    return get_base_image_urls(item) + get_additional_image_urls(item)

# ===================== КЭШ ИЗОБРАЖЕНИЙ =====================
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
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.head(url, timeout=HEAD_TIMEOUT, headers=headers)
        exists = response.status_code == 200
    except:
        exists = False
    cache[url] = exists
    return exists

# ===================== ДОБАВЛЕНИЕ ТОВАРА В XML (только шины) =====================
def add_tyre_to_root(root, item, diameter, replace_images=True, image_cache=None):
    """Добавляет только шины (product.tag всегда 'tyres')"""
    product = ET.SubElement(root, "tyres")   # явно задаём tyres

    for key, value in item.items():
        if key in ("Оптовая_Цена", "img"):
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

            original_retail = safe_float(value)
            apply_coeff = brand not in [b.lower() for b in IGNORE_COEFF_BRANDS]

            final_price = None
            try:
                if is_excluded or not apply_coeff:
                    final_price = original_retail
                else:
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

    # --- ФОРМИРОВАНИЕ ТЕГА IMAGES ---
    if replace_images and IMAGE_REPLACE_ENABLED:
        brand = item.get("brand", "").strip()
        model = item.get("model", "").strip()
        fallback_url = item.get("img_small", "") or item.get("img_big_my", "")

        s3_urls = []
        short_url = None
        if brand and model:
            brand_clean = clean_name(brand)
            model_clean = clean_name(model)
            short_url = f"{IMAGE_BASE_URL}/{brand_clean}/{brand_clean}_{model_clean}.jpg"
            s3_urls.append(short_url)
            s3_urls.extend(get_additional_image_urls(item))

        existing_s3 = {}
        if IMAGE_CHECK_ENABLED and image_cache is not None:
            for url in s3_urls:
                exists = image_cache.get(url, False)
                if exists:
                    filename = url[url.rfind('/')+1:] if '/' in url else url
                    existing_s3[url] = filename
        else:
            if short_url:
                filename = short_url[short_url.rfind('/')+1:] if '/' in short_url else short_url
                existing_s3[short_url] = filename

        main_url = None
        main_filename = None
        if short_url and short_url in existing_s3:
            main_url = short_url
            main_filename = existing_s3[short_url]
        elif fallback_url:
            main_url = fallback_url
            main_filename = fallback_url[fallback_url.rfind('/')+1:] if '/' in fallback_url else fallback_url

        if main_url:
            if short_url and short_url in existing_s3:
                photo_dir = main_url[:main_url.rfind('/')+1]
            else:
                photo_dir = main_url[:main_url.rfind('/')+1] if '/' in main_url else ""

            additional_filenames = []
            for url, fname in existing_s3.items():
                if url != main_url and fname not in additional_filenames:
                    additional_filenames.append(fname)

            images_elem = ET.SubElement(product, "Images")
            images_elem.set("PhotoDir", photo_dir)
            images_elem.set("PhotoMain", main_filename)

            for fname in additional_filenames:
                img_elem = ET.SubElement(images_elem, "Image")
                img_elem.set("name", fname)

    # Дополнительные теги
    inSet_elem = ET.SubElement(product, "inSet")
    inSet_elem.text = "1"

# ===================== ЗАГРУЗКА И НОРМАЛИЗАЦИЯ ИСХОДНЫХ ДАННЫХ =====================
def fetch_xml(url):
    response = requests.get(url)
    response.raise_for_status()
    return ET.fromstring(response.content)

def normalize_fields(elem):
    field_map = {
        "vendor_code": "cae",
        "product_id": "article",
        "countAll": "rest",
        "stockName": "stock",
        "shirina_secheniya": "width",
        "visota_secheniya": "height",
        "radius": "diameter",
        "seasonality": "season",
        "categoryname": "model",
        "priceOpt": "opt",
        "price": "price",
        "spikes": "thorn",
        "img_big_my": "img_small",
        "proizvoditel": "brand",
        "tiretype": "category",
        "rest_novosib3": "rest_nsk",
    }
    normalized = {}
    for child in elem:
        tag = field_map.get(child.tag, child.tag)
        normalized[tag] = child.text.strip() if child.text else ""
    return normalized

def is_tyre(item, diameter):
    """Определяет, является ли товар шиной (не диском)"""
    width = item.get("width", "")
    height = item.get("height", "")
    diam = item.get("diameter", "")
    # Если есть все три размера и диаметр ≤ 22 (типичный легковой/грузовой шинный диапазон) – считаем шиной
    if width and height and diam:
        try:
            d = float(diam)
            if d <= 22:
                return True
        except:
            pass
    # Дополнительная проверка: если в категории написано "Легковая" или "Грузовая" – шина
    category = item.get("category", "")
    if category in ("Легковая", "Грузовая"):
        return True
    return False

# ===================== ОСНОВНАЯ ЛОГИКА =====================
def process_and_save(api_url, output_file, filter_tag=None, include_tag=None, include_value=None, status=None):
    root = fetch_xml(api_url)
    all_items = []
    unique_urls = set()

    for elem in root.findall(".//item"):
        norm = normalize_fields(elem)

        if include_tag and include_value:
            val = norm.get(include_tag, "")
            if val != include_value:
                continue

        if filter_tag:
            rest_val = norm.get(filter_tag, "")
            if not rest_val or safe_float(rest_val) <= 0:
                continue

        brand = norm.get("brand", "")
        if brand in EXCLUDED_BRANDS_FROM_EXPORT:
            continue

        article = norm.get("article", "")
        if any(phrase in article for phrase in EXCLUDED_ARTICLES):
            continue

        if SEASON_EXCLUDE_ENABLED:
            season = norm.get("season", "")
            if season == SEASON_EXCLUDE_VALUE:
                continue

        # Извлечение диаметра
        diameter = safe_float(norm.get("diameter"), default=None)
        if diameter is None:
            name = norm.get("name", "")
            match = re.search(r'[Rr](\d{2})', name)
            if match:
                diameter = float(match.group(1))

        # Проверка, что товар — шина (отсеиваем диски)
        if not is_tyre(norm, diameter):
            continue

        if status:
            norm["status"] = status

        all_items.append((norm, diameter))

        if IMAGE_REPLACE_ENABLED:
            for url in get_all_image_urls(norm):
                unique_urls.add(url)

    print(f"📦 Загружено товаров после фильтрации (только шины): {len(all_items)}")
    print(f"🖼 Уникальных URL для проверки: {len(unique_urls)}")

    # Проверка изображений (кэш)
    image_cache = {}
    check_time = 0
    if IMAGE_REPLACE_ENABLED and IMAGE_CHECK_ENABLED:
        if IMAGE_CACHE_REFRESH and os.path.exists(IMAGE_CACHE_FILE):
            os.remove(IMAGE_CACHE_FILE)
            print("🔁 Кэш принудительно удалён.")
        image_cache = load_image_cache()
        print(f"📂 Загружено {len(image_cache)} записей из кэша.")
        new_urls = [url for url in unique_urls if url not in image_cache]
        if new_urls:
            print(f"🔍 Требуется проверить {len(new_urls)} новых URL...")
            start = time.time()
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [executor.submit(check_image_exists, url, image_cache) for url in new_urls]
                for future in as_completed(futures):
                    future.result()
            check_time = time.time() - start
            print(f"✅ Проверка завершена за {check_time:.2f} сек.")
            save_image_cache(image_cache)
        else:
            print("✅ Все URL уже в кэше.")
    else:
        print("🔍 Проверка изображений отключена.")

    # Создание XML только с шинами
    root_out = ET.Element("Products")
    for item, diameter in all_items:
        add_tyre_to_root(root_out, item, diameter,
                         replace_images=IMAGE_REPLACE_ENABLED,
                         image_cache=image_cache if IMAGE_CHECK_ENABLED else None)

    tree = ET.ElementTree(root_out)
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    print(f"✅ Файл сохранён: {output_file} (шин: {len(all_items)})")
    return all_items

def main():
    url = "https://b2b.4tochki.ru/export_data/M35352.xml"

    process_and_save(url, "4tyre_test.xml",
                     filter_tag=None,
                     include_tag="tiretype", include_value="Легковая",
                     status="Под заказ")

    process_and_save(url, "tyres_nsk.xml",
                     filter_tag="rest_nsk",
                     include_tag="tiretype", include_value="Легковая",
                     status="В наличии")

    process_and_save(url, "tyres_gruz.xml",
                     filter_tag=None,
                     include_tag="tiretype", include_value="Грузовая",
                     status="Под заказ")

    print("\n✅ Все XML файлы успешно созданы (только шины).")

if __name__ == "__main__":
    main()
