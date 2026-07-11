import os
import requests
import base64
import re
import csv
from collections import defaultdict
import statistics

# ===================== НАСТРОЙКИ =====================
API_URL = "https://ka2.sibzapaska.ru:16500/API/hs/V2/GetTires"
API_USER = "API_client"
API_PASSWORD = "rWp7mFWXRKOq"

# Пороги для анализа
HIGH_MARGIN_THRESHOLD = 700   # выше этого считаем высокой маржой
LOW_MARGIN_THRESHOLD = 300    # ниже этого считаем низкой
TARGET_MARGIN_PERCENT = 25    # желаемый процент маржи от оптовой цены (для рекомендаций)

# ===================== ФИЛЬТРЫ (без изменений) =====================
SEASON_EXCLUDE_ENABLED = False
SEASON_EXCLUDE_VALUE = "зима"

EXCLUDED_BRANDS = [
    "Mazzini", "Nexen", "MAXXIS", "Predator", "Compasal", "HIFLY", "Aoteli",
    "Torero", "Viatti", "Massimo", "Firemax", "Sonix", "Prinx", "Roadmarch",
    "Kelly", "Nitto", "Кама", "Roadcruza", "Hankook"
]

EXCLUDED_BRANDS_FROM_EXPORT = ["Compasal", "Aoteli"]  # для аналитики не используется, но оставим

EXCLUDED_CATEGORY = ["Грузовая"]

EXCLUDED_ARTICLES = [
    "195/75R16C Laufenn X FIT VAN LV01",
    "АТ27x8-12 MAXXIS M961 6PR",
    "185/75R16C Ikon Autograph Snow C4",
    "33x10,50-16 NORTEC ET-500 111N TL АШК",
]

EXCLUDE_CAE_ENABLED = True
EXCLUDED_CAE = [
    "КА-00049127", "КА-00049119", "КА-00049010", "КА-00049170", "КА-00049171",
    "КА-00040044", "00000019470", "КА-00048983", "КА-00046413", "КА-00049081",
    "КА-00049149", "КА-00049109", "КА-00046401", "КА-00046414", "КА-00049102",
    "КА-00049039", "КА-00046404", "КА-00046416", "КА-00049095", "КА-00049031",
    "КА-00049073", "КА-00046409", "КА-00049156", "КА-00049104", "КА-00046390",
    "КА-00046384", "КА-00046397", "КА-00049146", "КА-00063027", "КА-00046383",
    "КА-00049011", "КА-00049075", "КА-00049144", "КА-00049033", "КА-00049174",
    "КА-00049096", "КА-00049112", "КА-00049160", "КА-00049116", "КА-00049110",
    "КА-00049015", "КА-00049082", "КА-00046417", "КА-00049012", "КА-00046405",
    "КА-00046400", "КА-00049132", "КА-00048992", "КА-00049173", "КА-00049145",
    "КА-00049001", "КА-00074372", "КА-00049037", "КА-00046420", "КА-00046396",
    "КА-00046391", "КА-00049018", "КА-00049162", "КА-00046418", "КА-00049107",
    "КА-00049045", "КА-00049163", "КА-00046410", "КА-00049078", "КА-00049142",
    "КА-00049165",
]

# ===================== КОЭФФИЦИЕНТЫ (для расчёта итоговой цены) =====================
GLOBAL_COEFF = {
    "default": 0.92,
    "diameter_ranges": [
        {"min": 13, "max": 15, "coeff": 0.95},
        {"min": 16, "max": 17, "coeff": 0.94},
        {"min": 18, "max": 22, "coeff": 0.93},
    ],
}

BRAND_COEFFS = {
    "ikon": {
        "default": 0.88,
        "diameter_ranges": [
            {"min": 13, "max": 15, "coeff": 0.905},
            {"min": 17, "max": 22, "coeff": 0.88},
        ],
    },
    "laufenn": 0.905,
    "bridgestone": 0.965,
    "pirelli": {
        "diameter_ranges": [
            {"min": 13, "max": 16, "value": 700},
            {"min": 17, "max": 22, "value": 700},
        ],
    },
    "formula": {
        "diameter_ranges": [
            {"min": 13, "max": 16, "value": 500},
            {"min": 17, "max": 22, "value": 500},
        ],
    },
}

IGNORE_COEFF_BRANDS = ["Mazzini", "Nexen", "MAXXIS", "Sonix", "Hankook"]
MIN_MARGIN = 500  # если итоговая маржа меньше этой, то розница приравнивается к исходной

