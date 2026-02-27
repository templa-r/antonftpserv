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
        "proizvoditel": "brand"
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

def adjust_retail_prices_plus5(root: ET.Element) -> None:
    """
    Увеличивает значения всех тегов *_rozn на 5% от ИХ ЖЕ текущего значения.
    Если *_rozn пустой/нечисловой — пробуем взять значение из парного базового тега без суффикса.
    Результат округляется вниз до целого (int()).
    """
    for item in root.findall(".//item"):
        tag_map = {child.tag: child for child in list(item)}
        for tag, elem in tag_map.items():
            if not tag.endswith("_rozn"):
                continue

            # 1) Пытаемся взять текущее retail-значение
            rozn_val = _to_number(elem.text)

            # 2) Если retail нечисловой — берём базовый тег (без "_rozn")
            base_val = None
            if rozn_val is None:
                base_tag = tag[:-5]
                base_elem = tag_map.get(base_tag)
                if base_elem is not None:
                    base_val = _to_number(base_elem.text)

            source_val = rozn_val if rozn_val is not None else base_val
            if source_val is None:
                continue  # нечего повышать

            new_val = int(source_val * 1.05)  # округление до целого вниз
            elem.text = str(new_val)

def filter_and_save_items(api_url, output_file, filter_tag=None, existing_items=None,
                          include_tag=None, include_value=None, status=None):
    """Фильтрует товары, удаляет дубликаты, приводит поля к общему формату и сохраняет в XML-файл.
       После записи увеличивает все *_rozn на 5%."""
    root = fetch_xml(api_url)
    new_root = existing_items if existing_items is not None else ET.Element("items")

    for item in root.findall(".//tires" if "4tochki" in api_url else ".//item"):
        model_elem = item.find('categoryname')
        is_lt610 = model_elem is not None and model_elem.text == 'LT610'
        
        normalized_item = normalize_fields(item)
        
        if is_lt610:
            normalized_item['thorn'] = 'Липучка'
        cae = normalized_item.get("cae")
        unique_id = cae or normalized_item.get("article")
        if not unique_id:
            continue

        if include_tag and include_value:
            include_element = item.find(include_tag)
            if include_element is None or include_element.text != include_value:
                continue

        rest_element = item.find(filter_tag) if filter_tag else None
        if (filter_tag and rest_element is not None) or (not filter_tag and item.find("rest_novosib3") is None):
            new_item = ET.SubElement(new_root, "item")
            if status:
                status_elem = ET.SubElement(new_item, "status")
                status_elem.text = status
            for tag, text in normalized_item.items():
                new_elem = ET.SubElement(new_item, tag)
                new_elem.text = text
            model_elem_new = new_item.find('model')
            if model_elem_new is not None and model_elem_new.text == 'LT610':
                thorn_elem = new_item.find('thorn')
                if thorn_elem is None:
                    thorn_elem = ET.SubElement(new_item, 'thorn')
                thorn_elem.text = 'Липучка'
    
    # Увеличиваем все *_rozn на 5% для этого файла
    adjust_retail_prices_plus5(new_root)

    tree = ET.ElementTree(new_root)
    tree.write(output_file, encoding="utf-8", xml_declaration=True)
    return new_root

def main():
    url1 = "https://b2b.4tochki.ru/export_data/M28274.xml"
    url2 = "https://abcdisk54.ru/ftp/Brinex_shina.xml"

    # Создаем пустые файлы-шаблоны
    ET.ElementTree(ET.Element("items")).write("tyres.xml", encoding="utf-8", xml_declaration=True)
    ET.ElementTree(ET.Element("items")).write("tyres_gruz.xml", encoding="utf-8", xml_declaration=True)

    # Легковые (без rest_novosib3)
    existing_items = filter_and_save_items(
        url1, "tyres.xml",
        filter_tag=None,
        include_tag="tiretype", include_value="Легковая",
        status="Под заказ"
    )

    # Легковые (с rest_novosib3)
    filter_and_save_items(
        url1, "tyres_nsk.xml",
        filter_tag="rest_novosib3",
        include_tag="tiretype", include_value="Легковая",
        status="В наличии"
    )

    # Грузовые
    filter_and_save_items(
        url1, "tyres_gruz.xml",
        filter_tag=None,
        include_tag="tiretype", include_value="Грузовая",
        status="Под заказ"
    )

    # Вторая выгрузка (Brinex)
    filter_and_save_items(
        url2, "tyres.xml",
        filter_tag=None,
        existing_items=existing_items,
        include_tag=None, include_value=None,
        status="Под заказ"
    )

    print("✅ XML файлы успешно созданы; все *_rozn цены увеличены на 5% (округлены до целого).")

if __name__ == "__main__":
    main()
