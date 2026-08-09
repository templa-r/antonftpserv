import asyncio
import re
import json
import time
import os
import requests
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

def parse_competitor_page(html, source_url=''):
    soup = BeautifulSoup(html, 'html.parser')

    # 1. Цена за комплект (всегда есть, если объявление активно)
    price_elem = soup.select_one('[data-bulletin-price]')
    if not price_elem:
        return None
    total_price = int(price_elem['data-bulletin-price'])

    # 2. Количество шин в комплекте (по умолчанию 4)
    qty_elem = soup.select_one('[data-field="inSetQuantity"]')
    quantity = 4
    if qty_elem:
        match = re.search(r'(\d+)', qty_elem.text)
        if match:
            quantity = int(match.group(1))
    price_per_unit = total_price / quantity

    # 3. Модель (бренд + название)
    # Попытки: .viewbull-field__model-name, заголовок h1, title страницы
    model_elem = soup.select_one('.viewbull-field__model-name')
    if not model_elem:
        # Ищем в h1 (обычно класс "subject")
        h1 = soup.select_one('h1.subject')
        if h1:
            # Текст h1 обычно содержит полное описание, например "Charmhoo ice3, 265/45R21"
            # Извлекаем модель до запятой/тире
            full_text = h1.get_text(strip=True)
            # Отделяем часть до запятой или " - "
            model_part = re.split(r'[,|-]', full_text)[0].strip()
            # Если содержит размер, убираем его (паттерн размера)
            model_part = re.sub(r'\d{3}/\d{2,3}R\d{2}', '', model_part).strip()
            full_model = model_part
        else:
            # Пробуем из <title>
            title = soup.select_one('title')
            if title:
                title_text = title.get_text(strip=True)
                # Пример: "Charmhoo ice3, 265/45R21 - Купить шины в Новосибирске на Дром Запчасти"
                # Берем часть до запятой
                model_part = title_text.split(',')[0].strip()
                full_model = model_part
            else:
                return None
    else:
        full_model = model_elem.get_text(strip=True)

    # Разделяем бренд и модель
    parts = full_model.split(maxsplit=1)
    brand = parts[0] if parts else ''
    model_name = parts[1] if len(parts) > 1 else ''

    # 4. Типоразмер
    # Ищем в [data-field="marking"] или в заголовке/тексте
    marking_elem = soup.select_one('[data-field="marking"]')
    marking_text = ''
    if marking_elem:
        marking_text = marking_elem.get_text(strip=True)
    else:
        # Ищем в h1 или title
        if h1:
            marking_text = h1.get_text(strip=True)
        elif soup.select_one('title'):
            marking_text = soup.select_one('title').get_text(strip=True)

    # Ищем все размеры вида 265/45R21
    size_pattern = re.findall(r'(\d{3})/(\d{2,3})[Rr](\d{2})', marking_text)
    if not size_pattern:
        # может быть без профиля: 31x10.5R15 (редко)
        size_pattern = re.findall(r'(\d{2})[xX](\d{1,2}\.?\d?)[Rr](\d{2})', marking_text)
        if not size_pattern:
            return None
    # Берем первый размер (если их несколько - разноширокий комплект, можно пропустить)
    width, profile, diameter = size_pattern[0]
    width = int(width)
    diameter = int(diameter)
    profile = int(profile) if profile else 0

    return {
        'brand': brand,
        'model': model_name,
        'width': width,
        'profile': profile,
        'diameter': diameter,
        'price_per_unit': round(price_per_unit, 2),
        'total_price': total_price,
        'quantity': quantity,
        'source_url': source_url,
    }

# ... остальной код scroll_and_collect_links и main без изменений ...
