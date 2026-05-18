"""Настройки приложения"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from decimal import Decimal
import os


@dataclass
class APIConfig:
    """Конфигурация API"""
    url: str
    user: str
    password: str


@dataclass
class RoundingConfig:
    """Конфигурация округления"""
    step: int = 10
    method: str = 'nearest'


@dataclass
class ImageConfig:
    """Конфигурация изображений"""
    enabled: bool = True
    check_enabled: bool = True
    base_url: str = "https://s3.ru1.storage.beget.cloud/fa5a823588a1-antontyres/images"
    cache_file: str = "image_cache.json"
    cache_refresh: bool = False
    max_workers: int = 50
    timeout: int = 1


@dataclass
class PricingConfig:
    """Конфигурация ценообразования"""
    global_coeff: Dict[str, Any] = field(default_factory=lambda: {
        "default": 0.92,
        "diameter_ranges": [
            {"min": 13, "max": 15, "coeff": 0.95},
            {"min": 16, "max": 17, "coeff": 0.94},
            {"min": 18, "max": 22, "coeff": 0.93},
        ],
    })
    
    brand_coeffs: Dict[str, Any] = field(default_factory=lambda: {
        "ikon": {
            "default": 0.88,
            "diameter_ranges": [
                {"min": 13, "max": 15, "coeff": 0.905},
                {"min": 17, "max": 22, "coeff": 0.88},
            ],
        },
        "laufenn": 0.905,
        "bridgestone": 0.965,
        "hankook": {
            "default": 0.915,
            "diameter_ranges": [
                {"min": 13, "max": 16, "coeff": 0.907},
                {"min": 17, "max": 22, "coeff": 0.906},
            ],
        },
    })
    
    ignore_coeff_brands: List[str] = field(default_factory=lambda: [
        "Mazzini", "Nexen", "MAXXIS", "Sonix"
    ])
    
    min_margin: Decimal = Decimal('500')
    
    model_rules: Dict[tuple, Dict[str, Any]] = field(default_factory=lambda: {
        ("autograph", "autograph ice 9 suv"): {
            "type": "add_to_field_by_diameter",
            "field": "price",
            "ranges": [
                {"min": 16, "max": 17, "value": 1300},
                {"min": 18, "max": 19, "value": 1700},
            ],
            "default": None,
        },
    })


@dataclass
class FiltersConfig:
    """Конфигурация фильтров"""
    season_exclude_enabled: bool = False
    season_exclude_value: str = "зима"
    
    excluded_brands: List[str] = field(default_factory=lambda: [
        "Mazzini", "Nexen", "MAXXIS", "Predator", "Compasal", "HIFLY", "Aoteli",
        "Torero", "Viatti", "Massimo", "Firemax", "Sonix", "Prinx", "Roadmarch",
        "Kelly", "Nitto", "Кама"
    ])
    
    excluded_brands_from_export: List[str] = field(default_factory=lambda: [
        "Compasal", "Aoteli"
    ])
    
    excluded_category: List[str] = field(default_factory=lambda: ["Грузовая"])
    
    excluded_articles: List[str] = field(default_factory=lambda: [
        "195/75R16C Laufenn X FIT VAN LV01",
        "АТ27x8-12 MAXXIS M961 6PR",
        "185/75R16C Ikon Autograph Snow C4",
        "33x10,50-16 NORTEC ET-500 111N TL АШК",
    ])
    
    exclude_cae_enabled: bool = True
    excluded_cae: List[str] = field(default_factory=list)


@dataclass
class ExportConfig:
    """Основная конфигурация экспорта"""
    api: APIConfig
    rounding: RoundingConfig = field(default_factory=RoundingConfig)
    images: ImageConfig = field(default_factory=ImageConfig)
    pricing: PricingConfig = field(default_factory=PricingConfig)
    filters: FiltersConfig = field(default_factory=FiltersConfig)
    include_price_tag: bool = False
    output_file: str = "aztyre_full.xml"


def load_config() -> ExportConfig:
    """Загрузка конфигурации"""
    
    api_config = APIConfig(
        url="https://ka2.sibzapaska.ru:16500/API/hs/V2/GetTires",
        user="API_client",
        password="rWp7mFWXRKOq"
    )
    
    filters_config = FiltersConfig()
    
    # Заполняем список исключенных CAE
    filters_config.excluded_cae = [
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
    
    image_config = ImageConfig(
        cache_refresh=os.getenv("IMAGE_CACHE_REFRESH", "false").lower() == "true"
    )
    
    return ExportConfig(
        api=api_config,
        rounding=RoundingConfig(),
        images=image_config,
        pricing=PricingConfig(),
        filters=filters_config,
        output_file="aztyre_full.xml"
    )
