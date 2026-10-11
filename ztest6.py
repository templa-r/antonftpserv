import os
import requests
import base64
import xml.etree.ElementTree as ET
import re
import json
import time
import traceback
import hashlib
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ===================== НАСТРОЙКИ =====================
API_URL = "https://ka2.sibzapaska.ru:16500/API/hs/V2/GetTires"
API_USER = "API_client"
API_PASSWORD = "rWp7mFWXRKOq"

ROUND_STEP = 10
ROUND_METHOD = 'nearest'
INCLUDE_PRICE_TAG = False

# ===================== СВИТЧЕРЫ =====================
EXCLUDE_RRC_FROM_FULL = True
MIN_FINAL_PRICE = 2500
MIN_MARGIN = 500

LOCAL_MODE = os.getenv("LOCAL_MODE", "true").lower() == "true"

# ===================== ЗАМЕНА ИЗОБРАЖЕНИЙ =====================
IMAGE_REPLACE_ENABLED = True
IMAGE_BASE_URL = "https://s3.ru1.storage.beget.cloud/fa5a823588a1-antontyres/images"

# ===================== РУЧНЫЕ ЦЕНЫ =====================
MANUAL_PRICES = {
    "5918800": 15000,
}

# ===================== ФИЛЬТРЫ =====================
SEASON_EXCLUDE_ENABLED = False
SEASON_EXCLUDE_VALUE = "зима"

EXCLUDED_BRANDS = [
    "Mazzini", "Nexen", "MAXXIS", "Predator", "Compasal", "HIFLY", "Aoteli",
    "Torero", "Viatti", "Massimo", "Firemax", "Sonix", "Prinx", "Roadmarch",
    "Kelly", "Nitto", "Кама", "Roadcruza",
]

EXCLUDED_BRANDS_FROM_EXPORT = ["Compasal", "Aoteli"]
EXCLUDED_CATEGORY = ["Грузовая"]

EXCLUDED_ARTICLES = [
    "195/75R16C Laufenn X FIT VAN LV01",
    "АТ27x8-12 MAXXIS M961 6PR",
    "185/75R16C Ikon Autograph Snow C4",
    "33x10,50-16 NORTEC ET-500 111N TL АШК",
]

# ===================== ИСКЛЮЧЕНИЯ ПО CAE =====================
EXCLUDE_CAE_ENABLED = True
EXCLUDED_CAE = [
    "КА-00049127", "КА-00049119", "КА-00049010", "КА-00049170", "КА-00049171",
    "КА-00040044", "00000019470", "КА-00048983", "КА-00046413", "КА-00049081",
    "КА-00049149", "КА-00049109", "КА-00046401", "КА-00046414", "КА-00049102",
    "КА-00049039", "КА-00046404", "КА-00046416", "КА-00049095", "КА-00049031",
    "КА-00049073", "КА-00046409", "КА-00049156", "КА-00049104", "КА-00046390",
    "КА-00046384", "КА-00046397", "КА-00049146", "КА-00063027", "КА-00046383",
    "КА-00049011", "КА-00049075", "КА-00049144", "КА-00049033", "КА-00049174",
    "КА-00049096", "КА-00049112", "КА-00049160", "КА-00049116", "КА-00049110",
    "КА-00049015", "КА-00049082", "КА-00046417", "КА-00049012", "КА-00046405",
    "КА-00046400", "КА-00049132", "КА-00048992", "КА-00049173", "КА-00049145",
    "КА-00049001", "КА-00074372", "КА-00049037", "КА-00046420", "КА-00046396",
    "КА-00046391", "КА-00049018", "КА-00049162", "КА-00046418", "КА-00049107",
    "КА-00049045", "КА-00049163", "КА-00046410", "КА-00049078", "КА-00049142",
    "КА-00049165", "00000026689", "КА-00049125", "КА-00049051", "КА-00072771",
    "КА-00026972", "00000021531", "T000STD1032334", "00000017578", "T730720",
    "TS72014", "T729600", "TS72174", "TS72286", "TS72171", "TS72170", "44370000",
    "T731723", "КА-00045392", "КА-00069375", "КА-00025688", "КА-00060283",
    "КА-00070321", "00000000761", "00000000782", "КА-00074785", "КА-00074794",
    "КА-00074789",
    "КА-00072012", "КА-00049032", "КА-00063171", "КА-00049057", "КА-00049024",
    "КА-00049017", "КА-00049167", "КА-00049020", "КА-00052632", "КА-00046419",
    "КА-00078067", "КА-00078066", "КА-00045402", "КА-00077826", "КА-00077831",
    "КА-00077830",
]

