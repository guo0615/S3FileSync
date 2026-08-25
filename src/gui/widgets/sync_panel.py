"""
同步状态面板组件
"""
from typing import Optional
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QProgressBar, QLabel, QPushButton
)
from PyQt6.QtCore import Qt, pyqtSignal
from datetime import datetime

from ...models.transfer_state import TransferState, TransferStatus


class SyncPanelWidget(QWidget):
    """同步状态面板组件"""

    # 信号
    pause_requested = pyqtSignal()
    resume_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        """初始化"""
        super().__init__(parent)

        self._state: Optional[TransferState] = None

        self._init_ui()

    def _init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 标题
        title_label = QLabel("同步状态")
        title_label.setStyleSheet("""
            QLabel {
                font-size: 14px;
                font-weight: bold;
                color: #1976d2;
                padding: 4px;
            }
        """)
        layout.addWidget(title_label)

        # 任务信息
        info_layout = QHBoxLayout()
        info_layout.setSpacing(16)

        self.task_label = QLabel("任务: -")
        info_layout.addWidget(self.task_label)

        self.status_label = QLabel("状态: 就绪")
        info_layout.addWidget(self.status_label)

        layout.addLayout(info_layout)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        # 只显示百分比（进度条数值本身是 0-100 的百分比，不是字节数）
        self.progress_bar.setFormat("%p%")
        layout.addWidget(self.progress_bar)

        # 详细信息
        detail_layout = QHBoxLayout()
        detail_layout.setSpacing(16)

        self.speed_label = QLabel("速度: -")
        detail_layout.addWidget(self.speed_label)

        self.time_label = QLabel("剩余时间: -")
        detail_layout.addWidget(self.time_label)

        layout.addLayout(detail_layout)

        # 操作按钮
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)

        self.pause_button = QPushButton("暂停")
        self.pause_button.clicked.connect(self._on_pause)
        self.pause_button.setEnabled(False)
        button_layout.addWidget(self.pause_button)

        self.resume_button = QPushButton("恢复")
        self.resume_button.clicked.connect(self._on_resume)
        self.resume_button.setEnabled(False)
        button_layout.addWidget(self.resume_button)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.clicked.connect(self._on_cancel)
        self.cancel_button.setEnabled(False)
        button_layout.addWidget(self.cancel_button)

        layout.addLayout(button_layout)

        # 应用样式
        self.setStyleSheet("""
            QProgressBar {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
                text-align: center;
                height: 24px;
            }

            QProgressBar::chunk {
                background-color: #4caf50;
                border-radius: 3px;
            }

            QLabel {
                color: #424242;
            }

            QPushButton {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
                padding: 6px 12px;
                color: #424242;
            }

            QPushButton:hover {
                background-color: #e3f2fd;
                border-color: #1976d2;
            }

            QPushButton:pressed {
                background-color: #bbdefb;
            }

            QPushButton:disabled {
                background-color: #f5f5f5;
                color: #9e9e9e;
            }
        """)

    def update_state(self, state: TransferState):
        """更新传输状态"""
        self._state = state

        # 更新进度
        self.progress_bar.setValue(int(state.progress))

        # 更新状态
        status_text = {
            TransferStatus.PENDING: "等待中",
            TransferStatus.RUNNING: "同步中",
            TransferStatus.PAUSED: "已暂停",
            TransferStatus.COMPLETED: "已完成",
            TransferStatus.FAILED: "失败",
            TransferStatus.CANCELLED: "已取消",
        }.get(state.status, "未知")

        self.status_label.setText(f"状态: {status_text}")

        # 更新速度（自适应单位）
        if state.speed > 0:
            self.speed_label.setText(f"速度: {self._format_speed(state.speed)}")
        else:
            self.speed_label.setText("速度: -")

        # 更新剩余时间
        if state.speed > 0 and state.total_size > state.transferred_size:
            remaining_bytes = state.total_size - state.transferred_size
            remaining_seconds = remaining_bytes / state.speed
            self.time_label.setText(f"剩余时间: {self._format_eta(remaining_seconds)}")
        else:
            self.time_label.setText("剩余时间: -")

        # 更新按钮状态
        if state.status == TransferStatus.RUNNING:
            self.pause_button.setEnabled(True)
            self.resume_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
        elif state.status == TransferStatus.PAUSED:
            self.pause_button.setEnabled(False)
            self.resume_button.setEnabled(True)
            self.cancel_button.setEnabled(True)
        else:
            self.pause_button.setEnabled(False)
            self.resume_button.setEnabled(False)
            self.cancel_button.setEnabled(False)

    @staticmethod
    def _format_speed(speed_bytes_per_sec: float) -> str:
        """格式化传输速度（自适应单位）"""
        speed = float(speed_bytes_per_sec)
        for unit in ['B/s', 'KB/s', 'MB/s', 'GB/s']:
            if speed < 1024.0:
                return f"{speed:.2f} {unit}"
            speed /= 1024.0
        return f"{speed:.2f} TB/s"

    @staticmethod
    def _format_eta(seconds: float) -> str:
        """格式化剩余时间（自适应 秒/分/小时）"""
        seconds = max(int(seconds), 0)
        if seconds < 60:
            return f"{seconds}秒"
        minutes = seconds // 60
        secs = seconds % 60
        if minutes < 60:
            return f"{minutes}分{secs:02d}秒"
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}小时{mins:02d}分"

    def set_task_name(self, name: str):
        """设置任务名称"""
        self.task_label.setText(f"任务: {name}")

    def update_status(self, status: str):
        """更新状态文本"""
        self.status_label.setText(f"状态: {status}")

    def update_progress(self, value: int):
        """直接更新进度条百分比（0-100）

        供定时任务等不携带字节/速度信息的场景使用。
        """
        value = max(0, min(100, int(value)))
        self.progress_bar.setValue(value)

    def reset(self):
        """重置状态"""
        self._state = None
        self.progress_bar.setValue(0)
        self.status_label.setText("状态: 就绪")
        self.speed_label.setText("速度: -")
        self.time_label.setText("剩余时间: -")
        self.pause_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.cancel_button.setEnabled(False)

    def _on_pause(self):
        """暂停"""
        self.pause_requested.emit()

    def _on_resume(self):
        """恢复"""
        self.resume_requested.emit()

    def _on_cancel(self):
        """取消"""
        self.cancel_requested.emit()
