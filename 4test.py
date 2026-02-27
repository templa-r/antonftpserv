import requests
import xml.etree.ElementTree as ET
import re
import os
from typing import Optional

def fetch_xml(url):
    response = requests.get(url)
    response.raise_for_status()
    return ET.fromstring(response.content)

def normalize_fields(item):
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
        "img_big_my": "img_small",
        "brand": "brand"
    }
    
    normalized_item = {}
    for elem in item:
        tag = field_map.get(elem.tag, elem.tag)
        normalized_item[tag] = elem.text if elem.text else ""
    return normalized_item

def _to_number(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    s = text.strip().replace(' ', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None

def adjust_retail_prices_plus5(root: ET.Element, exclude_brands: set = None):
    """
    Увеличивает значения всех тегов *_rozn на 5% (скидка 5%).
    Параметр exclude_brands: множество брендов, для которых скидка НЕ применяется.
    """
    if exclude_brands is None:
        exclude_brands = set()
    # Приводим исключаемые бренды к нижнему регистру и убираем пробелы
    exclude_brands_clean = {b.lower().strip() for b in exclude_brands}
    
    # Для отладки: собираем все уникальные бренды в этом файле
    all_brands = set()
    for item in root.findall(".//item"):
        brand_elem = item.find("brand")
        if brand_elem is not None and brand_elem.text:
            brand_clean = brand_elem.text.strip()
            all_brands.add(brand_clean)
    print(f"[{root}] Бренды в файле: {all_brands}")
    print(f"Исключаемые бренды (после очистки): {exclude_brands_clean}")

    for item in root.findall(".//item"):
        brand_elem = item.find("brand")
        brand = brand_elem.text.lower().strip() if brand_elem is not None and brand_elem.text else None

        if brand and brand in exclude_brands_clean:
            # Пропускаем товар – скидка не применяется
            continue

        # Применяем скидку ко всем *_rozn
        tag_map = {child.tag: child for child in list(item)}
        for tag, elem in tag_map.items():
            if not tag.endswith("_rozn"):
                continue

            rozn_val = _to_number(elem.text)
            base_val = None
            if rozn_val is None:
                base_tag = tag[:-5]
                base_elem = tag_map.get(base_tag)
                if base_elem is not None:
                    base_val = _to_number(base_elem.text)

            source_val = rozn_val if rozn_val is not None else base_val
            if source_val is None:
                continue

            new_val = int(source_val * 0.95)  # округление вниз
            elem.text = str(new_val)

def filter_and_save_items(api_url, output_file, filter_tag=None,
                          include_tag=None, include_value=None, status=None,
                          exclude_brands: set = None):
    """Фильтрует товары, приводит поля к общему формату, применяет скидку (кроме исключённых брендов) и сохраняет в XML."""
    root = fetch_xml(api_url)
    new_root = ET.Element("items")

    # Определяем путь к элементам в зависимости от источника
    path = ".//tires" if "4tochki" in api_url else ".//item"
    for item in root.findall(path):
        # Нормализация полей
        normalized_item = normalize_fields(item)

        # Проверка на LT610 (особенность)
        model_elem = item.find('categoryname')
        is_lt610 = model_elem is not None and model_elem.text == 'LT610'
        if is_lt610:
            normalized_item['thorn'] = 'Липучка'

        # Уникальный идентификатор
        cae = normalized_item.get("cae")
        unique_id = cae or normalized_item.get("article")
        if not unique_id:
            continue

        # Фильтр по include_tag/include_value
        if include_tag and include_value:
            include_element = item.find(include_tag)
            if include_element is None or include_element.text != include_value:
                continue

        # Фильтр по наличию filter_tag (например, rest_novosib3)
        rest_element = item.find(filter_tag) if filter_tag else None
        if (filter_tag and rest_element is not None) or (not filter_tag and item.find("rest_novosib3") is None):
            new_item = ET.SubElement(new_root, "item")
            if status:
                status_elem = ET.SubElement(new_item, "status")
                status_elem.text = status
            for tag, text in normalized_item.items():
                new_elem = ET.SubElement(new_item, tag)
                new_elem.text = text
            # Дополнительная проверка для LT610 (на случай, если нормализация не сработала)
            model_elem_new = new_item.find('model')
            if model_elem_new is not None and model_elem_new.text == 'LT610':
                thorn_elem = new_item.find('thorn')
                if thorn_elem is None:
                    thorn_elem = ET.SubElement(new_item, 'thorn')
                thorn_elem.text = 'Липучка'

    # Применяем скидку к текущему корню (с исключением брендов)
    adjust_retail_prices_plus5(new_root, exclude_brands)

    # Сохраняем в файл
    tree = ET.ElementTree(new_root)
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return new_root

def main():
    url1 = "https://b2b.4tochki.ru/export_data/M35352.xml"

    # Задаём бренды, которые не должны получать скидку 5%
    brands_to_exclude = {"Tracmax", "Sailun", "Landspider", "HiFly", 
                         "Antares", "Fortune", "Goodride", "LingLong Leao", 
                         "Sailun RoadX", "Triangle"} # замените на реальные названия

    # Легковые (без rest_novosib3) -> tyres.xml
    filter_and_save_items(
        url1, "4test.xml",
        filter_tag=None,
        include_tag="tiretype", include_value="Легковая",
        status="Под заказ",
        exclude_brands=brands_to_exclude
    )

    # Легковые (с rest_novosib3) -> tyres_nsk.xml
    filter_and_save_items(
        url1, "4test_nsk.xml",
        filter_tag="rest_novosib3",
        include_tag="tiretype", include_value="Легковая",
        status="В наличии",
        exclude_brands=brands_to_exclude
    )

    # Грузовые -> tyres_gruz.xml
    filter_and_save_items(
        url1, "4test_gruz.xml",
        filter_tag=None,
        include_tag="tiretype", include_value="Грузовая",
        status="Под заказ",
        exclude_brands=brands_to_exclude
    )

    print("✅ XML файлы успешно созданы; все *_rozn цены увеличены на 5% (округлены до целого), кроме указанных брендов.")
    
if __name__ == "__main__":
    main()
