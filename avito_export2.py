import os
import requests
import base64
import xml.etree.ElementTree as ET
import re
import json
import time
import difflib
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict  # добавлено для аннотаций

# ===================== НАСТРОЙКИ =====================
API_URL = "https://ka2.sibzapaska.ru:16500/API/hs/V2/GetTires"
API_USER = "API_client"
API_PASSWORD = "rWp7mFWXRKOq"

ROUND_STEP = 10
ROUND_METHOD = 'nearest'

# ===================== НОРМАЛИЗАЦИЯ МОДЕЛЕЙ ПО СПРАВОЧНИКУ =====================
MODEL_REFERENCE_FILE = "models_reference.xml"   # путь к вашему XML-файлу

def normalize_string(s: str) -> str:
    """Нормализует строку для сравнения (без потери оригинального регистра)."""
    if not s:
        return ""
    for sep in ['-', '_', '/', '\\', '–', '—']:
        s = s.replace(sep, ' ')
    s = re.sub(r'[^a-zа-яё0-9\s]', '', s, flags=re.IGNORECASE)
    s = s.lower()
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def load_model_mapping(xml_path: str) -> Dict[str, Dict[str, str]]:
    """
    Загружает справочник моделей.
    Возвращает: { нормализованный_бренд: { нормализованная_модель: оригинальное_название_модели } }
    """
    if not os.path.exists(xml_path):
        print(f"⚠️ Файл {xml_path} не найден. Нормализация моделей отключена.")
        return {}
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        mapping = {}
        for make in root.findall("make"):
            brand = make.get("name", "").strip()
            if not brand:
                continue
            brand_norm = normalize_string(brand)
            models = {}
            for model in make.findall("model"):
                model_name = model.get("name", "").strip()
                if model_name:
                    model_norm = normalize_string(model_name)
                    models[model_norm] = model_name   # сохраняем оригинал
            if models:
                mapping[brand_norm] = models
        print(f"✅ Загружено {len(mapping)} брендов со списками моделей из {xml_path}")
        return mapping
    except Exception as e:
        print(f"❌ Ошибка загрузки справочника моделей: {e}")
        return {}

def normalize_model(brand: str, model: str, mapping: Dict[str, Dict[str, str]]) -> str:
    """Точное совпадение нормализованных строк – возвращает оригинал из справочника, иначе исходную модель."""
    if not model or not mapping:
        return model
    brand_norm = normalize_string(brand)
    model_norm = normalize_string(model)
    if brand_norm in mapping:
        models_dict = mapping[brand_norm]
        if model_norm in models_dict:
            return models_dict[model_norm]   # возвращаем оригинальное написание
        else:
            # Для отладки (можно закомментировать)
            print(f"⚠️ Модель '{model}' (норм: '{model_norm}') не найдена в справочнике для бренда '{brand}'")
    return model

# ===================== ЗАМЕНА ИЗОБРАЖЕНИЙ =====================
IMAGE_REPLACE_ENABLED = True
IMAGE_CHECK_ENABLED = True
IMAGE_BASE_URL = "https://s3.ru1.storage.beget.cloud/fa5a823588a1-antontyres/images"
IMAGE_CACHE_FILE = "image_cache.json"
IMAGE_CACHE_REFRESH = os.getenv("IMAGE_CACHE_REFRESH", "false").lower() == "true"

MAX_WORKERS = 50
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
    return coeff

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

# ===================== РАСЧЁТ ИТОГОВОЙ ЦЕНЫ (за 1 шт.) =====================
def calculate_price_per_one(item, diameter):
    brand = item.get("brand", "").strip().lower()
    model = item.get("model", "").strip().lower()
    category = item.get("category", "")
    original_retail = safe_float(item.get("retail", 0))
    price_val = safe_float(item.get("price", 0))

    is_excluded = (brand in [b.lower() for b in EXCLUDED_BRANDS] or
                   category in EXCLUDED_CATEGORY)
    apply_coeff = brand not in [b.lower() for b in IGNORE_COEFF_BRANDS]

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
                add_value = None
                if diameter is not None:
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
            brand_settings = BRAND_COEFFS.get(brand)
            if brand_settings is not None:
                coeff = get_coeff_from_settings(brand_settings, diameter)
            else:
                coeff = get_coeff_from_settings(GLOBAL_COEFF, diameter)
            adjusted_retail = original_retail * coeff

        if price_val > 0:
            margin = adjusted_retail - price_val
            if margin < MIN_MARGIN:
                final_price = original_retail
            else:
                final_price = adjusted_retail
        else:
            final_price = adjusted_retail

    return round_price(final_price, ROUND_STEP, ROUND_METHOD)

# ===================== ФУНКЦИЯ ДЛЯ ПРОВЕРКИ, ЯВЛЯЕТСЯ ЛИ ТОВАР ДИСКОМ =====================
def is_disk(item):
    """Возвращает True, если товар — диск (не шина)"""
    goods_type = item.get("Тип_товара", "").lower()
    if goods_type == "диск":
        return True
    name = item.get("name", "").lower()
    if "disk" in name or "диск" in name:
        return True
    return False

