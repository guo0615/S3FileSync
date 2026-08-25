"""
日志工具
"""
import logging
import os
import sys
from datetime import datetime
from typing import Optional


def setup_logger(
    name: str = "s3-sync",
    log_file: Optional[str] = None,
    level: str = "INFO",
    format_string: Optional[str] = None
) -> logging.Logger:
    """
    设置日志记录器

    该函数会同时配置 root logger 的文件处理器，确保所有模块的日志
    （通过 logging.getLogger(__name__) 创建）都能统一输出到同一个日志文件。

    Args:
        name: 日志记录器名称
        log_file: 日志文件路径
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR/CRITICAL)
        format_string: 日志格式字符串

    Returns:
        配置好的日志记录器
    """
    log_level = getattr(logging, level.upper())

    # 默认格式
    if format_string is None:
        format_string = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    formatter = logging.Formatter(format_string, datefmt="%Y-%m-%d %H:%M:%S")

    # 解析日志文件路径为绝对路径（基于用户目录，避免打包后相对路径问题）
    if log_file:
        log_file = _resolve_log_path(log_file)
        # 确保日志目录存在
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            try:
                os.makedirs(log_dir, exist_ok=True)
            except OSError as e:
                print(f"创建日志目录失败: {e}")

    # 配置 root logger（让所有模块的日志都能写入文件）
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 清除已有的文件处理器，避免重复写入
    _remove_file_handlers(root_logger)

    # 为 root logger 添加文件处理器（写入统一日志文件）
    if log_file:
        try:
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(log_level)
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except OSError as e:
            print(f"创建日志文件处理器失败: {e}")

    # 为 root logger 添加控制台处理器（如果还没有）
    _ensure_console_handler(root_logger, formatter)

    # 创建命名 logger
    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    logger.propagate = True  # 确保日志向上传播到 root logger

    return logger


def setup_global_logging(
    log_file: Optional[str] = None,
    level: str = "INFO",
    format_string: Optional[str] = None
) -> logging.Logger:
    """
    配置全局日志系统（root logger）

    在应用启动时调用一次，即可让所有模块的日志统一输出到文件和控制台。

    Args:
        log_file: 日志文件路径
        level: 日志级别
        format_string: 日志格式

    Returns:
        配置好的日志记录器
    """
    return setup_logger("root", log_file, level, format_string)


def _resolve_log_path(log_file: str) -> str:
    """
    解析日志文件路径为绝对路径

    相对路径基于用户主目录下的 .s3-sync-tool 目录，避免打包后路径问题。

    Args:
        log_file: 日志文件路径

    Returns:
        绝对路径
    """
    if os.path.isabs(log_file):
        return log_file

    # 基于用户主目录
    home = os.path.expanduser("~")
    base_dir = os.path.join(home, ".s3-sync-tool")
    return os.path.join(base_dir, log_file)


def _remove_file_handlers(logger: logging.Logger):
    """移除日志记录器上的文件处理器（避免重复）"""
    for handler in list(logger.handlers):
        if isinstance(handler, logging.FileHandler):
            try:
                handler.close()
            except Exception:
                pass
            logger.removeHandler(handler)


def _ensure_console_handler(logger: logging.Logger, formatter: logging.Formatter):
    """确保有控制台处理器"""
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            return

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


class LoggerManager:
    """日志管理器"""

    _loggers = {}

    @classmethod
    def get_logger(
        cls,
        name: str = "s3-sync",
        log_file: Optional[str] = None,
        level: str = "INFO"
    ) -> logging.Logger:
        """
        获取或创建日志记录器

        Args:
            name: 日志记录器名称
            log_file: 日志文件路径
            level: 日志级别

        Returns:
            日志记录器
        """
        if name not in cls._loggers:
            cls._loggers[name] = setup_logger(name, log_file, level)
        return cls._loggers[name]

    @classmethod
    def update_level(cls, name: str, level: str):
        """
        更新日志级别

        Args:
            name: 日志记录器名称
            level: 新的日志级别
        """
        if name in cls._loggers:
            logger = cls._loggers[name]
            logger.setLevel(getattr(logging, level.upper()))
            for handler in logger.handlers:
                handler.setLevel(getattr(logging, level.upper()))
