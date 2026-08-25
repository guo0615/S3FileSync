"""
日志查看对话框
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton, QLabel
)
from PyQt6.QtCore import Qt
from typing import Optional
import os


class LogViewerDialog(QDialog):
    """日志查看对话框"""

    def __init__(self, parent=None, log_file: Optional[str] = None):
        """初始化"""
        super().__init__(parent)

        self.log_file = log_file or "logs/sync.log"

        self.setWindowTitle("同步日志")
        self.setMinimumSize(800, 600)
        self.setModal(False)  # 非模态对话框，可以同时操作主窗口

        self._init_ui()
        self._load_log()

    def _init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # 标题
        title_label = QLabel("同步历史记录")
        title_label.setStyleSheet("""
            QLabel {
                font-size: 16px;
                font-weight: bold;
                color: #1976d2;
                padding: 8px;
            }
        """)
        layout.addWidget(title_label)

        # 日志文本框
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
                padding: 8px;
                color: #000000;
                font-family: 'Courier New', monospace;
                font-size: 12px;
            }
        """)
        layout.addWidget(self.log_text)

        # 按钮栏
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)

        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self._load_log)
        button_layout.addWidget(self.refresh_button)

        self.clear_button = QPushButton("清空日志")
        self.clear_button.clicked.connect(self._clear_log)
        button_layout.addWidget(self.clear_button)

        button_layout.addStretch()

        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.close)
        button_layout.addWidget(self.close_button)

        layout.addLayout(button_layout)

        # 应用样式
        self.setStyleSheet("""
            QDialog {
                background-color: #f5f5f5;
            }

            QPushButton {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
                padding: 6px 12px;
                color: #000000;
            }

            QPushButton:hover {
                background-color: #e3f2fd;
                border-color: #1976d2;
            }

            QPushButton:pressed {
                background-color: #bbdefb;
            }

            /* 白色滚动条 */
            QScrollBar:vertical {
                background: #f5f5f5;
                width: 12px;
                margin: 0px;
                border: none;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 5px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: #fafafa;
            }
            QScrollBar::handle:vertical:pressed {
                background: #e0e0e0;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                background: none;
                height: 0px;
                border: none;
            }
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {
                background: none;
            }
            QScrollBar:horizontal {
                background: #f5f5f5;
                height: 12px;
                margin: 0px;
                border: none;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal {
                background: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 5px;
                min-width: 30px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #fafafa;
            }
            QScrollBar::handle:horizontal:pressed {
                background: #e0e0e0;
            }
            QScrollBar::add-line:horizontal,
            QScrollBar::sub-line:horizontal {
                background: none;
                width: 0px;
                border: none;
            }
            QScrollBar::add-page:horizontal,
            QScrollBar::sub-page:horizontal {
                background: none;
            }
        """)

    def _load_log(self):
        """加载日志"""
        try:
            # 尝试读取日志文件
            if os.path.exists(self.log_file):
                with open(self.log_file, 'r', encoding='utf-8') as f:
                    log_content = f.read()

                if log_content.strip():
                    self.log_text.setPlainText(log_content)
                    # 滚动到底部
                    self.log_text.verticalScrollBar().setValue(
                        self.log_text.verticalScrollBar().maximum()
                    )
                else:
                    self.log_text.setPlainText("日志文件为空")
            else:
                self.log_text.setPlainText(f"日志文件不存在: {self.log_file}\n\n提示：\n1. 请先执行同步操作以生成日志\n2. 检查日志文件路径配置是否正确")

        except Exception as e:
            self.log_text.setPlainText(f"读取日志失败: {str(e)}")

    def _clear_log(self):
        """清空日志"""
        from PyQt6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "确认",
            "确定要清空日志文件吗？此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                if os.path.exists(self.log_file):
                    with open(self.log_file, 'w', encoding='utf-8') as f:
                        f.write("")
                    self.log_text.setPlainText("日志已清空")
            except Exception as e:
                QMessageBox.warning(self, "错误", f"清空日志失败: {str(e)}")