# ===================== ФОРМИРОВАНИЕ XML ДЛЯ АВИТО (ПОЛНОСТЬЮ ИСПРАВЛЕНО) =====================
def add_ad_to_root(root, item, diameter, image_cache=None):
    total_stock = safe_float(item.get("countAll", 0))
    if total_stock == 0:
        total_stock = safe_float(item.get("rest", 0))
    if total_stock <= 0:
        return

    quantity = 4 if total_stock >= 4 else 1

    ad = ET.SubElement(root, "Ad")

    # --- Адрес (обязателен) ---
    ET.SubElement(ad, "Address").text = "Новосибирская область, Новосибирск, Фабричная улица, 43"

    # --- Идентификатор ---
    article = item.get("article", "") or item.get("cae", "")
    width = item.get("width", "")
    profile = item.get("profile", "") or item.get("height", "")
    diam_str = item.get("diameter", "")
    if article:
        unique_id = f"{article}-{width}-{profile}-{diam_str}".strip('-')
    else:
        brand_name = item.get("brand", "")
        model_name = item.get("model", "")
        unique_id = f"{brand_name}-{model_name}-{width}-{profile}-{diam_str}".strip('-')
    unique_id = re.sub(r'[^a-zA-Zа-яА-ЯёЁ0-9\-]', '', unique_id)
    ET.SubElement(ad, "Id").text = unique_id

    # --- Заголовок ---
    title_parts = []
    if item.get("brand"):
        title_parts.append(item.get("brand"))
    if item.get("model"):
        title_parts.append(item.get("model"))
    if width and profile and diam_str:
        title_parts.append(f"{width}/{profile}R{diam_str}")
    title = " ".join(title_parts) if title_parts else "Шина"
    ET.SubElement(ad, "Title").text = title

    # --- Описание (единственный блок) ---
    name = item.get("name", "")
    if not name:
        name = f"{item.get('brand', '')} {item.get('model', '')} {width}/{profile}R{diam_str}".strip()
    description = "\n".join([
        name,
        "В наличии в Новосибирске. Бесплатная доставка по городу. Удалённые районы доставка с доплатой. Звоните пишите. Самые приятные цены. Доставляем быстро.",
        "",
        "«АНТОН ШИНЫ-МАШИНЫ»",
        "🚛 Работаю как интернет-магазин: заказ - доставка до адреса - оплата при получении",
        "Каталог шин в профиле",
        "Доставка по Новосибирску: на ваш адрес (дом/офис) - от 30 минут до 2 часов в день заказа",
        "Оплата: наличные, перевод на карту, безналичный расчёт (для юрлиц, счёт с НДС)",
        "Если шины под заказ (нет в наличии): предоплата 50% - остальное при получении",
        "Доставка в другие регионы РФ: через транспортные компании - 100% предоплата, условия уточняйте по телефону",
        "Работаю честно - никаких скрытых комиссий",
        "✉️ Звоните или пишите в чат - отвечаю быстро"
    ])
    ET.SubElement(ad, "Description").text = description

    # --- Контактный телефон (замените на реальный) ---
    ET.SubElement(ad, "ContactPhone").text = "+7 905 207-04-90"

    # --- Изображения ---
    images_elem = ET.SubElement(ad, "Images")
    fallback_img = item.get("img", "")
    brand = item.get("brand", "").strip()
    model = item.get("model", "").strip()
    if IMAGE_REPLACE_ENABLED and brand and model:
        all_urls = get_all_image_urls(item)
        existing_urls = []
        if IMAGE_CHECK_ENABLED and image_cache is not None:
            for url in all_urls:
                if image_cache.get(url, False):
                    existing_urls.append(url)
        else:
            existing_urls = all_urls[:1]
        if not existing_urls and fallback_img:
            existing_urls = [fallback_img]
        for url in existing_urls:
            img = ET.SubElement(images_elem, "Image")
            img.set("url", url)
    elif fallback_img:
        img = ET.SubElement(images_elem, "Image")
        img.set("url", fallback_img)

    # --- Цена за лот ---
    price_per_one = calculate_price_per_one(item, diameter)
    total_price = price_per_one * quantity
    total_price = round_price(total_price, ROUND_STEP, ROUND_METHOD)
    ET.SubElement(ad, "Price").text = str(total_price)

    # --- Категория и тип товара ---
    ET.SubElement(ad, "Category").text = "Запчасти и аксессуары"
    ET.SubElement(ad, "GoodsType").text = "Шины, диски и колёса"
    ET.SubElement(ad, "AdType").text = "Товар приобретен на продажу"
    ET.SubElement(ad, "ProductType").text = "Легковые шины"

    # --- Количество (строго по формату Авито) ---
    ET.SubElement(ad, "Quantity").text = f"за {quantity} шт."

    # --- Параметры шин ---
    ET.SubElement(ad, "Brand").text = item.get("brand", "")
    ET.SubElement(ad, "Model").text = item.get("model", "")
    ET.SubElement(ad, "TireSectionWidth").text = str(item.get("width", ""))
    ET.SubElement(ad, "RimDiameter").text = str(item.get("diameter", ""))
    ET.SubElement(ad, "TireAspectRatio").text = str(item.get("profile", "") or item.get("height", ""))

    # Сезонность (обязательное поле)
    season = item.get("season", "").lower()
    if season == "зима":
        studded = str(item.get("studded", "") or item.get("spikes", "")).lower()
        if studded in ("да", "yes", "true", "1"):
            tire_type = "Зимние шипованные"
        else:
            tire_type = "Зимние нешипованные"
    elif season == "лето":
        tire_type = "Летние"
    elif season == "всесезон":
        tire_type = "Всесезонные"
    else:
        tire_type = "Всесезонные"   # fallback, чтобы поле не осталось пустым
    ET.SubElement(ad, "TireType").text = tire_type

    # Необязательные индексы
    if item.get("load_index"):
        ET.SubElement(ad, "LoadIndex").text = str(item["load_index"])
    if item.get("speed_index"):
        ET.SubElement(ad, "SpeedIndex").text = str(item["speed_index"])

    # RunFlat
    runflat = item.get("runflat", "")
    if runflat:
        ET.SubElement(ad, "RunFlat").text = "Да" if runflat.lower() in ("да", "yes", "runflat") else "Нет"

    # Состояние
    ET.SubElement(ad, "Condition").text = "Новое"

    # Статус объявления
    ET.SubElement(ad, "AdStatus").text = "Free"

