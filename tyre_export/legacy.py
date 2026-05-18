"""Legacy adapter - временный слой для обратной совместимости"""

from typing import Dict, Any, List
import sys
import os

# Добавляем родительскую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tyre_export.domain.product import Product
from tyre_export.config.settings import load_config, ExportConfig
from tyre_export.utils.rounding import round_price
from tyre_export.utils.helpers import safe_float, clean_name

# Загружаем конфиг один раз
_config = None

def get_legacy_config() -> ExportConfig:
    """Получение конфига в старом стиле"""
    global _config
    if _config is None:
        _config = load_config()
    return _config

def process_legacy_item(api_item: Dict[str, Any]) -> Product:
    """Преобразование старого формата в новый Product"""
    return Product.from_api_response(api_item)

# Экспортируем старые функции для обратной совместимости
def legacy_round_price(price, step=None, method=None):
    """Старая функция округления"""
    config = get_legacy_config()
    step = step or config.rounding.step
    method = method or config.rounding.method
    return round_price(price, step, method)

def legacy_clean_name(s):
    """Старая функция очистки имени"""
    return clean_name(s)

def legacy_safe_float(val, default=0.0):
    """Старая функция safe_float"""
    return safe_float(val, default)
