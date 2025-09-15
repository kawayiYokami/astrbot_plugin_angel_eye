"""
Angel Eye 插件 - 自定义异常类
"""

class AngelEyeError(Exception):
    """插件的基础异常类"""
    pass

class ClientError(AngelEyeError):
    """客户端相关错误，例如API请求失败"""
    pass

class ConfigError(AngelEyeError):
    """配置相关错误"""
    pass

class ParsingError(AngelEyeError):
    """数据解析错误，例如解析JSON或wikitext失败"""
    pass

class ValidationError(AngelEyeError):
    """输入验证错误"""
    pass