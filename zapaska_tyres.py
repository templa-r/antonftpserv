import os
import requests
import base64
import xml.etree.ElementTree as ET
import re

# Настройки для API
url = "https://ka2.sibzapaska.ru:16500/API/hs/V2/GetTires"
username = "API_client"
password = "rWp7mFWXRKOq"

# Создание заголовка для Basic Auth
auth = base64.b64encode(f"{username}:{password}".encode()).decode()

# Выполнение запроса к API
response = requests.get(url, headers={"Authorization": f"Basic {auth}"})
response.raise_for_status()  # Проверка на наличие ошибок

# Обработка ответа в формате JSON
data = response.json()

# Создание корневого элемента XML
root = ET.Element("Products")

# Преобразование каждого товара в XML элемент
for item in data:
    # Проверка на наличие "ЗБ" в начале значения поля <name>
    name = item.get("name", "")
    if name.startswith("ЗБ"):
        continue  # Пропускаем этот товар

    product = ET.SubElement(root, "Product")
    for key, value in item.items():
        if key != "Оптовая_Цена":  # Исключаем поле "Оптовая_Цена"
            element = ET.SubElement(product, key)

        # --- увеличение розничной цены на 5% (с исключениями) ---
            if key.lower() == "retail":
                brand = item.get("brand", "")
                category = item.get("category", "")
            
                # Списки исключений 
                excluded_brands = ["Mazzini", "Nexen", "MAXXIS", "Predator", "Compasal","Massimo",
                                   "Firemax","Sonix","Prinx","Roadmarch", "Notto", "Rapid", "Matador", 
                                   "Kelly", "HIFLY", "Fluda", "Firemax", "Cordian", "Aoteli", "Torero",
                                  "Viatti", "Кама",]
                excluded_category = ["Грузовая"]
            
                # Словарь специальных коэффициентов для отдельных брендов
                special_coeffs = {
                    "Ikon (Nokian Tyres)": 0.82,       # -18%
                    "Yokohama": 0.95,                  # -5%
                    "Pirelli": 0.97,                   # -4%
                }
            
                if brand not in excluded_brands and category not in excluded_category:
                    try:
                        val = float(str(value).replace(",", ".").strip())
                        # Если бренд есть в special_coeffs — берём его коэффициент, иначе общий 0.92
                        coeff = special_coeffs.get(brand.lower(), 0.935)
                        val = int(val * coeff)
                        element.text = str(val)
                    except ValueError:
                        element.text = str(value)
                else:
                    element.text = str(value)
            else:
                element.text = str(value)
        # ----------------------------------------
    # Добавляем поле studded для модели Nortec LT 610
    model = item.get("model", "")
    if model == "Nortec LT 610":
        studded_element = ET.SubElement(product, "studded")
        studded_element.text = "Нет"

    # Определение тега для товара
    nomenclature = item.get("Номенклатура", "")
    if re.match(r'^(1[2-9]|2[0-4])\s', nomenclature):
        product.tag = "disk"
    else:
        product.tag = "tyres"

# Создание дерева XML и запись в файл
tree = ET.ElementTree(root)
with open("aztyre.xml", "wb") as file:
    tree.write(file, encoding="utf-8", xml_declaration=True)

print("✅ XML файл успешно создан; розничные цены <retail> с корректировкой цен.")
