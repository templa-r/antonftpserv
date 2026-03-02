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

# Список брендов, которые НЕ корректируем (цены оставляем как есть, но округляем)
EXCLUDED_BRANDS = [
    "Mazzini", "Nexen", "MAXXIS", "Predator", "Compasal", "HIFLY", "Aoteli",
    "Torero", "Viatti", "Massimo", "Firemax", "Sonix", "Prinx", "Roadmarch",
    "Kelly", "Nitto", "Кама"
]
# Категории, которые НЕ корректируем
EXCLUDED_CATEGORY = ["Грузовая"]

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

# ===================== ПОЛУЧЕНИЕ ДАННЫХ ИЗ API =====================
auth = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
response = requests.get(API_URL, headers={"Authorization": f"Basic {auth}"})
response.raise_for_status()
data = response.json()

# ===================== СОЗДАНИЕ XML =====================
root = ET.Element("Products")

for item in data:
    name = item.get("name", "")
    if name.startswith("ЗБ"):
        continue

    # Нормализация бренда
    if item.get("brand") == "Ikon (Nokian Tyres)":
        item["brand"] = "Ikon"

    # Удаление "(Nokian Tyres)" из названия
    if "name" in item and "(Nokian Tyres)" in item["name"]:
        item["name"] = item["name"].replace("(Nokian Tyres)", "").strip()
        item["name"] = re.sub(r'\s+', ' ', item["name"])

    product = ET.SubElement(root, "Product")

    for key, value in item.items():
        if key == "Оптовая_Цена":
            continue

        element = ET.SubElement(product, key)

        if key.lower() == "retail":
            brand = item.get("brand", "").strip().lower()
            model = item.get("model", "").strip().lower()
            category = item.get("category", "")

            # ---- Извлечение диаметра ----
            diameter = safe_float(item.get("diameter"), default=None)
            if diameter is None:
                nomenclature = item.get("Номенклатура", "")
                match = re.search(r'[Rr](\d{2})', nomenclature)
                if match:
                    diameter = float(match.group(1))

            # ---- Проверка на исключения ----
            is_excluded = (brand in [b.lower() for b in EXCLUDED_BRANDS] or
                           category in EXCLUDED_CATEGORY)

            # ---- Вычисление итоговой цены ----
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

            # ---- ОКРУГЛЕНИЕ ----
            step = ROUND_STEP
            method = ROUND_METHOD
            rule = MODEL_RULES.get((brand, model))
            if rule:
                step = rule.get("round_step", step)
                method = rule.get("round_method", method)
            # (здесь можно добавить использование round_step/method из настроек бренда/глобальных,
            # но для простоты пока оставим так)

            try:
                rounded = round_price(final_price, step, method)
                element.text = str(int(rounded))
            except:
                element.text = str(int(final_price))

        else:
            element.text = str(value)

    # ----- ДОБАВЛЕНИЕ <inSet> -----
    inSet_elem = ET.SubElement(product, "inSet")
    inSet_elem.text = "1"

    # Добавление поля studded для Nortec LT 610
    if item.get("model") == "Nortec LT 610":
        studded_elem = ET.SubElement(product, "studded")
        studded_elem.text = "Нет"

    # Определение тега (tyres/disk)
    nomenclature = item.get("Номенклатура", "")
    if re.match(r'^(1[2-9]|2[0-4])\s', nomenclature):
        product.tag = "disk"
    else:
        product.tag = "tyres"

# Сохранение XML
tree = ET.ElementTree(root)
with open("aztyre.xml", "wb") as file:
    tree.write(file, encoding="utf-8", xml_declaration=True)

print("✅ XML файл успешно создан; применены глобальные и брендовые коэффициенты с учётом диаметра.")
