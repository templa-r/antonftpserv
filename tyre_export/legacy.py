"""Legacy adapter - временный слой для обратной совместимости"""

from typing import Dict, Any, List
from .domain.product import Product
from .config.settings import load_config, ExportConfig
from .utils.rounding import round_price
from .utils.helpers import safe_float, clean_name

# Загружаем конфиг один раз
_config = load_config()


def process_legacy_item(api_item: Dict[str, Any]) -> Product:
    """Преобразование старого формата в новый Product"""
    return Product.from_api_response(api_item)


def get_legacy_config() -> ExportConfig:
    """Получение конфига в старом стиле"""
    return _config


# Экспортируем старые функции для обратной совместимости
def legacy_round_price(price, step=None, method=None):
    """Старая функция округления"""
    step = step or _config.rounding.step
    method = method or _config.rounding.method
    return round_price(price, step, method)


def legacy_clean_name(s):
    """Старая функция очистки имени"""
    return clean_name(s)


def legacy_safe_float(val, default=0.0):
    """Старая функция safe_float"""
    return safe_float(val, default)
