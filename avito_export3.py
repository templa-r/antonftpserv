import os
import requests
import base64
import xml.etree.ElementTree as ET
import re
import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict

# ===================== НАСТРОЙКИ =====================
API_URL = "https://ka2.sibzapaska.ru:16500/API/hs/V2/GetTires"
API_USER = "API_client"
API_PASSWORD = "rWp7mFWXRKOq"

ROUND_STEP = 10
ROUND_METHOD = 'nearest'

# ===================== НОРМАЛИЗАЦИЯ МОДЕЛЕЙ И БРЕНДОВ =====================
MODEL_REFERENCE_FILE = "models_reference.xml"
DIAMETER_REFERENCE_FILE = "diameter.xml"
PROFILE_REFERENCE_FILE = "profile.xml"
WIDTH_REFERENCE_FILE = "width.xml"
LOADINDEX_REFERENCE_FILE = "loadindex.xml"

def normalize_string(s: str) -> str:
    if not s:
        return ""
    for sep in ['-', '_', '/', '\\', '–', '—']:
        s = s.replace(sep, ' ')
    s = re.sub(r'[^a-zа-яё0-9\s]', '', s, flags=re.IGNORECASE)
    s = s.lower()
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def load_model_mapping(xml_path: str):
    if not os.path.exists(xml_path):
        print(f"⚠️ Файл {xml_path} не найден. Нормализация отключена.")
        return {}, {}
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        model_map = {}
        brand_map = {}
        for make in root.findall("make"):
            brand = make.get("name", "").strip()
            if not brand:
                continue
            brand_norm = normalize_string(brand)
            brand_map[brand_norm] = brand
            models = {}
            for model in make.findall("model"):
                model_name = model.get("name", "").strip()
                if model_name:
                    model_norm = normalize_string(model_name)
                    models[model_norm] = model_name
            if models:
                model_map[brand_norm] = models
        print(f"✅ Загружено {len(brand_map)} брендов со списками моделей из {xml_path}")
        return model_map, brand_map
    except Exception as e:
        print(f"❌ Ошибка загрузки справочника: {e}")
        return {}, {}

def load_values_set(xml_path: str, tag: str) -> set:
    if not os.path.exists(xml_path):
        print(f"⚠️ Файл {xml_path} не найден. Валидация отключена.")
        return set()
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        valid = set()
        for elem in root.findall(tag):
            val = (elem.text or "").strip()
            if val:
                try:
                    num = float(val.replace(",", "."))
                    normalized = f"{num:.2f}"
                except ValueError:
                    normalized = val
                valid.add(normalized)
        print(f"✅ Загружено {len(valid)} значений из {xml_path}")
        return valid
    except Exception as e:
        print(f"❌ Ошибка загрузки {xml_path}: {e}")
        return set()

def load_int_set(xml_path: str, tag: str) -> set:
    """Загружает целочисленные значения, игнорируя дробные типа '112/110'."""
    if not os.path.exists(xml_path):
        print(f"⚠️ Файл {xml_path} не найден. Валидация отключена.")
        return set()
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        valid = set()
        for elem in root.findall(tag):
            val = (elem.text or "").strip()
            if val:
                try:
                    num = int(val)
                    valid.add(num)
                except ValueError:
                    pass  # пропускаем дробные значения
        print(f"✅ Загружено {len(valid)} значений из {xml_path}")
        return valid
    except Exception as e:
        print(f"❌ Ошибка загрузки {xml_path}: {e}")
        return set()

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