# ===================== КОЭФФИЦИЕНТЫ ЦЕН =====================
GLOBAL_COEFF = {
    "default": 0.99,
    "diameter_ranges": [
        {"min": 13, "max": 15, "coeff": 0.98},
        {"min": 16, "max": 17, "coeff": 0.98},
        {"min": 18, "max": 22, "coeff": 0.98},
    ],
}

BRAND_COEFFS = {
    "ikon": {
        "default": 0.915,
        "diameter_ranges": [
            {"min": 13, "max": 14, "value": 805},
            {"diameter": 15, "value": 995},
            {"diameter": 16, "value": 1255},
            {"diameter": 17, "value": 1755},
            {"diameter": 18, "value": 2105},
            {"diameter": 19, "value": 2255},
            {"diameter": 20, "value": 2505},
            {"diameter": 21, "value": 2705},
        ],
    },
    "laufenn": 0.985,
    "bridgestone": 0.965,

    "hankook": {
        "diameter_ranges": [
            {"min": 13, "max": 14, "value": 800},
            {"min": 15, "max": 16, "value": 900},
            {"min": 17, "max": 18, "value": 1000},
            {"min": 19, "max": 20, "value": 1100},
        ],
    },

    "pirelli": {
        "diameter_ranges": [
            {"min": 13, "max": 16, "value": 1000},
            {"min": 17, "max": 22, "value": 1200},
        ],
    },

    "formula": {
        "diameter_ranges": [
            {"min": 13, "max": 16, "value": 700},
            {"min": 17, "max": 22, "value": 900},
        ],
    },
}

IGNORE_COEFF_BRANDS = ["Mazzini", "Nexen", "MAXXIS", "Predator", "Compasal", "HIFLY", "Aoteli",
    "Torero", "Viatti", "Massimo", "Firemax", "Sonix", "Prinx", "Roadmarch",
    "Kelly", "Nitto", "Кама", "Roadcruza"]

