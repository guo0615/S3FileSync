"""
同步任务模型
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid


class SyncMode(Enum):
    """同步模式枚举"""
    BIDIRECTIONAL = "bidirectional"    # 双向同步
    UPLOAD = "upload"                  # 仅上传
    DOWNLOAD = "download"              # 仅下载


class ConflictStrategy(Enum):
    """冲突处理策略枚举"""
    RENAME = "rename"          # 重命名冲突文件
    OVERWRITE = "overwrite"    # 覆盖旧文件
    SKIP = "skip"              # 跳过冲突文件
    MANUAL = "manual"          # 手动解决


@dataclass
class SyncTask:
    """同步任务模型"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""                              # 任务名称
    local_path: str = ""                        # 本地路径
    remote_prefix: str = ""                     # 远程前缀
    sync_mode: SyncMode = SyncMode.BIDIRECTIONAL  # 同步模式
    interval: int = 3600                        # 同步间隔(秒)
    enabled: bool = True                        # 是否启用
    last_sync: Optional[datetime] = None        # 上次同步时间
    next_sync: Optional[datetime] = None        # 下次同步时间
    conflict_strategy: ConflictStrategy = ConflictStrategy.RENAME  # 冲突处理策略

    def __post_init__(self):
        """初始化后处理"""
        if isinstance(self.sync_mode, str):
            self.sync_mode = SyncMode(self.sync_mode)
        if isinstance(self.conflict_strategy, str):
            self.conflict_strategy = ConflictStrategy(self.conflict_strategy)
        if isinstance(self.last_sync, str):
            self.last_sync = datetime.fromisoformat(self.last_sync)
        if isinstance(self.next_sync, str):
            self.next_sync = datetime.fromisoformat(self.next_sync)

    def calculate_next_sync(self) -> datetime:
        """计算下次同步时间（以当前时间为基准）

        无论上次同步时间如何，下次同步时间都从"当前时刻"起算，
        保证程序启动/重新调度任务时，下次同步时间以程序启动时间为准，
        而不是沿用上次会话保存的旧时间（否则程序关闭期间会漏掉调度）。
        """
        from datetime import timedelta
        now = datetime.now()
        self.next_sync = now + timedelta(seconds=self.interval)
        return self.next_sync

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'id': self.id,
            'name': self.name,
            'local_path': self.local_path,
            'remote_prefix': self.remote_prefix,
            'sync_mode': self.sync_mode.value,
            'interval': self.interval,
            'enabled': self.enabled,
            'last_sync': self.last_sync.isoformat() if self.last_sync else None,
            'next_sync': self.next_sync.isoformat() if self.next_sync else None,
            'conflict_strategy': self.conflict_strategy.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'SyncTask':
        """从字典创建"""
        return cls(
            id=data.get('id', str(uuid.uuid4())),
            name=data.get('name', ''),
            local_path=data.get('local_path', ''),
            remote_prefix=data.get('remote_prefix', ''),
            sync_mode=SyncMode(data.get('sync_mode', 'bidirectional')),
            interval=data.get('interval', 3600),
            enabled=data.get('enabled', True),
            last_sync=datetime.fromisoformat(data['last_sync']) if data.get('last_sync') else None,
            next_sync=datetime.fromisoformat(data['next_sync']) if data.get('next_sync') else None,
            conflict_strategy=ConflictStrategy(data.get('conflict_strategy', 'rename')),
        )