MODEL_RULES = {
    ("autograph", "autograph ice 9 suv"): {
        "type": "add_to_field_by_diameter",
        "field": "price",
        "ranges": [
            {"min": 16, "max": 17, "value": 1300},
            {"min": 18, "max": 19, "value": 1700},
        ],
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

def round_price(price, step=10, method='nearest'):
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
    return coeff

def apply_brand_adjustment(brand_settings, base_price, diameter):
    if not isinstance(brand_settings, dict):
        return base_price * brand_settings
    if diameter is not None and "diameter_ranges" in brand_settings:
        for r in brand_settings["diameter_ranges"]:
            if r["min"] <= diameter <= r["max"]:
                if "value" in r:
                    return base_price + r["value"]
                elif "coeff" in r:
                    return base_price * r["coeff"]
    if "default" in brand_settings:
        default = brand_settings["default"]
        if isinstance(default, (int, float)):
            return base_price * default
    if "coeff" in brand_settings:
        return base_price * brand_settings["coeff"]
    if "value" in brand_settings:
        return base_price + brand_settings["value"]
    return base_price

def get_full_name(item):
    nomenclature = item.get("Номенклатура", "").strip()
    if nomenclature:
        return nomenclature
    parts = []
    for f in ["width", "profile", "diameter"]:
        if item.get(f):
            parts.append(str(item[f]))
    if not parts:
        return item.get("name", "").strip()
    brand = item.get("brand", "").strip()
    model = item.get("model", "").strip()
    if len(parts) >= 3:
        size = f"{parts[0]}/{parts[1]}R{parts[2]}"
    else:
        size = " ".join(parts)
    return f"{size} {brand} {model}".strip()

# ===================== РАСЧЁТ РОЗНИЦЫ С ИНФОРМАЦИЕЙ =====================
def get_retail_with_info(item, diameter):
    brand = item.get("brand", "").strip().lower()
    model = item.get("model", "").strip().lower()
    category = item.get("category", "")
    is_excluded = (brand in [b.lower() for b in EXCLUDED_BRANDS] or
                   category in EXCLUDED_CATEGORY)
    original_retail = safe_float(item.get("retail", 0))
    apply_coeff = brand not in [b.lower() for b in IGNORE_COEFF_BRANDS]

    info = {
        'original_retail': original_retail,
        'adjustment_type': 'none',
        'description': 'Без изменений',
        'coeff_value': None,
        'used_base': 'retail'
    }

    if is_excluded or not apply_coeff:
        final_price = original_retail
        info['adjustment_type'] = 'excluded' if is_excluded else 'ignored_brand'
        info['description'] = 'Исключён (бренд или категория)' if is_excluded else 'Бренд в списке игнорирования'
        return final_price, info

    rule = MODEL_RULES.get((brand, model))
    if rule:
        rule_type = rule["type"]
        info['adjustment_type'] = 'rule'
        info['description'] = f"Правило: {brand} {model} ({rule_type})"
        if rule_type == "fixed":
            adjusted_retail = safe_float(rule["value"])
            info['description'] += f" фиксированная {adjusted_retail}"
        elif rule_type == "add_to_field":
            base_val = safe_float(item.get(rule["field"], "0"))
            adjusted_retail = base_val + rule["value"]
            info['description'] += f" +{rule['value']} к {rule['field']}"
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
                    info['description'] += f" +{add_value} к {rule['field']} для диаметра {diameter}"
                else:
                    if rule.get("default") is not None:
                        base_val = safe_float(item.get(rule["field"], "0"))
                        adjusted_retail = base_val + rule["default"]
                        info['description'] += f" +{rule['default']} к {rule['field']} (default)"
                    else:
                        adjusted_retail = original_retail
                        info['description'] += " без изменений (диаметр вне диапазона)"
            else:
                adjusted_retail = original_retail
                info['description'] += " без изменений (диаметр не определён)"
        else:
            adjusted_retail = original_retail
        info['coeff_value'] = None
    else:
        brand_settings = BRAND_COEFFS.get(brand)
        if brand_settings is not None:
            use_wholesale = False
            if isinstance(brand_settings, dict):
                if 'value' in brand_settings:
                    use_wholesale = True
                elif 'diameter_ranges' in brand_settings:
                    for r in brand_settings['diameter_ranges']:
                        if 'value' in r:
                            use_wholesale = True
                            break
            if use_wholesale:
                wholesale = safe_float(item.get("price", 0))
                if wholesale > 0:
                    adjusted_retail = apply_brand_adjustment(brand_settings, wholesale, diameter)
                    info['used_base'] = 'wholesale'
                    info['description'] = f"Брендовый коэффициент (добавка к оптовой): {brand_settings}"
                else:
                    adjusted_retail = apply_brand_adjustment(brand_settings, original_retail, diameter)
                    info['used_base'] = 'retail'
                    info['description'] = f"Брендовый коэффициент (к retail): {brand_settings}"
            else:
                adjusted_retail = apply_brand_adjustment(brand_settings, original_retail, diameter)
                info['used_base'] = 'retail'
                info['description'] = f"Брендовый коэффициент: {brand_settings}"
            info['adjustment_type'] = 'brand_coeff'
            if isinstance(brand_settings, dict):
                if 'coeff' in brand_settings:
                    info['coeff_value'] = brand_settings['coeff']
                elif 'default' in brand_settings:
                    info['coeff_value'] = brand_settings['default']
                elif 'diameter_ranges' in brand_settings:
                    for r in brand_settings['diameter_ranges']:
                        if r.get('coeff') is not None and (diameter is None or r['min'] <= diameter <= r['max']):
                            info['coeff_value'] = r['coeff']
                            break
        else:
            coeff = get_coeff_from_settings(GLOBAL_COEFF, diameter)
            adjusted_retail = original_retail * coeff
            info['adjustment_type'] = 'global_coeff'
            info['coeff_value'] = coeff
            info['description'] = f"Глобальный коэффициент: {coeff}"

    price_val = safe_float(item.get("price", 0))
    if price_val > 0:
        margin = adjusted_retail - price_val
        if margin < MIN_MARGIN:
            final_price = original_retail
            info['description'] += f" (отменено, маржа < {MIN_MARGIN})"
        else:
            final_price = adjusted_retail
    else:
        final_price = adjusted_retail

    # Округление (всегда по умолчанию)
    final_price = round_price(final_price, step=10, method='nearest')
    return final_price, info

# ===================== ОСНОВНАЯ ЗАГРУЗКА И ФИЛЬТРАЦИЯ =====================
auth = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
response = requests.get(API_URL, headers={"Authorization": f"Basic {auth}"})
response.raise_for_status()
data = response.json()

print("🔄 Загрузка данных завершена. Обработка...")

all_items = []
total_products = 0
excluded_zb = 0
excluded_article = 0
excluded_season = 0
excluded_cae = 0

for item in data:
    name = item.get("name", "")
    if name.startswith("ЗБ"):
        excluded_zb += 1
        continue

    if EXCLUDE_CAE_ENABLED:
        cae = item.get("cae", "")
        if cae in EXCLUDED_CAE:
            excluded_cae += 1
            continue

    if item.get("brand") == "Ikon (Nokian Tyres)":
        item["brand"] = "Ikon"

    if "name" in item and "(Nokian Tyres)" in item["name"]:
        item["name"] = item["name"].replace("(Nokian Tyres)", "").strip()
        item["name"] = re.sub(r'\s+', ' ', item["name"])

    brand = item.get("brand", "")
    if brand in EXCLUDED_BRANDS_FROM_EXPORT:
        continue

    model_replacements = {
        "Blu Earth V906": ("BluEarth Winter V906", "BluEarth Winter V906"),
        "VS-EV": ("Victra Sport EV", "Victra Sport EV"),
        "Ecsta PS72": ("Ecsta Sport PS72", "Ecsta Sport PS72"),
    }
    current_model = item.get("model", "")
    if current_model in model_replacements:
        new_model, new_name_part = model_replacements[current_model]
        item["model"] = new_model
        if "name" in item:
            item["name"] = item["name"].replace(current_model, new_name_part)

    diameter = safe_float(item.get("diameter"), default=None)
    if diameter is None:
        nomenclature = item.get("Номенклатура", "")
        match = re.search(r'[Rr](\d{2})', nomenclature)
        if match:
            diameter = float(match.group(1))

    article = item.get("article", "")
    if any(phrase in article for phrase in EXCLUDED_ARTICLES):
        excluded_article += 1
        continue

    if SEASON_EXCLUDE_ENABLED:
        season = item.get("season", "")
        if season == SEASON_EXCLUDE_VALUE:
            excluded_season += 1
            continue

    total_products += 1
    all_items.append((item, diameter))

print(f"🔍 Всего товаров после фильтров: {total_products}")
print(f"🔍 Исключено по CAE: {excluded_cae}")
print(f"🔍 Исключено по артикулам: {excluded_article}")

# ===================== СБОР ДАННЫХ ДЛЯ АНАЛИЗА =====================
analysis_data = []
brand_diameter_stats = defaultdict(lambda: defaultdict(lambda: {'margins': [], 'prices': []}))

for item, diameter in all_items:
    price = safe_float(item.get("price", 0))
    original_retail = safe_float(item.get("retail", 0))
    original_margin = original_retail - price
    original_margin_percent = (original_margin / price * 100) if price > 0 else None

    final_retail, info = get_retail_with_info(item, diameter)
    final_margin = final_retail - price
    final_margin_percent = (final_margin / price * 100) if price > 0 else None

    full_name = get_full_name(item)

    record = {
        "Бренд": item.get("brand", ""),
        "Модель": item.get("model", ""),
        "Полное_название": full_name,
        "Артикул": item.get("article", ""),
        "Размер": f"{item.get('width','')}/{item.get('profile','')}R{item.get('diameter','')}",
        "Сезон": item.get("season", ""),
        "Категория": item.get("category", ""),
        "Оптовая_цена": price,
        "Исходная_розница": original_retail,
        "Исходная_маржа_руб": original_margin,
        "Исходная_маржа_%": original_margin_percent,
        "Тип_корректировки": info['adjustment_type'],
        "Описание_корректировки": info['description'],
        "Коэффициент": info['coeff_value'],
        "База_для_расчёта": info['used_base'],
        "Итоговая_розница": final_retail,
        "Итоговая_маржа_руб": final_margin,
        "Итоговая_маржа_%": final_margin_percent,
        "Изменение_маржа_руб": final_margin - original_margin,
    }
    analysis_data.append(record)

    # Сбор статистики по брендам и диаметрам (итоговая маржа)
    brand = item.get("brand", "").strip()
    if brand and diameter is not None:
        d_key = int(diameter)
        brand_diameter_stats[brand][d_key]['margins'].append(final_margin)
        brand_diameter_stats[brand][d_key]['prices'].append(price)

# ===================== СОХРАНЕНИЕ ПОЛНОГО ОТЧЁТА =====================
csv_full = "product_analysis_full.csv"
with open(csv_full, "w", encoding="utf-8-sig", newline="") as f:
    if analysis_data:
        writer = csv.DictWriter(f, fieldnames=analysis_data[0].keys())
        writer.writeheader()
        writer.writerows(analysis_data)
print(f"✅ Полный аналитический отчёт сохранён в {csv_full}")

# ===================== ВЫДЕЛЕНИЕ ВЫСОКОЙ И НИЗКОЙ МАРЖИ =====================
high_margin = [rec for rec in analysis_data if rec["Итоговая_маржа_руб"] >= HIGH_MARGIN_THRESHOLD]
low_margin = [rec for rec in analysis_data if rec["Итоговая_маржа_руб"] <= LOW_MARGIN_THRESHOLD]

if high_margin:
    csv_high = "high_margin_products.csv"
    with open(csv_high, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=analysis_data[0].keys())
        writer.writeheader()
        writer.writerows(high_margin)
    print(f"✅ Товаров с высокой маржой (≥ {HIGH_MARGIN_THRESHOLD} руб.): {len(high_margin)}, сохранены в {csv_high}")

if low_margin:
    csv_low = "low_margin_products.csv"
    with open(csv_low, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=analysis_data[0].keys())
        writer.writeheader()
        writer.writerows(low_margin)
    print(f"⚠️ Товаров с низкой маржой (≤ {LOW_MARGIN_THRESHOLD} руб.): {len(low_margin)}, сохранены в {csv_low}")

# ===================== СТАТИСТИКА ПО БРЕНДАМ И ДИАМЕТРАМ =====================
stats_rows = []
for brand in sorted(brand_diameter_stats.keys()):
    for diam in sorted(brand_diameter_stats[brand].keys()):
        margins = brand_diameter_stats[brand][diam]['margins']
        prices = brand_diameter_stats[brand][diam]['prices']
        count = len(margins)
        if count == 0:
            continue
        avg_margin = sum(margins) / count
        min_margin = min(margins)
        max_margin = max(margins)
        # Средняя оптовая цена
        avg_price = sum(prices) / count if prices else 0
        stats_rows.append({
            "Бренд": brand,
            "Диаметр": diam,
            "Кол-во": count,
            "Средняя_маржа_руб": round(avg_margin, 2),
            "Мин_маржа_руб": min_margin,
            "Макс_маржа_руб": max_margin,
            "Средняя_оптовая_цена": round(avg_price, 2),
        })

if stats_rows:
    stats_csv = "brand_diameter_stats.csv"
    with open(stats_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=stats_rows[0].keys())
        writer.writeheader()
        writer.writerows(stats_rows)
    print(f"✅ Статистика по брендам и диаметрам сохранена в {stats_csv}")

# ===================== ГЕНЕРАЦИЯ РЕКОМЕНДАЦИЙ ПО ОПТИМИЗАЦИИ МАРЖИ =====================
recommendations = []

# Для каждого товара, если итоговая маржа не равна исходной (т.е. были применены коэффициенты)
# или если маржа сильно отличается от средней по группе (бренд+диаметр)
for rec in analysis_data:
    brand = rec["Бренд"]
    diam = rec.get("Диаметр")  # может быть None
    price = rec["Оптовая_цена"]
    current_margin = rec["Итоговая_маржа_руб"]
    original_margin = rec["Исходная_маржа_руб"]

    # Если оптовая цена <=0, пропускаем
    if price <= 0:
        continue

    # Найдём среднюю маржу по бренду и диаметру (если есть статистика)
    avg_margin_group = None
    if brand and diam is not None and diam in brand_diameter_stats.get(brand, {}):
        margins_group = brand_diameter_stats[brand][diam]['margins']
        if margins_group:
            avg_margin_group = sum(margins_group) / len(margins_group)

    # Определим целевую маржу: либо средняя по группе, либо желаемый процент от оптовой
    if avg_margin_group is not None and avg_margin_group > 0:
        target_margin = avg_margin_group
    else:
        target_margin = price * TARGET_MARGIN_PERCENT / 100

    # Если текущая маржа сильно отличается от целевой (на > 10% в обе стороны), генерируем рекомендацию
    if target_margin > 0:
        diff_percent = (current_margin - target_margin) / target_margin * 100
        if abs(diff_percent) > 10:  # если отклонение более 10%
            # Рассчитаем, какой коэффициент или добавка нужны, чтобы достичь целевой маржи
            # Предположим, что мы можем применить коэффициент к исходной рознице (retail)
            original_retail = rec["Исходная_розница"]
            if original_retail > 0:
                # Целевая розница = price + target_margin
                target_retail = price + target_margin
                # Рекомендуемый коэффициент = target_retail / original_retail
                recommended_coeff = target_retail / original_retail
                # Ограничим коэффициент разумными пределами (0.5 .. 1.5)
                recommended_coeff = max(0.5, min(1.5, recommended_coeff))
                action = "увеличить коэффициент" if recommended_coeff > 1 else "уменьшить коэффициент"
                recommendations.append({
                    "Бренд": brand,
                    "Модель": rec["Модель"],
                    "Диаметр": diam,
                    "Полное_название": rec["Полное_название"],
                    "Оптовая_цена": price,
                    "Текущая_маржа_руб": current_margin,
                    "Целевая_маржа_руб": round(target_margin, 2),
                    "Отклонение_%": round(diff_percent, 1),
                    "Рекомендуемый_коэффициент": round(recommended_coeff, 3),
                    "Рекомендуемая_розница": round(target_retail, 2),
                    "Действие": action,
                    "Тип_текущей_корректировки": rec["Тип_корректировки"],
                })

if recommendations:
    rec_csv = "margin_recommendations.csv"
    with open(rec_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=recommendations[0].keys())
        writer.writeheader()
        writer.writerows(recommendations)
    print(f"✅ Рекомендации по оптимизации маржи ({len(recommendations)} записей) сохранены в {rec_csv}")
else:
    print("ℹ️ Рекомендации не сгенерированы (все маржи близки к средним или нет данных).")

print("\n✅ Аналитический скрипт завершён.")
print(f"   - Всего товаров в выгрузке: {total_products}")
print(f"   - Пропущено (ЗБ): {excluded_zb}")
print(f"   - Исключено по артикулу: {excluded_article}")
print(f"   - Исключено по CAE: {excluded_cae}")
if SEASON_EXCLUDE_ENABLED:
    print(f"   - Исключено по сезону: {excluded_season}")
