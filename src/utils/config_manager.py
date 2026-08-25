"""
配置管理器
"""
import os
import yaml
from typing import Any, Dict, Optional
from dataclasses import dataclass, field
import shutil
from pathlib import Path


@dataclass
class S3Config:
    """S3配置"""
    endpoint: str = "http://localhost:9000"
    access_key: str = ""
    secret_key: str = ""
    bucket: str = ""
    region: str = "us-east-1"
    use_ssl: bool = False
    verify_ssl: bool = False

    def to_dict(self) -> dict:
        return {
            'endpoint': self.endpoint,
            'access_key': self.access_key,
            'secret_key': self.secret_key,
            'bucket': self.bucket,
            'region': self.region,
            'use_ssl': self.use_ssl,
            'verify_ssl': self.verify_ssl,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'S3Config':
        return cls(
            endpoint=data.get('endpoint', 'http://localhost:9000'),
            access_key=data.get('access_key', ''),
            secret_key=data.get('secret_key', ''),
            bucket=data.get('bucket', ''),
            region=data.get('region', 'us-east-1'),
            use_ssl=data.get('use_ssl', False),
            verify_ssl=data.get('verify_ssl', False),
        )


@dataclass
class SyncConfig:
    """同步配置"""
    max_concurrent: int = 3
    chunk_size: int = 8388608  # 8MB
    retry_times: int = 3
    retry_delay: int = 5
    conflict_strategy: str = "rename"
    sync_mode: str = "bidirectional"
    realtime_watch: bool = True  # 本地文件变更实时监听同步（本地→远程即时同步，含删除传播）

    def to_dict(self) -> dict:
        return {
            'max_concurrent': self.max_concurrent,
            'chunk_size': self.chunk_size,
            'retry_times': self.retry_times,
            'retry_delay': self.retry_delay,
            'conflict_strategy': self.conflict_strategy,
            'sync_mode': self.sync_mode,
            'realtime_watch': self.realtime_watch,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SyncConfig':
        return cls(
            max_concurrent=data.get('max_concurrent', 3),
            chunk_size=data.get('chunk_size', 8388608),
            retry_times=data.get('retry_times', 3),
            retry_delay=data.get('retry_delay', 5),
            conflict_strategy=data.get('conflict_strategy', 'rename'),
            sync_mode=data.get('sync_mode', 'bidirectional'),
            realtime_watch=data.get('realtime_watch', True),
        )


@dataclass
class AppConfig:
    """应用配置"""
    auto_start: bool = False
    minimize_to_tray: bool = True
    log_level: str = "INFO"
    log_file: str = "logs/sync.log"
    language: str = "zh_CN"

    def to_dict(self) -> dict:
        return {
            'auto_start': self.auto_start,
            'minimize_to_tray': self.minimize_to_tray,
            'log_level': self.log_level,
            'log_file': self.log_file,
            'language': self.language,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'AppConfig':
        return cls(
            auto_start=data.get('auto_start', False),
            minimize_to_tray=data.get('minimize_to_tray', True),
            log_level=data.get('log_level', 'INFO'),
            log_file=data.get('log_file', 'logs/sync.log'),
            language=data.get('language', 'zh_CN'),
        )


class ConfigManager:
    """配置管理器"""

    def __init__(self, config_path: Optional[str] = None):
        """
        初始化配置管理器

        Args:
            config_path: 配置文件路径，如果为None则使用默认路径
        """
        if config_path is None:
            # 使用默认配置路径
            config_dir = Path.home() / ".s3-sync-tool"
            config_dir.mkdir(parents=True, exist_ok=True)
            config_path = str(config_dir / "config.yaml")

        self.config_path = config_path
        self._config: Dict[str, Any] = {}
        self._s3_config: Optional[S3Config] = None
        self._sync_config: Optional[SyncConfig] = None
        self._app_config: Optional[AppConfig] = None
        self._tasks: list = []

        # 加载配置
        self.load_config()

    def load_config(self) -> bool:
        """
        加载配置文件

        Returns:
            是否成功加载
        """
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self._config = yaml.safe_load(f) or {}
            else:
                # 创建默认配置
                self._create_default_config()

            # 解析配置
            self._parse_config()
            return True
        except Exception as e:
            print(f"加载配置失败: {e}")
            self._create_default_config()
            self._parse_config()
            return False

    def _create_default_config(self):
        """创建默认配置"""
        # 获取默认配置文件路径
        default_config_path = os.path.join(
            os.path.dirname(__file__),
            '..',
            '..',
            'config',
            'default_config.yaml'
        )

        if os.path.exists(default_config_path):
            # 复制默认配置
            shutil.copy(default_config_path, self.config_path)
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f) or {}
        else:
            # 创建最小配置
            self._config = {
                's3': S3Config().to_dict(),
                'sync': SyncConfig().to_dict(),
                'tasks': [],
                'app': AppConfig().to_dict(),
            }
            self.save_config()

    def _parse_config(self):
        """解析配置"""
        self._s3_config = S3Config.from_dict(self._config.get('s3', {}))
        self._sync_config = SyncConfig.from_dict(self._config.get('sync', {}))
        self._app_config = AppConfig.from_dict(self._config.get('app', {}))
        self._tasks = self._config.get('tasks', [])

    def save_config(self) -> bool:
        """
        保存配置到文件

        Returns:
            是否成功保存
        """
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)

            # 更新配置字典
            self._config['s3'] = self._s3_config.to_dict()
            self._config['sync'] = self._sync_config.to_dict()
            self._config['app'] = self._app_config.to_dict()
            self._config['tasks'] = self._tasks

            # 写入文件
            with open(self.config_path, 'w', encoding='utf-8') as f:
                yaml.dump(self._config, f, default_flow_style=False, allow_unicode=True)

            return True
        except Exception as e:
            print(f"保存配置失败: {e}")
            return False

    def get_s3_config(self) -> S3Config:
        """获取S3配置"""
        return self._s3_config

    def update_s3_config(self, config: S3Config) -> bool:
        """更新S3配置"""
        self._s3_config = config
        return self.save_config()

    def get_sync_config(self) -> SyncConfig:
        """获取同步配置"""
        return self._sync_config

    def update_sync_config(self, config: SyncConfig) -> bool:
        """更新同步配置"""
        self._sync_config = config
        return self.save_config()

    def get_app_config(self) -> AppConfig:
        """获取应用配置"""
        return self._app_config

    def update_app_config(self, config: AppConfig) -> bool:
        """更新应用配置"""
        self._app_config = config
        return self.save_config()

    def get_tasks(self) -> list:
        """获取任务列表"""
        return self._tasks

    def update_tasks(self, tasks: list) -> bool:
        """更新任务列表"""
        self._tasks = tasks
        return self.save_config()

    def get_config(self, key: str, default: Any = None) -> Any:
        """
        获取配置项

        Args:
            key: 配置键，支持点号分隔的路径，如 's3.endpoint'
            default: 默认值

        Returns:
            配置值
        """
        keys = key.split('.')
        value = self._config

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    def set_config(self, key: str, value: Any) -> bool:
        """
        设置配置项

        Args:
            key: 配置键，支持点号分隔的路径
            value: 配置值

        Returns:
            是否成功
        """
        keys = key.split('.')
        config = self._config

        # 遍历到最后一个键的父级
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]

        # 设置值
        config[keys[-1]] = value
        return self.save_config()

    def export_config(self, export_path: str) -> bool:
        """
        导出配置到指定路径

        Args:
            export_path: 导出路径

        Returns:
            是否成功
        """
        try:
            shutil.copy(self.config_path, export_path)
            return True
        except Exception as e:
            print(f"导出配置失败: {e}")
            return False

    def import_config(self, import_path: str) -> bool:
        """
        从指定路径导入配置

        Args:
            import_path: 导入路径

        Returns:
            是否成功
        """
        try:
            shutil.copy(import_path, self.config_path)
            return self.load_config()
        except Exception as e:
            print(f"导入配置失败: {e}")
            return False
