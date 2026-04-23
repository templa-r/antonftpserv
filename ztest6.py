import os
import requests
import xml.etree.ElementTree as ET
import re
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional, List, Tuple

# ===================== НАСТРОЙКИ =====================
ROUND_STEP = 10
ROUND_METHOD = 'nearest'
INCLUDE_PRICE_TAG = False

# Изображения S3
IMAGE_REPLACE_ENABLED = True
IMAGE_CHECK_ENABLED = True
IMAGE_BASE_URL = "https://s3.ru1.storage.beget.cloud/fa5a823588a1-antontyres/images"
IMAGE_CACHE_FILE = "image_cache.json"
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
EXCLUDED_ARTICLES = []

# Коэффициенты
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
    step = step or ROUND_STEP
    method = method or ROUND_METHOD
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

# ===================== ФУНКЦИИ ДЛЯ ИЗОБРАЖЕНИЙ =====================
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
    # Убираем букву R из диаметра
    diameter = re.sub(r'[^0-9]', '', str(diameter))
    base_filename = f"{width}_{profile}_{diameter}_{brand_clean}_{model_clean}"
    return [f"{IMAGE_BASE_URL}/{brand_clean}/{base_filename}_{i}.jpg" for i in range(1, 5)]

def get_all_image_urls(item):
    return get_base_image_urls(item) + get_additional_image_urls(item)

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

# ===================== ЗАГРУЗКА И НОРМАЛИЗАЦИЯ =====================
def fetch_xml(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers)
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
        "img_big_my": "img_big_my",
        "img_small": "img_small",
        "img_big_pish": "img_big_pish",
        "proizvoditel": "brand",
        "brand": "brand",
        "tiretype": "category",
        "rest_novosib3": "rest_nsk",
        "price_novosib3_rozn": "price_novosib3_rozn",
        "name": "name",
        "article": "article",
        "diameter": "diameter",
        "width": "width",
        "height": "height",
        "season": "season",
        "model": "model",
        "retail": "retail",
        "opt": "opt",
    }
    normalized = {}
    for child in elem:
        tag = field_map.get(child.tag, child.tag)
        normalized[tag] = child.text.strip() if child.text else ""
    return normalized

def is_tyre(item, diameter):
    category = item.get("category", "")
    if category in ("Легковая", "Грузовая"):
        return True
    width = item.get("width", "")
    height = item.get("height", "")
    diam = item.get("diameter", "")
    return bool(width and height and diam)

# ===================== ПОДГОТОВКА NSK-ТОВАРА =====================
def prepare_nsk_item(original_item: Dict) -> Dict:
    """Создаёт словарь только с нужными полями, переименовывая rest_nsk→rest, price_novosib3_rozn→retail"""
    new_item = {}
    new_item['rest'] = original_item.get('rest_nsk', '0')
    new_item['retail'] = original_item.get('price_novosib3_rozn', '0')
    
    allowed_fields = [
        'brand', 'model', 'width', 'height', 'diameter', 'load_index', 'speed_index',
        'season', 'category', 'camera', 'name', 'noise', 'design', 'gtin', 'weight', 'volume'
    ]
    for field in allowed_fields:
        if field in original_item:
            new_item[field] = original_item[field]
    
    # Сохраняем fallback-изображение (оригинальное img_big_my)
    if 'img_big_my' in original_item:
        new_item['_fallback_img'] = original_item['img_big_my']
    
    return new_item

# ===================== ДОБАВЛЕНИЕ В XML ДЛЯ NSK (С ЗАМЕНОЙ ИЗОБРАЖЕНИЙ) =====================
def add_tyre_to_root_nsk(root, item: Dict, diameter: float, image_cache: Optional[Dict] = None):
    product = ET.SubElement(root, "tyres")
    output_data = dict(item)
    fallback_img = output_data.pop('_fallback_img', None)
    
    # Вывод обычных полей (теги)
    for key, value in output_data.items():
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
            
            step, method = ROUND_STEP, ROUND_METHOD
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
    
    # --- БЛОК IMAGES (замена на S3) ---
    if IMAGE_REPLACE_ENABLED:
        brand = item.get("brand", "").strip()
        model = item.get("model", "").strip()
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
        elif fallback_img:
            main_url = fallback_img
            main_filename = main_url[main_url.rfind('/')+1:] if '/' in main_url else main_url
        
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
            # Основное фото
            img_main = ET.SubElement(images_elem, "Image")
            img_main.set("name", main_filename)
            # Дополнительные фото
            for fname in additional_filenames:
                img_elem = ET.SubElement(images_elem, "Image")
                img_elem.set("name", fname)
    
    # Всегда добавляем inSet
    inSet_elem = ET.SubElement(product, "inSet")
    inSet_elem.text = "1"

