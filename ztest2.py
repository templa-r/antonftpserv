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

# Параметры округления цен (применяются ко всем товарам, включая исключения)
ROUND_STEP = 10        # шаг округления (рубли)
ROUND_METHOD = 'nearest'     # метод: 'up' (вверх), 'down' (вниз), 'nearest' (к ближайшему)

# Список брендов, которые НЕ корректируем (цены оставляем как есть, но округляем)
EXCLUDED_BRANDS = [
    "Mazzini", "Nexen", "MAXXIS", "Predator", "Compasal",
    "Massimo", "Firemax", "Sonix", "Prinx", "Roadmarch"
]
# Категории, которые НЕ корректируем
EXCLUDED_CATEGORY = ["Грузовая"]

# Специальные коэффициенты для брендов (если нет индивидуального правила для модели)
# Формат: "бренд": {"coeff": коэффициент, "round_step": шаг (опц.), "round_method": метод (опц.)}
# Если round_step/method не указаны, используются глобальные ROUND_STEP и ROUND_METHOD
BRAND_COEFFS = {
    "ikon": {"coeff": 0.90},          # -12% для Ikon
    # "michelin": {"coeff": 0.90},     # пример для другого бренда
    # "bridgestone": {"coeff": 0.85, "round_step": 50, "round_method": "nearest"},
}

