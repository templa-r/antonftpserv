import asyncio
import re
import json
import time
import os
import sys
import requests
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

# ================= ДИАГНОСТИЧЕСКОЕ СООБЩЕНИЕ =================
print("=== Запущен zpars.py ===")
print(f"Python: {sys.version}")

# ================= ПАРСЕР КАРТОЧКИ ТОВАРА =================
def parse_competitor_page(html, source_url=''):
    """
    Извлекает из HTML-страницы объявления drom.ru:
      - цену за комплект и штуку
      - количество шин в комплекте
      - бренд, модель
      - типоразмер (ширина, профиль, диаметр)
    Возвращает словарь или None при ошибке.
    """
    soup = BeautifulSoup(html, 'html.parser')

    # 1. Цена за комплект (обязательное поле)
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

    # 3. Бренд и модель (Charmhoo ice3)
    brand = ''
    model_name = ''
    h1 = soup.select_one('h1.subject')
    title_tag = soup.select_one('title')

    # а) специальный блок .viewbull-field__model-name
    model_elem = soup.select_one('.viewbull-field__model-name')
    if model_elem:
        full_model = model_elem.get_text(strip=True)
        parts = full_model.split(maxsplit=1)
        brand = parts[0].strip()
        model_name = parts[1].strip() if len(parts) > 1 else ''

    # б) если не нашли, разбираем заголовок <h1>
    if not brand and h1:
        h1_text = h1.get_text(strip=True)
        # Ищем первый размер вида 265/45R21
        size_match = re.search(r'(\d{3}/\d{2,3}[Rr]\d{2})', h1_text)
        if size_match:
            # Текст после размера: "Charmhoo ice 3"
            after = h1_text[size_match.end():].strip()
            after = re.sub(r'[,;.]', ' ', after)
            stop_words = {
                'nobr', 'зимние', 'летние', 'без', 'шипов', 'новый', 'новая',
                'зимняя', 'летняя', 'резина', 'шины', 'шина', 'в', 'на',
                'купить', 'продажа', 'дром', 'запчасти', 'и', 'с',
                'новосибирск', 'новосибирске', 'зимних', 'летних',
                'комплект', 'штук', 'шт', 'шипованная', 'нешипованная'
            }
            words = after.split()
            meaningful = [w for w in words if w.lower() not in stop_words]
            if meaningful:
                brand = meaningful[0]
                model_name = ' '.join(meaningful[1:]) if len(meaningful) > 1 else ''
            # Если в after ничего не нашли, попробуем до размера
            if not brand and size_match:
                before = h1_text[:size_match.start()].strip()
                before = re.sub(r'[,;.]', ' ', before)
                before_words = [w for w in before.split() if w.lower() not in stop_words]
                if before_words:
                    brand = before_words[-1]  # последнее слово перед размером
                    model_name = ''

    # в) если всё ещё нет – берём из <title>
    if not brand and title_tag:
        title_text = title_tag.get_text(strip=True)
        # "Charmhoo ice3, 265/45R21 - Купить шины ..."
        title_part = title_text.split(',')[0].strip()
        # Убираем размер, если он там есть
        title_part = re.sub(r'\d{3}/\d{2,3}R\d{2}', '', title_part).strip()
        brand_model = title_part.split()
        if brand_model:
            brand = brand_model[0]
            model_name = ' '.join(brand_model[1:]) if len(brand_model) > 1 else ''

    if not brand:
        return None

    # 4. Типоразмер (ширина, профиль, диаметр)
    marking_elem = soup.select_one('[data-field="marking"]')
    marking_text = ''
    if marking_elem:
        marking_text = marking_elem.get_text(strip=True)
    elif h1:
        marking_text = h1.get_text(strip=True)
    elif title_tag:
        marking_text = title_tag.get_text(strip=True)

    # Ищем 265/45R21
    sizes = re.findall(r'(\d{3})/(\d{2,3})[Rr](\d{2})', marking_text)
    if not sizes:
        # Альтернативный формат 31x10.5R15
        sizes = re.findall(r'(\d{2})[xX](\d{1,2}\.?\d?)[Rr](\d{2})', marking_text)
    if not sizes:
        return None

    # Если несколько размеров (разноширокий комплект), берём первый
    width, profile, diameter = sizes[0]
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

# ================= СБОР ССЫЛОК (Playwright) =================
async def scroll_and_collect_links(url, max_ads=500):
    """
    Открывает страницу продавца, прокручивает до конца,
    собирает все ссылки на объявления шин.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()
        await page.goto(url, wait_until="networkidle")

        links = set()
        prev_count = 0
        stale_loops = 0

        while True:
            html = await page.content()
            soup = BeautifulSoup(html, 'html.parser')
            for a in soup.find_all('a', href=re.compile(r'/wheel/tire/.*-\d+\.html')):
                full_url = urljoin(url, a['href'])
                links.add(full_url)

            # Прокручиваем и ждём
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)

            if len(links) == prev_count:
                stale_loops += 1
                if stale_loops >= 3:
                    break
            else:
                stale_loops = 0
                prev_count = len(links)

            if len(links) >= max_ads:
                break

        await browser.close()
        return list(links)

# ================= ГЛАВНАЯ ФУНКЦИЯ =================
async def main():
    seller_url = "https://baza.drom.ru/user/BigWheelsNsk/"
    max_ads = int(os.getenv("MAX_ADS", 0)) or 500

    print("Собираем ссылки на товары...")
    ad_links = await scroll_and_collect_links(seller_url, max_ads=max_ads)
    print(f"Найдено {len(ad_links)} объявлений")

    competitor_data = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    for i, link in enumerate(ad_links):
        print(f"[{i+1}/{len(ad_links)}] Обрабатываем {link}")
        try:
            resp = requests.get(link, headers=headers, timeout=10)
            resp.raise_for_status()
            item = parse_competitor_page(resp.text, link)
            if item:
                # Ключ: width_profile_diameter_brand_model
                key = f"{item['width']}_{item['profile']}_{item['diameter']}_{item['brand'].lower()}_{item['model'].lower()}"
                competitor_data.setdefault(key, []).append({
                    'price': item['price_per_unit'],
                    'seller': 'BigWheelsNsk',
                    'url': link
                })
                print(f"  -> цена: {item['price_per_unit']} руб.")
            else:
                print("  -> не удалось распарсить")
        except Exception as e:
            print(f"  Ошибка: {e}")
        time.sleep(2)  # задержка между запросами

    with open('competitor_prices.json', 'w', encoding='utf-8') as f:
        json.dump(competitor_data, f, ensure_ascii=False, indent=2)
    print(f"Готово. Сохранено {len(competitor_data)} уникальных товаров.")

if __name__ == '__main__':
    print("Вызов main()...")
    asyncio.run(main())
