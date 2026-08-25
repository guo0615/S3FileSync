"""
文件信息模型
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional


@dataclass
class FileInfo:
    """文件信息模型"""
    path: str                          # 文件路径(相对路径)
    size: int                          # 文件大小(字节)
    mtime: datetime                    # 修改时间
    hash: Optional[str] = None         # 文件哈希(MD5/SHA256)
    is_dir: bool = False               # 是否为目录
    metadata: Dict[str, str] = field(default_factory=dict)  # 元数据

    def __post_init__(self):
        """初始化后处理"""
        if isinstance(self.mtime, str):
            self.mtime = datetime.fromisoformat(self.mtime)
        # 统一为 naive datetime：仅剥离时区、保留原墙钟时间（远程 S3 LastModified 为
        # UTC 服务器时间；本地 mtime 由 FileUtils.get_file_mtime_utc 提供同口径值），
        # 不再换算成客户端本地时区，避免显示/比对出现 8 小时偏差。
        if self.mtime is not None and self.mtime.tzinfo is not None:
            self.mtime = self.mtime.replace(tzinfo=None)

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'path': self.path,
            'size': self.size,
            'mtime': self.mtime.isoformat() if self.mtime else None,
            'hash': self.hash,
            'is_dir': self.is_dir,
            'metadata': self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'FileInfo':
        """从字典创建"""
        return cls(
            path=data['path'],
            size=data['size'],
            mtime=datetime.fromisoformat(data['mtime']) if data.get('mtime') else datetime.now(),
            hash=data.get('hash'),
            is_dir=data.get('is_dir', False),
            metadata=data.get('metadata', {}),
        )

    def __eq__(self, other):
        """判断两个文件信息是否相等"""
        if not isinstance(other, FileInfo):
            return False
        return self.path == other.path and self.hash == other.hash

    def __hash__(self):
        """哈希值"""
        return hash((self.path, self.hash))
