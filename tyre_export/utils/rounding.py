"""Утилиты для округления цен"""

from decimal import Decimal
from typing import Union


def round_price(
    price: Union[Decimal, float, int],
    step: int = 10,
    method: str = 'nearest'
) -> int:
    """
    Округление цены с заданным шагом
    
    Args:
        price: Исходная цена
        step: Шаг округления (по умолчанию 10)
        method: Метод округления ('nearest', 'up', 'down')
    
    Returns:
        Округленная цена (int)
    """
    if isinstance(price, Decimal):
        price = float(price)
    
    if method == 'down':
        return int((price // step) * step)
    elif method == 'up':
        return int(((price + step - 1) // step) * step)
    else:  # nearest
        return int(round(price / step) * step)


def format_currency(price: Union[Decimal, float, int]) -> str:
    """Форматирование цены для вывода"""
    return f"{int(price):,}".replace(",", " ")
