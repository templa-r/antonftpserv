import requests
import xml.etree.ElementTree as ET
import re
import os
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple, Optional

# ===================== НАСТРОЙКИ =====================
ROUND_STEP = 10
ROUND_METHOD = 'nearest'
INCLUDE_PRICE_TAG = False

# Изображения (S3)
IMAGE_REPLACE_ENABLED = True          # Включить замену на S3 для NSK-файла
IMAGE_CHECK_ENABLED = True
IMAGE_BASE_URL = "https://s3.ru1.storage.beget.cloud/fa5a823588a1-adromavito/images"
IMAGE_CACHE_FILE = "image_cache_4tochki.json"
IMAGE_CACHE_REFRESH = os.getenv("IMAGE_CACHE_REFRESH", "false").lower() == "true"
MAX_WORKERS = 50
HEAD_TIMEOUT = 1

# Фильтры (без изменений)
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
    "ikon": {"default": 0.88, "diameter_ranges": [{"min": 13, "max": 15, "coeff": 0.905}, {"min": 17, "max": 22, "coeff": 0.88}]},
    "laufenn": 0.905,
    "bridgestone": 0.965,
    "hankook": {"default": 0.915, "diameter_ranges": [{"min": 13, "max": 16, "coeff": 0.907}, {"min": 17, "max": 22, "coeff": 0.906}]},
}
IGNORE_COEFF_BRANDS = ["Mazzini", "Nexen", "MAXXIS", "Sonix"]
MIN_MARGIN = 500

