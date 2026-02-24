import requests
import xml.etree.ElementTree as ET
import re
import os

api_url = os.getenv('FORTOCHKI_T')
if not api_url:
    raise ValueError("Не установлена переменная окружения FORTOCHKI_T с URL API")    

# Заголовки для запроса (если необходимо)
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# Получение данных из API
response = requests.get(api_url, headers=headers)
response.raise_for_status()  # Проверка успешности запроса

# Парсинг XML данных
root = ET.fromstring(response.content)

# Создание нового корневого элемента для нового XML файла
new_root = ET.Element("items")

# Поля, которые нужно сохранить
fields_to_keep = {
    'cae': 'article',
    'brand': 'brand',
    'model': 'model',
    'width': 'width',
    'height': 'height',
    'diameter': 'diameter',
    'season': 'season',
    'img_small': 'img_small',
    'name': 'name',
    'cae': 'cae',
    'price': 'price'
}

# Копирование данных из исходного XML
for item in root.findall('tires'):
    new_item = ET.SubElement(new_root, "item")
    
    for field in fields_to_keep:
        element = item.find(field)
        if element is not None:
            new_element = ET.SubElement(new_item, fields_to_keep[field])
            new_element.text = element.text

    # Добавляем все поля, содержащие 'rest_'
    for element in item:
        if element.tag.startswith('rest_'):
            new_element = ET.SubElement(new_item, element.tag)
            new_element.text = element.text

    # Добавляем розничную цену, если есть, иначе берем обычную цену
    price_rozn = None
    for element in item:
        if '_rozn' in element.tag:
            price_rozn = element
            break

    if price_rozn is not None:
        new_price_element = ET.SubElement(new_item, 'price')
        new_price_element.text = price_rozn.text
    else:
        price_element = item.find('price')
        if price_element is not None and len(price_element) == 0:  # Проверка на отсутствие вложенных элементов
            new_price_element = ET.SubElement(new_item, 'price')
            new_price_element.text = price_element.text

    # Проверка наличия поля <thorn>Да</thorn> и добавление поля <spikes>шипы</spikes>
    thorn_element = item.find('thorn')
    if thorn_element is not None and thorn_element.text == 'Да':
        new_spikes_element = ET.SubElement(new_item, 'spikes')
        new_spikes_element.text = 'шипы'

    # Добавляем поля, содержащие 'price_', но не 'price_..._rozn' и не 'price', как 'opt'
    opt_added = False
    for element in item:
        if element.tag.startswith('price_') and not element.tag.endswith('_rozn') and element.tag != 'price':
            if not opt_added:
                new_element = ET.SubElement(new_item, 'opt')
                new_element.text = element.text
                opt_added = True

# Запись данных в новый XML файл
tree = ET.ElementTree(new_root)
tree.write("4tochki_tyres.xml", encoding="utf-8", xml_declaration=True)

print("Новый XML файл успешно создан.")
