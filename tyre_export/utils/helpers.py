"""Вспомогательные утилиты"""

import re
from typing import Union
from decimal import Decimal


def safe_float(value: Union[str, float, int, None], default: float = 0.0) -> float:
    """Безопасное преобразование в float"""
    if value is None:
        return default
    try:
        return float(str(value).replace(",", ".").strip())
    except (ValueError, TypeError):
        return default


def safe_decimal(value: Union[str, float, int, None], default: Decimal = Decimal('0')) -> Decimal:
    """Безопасное преобразование в Decimal"""
    if value is None:
        return default
    try:
        return Decimal(str(value).replace(",", ".").strip())
    except (ValueError, TypeError):
        return default


def clean_name(s: str) -> str:
    """Очистка имени для использования в URL"""
    return re.sub(r'[^\w\-]', '_', s)


def extract_diameter_from_nomenclature(nomenclature: str) -> int:
    """Извлечение диаметра из номенклатуры"""
    if not nomenclature:
        return 0
    
    patterns = [
        r'[Rr](\d{2})',           # R16
        r'R(\d{2})[A-Za-z]?',     # R16C
    ]
    
    for pattern in patterns:
        match = re.search(pattern, nomenclature)
        if match:
            return int(match.group(1))
    
    return 0