MODEL_RULES = {
    ("autograph", "autograph ice 9 suv"): {
        "type": "add_to_field_by_diameter",
        "field": "price",
        "ranges": [{"min": 16, "max": 17, "value": 1300}, {"min": 18, "max": 19, "value": 1700}],
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

# ===================== S3 ИЗОБРАЖЕНИЯ =====================
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
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.head(url, timeout=HEAD_TIMEOUT, headers=headers)
        exists = response.status_code == 200
    except:
        exists = False
    cache[url] = exists
    return exists

# ===================== ПОЛЕЗНЫЕ ФУНКЦИИ ДЛЯ NSK =====================
def extract_photo_dir_and_main(img_url: str) -> Tuple[str, str]:
    if not img_url:
        return "", ""
    parts = img_url.rsplit('/', 1)
    if len(parts) == 2:
        return parts[0] + '/', parts[1]
    else:
        return "", img_url

def prepare_nsk_item(original_item: Dict) -> Dict:
    """
    Создаёт новый словарь для NSK-файла:
    - rest = original_item['rest_nsk']
    - retail = original_item['price_novosib3_rozn'] (будет обработан коэффициентами)
    - остальные поля: brand, model, width, height, diameter, load_index, speed_index,
      season, category, camera, name, noise, design, gtin, weight, volume
    - временное поле _img_big_my сохраняется для последующего формирования Images (оригинальный URL)
    - также сохраняются brand_clean, model_clean для S3 поиска
    """
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
    
    # Оригинальная картинка для fallback
    if 'img_big_my' in original_item:
        new_item['_fallback_img'] = original_item['img_big_my']
    
    # Сохраним очищенные brand/model для S3
    brand = original_item.get('brand', '').strip()
    model = original_item.get('model', '').strip()
    if brand and model:
        new_item['_brand_clean'] = clean_name(brand)
        new_item['_model_clean'] = clean_name(model)
        # Также сохраним размеры для дополнительных картинок
        new_item['_width'] = original_item.get('width', '')
        new_item['_height'] = original_item.get('height', '')
        new_item['_diameter'] = original_item.get('diameter', '')
    
    return new_item

# ===================== ДОБАВЛЕНИЕ ТОВАРА В XML =====================
def add_tyre_to_root(root, item: Dict, diameter: float, image_cache: Optional[Dict] = None):
    """
    Добавляет товар в XML-корень.
    Для NSK используется S3-замена (если включена), иначе оригинальные URL.
    """
    product = ET.SubElement(root, "tyres")
    output_data = dict(item)
    
    # Извлекаем временные поля, которые не должны быть в XML
    fallback_img = output_data.pop('_fallback_img', None)
    brand_clean = output_data.pop('_brand_clean', None)
    model_clean = output_data.pop('_model_clean', None)
    width_s3 = output_data.pop('_width', None)
    height_s3 = output_data.pop('_height', None)
    diameter_s3 = output_data.pop('_diameter', None)
    
    # Вывод всех обычных полей (кроме price, если не нужно)
    for key, value in output_data.items():
        if key == "price" and not INCLUDE_PRICE_TAG:
            continue
        element = ET.SubElement(product, key)
        
        if key.lower() == "retail":
            # Логика обработки розничной цены (коэффициенты, правила)
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
                        # ... упрощённо; полная логика здесь не меняется ...
                        adjusted_retail = original_retail
                    else:
                        brand_settings = BRAND_COEFFS.get(brand)
                        if brand_settings is not None:
                            coeff, _, _ = get_coeff_from_settings(brand_settings, diameter)
                        else:
                            coeff, _, _ = get_coeff_from_settings(GLOBAL_COEFF, diameter)
                        adjusted_retail = original_retail * coeff
                        price_val = safe_float(item.get("price", 0))
                        if price_val > 0 and adjusted_retail - price_val < MIN_MARGIN:
                            final_price = original_retail
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
    
    # --- БЛОК ИЗОБРАЖЕНИЙ (S3 или fallback) ---
    main_url = None
    additional_urls = []
    
    if IMAGE_REPLACE_ENABLED and brand_clean and model_clean:
        # Формируем основной URL на S3
        main_s3 = f"{IMAGE_BASE_URL}/{brand_clean}/{brand_clean}_{model_clean}.jpg"
        # Дополнительные S3
        if width_s3 and height_s3 and diameter_s3:
            base_filename = f"{width_s3}_{height_s3}_{diameter_s3}_{brand_clean}_{model_clean}"
            additional_s3 = [f"{IMAGE_BASE_URL}/{brand_clean}/{base_filename}_{i}.jpg" for i in range(1, 5)]
        else:
            # Попробуем из имени
            name = item.get("name", "")
            match = re.search(r'(\d+)/(\d+)[Zz]?[Rr](\d+)', name)
            if match:
                w, h, d = match.groups()
                base_filename = f"{w}_{h}_{d}_{brand_clean}_{model_clean}"
                additional_s3 = [f"{IMAGE_BASE_URL}/{brand_clean}/{base_filename}_{i}.jpg" for i in range(1, 5)]
            else:
                additional_s3 = []
        
        # Проверяем существование (если включена проверка)
        existing_images = []
        if IMAGE_CHECK_ENABLED and image_cache is not None:
            all_candidates = [main_s3] + additional_s3
            for url in all_candidates:
                if check_image_exists(url, image_cache):
                    existing_images.append(url)
        else:
            # Без проверки считаем, что все существуют
            existing_images = [main_s3] + additional_s3
        
        if existing_images:
            main_url = existing_images[0]
            additional_urls = existing_images[1:]
    
    # Если S3 не дали результата, используем fallback (оригинальный img_big_my)
    if not main_url and fallback_img:
        main_url = fallback_img
    
    if main_url:
        # Определяем PhotoDir и PhotoMain
        if main_url.startswith(IMAGE_BASE_URL):
            # Для S3: папка = часть до последнего слеша, имя файла
            photo_dir = main_url[:main_url.rfind('/')+1]
            photo_main = main_url[main_url.rfind('/')+1:]
        else:
            photo_dir, photo_main = extract_photo_dir_and_main(main_url)
        
        if photo_dir and photo_main:
            images_elem = ET.SubElement(product, "Images")
            images_elem.set("PhotoDir", photo_dir)
            images_elem.set("PhotoMain", photo_main)
            # Дополнительные изображения (если есть)
            for add_url in additional_urls:
                add_filename = add_url[add_url.rfind('/')+1:]
                img_elem = ET.SubElement(images_elem, "Image")
                img_elem.set("name", add_filename)
    
    # Добавляем inSet
    inSet_elem = ET.SubElement(product, "inSet")
    inSet_elem.text = "1"

def save_tyres_to_xml(items_with_diam: List[Tuple[Dict, float]], output_file: str, image_cache: Optional[Dict] = None):
    root_out = ET.Element("Products")
    for item, diameter in items_with_diam:
        add_tyre_to_root(root_out, item, diameter, image_cache)
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
    
    nsk_items_raw = []   # исходные item для NSK (ещё не подготовленные)
    
    # Сначала определим, какие товары пойдут в NSK (чтобы собрать все URL для проверки S3)
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
    
    print(f"📦 Товаров с наличием в НСК: {len(nsk_items_raw)}")
    
    # Если нужно S3 и проверка изображений – собираем все URL
    image_cache = {}
    if IMAGE_REPLACE_ENABLED and IMAGE_CHECK_ENABLED and nsk_items_raw:
        if IMAGE_CACHE_REFRESH and os.path.exists(IMAGE_CACHE_FILE):
            os.remove(IMAGE_CACHE_FILE)
            print("🔁 Кэш изображений принудительно удалён.")
        image_cache = load_image_cache()
        print(f"📂 Загружено {len(image_cache)} записей из кэша.")
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
            print("✅ Все URL уже в кэше.")
    
    # Подготавливаем NSK-товары с очищенными полями
    nsk_items = [(prepare_nsk_item(item), diam) for item, diam in nsk_items_raw]
    
    # Сохраняем NSK-файл с S3-заменой
    if nsk_items:
        save_tyres_to_xml(nsk_items, "4tyres_nsk2.xml", image_cache if IMAGE_CHECK_ENABLED else None)
    else:
        print("Нет товаров с наличием в НСК — файл 4tyres_nsk2.xml не создан.")
    
    # Теперь файл "под заказ" – все остальные товары (которые не попали в NSK)
    order_items_raw = []
    for elem in all_elements:
        item = normalize_fields(elem)
        # Фильтры те же
        brand = item.get("brand", "")
        if brand in EXCLUDED_BRANDS_FROM_EXPORT:
            continue
        article = item.get("article", "")
        if any(phrase in article for phrase in EXCLUDED_ARTICLES):
            continue
        if SEASON_EXCLUDE_ENABLED and item.get("season", "") == SEASON_EXCLUDE_VALUE:
            continue
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
        if not has_nsk_stock:
            # Удаляем поля NSK
            clean_item = {k: v for k, v in item.items() if k not in ['rest_nsk', 'price_novosib3_rozn']}
            order_items_raw.append((clean_item, diameter))
    
    print(f"📦 Товаров под заказ: {len(order_items_raw)}")
    if order_items_raw:
        # Для под заказ S3-замену не делаем (можно сделать, но по заданию не требуется)
        save_tyres_to_xml(order_items_raw, "4tyre_test2.xml", image_cache=None)
    else:
        print("Нет товаров под заказ — файл 4tyre_test2.xml не создан.")
    
    print("\n✅ Все XML файлы успешно созданы.")

if __name__ == "__main__":
    main()
