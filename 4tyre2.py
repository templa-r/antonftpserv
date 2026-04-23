import requests
import xml.etree.ElementTree as ET
import re
from typing import Dict, List, Tuple, Optional

# ===================== НАСТРОЙКИ =====================
ROUND_STEP = 10
ROUND_METHOD = 'nearest'
INCLUDE_PRICE_TAG = False

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

def extract_photo_dir_and_main(img_url: str) -> Tuple[str, str]:
    if not img_url:
        return "", ""
    parts = img_url.rsplit('/', 1)
    if len(parts) == 2:
        return parts[0] + '/', parts[1]
    else:
        return "", img_url

def fetch_xml(url):
    headers = {"User-Agent": "Mozilla/5.0"}
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
    if width and height and diam:
        return True
    return False

def prepare_nsk_item(original_item: Dict, diameter: float) -> Dict:
    """
    Создаёт новый словарь для NSK-файла:
    - rest = original_item['rest_nsk']
    - retail = original_item['price_novosib3_rozn'] (будет обработан коэффициентами позже)
    - копирует остальные нужные поля (без price_* и rest_*)
    - img_big_my сохраняется во временном поле _img_big_my для последующего формирования Images
    """
    new_item = {}
    # Переименованные поля
    new_item['rest'] = original_item.get('rest_nsk', '0')
    new_item['retail'] = original_item.get('price_novosib3_rozn', '0')
    
    # Список полей, которые нужно скопировать как есть (без price_*, rest_*, img_*)
    allowed_fields = [
        'brand', 'model', 'width', 'height', 'diameter', 'load_index', 'speed_index',
        'season', 'category', 'camera', 'name', 'noise', 'design', 'gtin', 'weight', 'volume'
    ]
    for field in allowed_fields:
        if field in original_item:
            new_item[field] = original_item[field]
    
    # Сохраняем img_big_my отдельно (не будет выведен как тег)
    if 'img_big_my' in original_item:
        new_item['_img_big_my'] = original_item['img_big_my']
    
    return new_item

def add_tyre_to_root(root, item: Dict, diameter: float):
    """
    Добавляет товар в XML-корень.
    Обрабатывает retail через коэффициенты, добавляет Images из _img_big_my.
    """
    product = ET.SubElement(root, "tyres")
    output_data = dict(item)
    
    # Извлекаем URL картинки и удаляем временное поле, чтобы оно не попало в вывод
    img_url = output_data.pop('_img_big_my', None)
    
    # Выводим все поля, кроме price (если INCLUDE_PRICE_TAG=False)
    for key, value in output_data.items():
        if key == "price" and not INCLUDE_PRICE_TAG:
            continue
        element = ET.SubElement(product, key)
        
        # Логика обработки розничной цены
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
    
    # Блок Images (только если есть img_url)
    if img_url:
        photo_dir, photo_main = extract_photo_dir_and_main(img_url)
        if photo_dir and photo_main:
            images_elem = ET.SubElement(product, "Images")
            images_elem.set("PhotoDir", photo_dir)
            images_elem.set("PhotoMain", photo_main)
    
    # Всегда добавляем inSet
    inSet_elem = ET.SubElement(product, "inSet")
    inSet_elem.text = "1"

def save_tyres_to_xml(items_with_diam: List[Tuple[Dict, float]], output_file: str):
    """Сохраняет список товаров в XML-файл."""
    root_out = ET.Element("Products")
    for item, diameter in items_with_diam:
        add_tyre_to_root(root_out, item, diameter)
    tree = ET.ElementTree(root_out)
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    print(f"✅ Файл сохранён: {output_file} (шин: {len(items_with_diam)})")

def main():
    url = "https://b2b.4tochki.ru/export_data/M35352.xml"
    print("Загрузка данных...")
    root_xml = fetch_xml(url)
    all_elements = root_xml.findall(".//tires")
    print(f"Найдено элементов tires: {len(all_elements)}")
    
    nsk_items = []   # для 4tyres_nsk2.xml
    order_items = [] # для 4tyre_test2.xml
    
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
        
        # Условие для NSK: rest_nsk > 0 и price_novosib3_rozn не пусто
        rest_nsk = safe_float(item.get("rest_nsk", 0))
        price_nsk = item.get("price_novosib3_rozn", "").strip()
        has_nsk_stock = (rest_nsk > 0) and (price_nsk != "")
        
        if has_nsk_stock:
            # Подготавливаем очищенный словарь для NSK
            prepared = prepare_nsk_item(item, diameter)
            nsk_items.append((prepared, diameter))
        else:
            # Для "под заказ" удаляем только поля NSK, остальное оставляем как есть (без очистки)
            # Но чтобы не плодить лишние price_* в заказе, лучше тоже почистить? 
            # По условию задачи этого не требуется, оставляем как раньше.
            order_items.append((item, diameter))
    
    print(f"📦 Товаров с наличием в НСК: {len(nsk_items)}")
    print(f"📦 Товаров под заказ: {len(order_items)}")
    
    if nsk_items:
        save_tyres_to_xml(nsk_items, "4tyres_nsk2.xml")
    else:
        print("Нет товаров с наличием в НСК — файл 4tyres_nsk2.xml не создан.")
    
    if order_items:
        # Для заказа используем старую логику (без переименований, просто удаляем rest_nsk и price_novosib3_rozn)
        # Вызовем отдельную функцию, которая создаст XML с удалением этих полей.
        # Чтобы не дублировать код, можно передать remove_fields, но для простоты создадим копию.
        cleaned_order = []
        for item, diam in order_items:
            # Удаляем поля NSK
            clean_item = {k: v for k, v in item.items() if k not in ['rest_nsk', 'price_novosib3_rozn']}
            cleaned_order.append((clean_item, diam))
        save_tyres_to_xml(cleaned_order, "4tyre_test2.xml")
    else:
        print("Нет товаров под заказ — файл 4tyre_test2.xml не создан.")
    
    print("\n✅ Все XML файлы успешно созданы.")

if __name__ == "__main__":
    main()
