import requests
import xml.etree.ElementTree as ET
import re
import os
import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Tuple, Dict, Any

# ===================== НАСТРОЙКИ =====================
ROUND_STEP = 10
ROUND_METHOD = 'nearest'
INCLUDE_PRICE_TAG = False

# Фильтры (оставлены без изменений)
SEASON_EXCLUDE_ENABLED = False
SEASON_EXCLUDE_VALUE = "зима"

EXCLUDED_BRANDS = [
    "Mazzini", "Nexen", "MAXXIS", "Predator", "Compasal", "HIFLY", "Aoteli",
    "Torero", "Viatti", "Massimo", "Firemax", "Sonix", "Prinx", "Roadmarch",
    "Kelly", "Nitto", "Кама"
]
EXCLUDED_BRANDS_FROM_EXPORT = ["Compasal", "Aoteli"]
EXCLUDED_CATEGORY = ["Грузовая"]          # грузовые НЕ исключаются из выгрузки (оставляем)
EXCLUDED_ARTICLES = []

# Коэффициенты цен (без изменений)
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
    round_step = settings.get("round_step")
    round_method = settings.get("round_method")
    return coeff, round_step, round_method

def clean_name(s):
    return re.sub(r'[^\w\-]', '_', s)

def fetch_xml(url):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return ET.fromstring(response.content)

def normalize_fields(elem):
    """Приведение имён тегов к единому виду"""
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
        "rest_novosib3": "rest_nsk",               # остаток в Новосибирске
        "price_novosib3_rozn": "price_novosib3_rozn",  # розница Новосибирск
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
    """Определяем, является ли товар шиной (легковой или грузовой)"""
    category = item.get("category", "")
    if category in ("Легковая", "Грузовая"):
        return True
    width = item.get("width", "")
    height = item.get("height", "")
    diam = item.get("diameter", "")
    if width and height and diam:
        return True
    return False

def extract_photo_dir_and_main(img_url: str) -> Tuple[str, str]:
    """
    Из URL вида https://.../path/dir/filename.png
    возвращает (PhotoDir, PhotoMain)
    Пример: https://4tochki.ru/pictures/tyres/Yokohama/iceGuard_Studless_iG50Plus/small/0.png
    -> ('https://4tochki.ru/pictures/tyres/Yokohama/iceGuard_Studless_iG50Plus/small/', '0.png')
    """
    if not img_url:
        return "", ""
    # Отделяем последний компонент (имя файла)
    parts = img_url.rsplit('/', 1)
    if len(parts) == 2:
        return parts[0] + '/', parts[1]
    else:
        return "", img_url

def add_tyre_to_root(root, item: Dict, diameter: float,
                     rename_map: Optional[Dict[str, str]] = None,
                     remove_fields: Optional[List[str]] = None):
    """
    Добавляет товар в XML-корень.
    - rename_map: словарь замены имён полей (например {'rest_nsk': 'rest'})
    - remove_fields: список полей, которые НЕ нужно выводить
    - изображения формируются только из item['img_big_my'] (если есть)
    """
    product = ET.SubElement(root, "tyres")

    # Сначала подготовим итоговый словарь полей
    output_data = dict(item)

    # Переименовываем поля
    if rename_map:
        for old, new in rename_map.items():
            if old in output_data:
                output_data[new] = output_data.pop(old)

    # Удаляем ненужные поля
    if remove_fields is None:
        remove_fields = []
    # Всегда удаляем все img_* поля, кроме тех, что используются для Images
    img_keys = [k for k in output_data.keys() if k.startswith('img_') and k != 'img_big_my']
    remove_fields.extend(img_keys)
    # Дополнительно удаляем исходные rest/retail, если они были переименованы (чтобы не дублировать)
    if rename_map:
        if 'rest_nsk' in rename_map:
            remove_fields.append('rest')        # удаляем старый общий остаток
        if 'price_novosib3_rozn' in rename_map:
            remove_fields.append('retail')      # удаляем старую розницу
    # Также удаляем служебные поля
    remove_fields.extend(['Оптовая_Цена', 'img'])
    for field in set(remove_fields):
        output_data.pop(field, None)

    # Выводим все оставшиеся поля
    for key, value in output_data.items():
        if key == "price" and not INCLUDE_PRICE_TAG:
            continue
        element = ET.SubElement(product, key)
        # Розничная цена (retail) проходит через коэффициенты, если это не запрещено
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

    # --- Обработка изображений: только img_big_my ---
    img_big_my = item.get("img_big_my", "")
    if img_big_my:
        photo_dir, photo_main = extract_photo_dir_and_main(img_big_my)
        if photo_dir and photo_main:
            images_elem = ET.SubElement(product, "Images")
            images_elem.set("PhotoDir", photo_dir)
            images_elem.set("PhotoMain", photo_main)

    # Добавляем признак "входит в набор"
    inSet_elem = ET.SubElement(product, "inSet")
    inSet_elem.text = "1"