MODEL_RULES = {
    ("autograph", "autograph ice 9 suv"): {
        "type": "add_to_field_by_diameter",
        "field": "price",
        "ranges": [
            {"min": 16, "max": 17, "value": 1500},
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
        return settings, None, None

    if diameter is not None and "diameter_ranges" in settings:
        for r in settings["diameter_ranges"]:
            match = False
            if "min" in r and "max" in r:
                match = r["min"] <= diameter <= r["max"]
            elif "diameter" in r and isinstance(r["diameter"], (int, float)):
                match = int(diameter) == int(r["diameter"])
            elif "diameters" in r and isinstance(r["diameters"], list):
                match = int(diameter) in [int(x) for x in r["diameters"]]

            if match and "coeff" in r:
                return r["coeff"], settings.get("round_step"), settings.get("round_method")

    if "default" in settings and isinstance(settings["default"], (int, float)):
        return settings["default"], settings.get("round_step"), settings.get("round_method")

    if "coeff" in settings:
        return settings["coeff"], settings.get("round_step"), settings.get("round_method")

    return None, None, None


def get_value_from_settings(settings, diameter):
    if not isinstance(settings, dict):
        return None

    if diameter is not None and "diameter_ranges" in settings:
        for r in settings["diameter_ranges"]:
            match = False
            if "min" in r and "max" in r:
                match = r["min"] <= diameter <= r["max"]
            elif "diameter" in r and isinstance(r["diameter"], (int, float)):
                match = int(diameter) == int(r["diameter"])
            elif "diameters" in r and isinstance(r["diameters"], list):
                match = int(diameter) in [int(x) for x in r["diameters"]]

            if match and "value" in r:
                return r["value"]

    if "value" in settings:
        return settings["value"]
    return None

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

# ===================== СПИСОК ФАЙЛОВ НА S3 (через s3cmd) =====================
# Загружаем один раз все ключи бакета и храним в памяти.
# Проверка файла — простой поиск по множеству, никаких HTTP-запросов.

S3_FILES = set()          # будет содержать пути без префикса бакета: "Mazzini/Mazzini_ICE_LEOPARD.jpg"
S3_AVAILABLE = False      # True, если список успешно загружен


def load_s3_file_list():
    """Загружает список всех файлов на S3 через s3cmd и сохраняет в S3_FILES."""
    global S3_FILES, S3_AVAILABLE

    S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
    S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
    S3_BUCKET = os.getenv("S3_BUCKET", "fa5a823588a1-antontyres")
    S3_ENDPOINT = os.getenv("S3_ENDPOINT", "https://s3.ru1.storage.beget.cloud")

    if not S3_ACCESS_KEY or not S3_SECRET_KEY:
        print("⚠️ S3: нет ключей, список файлов не загружен. Все проверки будут возвращать False.")
        S3_AVAILABLE = False
        return

    print("🔧 Устанавливаем s3cmd (если ещё нет)...")
    subprocess.run([sys.executable, "-m", "pip", "install", "s3cmd"],
                   check=True, capture_output=True)

    host_base = S3_ENDPOINT.replace('https://', '').replace('http://', '')
    config_content = f"""[default]
access_key = {S3_ACCESS_KEY}
secret_key = {S3_SECRET_KEY}
host_base = {host_base}
host_bucket = {host_base}
use_https = True
signature_v2 = False
"""
    with open("/tmp/s3cfg", "w") as f:
        f.write(config_content)

    print("📥 Загружаем список файлов с S3 (одна операция, без HTTP-запросов)...")
    t0 = time.time()
    try:
        result = subprocess.run(
            ["s3cmd", "--config=/tmp/s3cfg", "ls", "-r", f"s3://{S3_BUCKET}/images/"],
            capture_output=True, text=True, timeout=180
        )
    except subprocess.TimeoutExpired:
        print("❌ s3cmd ls timeout")
        S3_AVAILABLE = False
        return

    if result.returncode != 0:
        print(f"❌ s3cmd ls вернул ошибку: {result.stderr}")
        S3_AVAILABLE = False
        return

    # Формат строк:
    # 2024-01-01 12:00      1234   s3://bucket/images/Mazzini/Mazzini_ICE_LEOPARD.jpg
    prefix = f"s3://{S3_BUCKET}/images/"
    count = 0
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if "s3://" in line:
            path = line.split("s3://", 1)[1]
            # Убираем bucket/
            parts = path.split("/", 1)
            if len(parts) == 2:
                rel = parts[1]   # "images/Mazzini/Mazzini_ICE_LEOPARD.jpg"
                # убираем "images/"
                if rel.startswith("images/"):
                    rel = rel[len("images/"):]
                S3_FILES.add(rel)
                count += 1

    S3_AVAILABLE = True
    print(f"✅ Загружено {count} файлов с S3 за {time.time()-t0:.2f} сек.")


def check_image_exists_by_filename(rel_path):
    """
    Проверяет файл в S3_FILES. rel_path например: "Mazzini/Mazzini_ICE_LEOPARD.jpg".
    Никаких HTTP-запросов, просто поиск по множеству.
    """
    if not S3_AVAILABLE:
        return False
    return rel_path in S3_FILES


# ===================== СЧЁТЧИКИ =====================
low_price_count = 0
no_price_count = 0
manual_price_count = 0
loss_count = 0

# ===================== РАСЧЁТ ИТОГОВОЙ ЦЕНЫ =====================
def calculate_final_price(item, diameter, is_rrc):
    global low_price_count, no_price_count, manual_price_count, loss_count

    brand = item.get("brand", "").strip().lower()
    model = item.get("model", "").strip().lower()
    category = item.get("category", "")
    article = str(item.get("article", "")).strip()
    original_retail = safe_float(item.get("retail", 0))
    price_val = safe_float(item.get("price", 0))

    manual_price = MANUAL_PRICES.get(article)
    if manual_price is not None:
        manual_price_count += 1
        return manual_price, "manual"

    if original_retail == 0 and price_val == 0:
        no_price_count += 1
        return None, "no_price"

    is_excluded = (brand in [b.lower() for b in EXCLUDED_BRANDS] or
                   category in EXCLUDED_CATEGORY)

    final_price = None
    try:
        if is_excluded or is_rrc:
            final_price = original_retail
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
                                final_price = original_retail
                    else:
                        final_price = original_retail
                else:
                    final_price = original_retail
            else:
                brand_settings = BRAND_COEFFS.get(brand)
                if brand_settings is not None:
                    value_add = get_value_from_settings(brand_settings, diameter)
                    if value_add is not None and price_val > 0:
                        final_price = price_val + value_add
                    else:
                        coeff, _, _ = get_coeff_from_settings(brand_settings, diameter)
                        if coeff is not None:
                            final_price = original_retail * coeff
                        else:
                            coeff, _, _ = get_coeff_from_settings(GLOBAL_COEFF, diameter)
                            final_price = original_retail * coeff
                else:
                    coeff, _, _ = get_coeff_from_settings(GLOBAL_COEFF, diameter)
                    final_price = original_retail * coeff

        if final_price is None:
            final_price = original_retail

    except Exception as e:
        print(f"⚠️ Ошибка в расчёте цены для {item.get('name')}: {e}")
        final_price = original_retail

    step = ROUND_STEP
    method = ROUND_METHOD
    rule = MODEL_RULES.get((brand, model))
    if rule:
        step = rule.get("round_step", step)
        method = rule.get("round_method", method)

    try:
        final_price = int(round_price(final_price, step, method))
    except:
        final_price = int(final_price)

    if final_price < MIN_FINAL_PRICE:
        low_price_count += 1
        return final_price, "low"

    if not is_rrc and not is_excluded:
        if price_val > 0 and final_price < price_val + MIN_MARGIN:
            loss_count += 1
            return final_price, "loss"

    return final_price, "ok"

# ===================== ДОБАВЛЕНИЕ ТОВАРА В XML =====================
def add_product_to_root(root, item, diameter, final_price, replace_images=True):
    product = ET.SubElement(root, "Product")

    for key, value in item.items():
        if key == "Оптовая_Цена":
            continue
        if key == "price" and not INCLUDE_PRICE_TAG:
            continue
        if key == "img":
            continue

        element = ET.SubElement(product, key)

        if key.lower() == "retail":
            element.text = str(int(final_price))
        else:
            element.text = str(value)

    if replace_images and IMAGE_REPLACE_ENABLED:
        brand = item.get("brand", "").strip()
        model = item.get("model", "").strip()
        fallback_url = item.get("img", "").strip()

        s3_urls = []
        short_url = None
        brand_clean = None
        if brand and model:
            brand_clean = clean_name(brand)
            model_clean = clean_name(model)

            # 1. URL с размерами (со суффиксами)
            s3_urls.extend(get_additional_image_urls(item))

            # 2. Короткое имя (бренд_модель.jpg)
            short_url = f"{IMAGE_BASE_URL}/{brand_clean}/{brand_clean}_{model_clean}.jpg"
            s3_urls.append(short_url)

        # Ищем, какие из s3_urls реально существуют (по списку S3_FILES)
        existing_s3 = {}
        for url in s3_urls:
            # извлекаем относительный путь после /images/
            rel = url.split("/images/", 1)[1] if "/images/" in url else None
            if rel and check_image_exists_by_filename(rel):
                filename = rel.split("/", 1)[1] if "/" in rel else rel
                existing_s3[url] = filename

        if existing_s3:
            photo_dir = f"{IMAGE_BASE_URL}/{brand_clean}/"

            filenames = []
            for url in s3_urls:
                if url in existing_s3:
                    fname = existing_s3[url]
                    if fname not in filenames:
                        filenames.append(fname)
            for url, fname in existing_s3.items():
                if fname not in filenames:
                    filenames.append(fname)

            images_elem = ET.SubElement(product, "Images")
            images_elem.set("PhotoDir", photo_dir)
            for fname in filenames:
                img_elem = ET.SubElement(images_elem, "Image")
                img_elem.set("name", fname)
        else:
            # Fallback — оригинальный URL из API
            if fallback_url:
                photo_dir = fallback_url[:fallback_url.rfind('/')+1] if '/' in fallback_url else ""
                main_filename = fallback_url[fallback_url.rfind('/')+1:] if '/' in fallback_url else fallback_url
                images_elem = ET.SubElement(product, "Images")
                images_elem.set("PhotoDir", photo_dir)
                img_main = ET.SubElement(images_elem, "Image")
                img_main.set("name", main_filename)

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
# Сразу подгружаем список файлов с S3 — это одна операция на весь запуск
load_s3_file_list()

session = requests.Session()
retries = Retry(total=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))
session.mount('http://', HTTPAdapter(max_retries=retries))

auth = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
response = session.get(API_URL, headers={"Authorization": f"Basic {auth}"}, timeout=30)
response.raise_for_status()
data = response.json()

print(f"🔄 Загрузка данных завершена. Режим: {'LOCAL' if LOCAL_MODE else 'S3'}")
print("🔄 Обработка...")

all_items = []
rrc_items = []

total_products_full = 0
total_products_rrc = 0
excluded_zb = 0
excluded_article = 0
excluded_season = 0
excluded_cae = 0
excluded_low_price = 0
excluded_no_price = 0
excluded_from_full_rrc = 0
excluded_loss = 0

for item in data:
    name = item.get("name", "")
    if name.startswith("ЗБ"):
        excluded_zb += 1
        continue

    if EXCLUDE_CAE_ENABLED:
        cae = item.get("cae", "")
        if cae in EXCLUDED_CAE:
            excluded_cae += 1
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

    is_rrc = brand in IGNORE_COEFF_BRANDS

    final_price, status = calculate_final_price(item, diameter, is_rrc)

    if status == "no_price":
        excluded_no_price += 1
        continue
    if status == "low":
        excluded_low_price += 1
        continue
    if status == "loss":
        excluded_loss += 1
        continue
    if status == "manual":
        print(f"🔧 Ручная цена: {final_price} для {name} | article={article}")

    if is_rrc:
        rrc_items.append((item, diameter, final_price))
        total_products_rrc += 1
        if EXCLUDE_RRC_FROM_FULL:
            excluded_from_full_rrc += 1
        else:
            all_items.append((item, diameter, final_price))
            total_products_full += 1
    else:
        all_items.append((item, diameter, final_price))
        total_products_full += 1

print(f"🔍 Свитчер EXCLUDE_RRC_FROM_FULL = {EXCLUDE_RRC_FROM_FULL}")
print(f"🔍 Всего товаров для aztyre_full.xml: {total_products_full}")
print(f"🔍 Всего товаров для aztyre_rrc.xml:  {total_products_rrc}")
if EXCLUDE_RRC_FROM_FULL:
    print(f"🔍 Исключено РРЦ-позиций из full: {excluded_from_full_rrc}")
print(f"🔍 Исключено по CAE: {excluded_cae}")
print(f"🔍 Исключено без цены: {excluded_no_price}")
print(f"🔍 Исключено низкой цены (<{MIN_FINAL_PRICE}): {excluded_low_price}")
print(f"🔍 Исключено убыточных (не-РРЦ) (<price+{MIN_MARGIN}): {excluded_loss}")

# ===================== СТАТИСТИКА =====================
all_items_for_stats = all_items + rrc_items
brand_diameter_stats = defaultdict(lambda: defaultdict(lambda: {'sum': 0, 'count': 0}))
stats_count = 0
for item, diameter, final_price in all_items_for_stats:
    brand = item.get("brand", "").strip()
    if not brand:
        continue
    price = safe_float(item.get("price", 0))
    margin = final_price - price if price else final_price
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

# ===================== ВЫГРУЗКА ОТСУТСТВУЮЩИХ ФОТО =====================
missing_by_brand = defaultdict(set)

for item, diameter, final_price in all_items_for_stats:
    if not IMAGE_REPLACE_ENABLED:
        break
    brand = item.get("brand", "").strip()
    model = item.get("model", "").strip()
    if not brand or not model:
        continue

    brand_clean = clean_name(brand)
    model_clean = clean_name(model)
    rel = f"{brand_clean}/{brand_clean}_{model_clean}.jpg"
    if not check_image_exists_by_filename(rel):
        missing_by_brand[brand].add(f"{brand_clean}_{model_clean}.jpg")

with open("photos_missing.txt", "w", encoding="utf-8") as f:
    f.write("Список отсутствующих фото на S3 (Beget)\n")
    f.write("Формат: Бренд_Модель.jpg (без размеров)\n")
    f.write("=" * 60 + "\n\n")

    total_missing = 0
    for brand in sorted(missing_by_brand.keys()):
        files = sorted(missing_by_brand[brand])
        f.write(f"### {brand} ({len(files)} шт.)\n")
        for fname in files:
            f.write(f"  {fname}\n")
        f.write("\n")
        total_missing += len(files)

    f.write("=" * 60 + "\n")
    f.write(f"Всего отсутствующих моделей: {total_missing}\n")
    f.write(f"Брендов затронуто: {len(missing_by_brand)}\n")

print(f"📄 Список отсутствующих фото сохранён в photos_missing.txt")

# ===================== ЗАПИСЬ XML ФАЙЛОВ =====================
root_full = ET.Element("Products")
for item, diameter, final_price in all_items:
    add_product_to_root(root_full, item, diameter, final_price, replace_images=True)
tree_full = ET.ElementTree(root_full)
with open("aztyre_full.xml", "wb") as file:
    tree_full.write(file, encoding="utf-8", xml_declaration=True)
print(f"✅ Файл aztyre_full.xml создан: {total_products_full} товаров")

root_rrc = ET.Element("Products")
for item, diameter, final_price in rrc_items:
    add_product_to_root(root_rrc, item, diameter, final_price, replace_images=True)
tree_rrc = ET.ElementTree(root_rrc)
with open("aztyre_rrc.xml", "wb") as file:
    tree_rrc.write(file, encoding="utf-8", xml_declaration=True)
print(f"✅ Файл aztyre_rrc.xml создан: {total_products_rrc} товаров")

print(f"\n📊 ИТОГО:")
print(f"   - Позиций без цены (пропущено): {excluded_no_price}")
print(f"   - Позиций с ценой < {MIN_FINAL_PRICE} (пропущено): {excluded_low_price}")
print(f"   - Убыточных (не-РРЦ) позиций (пропущено): {excluded_loss}")
print(f"   - Ручных цен применено: {manual_price_count}")
print(f"   - РРЦ исключено из full: {excluded_from_full_rrc if EXCLUDE_RRC_FROM_FULL else 0}")

# ===================== ЗАГРУЗКА В S3 =====================
S3_ENABLED = not LOCAL_MODE

if S3_ENABLED:
    try:
        S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
        S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
        S3_BUCKET = os.getenv("S3_BUCKET", "fa5a823588a1-antontyres")
        S3_ENDPOINT = os.getenv("S3_ENDPOINT", "https://s3.ru1.storage.beget.cloud")

        if S3_ACCESS_KEY and S3_SECRET_KEY:
            if os.path.exists("aztyre_full.xml") and os.path.getsize("aztyre_full.xml") > 0:
                cmd_full = [
                    "s3cmd", "--config=/tmp/s3cfg", "put",
                    "aztyre_full.xml",
                    f"s3://{S3_BUCKET}/exports/aztyre_full.xml"
                ]
                print(f"🔍 Загружаем aztyre_full.xml...")
                result = subprocess.run(cmd_full, capture_output=True, text=True)
                if result.returncode == 0:
                    print(f"✅ aztyre_full.xml загружен в S3")
                else:
                    print(f"❌ Ошибка загрузки aztyre_full.xml: {result.stderr}")

            if os.path.exists("aztyre_rrc.xml") and os.path.getsize("aztyre_rrc.xml") > 0:
                cmd_rrc = [
                    "s3cmd", "--config=/tmp/s3cfg", "put",
                    "aztyre_rrc.xml",
                    f"s3://{S3_BUCKET}/exports/aztyre_rrc.xml"
                ]
                print(f"🔍 Загружаем aztyre_rrc.xml...")
                result = subprocess.run(cmd_rrc, capture_output=True, text=True)
                if result.returncode == 0:
                    print(f"✅ aztyre_rrc.xml загружен в S3")
                else:
                    print(f"❌ Ошибка загрузки aztyre_rrc.xml: {result.stderr}")
        else:
            print("⚠️ S3: не заданы ключи доступа, загрузка пропущена")
    except Exception as e:
        print(f"❌ Ошибка при загрузке в S3: {e}")
else:
    print("ℹ️ S3: загрузка отключена (LOCAL_MODE=True)")
