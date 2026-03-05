import os
import requests
import base64
import xml.etree.ElementTree as ET
import re

# ===================== НАСТРОЙКИ =====================
# Параметры подключения к API
API_URL = "https://ka2.sibzapaska.ru:16500/API/hs/V2/GetTires"
API_USER = "API_client"
API_PASSWORD = "rWp7mFWXRKOq"

# Параметры округления цен (применяются, если не переопределены ниже)
ROUND_STEP = 10          # шаг округления (рубли)
ROUND_METHOD = 'nearest' # метод: 'up', 'down', 'nearest'

INCLUDE_PRICE_TAG = False  # True для отладки, чтобы видеть исходную цену

# Список брендов, которые НЕ корректируем (цены оставляем как есть, но округляем)
EXCLUDED_BRANDS = [
    "Mazzini", "Nexen", "MAXXIS", "Predator", "Compasal", "HIFLY", "Aoteli",
    "Torero", "Viatti", "Massimo", "Firemax", "Sonix", "Prinx", "Roadmarch",
    "Kelly", "Nitto", "Кама"
]
# Категории, которые НЕ корректируем
EXCLUDED_CATEGORY = ["Грузовая"]

# ===================== ИСКЛЮЧЕНИЯ ПО АРТИКУЛАМ =====================
EXCLUDED_ARTICLES = [
    "2021724 Н/А",
    "T743460",
    "Х0000029644",
]
# ===================== ИСКЛЮЧЕНИЯ ПО CAE =====================
EXCLUDED_CAE = [
    "00000006983",
]
# ===================== ГЛОБАЛЬНЫЙ КОЭФФИЦИЕНТ =====================
# Применяется ко всем шинам, если нет специального правила для модели или бренда.
GLOBAL_COEFF = {
    "default": 0.92,
    "diameter_ranges": [
        {"min": 13, "max": 15, "coeff": 0.95},
        {"min": 16, "max": 17, "coeff": 0.94},
        {"min": 18, "max": 22, "coeff": 0.93},
    ],
}
# ===================== НАСТРОЙКИ БРЕНДОВ =====================
# Для каждого бренда можно задать:
#   - просто число (коэффициент)
#   - словарь с "coeff" (и опционально round_step/round_method)
#   - словарь с "diameter_ranges" и "default"
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
    # ... другие бренды
}

# ===================== НАСТРОЙКИ МОДЕЛЕЙ =====================
# Специальные правила для конкретных моделей (приоритет выше всего)
MODEL_RULES = {
    ("autograph", "autograph ice 9 suv"): {
        "type": "add_to_field_by_diameter",
        "field": "price",
        "ranges": [
            {"min": 16, "max": 17, "value": 1300},
            {"min": 18, "max": 19, "value": 1700},
        ],
        "default": None,
        # "round_step": 50,
        # "round_method": "nearest",
    },
    # Примеры других правил...
}

# ===================== ФУНКЦИИ =====================
def safe_float(val, default=0.0):
    """Безопасное преобразование в float. Возвращает default, если не удалось."""
    if val is None:
        return default
    try:
        return float(str(val).replace(",", ".").strip())
    except ValueError:
        return default

