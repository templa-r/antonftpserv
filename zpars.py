import asyncio
import re
import json
import time
import os
import requests
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

# ================= ПОЛНАЯ РЕАЛИЗАЦИЯ ПАРСЕРА КАРТОЧКИ =================
def parse_competitor_page(html, source_url=''):
    """
    Принимает HTML-код страницы объявления drom.ru (шины)
    Возвращает словарь с данными или None, если не удалось распарсить.
    """
    soup = BeautifulSoup(html, 'html.parser')

    # 1. Цена за комплект
    price_elem = soup.select_one('[data-bulletin-price]')
    if not price_elem:
        return None
    total_price = int(price_elem['data-bulletin-price'])

    # 2. Количество в комплекте
    qty_elem = soup.select_one('[data-field="inSetQuantity"]')
    quantity = 4
    if qty_elem:
        match = re.search(r'(\d+)', qty_elem.text)
        if match:
            quantity = int(match.group(1))
    price_per_unit = total_price / quantity

    # 3. Модель (бренд + название)
    model_elem = soup.select_one('.viewbull-field__model-name')
    if not model_elem:
        return None
    full_model = model_elem.get_text(strip=True)
    parts = full_model.split(maxsplit=1)
    brand = parts[0] if parts else ''
    model_name = parts[1] if len(parts) > 1 else ''

    # 4. Типоразмер (265/45R21)
    marking_elem = soup.select_one('[data-field="marking"]')
    if not marking_elem:
        return None
    marking_text = marking_elem.get_text(strip=True)
    match = re.match(r'(\d+)/?(\d*)R?(\d+)', marking_text, re.IGNORECASE)
    if not match:
        return None
    width, profile, diameter = match.groups()
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

# ================= СБОР ССЫЛОК СО СТРАНИЦЫ ПРОДАВЦА =================
async def scroll_and_collect_links(url, max_ads=500):
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
        time.sleep(2)

    with open('competitor_prices.json', 'w', encoding='utf-8') as f:
        json.dump(competitor_data, f, ensure_ascii=False, indent=2)
    print(f"Готово. Сохранено {len(competitor_data)} уникальных товаров.")

if __name__ == '__main__':
    asyncio.run(main())
