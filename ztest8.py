#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ========== НОВЫЙ КОД ДЛЯ ИНТЕГРАЦИИ С МОДУЛЕМ ==========
import sys
import os

# Добавляем текущую директорию в путь для импорта
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Импортируем новые модули
from tyre_export.legacy import (
    legacy_round_price as round_price,
    legacy_clean_name as clean_name,
    legacy_safe_float as safe_float,
    get_legacy_config,
)

# Загружаем конфигурацию
config = get_legacy_config()

# ========== ЗАМЕНЯЕМ СТАРЫЕ КОНСТАНТЫ НА КОНФИГ ==========
# УДАЛИТЕ или ЗАКОММЕНТИРУЙТЕ старые определения и используйте эти:

ROUND_STEP = config.rounding.step
ROUND_METHOD = config.rounding.method
INCLUDE_PRICE_TAG = config.include_price_tag

# Настройки API
API_URL = config.api.url
API_USER = config.api.user
API_PASSWORD = config.api.password

# Настройки изображений
IMAGE_REPLACE_ENABLED = config.images.enabled
IMAGE_CHECK_ENABLED = config.images.check_enabled
IMAGE_BASE_URL = config.images.base_url
IMAGE_CACHE_FILE = config.images.cache_file
IMAGE_CACHE_REFRESH = config.images.cache_refresh
MAX_WORKERS = config.images.max_workers
HEAD_TIMEOUT = config.images.timeout

# Фильтры
SEASON_EXCLUDE_ENABLED = config.filters.season_exclude_enabled
SEASON_EXCLUDE_VALUE = config.filters.season_exclude_value
EXCLUDED_BRANDS = config.filters.excluded_brands
EXCLUDED_BRANDS_FROM_EXPORT = config.filters.excluded_brands_from_export
EXCLUDED_CATEGORY = config.filters.excluded_category
EXCLUDED_ARTICLES = config.filters.excluded_articles
EXCLUDE_CAE_ENABLED = config.filters.exclude_cae_enabled
EXCLUDED_CAE = config.filters.excluded_cae

# Коэффициенты цен
GLOBAL_COEFF = config.pricing.global_coeff
BRAND_COEFFS = config.pricing.brand_coeffs
IGNORE_COEFF_BRANDS = config.pricing.ignore_coeff_brands
MIN_MARGIN = float(config.pricing.min_margin)
MODEL_RULES = config.pricing.model_rules
# ========== ЛОГИКА ==========
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
    """Возвращает список возможных URL для основного изображения (только короткое имя на S3)."""
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

    width = item.get("width", "")
    profile = item.get("profile", "") or item.get("height", "")
    diameter = item.get("diameter", "")
    if not (width and profile and diameter):
        nomenclature = item.get("Номенклатура", "")
        match = re.search(r'(\d+)/(\d+)[Zz]?[Rr](\d+)', nomenclature)
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
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.head(url, timeout=HEAD_TIMEOUT, headers=headers)
        exists = response.status_code == 200
    except:
        exists = False
    cache[url] = exists
    return exists

# ===================== ДОБАВЛЕНИЕ ТОВАРА В XML =====================
def add_product_to_root(root, item, diameter, replace_images=True, image_cache=None):
    product = ET.SubElement(root, "Product")

    # Обработка всех полей, кроме img
    for key, value in item.items():
        if key == "Оптовая_Цена":
            continue
        if key == "price" and not INCLUDE_PRICE_TAG:
            continue
        if key == "img":
            continue

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

    # --- ФОРМИРОВАНИЕ ТЕГА IMAGES (замена img) ---
    if replace_images and IMAGE_REPLACE_ENABLED:
        brand = item.get("brand", "").strip()
        model = item.get("model", "").strip()
        fallback_url = item.get("img", "").strip()

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

            # Основное фото (первое в списке)
            img_main = ET.SubElement(images_elem, "Image")
            img_main.set("name", main_filename)

            # Дополнительные фото
            for fname in additional_filenames:
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
        product.tag = "tyres"

# ===================== ОСНОВНАЯ ЛОГИКА =====================
auth = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
response = requests.get(API_URL, headers={"Authorization": f"Basic {auth}"})
response.raise_for_status()
data = response.json()

print("🔄 Загрузка данных завершена. Обработка...")

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
# --- Исключение по cae (артикулу) ---
    if EXCLUDE_CAE_ENABLED:
        cae = item.get("cae", "")
        if cae in EXCLUDED_CAE:
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
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                response = requests.head(url, timeout=HEAD_TIMEOUT, headers=headers)
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

# Статистика по брендам (оставляем без изменений)
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
    with open("brand_statistics.txt2", "w", encoding="utf-8") as f:
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
    print("✅ Статистика по брендам сохранена в файл brand_statistics2.txt")
except Exception as e:
    print(f"❌ Ошибка при записи статистики: {e}")
    traceback.print_exc()

# Создание итогового XML
root = ET.Element("Products")
for item, diameter in all_items:
    add_product_to_root(root, item, diameter,
                        replace_images=True,
                        image_cache=image_cache if IMAGE_CHECK_ENABLED else None)

tree = ET.ElementTree(root)
with open("aztyre_full2.xml", "wb") as file:
    tree.write(file, encoding="utf-8", xml_declaration=True)

print(f"✅ Итоговый XML файл создан: aztyre_full.xml, всего товаров: {total_products}")
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
