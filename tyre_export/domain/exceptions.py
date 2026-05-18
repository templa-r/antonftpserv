"""Доменные исключения для системы выгрузки шин"""

class TyreExportError(Exception):
    """Базовое исключение для всех ошибок выгрузки"""
    pass

class ProductValidationError(TyreExportError):
    """Ошибка валидации продукта"""
    pass

class ConfigurationError(TyreExportError):
    """Ошибка в конфигурации"""
    pass

class DataFetchError(TyreExportError):
    """Ошибка получения данных от API"""
    pass