def save_nsk_tyres_to_xml(items_with_diam: List[Tuple[Dict, float]], output_file: str, image_cache: Optional[Dict]):
    root_out = ET.Element("Products")
    for item, diameter in items_with_diam:
        add_tyre_to_root_nsk(root_out, item, diameter, image_cache)
    tree = ET.ElementTree(root_out)
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    print(f"✅ Файл сохранён: {output_file} (шин: {len(items_with_diam)})")

# ===================== ОСНОВНАЯ ЛОГИКА =====================
def main():
    url = "https://b2b.4tochki.ru/export_data/M35352.xml"
    print("Загрузка данных...")
    root_xml = fetch_xml(url)
    all_elements = root_xml.findall(".//tires")
    print(f"Найдено элементов tires: {len(all_elements)}")
    
    nsk_items_raw = []   # (item, diameter)
    order_items_raw = []
    
    for elem in all_elements:
        item = normalize_fields(elem)
        
        # Фильтры
        brand = item.get("brand", "")
        if brand in EXCLUDED_BRANDS_FROM_EXPORT:
            continue
        article = item.get("article", "")
        if any(phrase in article for phrase in EXCLUDED_ARTICLES):
            continue
        if SEASON_EXCLUDE_ENABLED and item.get("season", "") == SEASON_EXCLUDE_VALUE:
            continue
        
        # Диаметр
        diameter = safe_float(item.get("diameter"), default=None)
        if diameter is None:
            name = item.get("name", "")
            match = re.search(r'[Rr](\d{2})', name)
            if match:
                diameter = float(match.group(1))
        
        if not is_tyre(item, diameter):
            continue
        
        rest_nsk = safe_float(item.get("rest_nsk", 0))
        price_nsk = item.get("price_novosib3_rozn", "").strip()
        has_nsk_stock = (rest_nsk > 0) and (price_nsk != "")
        
        if has_nsk_stock:
            nsk_items_raw.append((item, diameter))
        else:
            # Для "под заказ" удаляем поля NSK
            clean_item = {k: v for k, v in item.items() if k not in ['rest_nsk', 'price_novosib3_rozn']}
            order_items_raw.append((clean_item, diameter))
    
    print(f"📦 Товаров с наличием в НСК: {len(nsk_items_raw)}")
    print(f"📦 Товаров под заказ: {len(order_items_raw)}")
    
    # --- КЭШ ИЗОБРАЖЕНИЙ (только для NSK) ---
    image_cache = {}
    if IMAGE_REPLACE_ENABLED and IMAGE_CHECK_ENABLED and nsk_items_raw:
        if IMAGE_CACHE_REFRESH and os.path.exists(IMAGE_CACHE_FILE):
            os.remove(IMAGE_CACHE_FILE)
            print("🔁 Кэш принудительно удалён.")
        image_cache = load_image_cache()
        print(f"📂 Загружено {len(image_cache)} записей из кэша.")
        # Собираем все URL для NSK товаров
        unique_urls = set()
        for item, _ in nsk_items_raw:
            for url in get_all_image_urls(item):
                unique_urls.add(url)
        new_urls = [url for url in unique_urls if url not in image_cache]
        if new_urls:
            print(f"🔍 Требуется проверить {len(new_urls)} новых URL...")
            start = time.time()
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [executor.submit(check_image_exists, url, image_cache) for url in new_urls]
                for future in as_completed(futures):
                    future.result()
            print(f"✅ Проверка завершена за {time.time() - start:.2f} сек.")
            save_image_cache(image_cache)
        else:
            print("✅ Все URL уже в кэше, проверка не требуется.")
    
    # Подготовка NSK товаров (очищенные поля)
    nsk_items = [(prepare_nsk_item(item), diam) for item, diam in nsk_items_raw]
    if nsk_items:
        save_nsk_tyres_to_xml(nsk_items, "4tyres_nsk2.xml", image_cache if IMAGE_CHECK_ENABLED else None)
    else:
        print("Нет товаров с наличием в НСК — файл 4tyres_nsk2.xml не создан.")
    
    # --- Файл "под заказ" (без замены изображений) ---
    if order_items_raw:
        root_out = ET.Element("Products")
        for item, diam in order_items_raw:
            product = ET.SubElement(root_out, "tyres")
            for key, value in item.items():
                if key in ("Оптовая_Цена", "img"):
                    continue
                if key == "price" and not INCLUDE_PRICE_TAG:
                    continue
                element = ET.SubElement(product, key)
                element.text = str(value)
            # Не создаём Images, оставляем оригинальные img_* поля, если они есть
            inSet_elem = ET.SubElement(product, "inSet")
            inSet_elem.text = "1"
        tree = ET.ElementTree(root_out)
        tree.write("4tyre_test2.xml", encoding="utf-8", xml_declaration=True)
        print(f"✅ Файл сохранён: 4tyre_test2.xml (шин: {len(order_items_raw)})")
    else:
        print("Нет товаров под заказ — файл 4tyre_test2.xml не создан.")
    
    print("\n✅ Все XML файлы успешно созданы.")

if __name__ == "__main__":
    main()