def round_price(price, step=None, method=None):
    """Округляет цену price до числа, кратного step."""
    if step is None:
        step = ROUND_STEP
    if method is None:
        method = ROUND_METHOD
    if method == 'down':
        return (price // step) * step
    elif method == 'up':
        return ((price + step - 1) // step) * step
    else:  # nearest
        return int(round(price / step) * step)

def get_coeff_from_settings(settings, diameter):
    """
    Универсальная функция для получения коэффициента и параметров округления
    из настроек (могут быть числом или словарём с diameter_ranges).
    Возвращает кортеж (coeff, round_step, round_method).
    Параметры округления могут отсутствовать (None).
    """
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
    """Создаёт элемент Product в указанном корне со всеми полями и логикой."""
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

            # Проверка на исключения
            is_excluded = (brand in [b.lower() for b in EXCLUDED_BRANDS] or
                           category in EXCLUDED_CATEGORY)

            # Вычисление итоговой цены
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
                        # Нет правила модели — применяем брендовый или глобальный коэффициент
                        brand_settings = BRAND_COEFFS.get(brand)
                        if brand_settings is not None:
                            coeff, _, _ = get_coeff_from_settings(brand_settings, diameter)
                        else:
                            coeff, _, _ = get_coeff_from_settings(GLOBAL_COEFF, diameter)
                        orig_val = safe_float(value)
                        final_price = orig_val * coeff
            except Exception:
                final_price = safe_float(value)

            # Округление
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

    # Добавление <inSet>
    inSet_elem = ET.SubElement(product, "inSet")
    inSet_elem.text = "1"

    # Добавление studded для Nortec LT 610
    if item.get("model") == "Nortec LT 610":
        studded_elem = ET.SubElement(product, "studded")
        studded_elem.text = "Нет"

    # Определение тега (tyres/disk)
    nomenclature = item.get("Номенклатура", "")
    if re.match(r'^(1[2-9]|2[0-4])\s', nomenclature):
        product.tag = "disk"
    else:
        product.tag = "tyres"

# ===================== ПОЛУЧЕНИЕ ДАННЫХ ИЗ API =====================
auth = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
response = requests.get(API_URL, headers={"Authorization": f"Basic {auth}"})
response.raise_for_status()
data = response.json()

# ===================== СОЗДАНИЕ XML =====================
# Основной корень
root = ET.Element("Products")

# Дополнительные корни для разных диаметров
extra_roots = {
    '15': ET.Element("Products"),
    '16': ET.Element("Products"),
    '17': ET.Element("Products"),
    '18': ET.Element("Products"),
    '19_20': ET.Element("Products"),
    '21_24': ET.Element("Products"),
}

total_products = 0
excluded_zb = 0
excluded_diameter = 0  # больше не используется, но оставим для совместимости
excluded_article = 0
excluded_cae = 0
excluded_name = 0
diameter_count = {}

for item in data:
    name = item.get("name", "")
    if name.startswith("ЗБ"):
        excluded_zb += 1
        continue

    # Нормализация бренда
    if item.get("brand") == "Ikon (Nokian Tyres)":
        item["brand"] = "Ikon"

    # Удаление "(Nokian Tyres)" из названия
    if "name" in item and "(Nokian Tyres)" in item["name"]:
        item["name"] = item["name"].replace("(Nokian Tyres)", "").strip()
        item["name"] = re.sub(r'\s+', ' ', item["name"])
        name = item["name"]

    # Замена моделей
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

    # Извлечение диаметра
    diameter = safe_float(item.get("diameter"), default=None)
    if diameter is None:
        nomenclature = item.get("Номенклатура", "")
        match = re.search(r'[Rr](\d{2})', nomenclature)
        if match:
            diameter = float(match.group(1))

      # ---- Исключение по артикулу (новая проверка) ----
    article = item.get("article", "")
    if any(phrase in article for phrase in EXCLUDED_ARTICLES):
        excluded_article += 1
        continue

    cae = item.get("cae", "")
    if any(phrase in cae for phrase in EXCLUDED_CAE):
        excluded_cae += 1
        continue

    # Товар прошёл все фильтры
    total_products += 1
    if diameter is not None:
        d_int = int(diameter)
        diameter_count[d_int] = diameter_count.get(d_int, 0) + 1
    else:
        diameter_count['unknown'] = diameter_count.get('unknown', 0) + 1

    # Добавление в основной файл (исключая диаметр 15)
    if diameter != 15:
        add_product_to_root(root, item, diameter)

    # Добавление в дополнительные файлы по диапазонам
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
    # Товары с неизвестным диаметром никуда не добавляем в дополнительные

# Сохранение основного файла
tree = ET.ElementTree(root)
with open("aztyre.xml", "wb") as file:
    tree.write(file, encoding="utf-8", xml_declaration=True)

# Сохранение дополнительных файлов
file_names = {
    '15': "ztyrer15.xml",
    '16': "ztyrer16.xml",
    '17': "ztyrer17.xml",
    '18': "ztyrer18.xml",
    '19_20': "ztyrer1920.xml",
    '21_24': "ztyrer2024.xml",
}
for key, xml_root in extra_roots.items():
    if len(xml_root) > 0:  # если есть товары
        tree = ET.ElementTree(xml_root)
        with open(file_names[key], "wb") as file:
            tree.write(file, encoding="utf-8", xml_declaration=True)

# Вывод статистики
print(f"✅ XML файлы успешно созданы.")
print(f"   - Пропущено (ЗБ): {excluded_zb}")
print(f"   - Исключено по артикулу: {excluded_article}")
print(f"   - Всего обработано (в XML): {total_products}")
print(f"   - Всего обработано (в XML): {excluded_cae}")
print(f"\n📊 Статистика по диаметрам:")
if diameter_count:
    for d in sorted([k for k in diameter_count if k != 'unknown']):
        print(f"   - {d}\" : {diameter_count[d]} шт.")
    if 'unknown' in diameter_count:
        print(f"   - диаметр не определён : {diameter_count['unknown']} шт.")
else:
    print("   (нет данных)")
