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
            brand = item.get("brand", "").strip().lower()
            model = item.get("model", "").strip().lower()
            category = item.get("category", "")
        
            # Списки исключений (бренды и категории, которые не трогаем)
            excluded_brands = ["Mazzini", "Nexen", "MAXXIS", "Predator", "Compasal","Massimo",
                                   "Firemax","Sonix","Prinx","Roadmarch", "Notto", "Rapid", "Matador", 
                                   "Kelly", "HIFLY", "Fluda", "Firemax", "Cordian", "Aoteli", "Torero",
                                  "Viatti", "Кама"]
            excluded_category = ["Грузовая"]
            excluded_brands_lower = [b.lower() for b in excluded_brands]
        
            # Словарь специальных коэффициентов для отдельных брендов (если нет правила для модели)
            special_coeffs = {
                "ikon": 0.88,                   # -12%
                "yokohama": 0.95,               # -5%
                "pirelli": 0.97,                # -4%
                # добавьте другие бренды при необходимости
            }
            # Словарь специальных правил для конкретных моделей
            # Ключ — кортеж (бренд, модель) в нижнем регистре
            special_model_rules = {
                ("autograph", "autograph ice 9 suv"): {
                    "type": "add_to_field",
                    "field": "price",      # из какого поля берём базу
                    "value": 1500          # сколько добавляем
                },
                # Пример фиксированной цены:
                # ("nokian", "hakkapeliitta 10"): {
                #     "type": "fixed",
                #     "value": 12000
                # },
            }
        
            # Проверяем, попадает ли товар под исключения
            if brand not in excluded_brands_lower and category not in excluded_category:
                # Проверяем, есть ли специальное правило для этой модели
                rule = special_model_rules.get((brand, model))
                if rule:
                    try:
                        if rule["type"] == "add_to_field":
                            # Берём значение указанного поля
                            base_value_str = item.get(rule["field"], "0")
                            base_val = float(str(base_value_str).replace(",", ".").strip())
                            new_val = int(base_val + rule["value"])
                        elif rule["type"] == "fixed":
                            new_val = int(rule["value"])
                        else:
                            # Неизвестный тип правила — оставляем исходное значение retail
                            new_val = float(str(value).replace(",", ".").strip())
                        element.text = str(new_val)
                    except (ValueError, TypeError):
                        # Если не удалось вычислить, оставляем исходное значение
                        element.text = str(value)
                else:
                    # Нет специального правила для модели — применяем логику для бренда
                    try:
                        val = float(str(value).replace(",", ".").strip())
                        coeff = special_coeffs.get(brand, 0.92)   # по умолчанию -8%
                        val = int(val * coeff)
                        element.text = str(val)
                    except ValueError:
                        element.text = str(value)
            else:
                # Товар в исключениях — оставляем цену без изменений
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
with open("testtyres.xml", "wb") as file:
    tree.write(file, encoding="utf-8", xml_declaration=True)

print("✅ XML файл успешно создан; розничные цены <retail> с корректировкой цен.")
