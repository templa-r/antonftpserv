import asyncio
import re
import json
import time
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

# Импортируем или вставляем функцию parse_competitor_page из предыдущего ответа
def parse_competitor_page(html, source_url=''):
    # ... (полный код функции, приведённый в предыдущем ответе)
    # см. сообщение выше
    pass

async def scroll_and_collect_links(url, max_ads=500):
    """Возвращает список URL объявлений со страницы продавца."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)  # headless=False для отладки
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()
        await page.goto(url, wait_until="networkidle")
        
        links = set()
        prev_count = 0
        stale_loops = 0
        
        while True:
            # Извлекаем ссылки на товары (объявления) из текущего DOM
            html = await page.content()
            soup = BeautifulSoup(html, 'html.parser')
            for a in soup.find_all('a', href=re.compile(r'/wheel/tire/.*-\d+\.html')):
                full_url = urljoin(url, a['href'])
                links.add(full_url)
            
            # Прокручиваем страницу вниз
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)  # даём время на подгрузку
            
            # Проверяем, появились ли новые ссылки
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

async def main():
    seller_url = "https://baza.drom.ru/user/BigWheelsNsk/"
    print("Собираем ссылки на товары...")
    ad_links = await scroll_and_collect_links(seller_url)
    print(f"Найдено {len(ad_links)} объявлений")
    
    competitor_data = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    for i, link in enumerate(ad_links):
        print(f"[{i+1}/{len(ad_links)}] Обрабатываем {link}")
        try:
            # Здесь можно использовать requests, т.к. отдельные страницы статичны
            resp = requests.get(link, headers=headers, timeout=10)
            resp.raise_for_status()
            item = parse_competitor_page(resp.text, link)
            if item:
                # Ключ: width_profile_diameter_brand_model
                key = f"{item['width']}_{item['profile']}_{item['diameter']}_{item['brand'].lower()}_{item['model'].lower()}"
                if key not in competitor_data:
                    competitor_data[key] = []
                competitor_data[key].append({
                    'price': item['price_per_unit'],
                    'seller': 'BigWheelsNsk',
                    'url': link
                })
        except Exception as e:
            print(f"  Ошибка: {e}")
        
        time.sleep(2)  # задержка между запросами
    
    with open('competitor_prices.json', 'w', encoding='utf-8') as f:
        json.dump(competitor_data, f, ensure_ascii=False, indent=2)
    print(f"Готово. Данные сохранены в competitor_prices.json")

if __name__ == '__main__':
    asyncio.run(main())
