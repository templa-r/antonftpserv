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
    """
    if isinstance(price, Decimal):
        price = float(price)
    
    if method == 'down':
        return int((price // step) * step)
    elif method == 'up':
        return int(((price + step - 1) // step) * step)
    else:
        return int(round(price / step) * step)
