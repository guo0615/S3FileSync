"""
任务列表组件
"""
from typing import List, Optional
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLabel, QMenu
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor

from ...models.sync_task import SyncTask


class TaskListWidget(QWidget):
    """任务列表组件"""

    # 信号
    task_selected = pyqtSignal(object)  # 任务选中
    task_activated = pyqtSignal(object)  # 任务激活(双击)
    task_delete_requested = pyqtSignal(str)  # 任务删除请求 (task_id)
    task_toggle_requested = pyqtSignal(object, bool)  # 任务启用/禁用请求 (task, enabled)

    def __init__(self, parent=None):
        """初始化"""
        super().__init__(parent)

        self._tasks: List[SyncTask] = []
        self._selected_task: Optional[SyncTask] = None

        self._init_ui()

    def _init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 标题
        title_label = QLabel("同步任务")
        title_label.setStyleSheet("""
            QLabel {
                font-size: 14px;
                font-weight: bold;
                color: #1976d2;
                padding: 4px;
            }
        """)
        layout.addWidget(title_label)

        # 任务树
        self.task_tree = QTreeWidget()
        self.task_tree.setHeaderLabels(["任务名称", "状态", "下次同步"])
        self.task_tree.setRootIsDecorated(False)
        self.task_tree.setAlternatingRowColors(True)
        self.task_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.task_tree.itemClicked.connect(self._on_item_clicked)
        self.task_tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.task_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.task_tree.customContextMenuRequested.connect(self._show_context_menu)

        # 设置列宽
        self.task_tree.setColumnWidth(0, 150)
        self.task_tree.setColumnWidth(1, 80)
        self.task_tree.setColumnWidth(2, 120)

        layout.addWidget(self.task_tree)

        # 按钮栏
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)

        self.add_button = QPushButton("添加任务")
        self.add_button.clicked.connect(self._on_add_task)
        button_layout.addWidget(self.add_button)

        self.delete_button = QPushButton("删除任务")
        self.delete_button.clicked.connect(self._on_delete_task)
        self.delete_button.setEnabled(False)
        button_layout.addWidget(self.delete_button)

        layout.addLayout(button_layout)

        # 应用样式
        self.setStyleSheet("""
            QTreeWidget {
                background-color: #ffffff;
                alternate-background-color: #f5f5f5;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
                color: #000000;
            }

            QTreeWidget::item {
                padding: 4px;
                border-bottom: 1px solid #f0f0f0;
                color: #000000;
            }

            QTreeWidget::item:selected {
                background-color: #e3f2fd;
                color: #000000;
            }

            QTreeWidget::item:hover {
                background-color: #f5f5f5;
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

            QPushButton:disabled {
                background-color: #f5f5f5;
                color: #9e9e9e;
            }

            QLabel {
                color: #1976d2;
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

    def update_tasks(self, tasks: List[SyncTask], running_ids: Optional[set] = None):
        """更新任务列表

        Args:
            tasks: 任务列表
            running_ids: 正在运行的任务ID集合（用于显示"同步中"状态）
        """
        self._tasks = tasks
        self._running_ids = running_ids or set()
        self._refresh_list()

    def _refresh_list(self):
        """刷新列表"""
        self.task_tree.clear()

        for task in self._tasks:
            item = QTreeWidgetItem()

            # 任务名称
            item.setText(0, task.name)
            item.setData(0, Qt.ItemDataRole.UserRole, task.id)

            # 状态（运行中优先显示，其次启用/禁用）
            if getattr(self, "_running_ids", None) and task.id in self._running_ids:
                status = "同步中"
                item.setForeground(1, QBrush(QColor("#1976d2")))
            elif task.enabled:
                status = "启用"
            else:
                status = "禁用"
            item.setText(1, status)

            # 下次同步时间
            if task.next_sync:
                item.setText(2, task.next_sync.strftime("%Y-%m-%d %H:%M"))
            else:
                item.setText(2, "-")

            self.task_tree.addTopLevelItem(item)

    def get_selected_task(self) -> Optional[SyncTask]:
        """获取选中的任务"""
        return self._selected_task

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int):
        """项目点击"""
        task_id = item.data(0, Qt.ItemDataRole.UserRole)
        task = next((t for t in self._tasks if t.id == task_id), None)

        if task:
            self._selected_task = task
            self.delete_button.setEnabled(True)
            self.task_selected.emit(task)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        """项目双击"""
        task_id = item.data(0, Qt.ItemDataRole.UserRole)
        task = next((t for t in self._tasks if t.id == task_id), None)

        if task:
            self.task_activated.emit(task)

    def _show_context_menu(self, pos):
        """显示上下文菜单"""
        item = self.task_tree.itemAt(pos)
        if not item:
            return

        task_id = item.data(0, Qt.ItemDataRole.UserRole)
        task = next((t for t in self._tasks if t.id == task_id), None)
        if not task:
            return

        menu = QMenu(self)

        # 编辑
        edit_action = QAction("编辑", self)
        edit_action.triggered.connect(lambda: self._on_edit_task(task))
        menu.addAction(edit_action)

        # 启用/禁用
        if task.enabled:
            disable_action = QAction("禁用", self)
            disable_action.triggered.connect(lambda: self._on_toggle_task(task, False))
            menu.addAction(disable_action)
        else:
            enable_action = QAction("启用", self)
            enable_action.triggered.connect(lambda: self._on_toggle_task(task, True))
            menu.addAction(enable_action)

        menu.addSeparator()

        # 删除
        delete_action = QAction("删除", self)
        delete_action.triggered.connect(lambda: self._on_delete_task_by_id(task.id))
        menu.addAction(delete_action)

        menu.exec(self.task_tree.mapToGlobal(pos))

    def _on_add_task(self):
        """添加任务"""
        # 发送空任务激活信号，由主窗口处理
        self.task_activated.emit(None)

    def _on_delete_task(self):
        """删除任务"""
        if self._selected_task:
            self._on_delete_task_by_id(self._selected_task.id)

    def _on_delete_task_by_id(self, task_id: str):
        """根据ID删除任务，发出删除请求信号由主窗口处理"""
        if task_id:
            self.task_delete_requested.emit(task_id)

    def _on_edit_task(self, task: SyncTask):
        """编辑任务"""
        self.task_activated.emit(task)

    def _on_toggle_task(self, task: SyncTask, enabled: bool):
        """切换任务状态（禁用/启用）

        仅更新本地显示，并发出 task_toggle_requested 信号
        由主窗口真正暂停/恢复调度器并保存配置。
        """
        task.enabled = enabled
        self._refresh_list()
        self.task_toggle_requested.emit(task, enabled)
