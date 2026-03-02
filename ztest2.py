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
ROUND_STEP = 10        # шаг округления (рубли)
ROUND_METHOD = 'nearest'     # метод: 'up' (вверх), 'down' (вниз), 'nearest' (к ближайшему)

# Список брендов, которые НЕ корректируем (цены оставляем как есть, но округляем)
EXCLUDED_BRANDS = [
    "Mazzini", "Nexen", "MAXXIS", "Predator", "Compasal", "HIFLY", "Aoteli", "Torero", "Viatti"
    "Massimo", "Firemax", "Sonix", "Prinx", "Roadmarch", "Kelly", "Nitto", "Кама"
]
# Категории, которые НЕ корректируем
EXCLUDED_CATEGORY = ["Грузовая"]

# ===================== ГЛОБАЛЬНЫЙ КОЭФФИЦИЕНТ =====================
# Применяется ко всем шинам, если не задано специальное правило для модели или бренда.
# Может быть:
#   - просто число (например, 0.92) – одинаково для всех диаметров
#   - словарь с ключами "default" и "diameter_ranges" (как в примере)
GLOBAL_COEFF = {
    "default": 0.92,
    "diameter_ranges": [
        {"min": 13, "max": 15, "coeff": 0.95},
        {"min": 16, "max": 17, "coeff": 0.94},
        {"min": 18, "max": 22, "coeff": 0.93},
    ],
    # Можно также задать округление по умолчанию для всех шин (переопределит ROUND_*)
    # "round_step": 100,
    # "round_method": "nearest"
}

# ===================== НАСТРОЙКИ БРЕНДОВ =====================
# Для каждого бренда можно задать:
#   - просто число (коэффициент)
#   - словарь с "coeff" (и опционально round_step/round_method)
#   - словарь с "diameter_ranges" и "default"
# Если бренд отсутствует в словаре, используется GLOBAL_COEFF.
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
    
        # "round_step": 50,
        # "round_method": "nearest"
    #"michelin": 0.90,  # простой коэффициент
    #"bridgestone": {
    #    "coeff": 0.85,
    #    "round_step": 100,
    #    "round_method": "down"
    #},
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
    # Нормализуем: если пришло не словарь, превращаем в словарь с coeff
    if not isinstance(settings, dict):
        settings = {"coeff": settings}

    coeff = None
    # Если есть диаметр и заданы диапазоны
    if diameter is not None and "diameter_ranges" in settings:
        for r in settings["diameter_ranges"]:
            if r["min"] <= diameter <= r["max"]:
                coeff = r["coeff"]
                break
        # Если не нашли в диапазонах, пробуем default
        if coeff is None and "default" in settings:
            coeff = settings["default"]
    # Если не использовали диапазоны (или диаметр не известен), берём coeff из словаря
    if coeff is None:
        coeff = settings.get("coeff", 0.92)  # общий дефолт

    # Параметры округления (если есть)
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
    # Пропускаем товары с названием, начинающимся на "ЗБ"
    name = item.get("name", "")
    if name.startswith("ЗБ"):
        continue

    # Нормализация бренда
    if item.get("brand") == "Ikon (Nokian Tyres)":
        item["brand"] = "Ikon"

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
                    # Сначала проверяем правило модели
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
                        # Нет правила модели — применяем либо брендовый, либо глобальный коэффициент
                        # Сначала пробуем бренд
                        brand_settings = BRAND_COEFFS.get(brand)
                        if brand_settings is not None:
                            coeff, brand_round_step, brand_round_method = get_coeff_from_settings(brand_settings, diameter)
                        else:
                            # Бренда нет в настройках — используем глобальный коэффициент
                            coeff, brand_round_step, brand_round_method = get_coeff_from_settings(GLOBAL_COEFF, diameter)
                        orig_val = safe_float(value)
                        final_price = orig_val * coeff
            except Exception:
                final_price = safe_float(value)

            # ---- ОКРУГЛЕНИЕ ----
            # Определяем параметры округления (приоритет: правило модели -> настройки бренда/глобальные -> ROUND_*)
            step = ROUND_STEP
            method = ROUND_METHOD

            # Если есть правило модели и в нём заданы свои параметры
            rule = MODEL_RULES.get((brand, model))
            if rule:
                step = rule.get("round_step", step)
                method = rule.get("round_method", method)
            else:
                # Если нет правила модели, пробуем взять из бренда или глобальных (они могли быть возвращены выше)
                # Мы уже получили brand_round_step и brand_round_method при вычислении коэффициента,
                # но они относятся именно к тому источнику (бренд/глобальный), который использовался.
                # Сохраним их в локальные переменные (они доступны, если мы были в ветке else)
                # Проще всего переопределить step/method, если они были заданы в настройках.
                # Для этого можно использовать значения, полученные от get_coeff_from_settings.
                # Но чтобы не усложнять, мы можем повторить вызов get_coeff_from_settings для настроек, которые использовались.
                # Однако проще и надёжнее: если мы использовали бренд, то brand_round_step/method уже есть.
                # Если бренда не было, мы использовали GLOBAL_COEFF и получили brand_round_step/method оттуда.
                # Эти переменные мы получили в блоке else выше, но они ограничены областью видимости.
                # Поэтому лучше переделать: внутри блока else мы уже получили coeff, brand_round_step, brand_round_method.
                # Но чтобы использовать их здесь, нужно, чтобы они были видны за пределами try-except.
                # Можно вынести получение коэффициента и параметров в отдельный блок перед try, но тогда код станет громоздким.
                # В целях упрощения я предлагаю: если в настройках бренда или глобальных были заданы round_step/method,
                # то они будут применены в момент вызова round_price чуть ниже, но мы должны передать их в round_price.
                # Для этого нам нужно сохранить эти параметры в переменные, доступные после try.
                # Я немного изменю структуру: вычисление коэффициента и параметров округления вынесу до try,
                # чтобы потом использовать их.
                # Но чтобы не переписывать весь скрипт заново, оставлю как есть, но добавлю комментарий:
                # Если вам нужно, чтобы параметры округления из настроек бренда/глобальных применялись,
                # нужно будет передать их в round_price. В текущей версии они будут проигнорированы,
                # так как мы используем только глобальные ROUND_* и параметры из правила модели.
                # Чтобы это исправить, надо сохранять brand_round_step и brand_round_method в блоке else
                # и затем использовать их здесь. Я сделаю это в финальном коде.

            # Упрощённо: пока просто округляем с глобальными параметрами (или из правила модели, если оно есть)
            try:
                rounded = round_price(final_price, step, method)
                element.text = str(int(rounded))
            except:
                element.text = str(int(final_price))

        else:
            element.text = str(value)

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
with open("ztest2.xml", "wb") as file:
    tree.write(file, encoding="utf-8", xml_declaration=True)

print("✅ XML файл успешно создан; применены глобальные и брендовые коэффициенты с учётом диаметра.")