def save_tyres_to_xml(items_with_diam: List[Tuple[Dict, float]], output_file: str,
                      rename_map: Optional[Dict[str, str]] = None,
                      remove_fields: Optional[List[str]] = None):
    """Сохраняет список товаров в XML-файл."""
    root_out = ET.Element("Products")
    for item, diameter in items_with_diam:
        add_tyre_to_root(root_out, item, diameter, rename_map, remove_fields)
    tree = ET.ElementTree(root_out)
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    print(f"✅ Файл сохранён: {output_file} (шин: {len(items_with_diam)})")

def main():
    url = "https://b2b.4tochki.ru/export_data/M35352.xml"
    print("Загрузка данных...")
    root_xml = fetch_xml(url)
    all_elements = root_xml.findall(".//tires")
    print(f"Найдено элементов tires: {len(all_elements)}")

    # Списки для разделения
    nsk_items = []      # будут в 4tyres_nsk2.xml
    order_items = []    # будут в 4tyre_test2.xml

    for elem in all_elements:
        item = normalize_fields(elem)

        # --- Фильтры (бренды, артикулы, сезон) ---
        brand = item.get("brand", "")
        if brand in EXCLUDED_BRANDS_FROM_EXPORT:
            continue

        article = item.get("article", "")
        if any(phrase in article for phrase in EXCLUDED_ARTICLES):
            continue

        if SEASON_EXCLUDE_ENABLED:
            season = item.get("season", "")
            if season == SEASON_EXCLUDE_VALUE:
                continue

        # Определяем диаметр
        diameter = safe_float(item.get("diameter"), default=None)
        if diameter is None:
            name = item.get("name", "")
            match = re.search(r'[Rr](\d{2})', name)
            if match:
                diameter = float(match.group(1))

        # Проверяем, является ли товар шиной
        if not is_tyre(item, diameter):
            continue

        # --- Условие для наличия в НСК ---
        rest_nsk = safe_float(item.get("rest_nsk", 0))
        price_nsk = item.get("price_novosib3_rozn", "").strip()
        has_nsk_stock = (rest_nsk > 0) and (price_nsk != "")

        if has_nsk_stock:
            # Для НСК: переименуем rest_nsk -> rest, price_novosib3_rozn -> retail
            # и удалим исходные поля rest и retail (общий остаток и старая розница)
            nsk_items.append((item, diameter))
        else:
            # Для "под заказ": удалим поля rest_nsk и price_novosib3_rozn, чтобы они не попали в XML
            order_items.append((item, diameter))

    print(f"📦 Товаров с наличием в НСК: {len(nsk_items)}")
    print(f"📦 Товаров под заказ: {len(order_items)}")

    # Сохраняем файл с наличием в НСК (с переименованием)
    if nsk_items:
        rename_map = {
            "rest_nsk": "rest",
            "price_novosib3_rozn": "retail"
        }
        # Удаляем исходные rest, retail, а также все img_* кроме img_big_my (удалится автоматически)
        save_tyres_to_xml(nsk_items, "4tyres_nsk2.xml",
                          rename_map=rename_map,
                          remove_fields=["rest", "retail"])
    else:
        print("Нет товаров с наличием в НСК — файл 4tyres_nsk2.xml не создан.")

    # Сохраняем файл "под заказ" (без специфических полей НСК)
    if order_items:
        # Удаляем поля, связанные с НСК
        save_tyres_to_xml(order_items, "4tyre_test2.xml",
                          remove_fields=["rest_nsk", "price_novosib3_rozn"])
    else:
        print("Нет товаров под заказ — файл 4tyre_test2.xml не создан.")

    print("\n✅ Все XML файлы успешно созданы.")

if __name__ == "__main__":
    main()