# ===================== ОСНОВНАЯ ЛОГИКА =====================
def main():
    auth = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
    response = requests.get(API_URL, headers={"Authorization": f"Basic {auth}"})
    response.raise_for_status()
    data = response.json()

    print("🔄 Загрузка данных завершена. Обработка...")

    # ---- Загружаем справочник моделей ----
    model_mapping = load_model_mapping(MODEL_REFERENCE_FILE)

    all_items = []
    unique_urls = set()
    total_products = 0
    excluded_zb = 0
    excluded_article = 0
    excluded_season = 0
    excluded_disk = 0

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

        brand = item.get("brand", "")
        if brand in EXCLUDED_BRANDS_FROM_EXPORT:
            continue

        # Исключаем диски
        if is_disk(item):
            excluded_disk += 1
            continue

        # ---- Нормализация модели (замена на правильное название из справочника) ----
        original_model = item.get("model", "")
        if brand and original_model and model_mapping:
            corrected_model = normalize_model(brand, original_model, model_mapping)
            if corrected_model != original_model:
                item["model"] = corrected_model

        # Ручные замены (оставлены для совместимости)
        model_replacements = {
            "Blu Earth V906": ("BluEarth Winter V906", "BluEarth Winter V906"),
            "VS-EV": ("Victra Sport EV", "Victra Sport EV"),
            "Ecsta PS72": ("Ecsta Sport PS72", "Ecsta Sport PS72"),
        }
        current_model = item.get("model", "")
        if current_model in model_replacements:
            new_model, _ = model_replacements[current_model]
            item["model"] = new_model

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

    print(f"🔍 Всего товаров после фильтров (только шины): {total_products}")
    print(f"🔍 Уникальных URL для проверки: {len(unique_urls)}")

    # Кэширование изображений
    image_cache = {}
    check_time = 0
    if IMAGE_REPLACE_ENABLED and IMAGE_CHECK_ENABLED:
        if IMAGE_CACHE_REFRESH and os.path.exists(IMAGE_CACHE_FILE):
            os.remove(IMAGE_CACHE_FILE)
            print("🔁 Кэш принудительно удалён.")
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
        print("🔍 Проверка существования файлов отключена.")

    # Создание XML для Авито
    root = ET.Element("Ads")
    root.set("formatVersion", "3")
    root.set("target", "Avito.ru")

    for item, diameter in all_items:
        add_ad_to_root(root, item, diameter,
                       image_cache=image_cache if IMAGE_CHECK_ENABLED else None)

    tree = ET.ElementTree(root)
    output_file = "avito_export2.xml"
    tree.write(output_file, encoding="utf-8", xml_declaration=True)

    print(f"✅ Итоговый XML для Авито создан: {output_file}, всего объявлений: {total_products}")
    print(f"   - Пропущено (ЗБ): {excluded_zb}")
    print(f"   - Исключено по артикулу: {excluded_article}")
    print(f"   - Исключено дисков: {excluded_disk}")
    if SEASON_EXCLUDE_ENABLED:
        print(f"   - Исключено по сезону ({SEASON_EXCLUDE_VALUE}): {excluded_season}")

if __name__ == "__main__":
    main()
