"""
传输状态模型
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TransferStatus(Enum):
    """传输状态枚举"""
    PENDING = "pending"           # 等待中
    RUNNING = "running"           # 运行中
    PAUSED = "paused"             # 已暂停
    COMPLETED = "completed"       # 已完成
    FAILED = "failed"             # 失败
    CANCELLED = "cancelled"       # 已取消


@dataclass
class TransferState:
    """传输状态模型"""
    transfer_id: str                     # 传输ID
    file_path: str                       # 文件路径
    total_size: int                      # 总大小(字节)
    transferred_size: int = 0            # 已传输大小(字节)
    progress: float = 0.0                # 进度(0-100)
    status: TransferStatus = TransferStatus.PENDING  # 状态
    start_time: Optional[datetime] = None  # 开始时间
    end_time: Optional[datetime] = None    # 结束时间
    error: Optional[str] = None            # 错误信息
    speed: float = 0.0                    # 传输速度(bytes/s)

    def update_progress(self, transferred: int):
        """更新进度"""
        self.transferred_size = transferred
        if self.total_size > 0:
            self.progress = (transferred / self.total_size) * 100

    def calculate_speed(self, elapsed_seconds: float):
        """计算传输速度"""
        if elapsed_seconds > 0:
            self.speed = self.transferred_size / elapsed_seconds

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            'transfer_id': self.transfer_id,
            'file_path': self.file_path,
            'total_size': self.total_size,
            'transferred_size': self.transferred_size,
            'progress': self.progress,
            'status': self.status.value,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'error': self.error,
            'speed': self.speed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'TransferState':
        """从字典创建"""
        return cls(
            transfer_id=data['transfer_id'],
            file_path=data['file_path'],
            total_size=data['total_size'],
            transferred_size=data.get('transferred_size', 0),
            progress=data.get('progress', 0.0),
            status=TransferStatus(data.get('status', 'pending')),
            start_time=datetime.fromisoformat(data['start_time']) if data.get('start_time') else None,
            end_time=datetime.fromisoformat(data['end_time']) if data.get('end_time') else None,
            error=data.get('error'),
            speed=data.get('speed', 0.0),
        )
