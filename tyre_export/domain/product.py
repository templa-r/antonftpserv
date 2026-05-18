"""Модель продукта (шины/диски)"""

from typing import Optional, Dict, Any, List
from decimal import Decimal
from dataclasses import dataclass, field
from datetime import datetime
import re


@dataclass
class Product:
    """Основная модель товара - шина или диск"""
    
    # Идентификаторы
    cae: str                          # Уникальный артикул поставщика
    article: str                      # Артикул производителя
    brand: str                        # Бренд
    model: str                        # Модель
    
    # Размеры
    width: Optional[int] = None
    profile: Optional[int] = None
    diameter: Optional[int] = None
    
    # Цены
    purchase_price: Decimal = Decimal('0')    # Закупочная цена
    retail_original: Decimal = Decimal('0')   # Исходная розничная цена от поставщика
    final_retail: Optional[Decimal] = None    # Рассчитанная итоговая цена
    
    # Категории и свойства
    season: Optional[str] = None
    category: Optional[str] = None
    name: str = ""
    nomenclature: str = ""
    
    # Изображения
    original_image_url: str = ""               # URL от поставщика
    processed_images: List[str] = field(default_factory=list)  # Найденные S3 URL
    
    # Метаданные
    raw_data: Dict[str, Any] = field(default_factory=dict)      # Исходные данные от API
    supplier: str = "default"                  # Поставщик (для многопоточности)
    
    def __post_init__(self):
        """Автоматическая очистка и валидация после создания"""
        self._clean_brand()
        self._clean_model()
        self._extract_dimensions_from_nomenclature()
    
    def _clean_brand(self):
        """Очистка названия бренда"""
        if self.brand == "Ikon (Nokian Tyres)":
            self.brand = "Ikon"
    
    def _clean_model(self):
        """Очистка названия модели"""
        # Замены моделей
        replacements = {
            "Blu Earth V906": "BluEarth Winter V906",
            "VS-EV": "Victra Sport EV", 
            "Ecsta PS72": "Ecsta Sport PS72",
        }
        if self.model in replacements:
            self.model = replacements[self.model]
            
        # Очистка name если нужно
        if "(Nokian Tyres)" in self.name:
            self.name = self.name.replace("(Nokian Tyres)", "").strip()
            self.name = re.sub(r'\s+', ' ', self.name)
    
    def _extract_dimensions_from_nomenclature(self):
        """Извлечение размеров из номенклатуры если нет прямых полей"""
        if self.width and self.profile and self.diameter:
            return
            
        if not self.nomenclature:
            return
            
        # Паттерны для поиска размеров: 205/55R16, 33x10.50R15, 195/75R16C
        patterns = [
            r'(\d+)/(\d+)[Zz]?[Rr](\d+)',           # 205/55R16
            r'(\d+)x([\d\.]+)[Rr](\d+)',            # 33x10.50R15
            r'(\d+)/(\d+)[Zz]?[Rr](\d+)[A-Za-z]?',  # 195/75R16C
        ]
        
        for pattern in patterns:
            match = re.search(pattern, self.nomenclature)
            if match:
                if not self.width and len(match.groups()) >= 1:
                    self.width = int(match.group(1))
                if not self.profile and len(match.groups()) >= 2:
                    self.profile = int(float(match.group(2)))
                if not self.diameter and len(match.groups()) >= 3:
                    self.diameter = int(match.group(3))
                break
    
    @property
    def product_type(self) -> str:
        """Тип продукта: 'tyres' или 'disk'"""
        # Диски определяются по номенклатуре: 16 7.5J 5x139.7 ET40 DIA110.6
        if self.nomenclature and re.match(r'^(1[2-9]|2[0-4])\s', self.nomenclature):
            return "disk"
        return "tyres"
    
    @property
    def size_key(self) -> str:
        """Ключ для дедупликации: brand + width + profile + diameter"""
        return f"{self.brand}_{self.width}_{self.profile}_{self.diameter}"
    
    def to_dict(self) -> Dict[str, Any]:
        """Преобразование в словарь для XML генерации"""
        data = {
            "cae": self.cae,
            "article": self.article,
            "brand": self.brand,
            "model": self.model,
            "name": self.name,
            "Номенклатура": self.nomenclature,
            "season": self.season or "",
            "category": self.category or "",
            "width": self.width or "",
            "profile": self.profile or "",
            "diameter": self.diameter or "",
            "price": str(self.purchase_price),
            "retail": str(self.final_retail if self.final_retail else self.retail_original),
        }
        
        # Добавляем сырые данные если они есть (для обратной совместимости)
        data.update(self.raw_data)
        
        return data
    
    @classmethod
    def from_api_response(cls, api_item: Dict[str, Any], supplier: str = "default") -> 'Product':
        """Создание продукта из ответа API"""
        return cls(
            cae=api_item.get("cae", ""),
            article=api_item.get("article", ""),
            brand=api_item.get("brand", ""),
            model=api_item.get("model", ""),
            width=api_item.get("width"),
            profile=api_item.get("profile") or api_item.get("height"),
            diameter=api_item.get("diameter"),
            purchase_price=Decimal(str(api_item.get("price", 0))),
            retail_original=Decimal(str(api_item.get("retail", 0))),
            season=api_item.get("season", ""),
            category=api_item.get("category", ""),
            name=api_item.get("name", ""),
            nomenclature=api_item.get("Номенклатура", ""),
            original_image_url=api_item.get("img", ""),
            raw_data=api_item,
            supplier=supplier
        )