# Специальные правила для конкретных моделей (приоритет выше, чем брендовые коэффициенты)
# Ключ — кортеж (бренд, модель) в нижнем регистре
MODEL_RULES = {
    ("autograph", "autograph ice 9 suv"): {
        "type": "add_to_field_by_diameter",    # тип правила
        "field": "price",                        # из какого поля брать базу
        "ranges": [                               # диапазоны диаметров
            {"min": 16, "max": 17, "value": 1300},
            {"min": 18, "max": 19, "value": 1700},
            # добавьте другие диапазоны
        ],
        "default": None,                          # значение, если диаметр вне диапазонов (None = не менять)
        # "round_step": 50,                        # можно переопределить округление для этой модели
        # "round_method": "nearest",
    },
    # Пример правила с фиксированной ценой:
    # ("nokian", "hakkapeliitta 10"): {
    #     "type": "fixed",
    #     "value": 12000,
    #     "round_step": 100,
    #     "round_method": "down",
    # },
    # Пример простого добавления к полю (без диаметра):
    # ("toyo", "observe gsi-6"): {
    #     "type": "add_to_field",
    #     "field": "price",
    #     "value": 800,
    # },
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
    """Округляет цену price до числа, кратного step.
       Если step или method не указаны, используются глобальные значения.
    """
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

# ===================== ПОЛУЧЕНИЕ ДАННЫХ ИЗ API =====================
auth = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
response = requests.get(API_URL, headers={"Authorization": f"Basic {auth}"})
response.raise_for_status()
data = response.json()

# ===================== СОЗДАНИЕ XML =====================
root = ET.Element("Products")

# Преобразование каждого товара
for item in data:
    # Пропускаем товары, у которых название начинается с "ЗБ"
    name = item.get("name", "")
    if name.startswith("ЗБ"):
        continue

    # ----- НОРМАЛИЗАЦИЯ БРЕНДА -----
    # Если бренд пришёл как "Ikon (Nokian Tyres)", заменяем на "Ikon"
    if item.get("brand") == "Ikon (Nokian Tyres)":
        item["brand"] = "Ikon"

    # Создаём элемент Product (позже тег может быть изменён)
    product = ET.SubElement(root, "Product")

    # Перебираем все поля товара
    for key, value in item.items():
        if key == "Оптовая_Цена":
            continue  # пропускаем это поле

        element = ET.SubElement(product, key)

        # ----- ОБРАБОТКА ПОЛЯ retail (розничная цена) -----
        if key.lower() == "retail":
            brand = item.get("brand", "").strip().lower()
            model = item.get("model", "").strip().lower()
            category = item.get("category", "")

            # ---- Извлечение диаметра (для правил, зависящих от диаметра) ----
            diameter = safe_float(item.get("diameter"), default=None)
            if diameter is None:
                # Пробуем извлечь из "Номенклатура" (например, "R16")
                nomenclature = item.get("Номенклатура", "")
                match = re.search(r'[Rr](\d{2})', nomenclature)
                if match:
                    diameter = float(match.group(1))

            # ---- Определяем, попадает ли товар в исключения ----
            is_excluded = (brand in [b.lower() for b in EXCLUDED_BRANDS] or
                           category in EXCLUDED_CATEGORY)

            # ---- Итоговая цена (сначала вычисляем, потом округлим) ----
            final_price = None

            try:
                # Если товар в исключениях — берём исходное retail без изменений
                if is_excluded:
                    final_price = safe_float(value)
                else:
                    # Проверяем, есть ли правило для конкретной модели
                    rule = MODEL_RULES.get((brand, model))
                    if rule:
                        rule_type = rule["type"]
                        if rule_type == "fixed":
                            final_price = safe_float(rule["value"])
                        elif rule_type == "add_to_field":
                            base_field = rule["field"]
                            base_val = safe_float(item.get(base_field, "0"))
                            final_price = base_val + rule["value"]
                        elif rule_type == "add_to_field_by_diameter":
                            if diameter is not None:
                                add_value = None
                                for r in rule["ranges"]:
                                    if r["min"] <= diameter <= r["max"]:
                                        add_value = r["value"]
                                        break
                                if add_value is not None:
                                    base_field = rule["field"]
                                    base_val = safe_float(item.get(base_field, "0"))
                                    final_price = base_val + add_value
                                else:
                                    # Если диаметр вне диапазонов, используем default (если есть)
                                    if rule.get("default") is not None:
                                        base_field = rule["field"]
                                        base_val = safe_float(item.get(base_field, "0"))
                                        final_price = base_val + rule["default"]
                                    else:
                                        # Оставляем исходное retail
                                        final_price = safe_float(value)
                            else:
                                # Диаметр не определён — оставляем исходное
                                final_price = safe_float(value)
                        else:
                            # Неизвестный тип правила — оставляем исходное
                            final_price = safe_float(value)
                    else:
                        # Нет правила для модели — применяем брендовый коэффициент
                        coeff_info = BRAND_COEFFS.get(brand, {"coeff": 0.92})
                        coeff = coeff_info["coeff"]
                        orig_val = safe_float(value)
                        final_price = orig_val * coeff
            except Exception as e:
                # Если что-то пошло не так, оставляем исходное значение
                final_price = safe_float(value)

            # ---- ОКРУГЛЕНИЕ (применяется ко всем товарам) ----
            # Определяем параметры округления
            step = ROUND_STEP
            method = ROUND_METHOD

            # Если есть правило модели и в нём заданы свои параметры
            rule = MODEL_RULES.get((brand, model))
            if rule:
                step = rule.get("round_step", step)
                method = rule.get("round_method", method)
            else:
                # Если нет правила модели, но есть настройки бренда
                coeff_info = BRAND_COEFFS.get(brand)
                if coeff_info:
                    step = coeff_info.get("round_step", step)
                    method = coeff_info.get("round_method", method)

            # Округляем и приводим к целому (int)
            try:
                rounded = round_price(final_price, step, method)
                element.text = str(int(rounded))
            except:
                # Если округление не удалось, пишем хотя бы целую часть от final_price
                element.text = str(int(final_price))

        else:
            # Для всех остальных полей — просто копируем значение
            element.text = str(value)

    # ----- ДОБАВЛЕНИЕ ПОЛЯ studded ДЛЯ Nortec LT 610 -----
    if item.get("model") == "Nortec LT 610":
        studded_elem = ET.SubElement(product, "studded")
        studded_elem.text = "Нет"

    # ----- ОПРЕДЕЛЕНИЕ ТЕГА (tyres или disk) НА ОСНОВЕ НОМЕНКЛАТУРЫ -----
    nomenclature = item.get("Номенклатура", "")
    if re.match(r'^(1[2-9]|2[0-4])\s', nomenclature):
        product.tag = "disk"
    else:
        product.tag = "tyres"

# ===================== СОХРАНЕНИЕ XML =====================
tree = ET.ElementTree(root)
with open("ztest2.xml", "wb") as file:
    tree.write(file, encoding="utf-8", xml_declaration=True)

print("✅ XML файл успешно создан; цены округлены, применены скидки и правила.")