# Список идентификаторов, которые нужно исключить (обычно из-за нулевой цены)
EXCLUDED_IDS = {
    "6924590215153-195-65-15",
    "2167333-235-40-18",
    "6924590215306-225-60-17",
    "6924590215191-215-60-16",
    "6924590215221-215-55-17",
    "RA86201-1250-80-15",

}

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
    "ikon tyres": {
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

IGNORE_COEFF_BRANDS = ["Mazzini", "Nexen", "MAXXIS", "Sonix", "Haankook"]
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

def make_id(item):
    """Создаёт уникальный идентификатор объявления (как в XML)."""
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
    return unique_id

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
        headers = {"User-Agent": "Mozilla/5.0"}
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
    goods_type = item.get("Тип_товара", "").lower()
    if goods_type == "диск":
        return True
    name = item.get("name", "").lower()
    if "disk" in name or "диск" in name:
        return True
    return False

# ===================== ФОРМИРОВАНИЕ XML ДЛЯ АВИТО =====================
def add_ad_to_root(root, item, diameter, image_cache=None, seen_ids=None):
    if seen_ids is None:
        seen_ids = set()

    total_stock = safe_float(item.get("countAll", 0))
    if total_stock == 0:
        total_stock = safe_float(item.get("rest", 0))
    if total_stock <= 0:
        return

    unique_id = make_id(item)

    # Пропускаем дубликаты ID в рамках текущего файла
    if unique_id in seen_ids:
        return
    seen_ids.add(unique_id)

    quantity = 4 if total_stock >= 4 else 1

    price_per_one = calculate_price_per_one(item, diameter)
    total_price = price_per_one * quantity
    total_price = round_price(total_price, ROUND_STEP, ROUND_METHOD)

    # Не выгружаем товары с нулевой ценой
    if total_price <= 0:
        return

    ad = ET.SubElement(root, "Ad")

    ET.SubElement(ad, "Address").text = "Новосибирская область, Новосибирск, Фабричная улица, 43"

    ET.SubElement(ad, "Id").text = unique_id

    # Заголовок
    title_parts = []
    if item.get("brand"):
        title_parts.append(item.get("brand"))
    if item.get("model"):
        title_parts.append(item.get("model"))
    width = item.get("width", "")
    profile = item.get("profile", "") or item.get("height", "")
    diam_str = item.get("diameter", "")
    if width and profile and diam_str:
        title_parts.append(f"{width}/{profile}R{diam_str}")
    title = " ".join(title_parts) if title_parts else "Шина"
    ET.SubElement(ad, "Title").text = title

    # Описание
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

    ET.SubElement(ad, "ContactPhone").text = "8 913 703-55-88"

    # Изображения
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

    ET.SubElement(ad, "Price").text = str(total_price)

    ET.SubElement(ad, "Category").text = "Запчасти и аксессуары"
    ET.SubElement(ad, "GoodsType").text = "Шины, диски и колёса"
    ET.SubElement(ad, "AdType").text = "Товар приобретен на продажу"
    ET.SubElement(ad, "ProductType").text = "Легковые шины"
    ET.SubElement(ad, "Quantity").text = f"за {quantity} шт."

    ET.SubElement(ad, "Brand").text = item.get("brand", "")
    ET.SubElement(ad, "Model").text = item.get("model", "")
    ET.SubElement(ad, "TireSectionWidth").text = str(item.get("width", ""))
    ET.SubElement(ad, "RimDiameter").text = str(item.get("diameter", ""))
    ET.SubElement(ad, "TireAspectRatio").text = str(item.get("profile", "") or item.get("height", ""))

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
        tire_type = "Всесезонные"
    ET.SubElement(ad, "TireType").text = tire_type

    # Индекс нагрузки (только если задан и не пуст)
    load_index_val = str(item.get("load_index", "")).strip()
    if load_index_val:
        ET.SubElement(ad, "LoadIndex").text = load_index_val

    if item.get("speed_index"):
        ET.SubElement(ad, "SpeedIndex").text = str(item["speed_index"])

    runflat = item.get("runflat", "")
    if runflat:
        ET.SubElement(ad, "RunFlat").text = "Да" if runflat.lower() in ("да", "yes", "runflat") else "Нет"

    ET.SubElement(ad, "Condition").text = "Новое"
    ET.SubElement(ad, "AdStatus").text = "Free"

# ===================== ОСНОВНАЯ ЛОГИКА =====================
def main():
    auth = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
    response = requests.get(API_URL, headers={"Authorization": f"Basic {auth}"})
    response.raise_for_status()
    data = response.json()

    print("🔄 Загрузка данных завершена. Обработка...")

    model_mapping, brand_map = load_model_mapping(MODEL_REFERENCE_FILE)
    valid_brands_norms = set(brand_map.keys())

    valid_diameters = load_values_set(DIAMETER_REFERENCE_FILE, "RimDiameter")
    valid_profiles = load_values_set(PROFILE_REFERENCE_FILE, "TireAspectRatio")
    valid_widths = load_values_set(WIDTH_REFERENCE_FILE, "TireSectionWidth")
    valid_loadindexes = load_int_set(LOADINDEX_REFERENCE_FILE, "LoadIndex")

    log_file = "validation_errors.log"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("Артикул\tБренд\tМодель\tПараметр\tЗначение\tПричина\n")

    def log_invalid(item, param, value, reason):
        article = item.get("article", "") or item.get("cae", "")
        brand = item.get("brand", "")
        model = item.get("model", "")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"{article}\t{brand}\t{model}\t{param}\t{value}\t{reason}\n")

    all_items = []
    unique_urls = set()
    total_products = 0
    excluded_zb = 0
    excluded_article = 0
    excluded_season = 0
    excluded_disk = 0
    excluded_validation = 0
    excluded_zero_retail = 0
    excluded_by_id = 0

    for item in data:
        name = item.get("name", "")
        if name.startswith("ЗБ"):
            excluded_zb += 1
            continue

        if item.get("brand") == "Ikon (Nokian Tyres)":
            item["brand"] = "Ikon Tyres"
        if "name" in item and "(Nokian Tyres)" in item["name"]:
            item["name"] = item["name"].replace("(Nokian Tyres)", "").strip()
            item["name"] = re.sub(r'\s+', ' ', item["name"])

        original_retail = safe_float(item.get("retail", 0))
        if original_retail <= 0:
            excluded_zero_retail += 1
            continue

        brand = item.get("brand", "")
        if brand in EXCLUDED_BRANDS_FROM_EXPORT:
            continue

        # --- Валидация бренда ---
        if not brand:
            log_invalid(item, "Brand", "", "пустой бренд")
            excluded_validation += 1
            continue
        brand_norm = normalize_string(brand)
        if brand_norm not in valid_brands_norms:
            log_invalid(item, "Brand", brand, f"не найден в справочнике (норм: {brand_norm})")
            excluded_validation += 1
            continue
        item["brand"] = brand_map[brand_norm]

        # --- Валидация модели ---
        model = item.get("model", "")
        if not model:
            log_invalid(item, "Model", "", "пустая модель")
            excluded_validation += 1
            continue
        if brand_norm in model_mapping:
            models_dict = model_mapping[brand_norm]
            model_norm = normalize_string(model)
            if model_norm in models_dict:
                item["model"] = models_dict[model_norm]
            else:
                log_invalid(item, "Model", model, f"не найдена в справочнике для бренда {item['brand']}")
                excluded_validation += 1
                continue
        else:
            log_invalid(item, "Model", model, f"бренд {item['brand']} не имеет списка моделей")
            excluded_validation += 1
            continue

        # --- Валидация диаметра ---
        diam_str = str(item.get("diameter", "")).strip()
        if not diam_str:
            log_invalid(item, "RimDiameter", "", "пустой диаметр")
            excluded_validation += 1
            continue
        try:
            diameter = float(diam_str.replace(",", "."))
            diam_norm = f"{diameter:.2f}"
        except ValueError:
            log_invalid(item, "RimDiameter", diam_str, "некорректное число")
            excluded_validation += 1
            continue
        if diam_norm not in valid_diameters:
            log_invalid(item, "RimDiameter", diam_str, "отсутствует в справочнике")
            excluded_validation += 1
            continue

        # --- Валидация ширины ---
        width_str = str(item.get("width", "")).strip()
        if not width_str:
            log_invalid(item, "TireSectionWidth", "", "пустая ширина")
            excluded_validation += 1
            continue
        try:
            width_val = float(width_str.replace(",", "."))
            width_norm = f"{width_val:.2f}"
        except ValueError:
            log_invalid(item, "TireSectionWidth", width_str, "некорректное число")
            excluded_validation += 1
            continue
        if width_norm not in valid_widths:
            log_invalid(item, "TireSectionWidth", width_str, "отсутствует в справочнике")
            excluded_validation += 1
            continue

        # --- Валидация профиля ---
        profile_str = str(item.get("profile", "") or item.get("height", "")).strip()
        if not profile_str:
            log_invalid(item, "TireAspectRatio", "", "пустая высота профиля")
            excluded_validation += 1
            continue
        try:
            profile_val = float(profile_str.replace(",", "."))
            profile_norm = f"{profile_val:.2f}"
        except ValueError:
            log_invalid(item, "TireAspectRatio", profile_str, "некорректное число")
            excluded_validation += 1
            continue
        if profile_norm not in valid_profiles:
            log_invalid(item, "TireAspectRatio", profile_str, "отсутствует в справочнике")
            excluded_validation += 1
            continue

        # --- Валидация индекса нагрузки ---
        load_index_raw = str(item.get("load_index", "")).strip()
        if load_index_raw:
            if '/' in load_index_raw:
                load_index_raw = load_index_raw.split('/')[0].strip()
            try:
                li = int(load_index_raw)
                if li in valid_loadindexes:
                    item["load_index"] = str(li)
                else:
                    log_invalid(item, "LoadIndex", str(item.get("load_index", "")), "отсутствует в справочнике")
                    excluded_validation += 1
                    continue
            except ValueError:
                log_invalid(item, "LoadIndex", str(item.get("load_index", "")), "некорректное число")
                excluded_validation += 1
                continue
        else:
            item.pop("load_index", None)

        # --- Проверка по ID исключения ---
        if make_id(item) in EXCLUDED_IDS:
            excluded_by_id += 1
            continue

        # --- Остальные проверки ---
        if is_disk(item):
            excluded_disk += 1
            continue

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
    print(f"🔍 Исключено по валидации: {excluded_validation}")
    print(f"🔍 Исключено с нулевой розничной ценой: {excluded_zero_retail}")
    print(f"🔍 Исключено по ID (EXCLUDED_IDS): {excluded_by_id}")
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
                    headers = {"User-Agent": "Mozilla/5.0"}
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

    # Создание XML с защитой от дубликатов
    root = ET.Element("Ads")
    root.set("formatVersion", "3")
    root.set("target", "Avito.ru")

    seen_ids = set()
    for item, diameter in all_items:
        add_ad_to_root(root, item, diameter,
                       image_cache=image_cache if IMAGE_CHECK_ENABLED else None,
                       seen_ids=seen_ids)

    tree = ET.ElementTree(root)
    output_file = "avito_export3.xml"
    tree.write(output_file, encoding="utf-8", xml_declaration=True)

    print(f"✅ Итоговый XML для Авито создан: {output_file}, всего объявлений в файле: {len(tree.getroot().findall('Ad'))}")
    print(f"   - Пропущено (ЗБ): {excluded_zb}")
    print(f"   - Исключено по артикулу: {excluded_article}")
    print(f"   - Исключено дисков: {excluded_disk}")
    print(f"   - Исключено по валидации: {excluded_validation}")
    print(f"   - Исключено с нулевой розничной ценой: {excluded_zero_retail}")
    print(f"   - Исключено по ID: {excluded_by_id}")
    if SEASON_EXCLUDE_ENABLED:
        print(f"   - Исключено по сезону ({SEASON_EXCLUDE_VALUE}): {excluded_season}")

if __name__ == "__main__":
    main()
