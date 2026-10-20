"""
任务对话框
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QComboBox, QCheckBox,
    QDialogButtonBox, QFormLayout, QGroupBox, QFileDialog,
    QPlainTextEdit
)
from PyQt6.QtCore import Qt
from typing import Optional
import uuid

from ...models.sync_task import SyncTask, SyncMode, ConflictStrategy
from ...utils.icon_utils import get_checkbox_checked_icon, get_checkbox_unchecked_icon


class TaskDialog(QDialog):
    """任务对话框"""

    def __init__(self, parent=None, task: Optional[SyncTask] = None):
        """初始化"""
        super().__init__(parent)

        self._task = task

        self.setWindowTitle("新建任务" if not task else "编辑任务")
        self.setMinimumSize(560, 580)
        self.setModal(True)

        self._init_ui()

        if task:
            self._load_task(task)

    def _init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # 基本信息
        basic_group = QGroupBox("基本信息")
        form_layout = QFormLayout(basic_group)
        form_layout.setSpacing(12)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("任务名称")
        form_layout.addRow("名称:", self.name_edit)

        # 本地路径
        local_layout = QHBoxLayout()
        self.local_path_edit = QLineEdit()
        self.local_path_edit.setPlaceholderText("选择本地目录")
        local_layout.addWidget(self.local_path_edit)

        self.browse_local_button = QPushButton("浏览...")
        self.browse_local_button.clicked.connect(self._on_browse_local)
        local_layout.addWidget(self.browse_local_button)

        form_layout.addRow("本地路径:", local_layout)

        # 远程前缀
        self.remote_prefix_edit = QLineEdit()
        self.remote_prefix_edit.setPlaceholderText("例如: documents/")
        form_layout.addRow("远程前缀:", self.remote_prefix_edit)

        layout.addWidget(basic_group)

        # 同步设置
        sync_group = QGroupBox("同步设置")
        form_layout = QFormLayout(sync_group)
        form_layout.setSpacing(12)

        # 同步模式
        self.sync_mode_combo = QComboBox()
        self.sync_mode_combo.addItems(["双向同步", "仅上传", "仅下载"])
        form_layout.addRow("同步模式:", self.sync_mode_combo)

        # 同步间隔
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(60, 86400)
        self.interval_spin.setValue(3600)
        self.interval_spin.setSuffix(" 秒")
        form_layout.addRow("同步间隔:", self.interval_spin)

        # 冲突策略
        self.conflict_combo = QComboBox()
        self.conflict_combo.addItems(["重命名", "覆盖", "跳过", "手动解决"])
        form_layout.addRow("冲突策略:", self.conflict_combo)

        # 启用
        self.enabled_check = QCheckBox("启用任务")
        self.enabled_check.setChecked(True)
        form_layout.addRow(self.enabled_check)

        layout.addWidget(sync_group)

        # 排除规则（.gitignore 风格）
        exclude_group = QGroupBox("排除规则")
        exclude_layout = QVBoxLayout(exclude_group)
        exclude_layout.setSpacing(6)

        exclude_hint = QLabel(
            "每行一条规则，语法类似 .gitignore。命中的文件不参与「本地→远程」上传，"
            "也不会因此删除远程；远程已有的仍可下载。"
        )
        exclude_hint.setWordWrap(True)
        exclude_hint.setStyleSheet("color: #757575; font-weight: normal;")
        exclude_layout.addWidget(exclude_hint)

        self.exclude_edit = QPlainTextEdit()
        self.exclude_edit.setPlaceholderText(
            "示例:\n"
            "# 注释行\n"
            "*.log            # 任意层级的 .log 文件\n"
            "node_modules/    # 任意层级的该目录及其内容\n"
            "/build/          # 仅根目录下的 build 目录\n"
            "*.tmp\n"
            "!important.log   # 取反：重新包含"
        )
        self.exclude_edit.setMinimumHeight(120)
        exclude_layout.addWidget(self.exclude_edit)

        layout.addWidget(exclude_group)

        # 按钮
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_ok)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # 应用样式
        checked_icon = get_checkbox_checked_icon()
        unchecked_icon = get_checkbox_unchecked_icon()

        self.setStyleSheet("""
            QDialog {
                background-color: #f5f5f5;
                color: #000000;
            }

            QGroupBox {
                font-weight: bold;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
                margin-top: 8px;
                padding-top: 8px;
                color: #000000;
            }

            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
                color: #000000;
            }

            QLabel {
                color: #000000;
            }

            QLineEdit, QSpinBox, QPlainTextEdit {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
                padding: 6px;
                color: #000000;
            }

            QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus {
                border-color: #1976d2;
            }

            QComboBox {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
                padding: 6px;
                color: #000000;
            }

            QComboBox:focus {
                border-color: #1976d2;
            }

            QComboBox::drop-down {
                border: none;
                width: 20px;
            }

            QComboBox::down-arrow {
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid #757575;
                margin-right: 5px;
            }

            QComboBox QAbstractItemView {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                selection-background-color: #e3f2fd;
                selection-color: #000000;
                color: #000000;
            }

            QComboBox QAbstractItemView::item {
                background-color: #ffffff;
                color: #000000;
                padding: 4px;
            }

            QComboBox QAbstractItemView::item:hover {
                background-color: #e3f2fd;
                color: #000000;
            }

            QComboBox QAbstractItemView::item:selected {
                background-color: #bbdefb;
                color: #000000;
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

            QCheckBox {
                spacing: 8px;
                color: #000000;
            }

            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                background-color: #ffffff;
                border: 1px solid #757575;
                border-radius: 3px;
                image: url(UNCHECKED_ICON);
            }

            QCheckBox::indicator:hover {
                border-color: #1976d2;
            }

            QCheckBox::indicator:checked {
                background-color: #ffffff;
                border-color: #1976d2;
                image: url(CHECKED_ICON);
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
        """.replace("CHECKED_ICON", checked_icon).replace("UNCHECKED_ICON", unchecked_icon))

    def _load_task(self, task: SyncTask):
        """加载任务"""
        self.name_edit.setText(task.name)
        self.local_path_edit.setText(task.local_path)
        self.remote_prefix_edit.setText(task.remote_prefix)

        # 同步模式
        mode_index = {
            SyncMode.BIDIRECTIONAL: 0,
            SyncMode.UPLOAD: 1,
            SyncMode.DOWNLOAD: 2
        }
        self.sync_mode_combo.setCurrentIndex(mode_index.get(task.sync_mode, 0))

        self.interval_spin.setValue(task.interval)

        # 冲突策略
        conflict_index = {
            ConflictStrategy.RENAME: 0,
            ConflictStrategy.OVERWRITE: 1,
            ConflictStrategy.SKIP: 2,
            ConflictStrategy.MANUAL: 3
        }
        self.conflict_combo.setCurrentIndex(conflict_index.get(task.conflict_strategy, 0))

        self.enabled_check.setChecked(task.enabled)

        # 排除规则（每行一条）
        self.exclude_edit.setPlainText("\n".join(task.exclude_patterns or []))

    def _on_browse_local(self):
        """浏览本地路径"""
        path = QFileDialog.getExistingDirectory(
            self,
            "选择本地目录",
            self.local_path_edit.text() or ""
        )
        if path:
            self.local_path_edit.setText(path)

    def _on_ok(self):
        """确定"""
        # 验证
        if not self.name_edit.text().strip():
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "警告", "请输入任务名称")
            return

        if not self.local_path_edit.text().strip():
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "警告", "请选择本地路径")
            return

        self.accept()

    def get_task(self) -> SyncTask:
        """获取任务"""
        # 同步模式
        modes = [SyncMode.BIDIRECTIONAL, SyncMode.UPLOAD, SyncMode.DOWNLOAD]
        sync_mode = modes[self.sync_mode_combo.currentIndex()]

        # 冲突策略
        strategies = [
            ConflictStrategy.RENAME,
            ConflictStrategy.OVERWRITE,
            ConflictStrategy.SKIP,
            ConflictStrategy.MANUAL
        ]
        conflict_strategy = strategies[self.conflict_combo.currentIndex()]

        # 创建或更新任务
        if self._task:
            task = self._task
        else:
            task = SyncTask()

        task.name = self.name_edit.text().strip()
        task.local_path = self.local_path_edit.text().strip()
        task.remote_prefix = self.remote_prefix_edit.text().strip()
        task.sync_mode = sync_mode
        task.interval = self.interval_spin.value()
        task.conflict_strategy = conflict_strategy
        task.enabled = self.enabled_check.isChecked()

        # 排除规则：按行拆分，去掉空行（保留 # 注释行以便用户回看）
        raw_lines = self.exclude_edit.toPlainText().splitlines()
        task.exclude_patterns = [ln.strip() for ln in raw_lines if ln.strip()]

        return task
