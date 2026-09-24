"""
文件浏览器组件
"""
from typing import Optional
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTreeWidget, QTreeWidgetItem,
    QPushButton, QLabel, QLineEdit, QMenu, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtGui import QAction, QIcon
import os
import threading

from ...models.sync_task import SyncTask
from ...models.file_info import FileInfo
from ...utils.icon_utils import get_file_icon, get_file_type_sort_key
from ...utils.file_utils import FileUtils


class FileBrowserWidget(QWidget):
    """文件浏览器组件"""

    # 信号
    file_selected = pyqtSignal(object)  # 文件选中
    status_message = pyqtSignal(str, int)  # 状态消息 (文本, 毫秒)
    op_done = pyqtSignal()  # 文件操作完成（主线程刷新）
    op_failed = pyqtSignal(str)  # 文件操作失败 (错误信息)

    def __init__(self, parent=None):
        """初始化"""
        super().__init__(parent)

        self._task: Optional[SyncTask] = None
        self._s3_client = None
        self._sync_engine = None
        self._scheduler = None
        self._local_files = []
        self._remote_files = []
        self._selected_local: Optional[QTreeWidgetItem] = None  # 本地树当前选中项
        self._selected_remote: Optional[QTreeWidgetItem] = None  # 远程树当前选中项

        # 当前浏览路径（相对路径）
        self._local_current = ""   # 相对本地 local_path，"" 表示根目录
        self._remote_current = ""  # 相对远程 remote_prefix，"" 表示前缀根目录
        self._last_error = ""

        self._init_ui()

        # 后台操作完成信号 -> 主线程槽
        self.op_done.connect(self._on_op_done)
        self.op_failed.connect(self._on_op_fail)

    def _init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 工具栏
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(8)

        # 路径输入
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("选择或输入路径...")
        self.path_edit.setReadOnly(True)
        toolbar_layout.addWidget(self.path_edit, stretch=1)

        # 上一级按钮（用于返回上级目录）
        self.up_button = QPushButton("上一级")
        self.up_button.setToolTip("返回上级目录")
        self.up_button.clicked.connect(self._on_go_up)
        self.up_button.setEnabled(False)
        toolbar_layout.addWidget(self.up_button)

        # 浏览按钮
        self.browse_button = QPushButton("浏览...")
        self.browse_button.clicked.connect(self._on_browse)
        toolbar_layout.addWidget(self.browse_button)

        # 刷新按钮
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh)
        toolbar_layout.addWidget(self.refresh_button)

        layout.addLayout(toolbar_layout)

        # 分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 本地文件树
        local_widget = QWidget()
        local_layout = QVBoxLayout(local_widget)
        local_layout.setContentsMargins(0, 0, 0, 0)

        local_label = QLabel("本地文件")
        local_label.setStyleSheet("font-weight: bold; color: #1976d2;")
        local_layout.addWidget(local_label)

        self.local_tree = QTreeWidget()
        self.local_tree.setHeaderLabels(["名称(类型)", "大小", "修改时间"])
        self.local_tree.setAlternatingRowColors(True)  # 启用交替行颜色
        self.local_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.local_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.local_tree.itemClicked.connect(self._on_local_item_clicked)
        self.local_tree.itemDoubleClicked.connect(self._on_local_item_double_clicked)
        self.local_tree.customContextMenuRequested.connect(self._on_local_context_menu)
        self.local_tree.itemSelectionChanged.connect(self._on_local_selection_changed)
        # 表头点击排序：点击标题按该列手动排序（不使用 Qt 自动排序，因为
        # 自动排序按文本比较且不保证"文件夹优先"，大小/时间列无法正确比较）
        # 默认按"名称(类型)"列排序：先按文件类型分组，组内再按名称。
        self.local_tree.header().setSectionsClickable(True)
        self.local_tree.header().setSortIndicatorShown(True)
        self.local_tree.header().setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        self.local_tree.header().sectionClicked.connect(self._on_local_header_clicked)
        local_layout.addWidget(self.local_tree)

        splitter.addWidget(local_widget)

        # 远程文件树
        remote_widget = QWidget()
        remote_layout = QVBoxLayout(remote_widget)
        remote_layout.setContentsMargins(0, 0, 0, 0)

        remote_label = QLabel("远程文件")
        remote_label.setStyleSheet("font-weight: bold; color: #1976d2;")
        remote_layout.addWidget(remote_label)

        self.remote_tree = QTreeWidget()
        self.remote_tree.setHeaderLabels(["名称(类型)", "大小", "修改时间"])
        self.remote_tree.setAlternatingRowColors(True)  # 启用交替行颜色
        self.remote_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.remote_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.remote_tree.itemClicked.connect(self._on_remote_item_clicked)
        self.remote_tree.itemDoubleClicked.connect(self._on_remote_item_double_clicked)
        self.remote_tree.customContextMenuRequested.connect(self._on_remote_context_menu)
        self.remote_tree.itemSelectionChanged.connect(self._on_remote_selection_changed)
        # 表头点击排序：点击标题按该列手动排序（不使用 Qt 自动排序，因为
        # 自动排序按文本比较且不保证"文件夹优先"，大小/时间列无法正确比较）
        # 默认按"名称(类型)"列排序：先按文件类型分组，组内再按名称。
        self.remote_tree.header().setSectionsClickable(True)
        self.remote_tree.header().setSortIndicatorShown(True)
        self.remote_tree.header().setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        self.remote_tree.header().sectionClicked.connect(self._on_remote_header_clicked)
        remote_layout.addWidget(self.remote_tree)

        splitter.addWidget(remote_widget)

        # 设置分割比例
        splitter.setSizes([400, 400])

        layout.addWidget(splitter)

        # 应用样式
        self.setStyleSheet("""
            QTreeWidget {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
                color: #000000;
                alternate-background-color: #f5f5f5;
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
            }

            QLineEdit {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
                padding: 6px;
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

    def set_s3_client(self, client):
        """设置S3客户端（由主窗口注入）"""
        self._s3_client = client
        # 若当前已有任务，重新刷新远程文件
        if self._task:
            self.refresh()

    def set_sync_engine(self, sync_engine):
        """设置同步引擎（由主窗口注入，用于删除操作）"""
        self._sync_engine = sync_engine

    def set_scheduler(self, scheduler):
        """设置调度器（由主窗口注入，用于操作期间临时抑制实时监听）"""
        self._scheduler = scheduler

    def set_task(self, task: Optional[SyncTask]):
        """设置当前任务"""
        self._task = task
        # 重置浏览位置到根目录
        self._local_current = ""
        self._remote_current = ""
        if task:
            self.path_edit.setText(task.local_path)
            self.refresh()
        else:
            self.path_edit.clear()
            self.up_button.setEnabled(False)
            self.local_tree.clear()
            self.remote_tree.clear()

    def refresh(self):
        """刷新文件列表"""
        if not self._task:
            return

        # 清空现有列表
        self.local_tree.clear()
        self.remote_tree.clear()

        # 更新路径显示
        self._update_path_display()

        # 扫描本地文件
        self._scan_local_files()

        # 扫描远程文件
        self._scan_remote_files()

    def _update_path_display(self):
        """更新路径输入框显示和上一级按钮状态"""
        if not self._task:
            self.path_edit.clear()
            self.up_button.setEnabled(False)
            return

        if self._local_current or self._remote_current:
            # 有进入子目录时，显示相对路径
            rel = self._remote_current or self._local_current
            self.path_edit.setText(rel)
        else:
            self.path_edit.setText(self._task.local_path)

        self.up_button.setEnabled(bool(self._local_current) or bool(self._remote_current))

    def _normalize_relative(self, rel: str) -> str:
        """规范化相对路径（去首尾斜杠）"""
        return rel.strip('/')

    def _join_relative(self, base: str, child: str) -> str:
        """拼接相对路径"""
        base = base.strip('/')
        child = child.strip('/')
        if not base:
            return child
        if not child:
            return base
        return f"{base}/{child}"

    def _parent_relative(self, rel: str) -> str:
        """获取相对路径的上级路径，已是根目录则返回空串"""
        rel = rel.strip('/')
        if not rel or '/' not in rel:
            return ""
        return rel.rsplit('/', 1)[0]

    def _scan_local_files(self):
        """扫描本地文件（当前浏览目录下）"""
        self.local_tree.clear()
        if not self._task or not self._task.local_path:
            return

        try:
            from datetime import datetime

            local_path = self._task.local_path
            # 当前浏览目录 = 本地根 + 相对路径
            current_dir = os.path.normpath(os.path.join(local_path, self._local_current)) if self._local_current else local_path
            if not os.path.exists(current_dir) or not os.path.isdir(current_dir):
                return

            # 扫描当前目录下的第一层
            for name in sorted(os.listdir(current_dir)):
                child_path = os.path.join(current_dir, name)
                rel_path = self._join_relative(self._local_current, name)

                item = QTreeWidgetItem()
                item.setText(0, name)

                if os.path.isdir(child_path):
                    item.setText(1, "文件夹")
                    item.setText(2, "")
                    item.setData(0, Qt.ItemDataRole.UserRole, rel_path)
                    item.setData(0, int(Qt.ItemDataRole.UserRole) + 1, True)  # 标记为目录
                    icon = get_file_icon(name, is_dir=True)
                else:
                    size = os.path.getsize(child_path)
                    # 本地 mtime 取 UTC 口径后转本机时区显示，与 MinIO 控制台一致
                    mtime = FileUtils.to_local_display(FileUtils.get_file_mtime_utc(child_path))
                    item.setText(1, FileUtils.format_size(size))
                    item.setText(2, mtime.strftime("%Y-%m-%d %H:%M"))
                    item.setData(0, Qt.ItemDataRole.UserRole, rel_path)
                    item.setData(0, int(Qt.ItemDataRole.UserRole) + 1, False)  # 标记为文件
                    # 保存排序用原始数据（统一存数值，避免 PyQt6 对 datetime 的
                    # 类型转换导致排序比较时崩溃）
                    item.setData(0, self._SORT_SIZE_ROLE, size)
                    item.setData(0, self._SORT_MTIME_ROLE, mtime.timestamp())
                    icon = get_file_icon(name, is_dir=False)

                if icon:
                    item.setIcon(0, icon)

                self.local_tree.addTopLevelItem(item)

            # 扫描完成后按当前排序规则重排（默认按"名称(类型)"列：类型分组 + 名称）
            self._apply_sort(self.local_tree)

        except Exception as e:
            print(f"扫描本地文件失败: {e}")

    def _scan_remote_files(self):
        """扫描远程文件（通过S3客户端列出远程目录）"""
        if not self._task:
            return

        try:
            if not self._s3_client:
                self.remote_tree.clear()
                item = QTreeWidgetItem()
                item.setText(0, "（未连接，请先在设置中配置S3）")
                item.setText(1, "")
                item.setText(2, "")
                self.remote_tree.addTopLevelItem(item)
                return

            remote_prefix = self._task.remote_prefix or ""
            # 确保前缀以 / 结尾，否则S3会把整个目录折叠成单个CommonPrefix，导致看不到里面的内容
            if remote_prefix and not remote_prefix.endswith('/'):
                remote_prefix = remote_prefix + '/'
            # 当前浏览目录 = 前缀根 + 相对路径
            current_prefix = self._join_relative(remote_prefix, self._remote_current)
            if current_prefix and not current_prefix.endswith('/'):
                current_prefix = current_prefix + '/'

            # 列出远程文件（单层目录视图）
            remote_files = self._s3_client.list_files(prefix=current_prefix, delimiter='/')
            self._remote_files = remote_files

            if not remote_files:
                self.remote_tree.clear()
                item = QTreeWidgetItem()
                item.setText(0, "（远程目录为空）")
                item.setText(1, "")
                item.setText(2, "")
                self.remote_tree.addTopLevelItem(item)
                return

            self.remote_tree.clear()

            for file_info in remote_files:
                # 去掉当前前缀，显示相对名称
                rel_path = file_info.path
                if current_prefix and rel_path.startswith(current_prefix):
                    rel_path = rel_path[len(current_prefix):]

                rel_path = FileUtils.normalize_path(rel_path)
                # 显示名称：取路径最后一段；空串表示前缀本身（根目录）
                display_name = os.path.basename(rel_path.rstrip('/'))
                if not display_name:
                    display_name = rel_path or remote_prefix.rstrip('/') or "/"
                display_name = display_name.strip('/') or "/"

                item = QTreeWidgetItem()
                item.setText(0, display_name)

                if file_info.is_dir:
                    item.setText(1, "文件夹")
                    item.setText(2, "")
                    icon = get_file_icon(display_name, is_dir=True)
                else:
                    item.setText(1, FileUtils.format_size(file_info.size))
                    # 远程 LastModified 为 UTC，转本机时区显示以与 MinIO 控制台一致
                    mtime = FileUtils.to_local_display(file_info.mtime)
                    item.setText(2, mtime.strftime("%Y-%m-%d %H:%M") if mtime else "")
                    # 保存排序用原始数据（统一存数值，避免 PyQt6 对 datetime 的
                    # 类型转换导致排序比较时崩溃）
                    item.setData(0, self._SORT_SIZE_ROLE, file_info.size)
                    item.setData(0, self._SORT_MTIME_ROLE, mtime.timestamp() if mtime else 0)
                    icon = get_file_icon(display_name, is_dir=False)

                item.setData(0, Qt.ItemDataRole.UserRole, file_info.path)
                # 标记目录/文件，用于双击导航判断
                item.setData(0, int(Qt.ItemDataRole.UserRole) + 1, file_info.is_dir)

                # 设置文件类型图标
                if icon:
                    item.setIcon(0, icon)

                self.remote_tree.addTopLevelItem(item)

            # 扫描完成后按当前排序规则重排（默认按"名称(类型)"列：类型分组 + 名称）
            self._apply_sort(self.remote_tree)

        except Exception as e:
            print(f"扫描远程文件失败: {e}")
            self.remote_tree.clear()
            item = QTreeWidgetItem()
            item.setText(0, f"（远程文件加载失败: {e}）")
            item.setText(1, "")
            item.setText(2, "")
            self.remote_tree.addTopLevelItem(item)

    def _on_browse(self):
        """浏览路径"""
        from PyQt6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(
            self,
            "选择目录",
            self.path_edit.text() or ""
        )
        if path:
            self.path_edit.setText(path)
            if self._task:
                self._task.local_path = path
                self.refresh()

    def _on_local_item_clicked(self, item: QTreeWidgetItem, column: int):
        """本地文件项点击"""
        self._selected_local = item

    def _on_remote_item_clicked(self, item: QTreeWidgetItem, column: int):
        """远程文件项点击"""
        self._selected_remote = item

    def _on_local_selection_changed(self):
        """本地树选择变化"""
        items = self.local_tree.selectedItems()
        self._selected_local = items[0] if items else None

    def _on_remote_selection_changed(self):
        """远程树选择变化"""
        items = self.remote_tree.selectedItems()
        self._selected_remote = items[0] if items else None

    def _is_dir_item(self, item: QTreeWidgetItem) -> bool:
        """判断条目是否为目录（通过 UserRole+1 标记）"""
        try:
            return bool(item.data(0, int(Qt.ItemDataRole.UserRole) + 1))
        except Exception:
            return False

    # ---------- 列表排序 ----------
    # 排序键存储位置（UserRole 之后的附加数据槽位，避免与现有数据冲突）
    _SORT_SIZE_ROLE = int(Qt.ItemDataRole.UserRole) + 2   # 大小(字节)，数值排序用
    _SORT_MTIME_ROLE = int(Qt.ItemDataRole.UserRole) + 3  # 修改时间(epoch秒)，数值排序用

    def _apply_sort(self, tree: QTreeWidget):
        """对树的顶层项按当前排序列排序

        - 目录始终排在文件之前（无论正序/倒序，符合文件管理器习惯）
        - 目录组、文件组内各自按排序列比较：
          名称列按"文件类型分组 + 名称"排序（默认，类型相同的聚在一起）；
          大小列用字节数；修改时间列用 epoch 秒
        - 升序/降序由表头的排序指示器方向决定
        """
        sort_col = tree.header().sortIndicatorSection()
        desc = tree.header().sortIndicatorOrder() == Qt.SortOrder.DescendingOrder

        def _field_key(item: QTreeWidgetItem):
            """按排序列提取排序键

            排序键统一存储为可比较类型（大小/时间用数值；名称用
            (类型序号, 名称小写) 元组），避免 PyQt6 对 datetime 等类型的转换
            导致比较时崩溃。未设置数据的 item.data() 在 PyQt6 中返回 None/无效值，
            这里一律用 isinstance 做类型检查，非目标类型时走回退逻辑。
            """
            if sort_col == 1:
                # 大小：优先使用原始字节数，缺失（目录/占位行）时回退到显示文本
                raw = item.data(0, self._SORT_SIZE_ROLE)
                if isinstance(raw, (int, float)):
                    return float(raw)
                text = item.text(1)
                return float("inf") if text and text != "文件夹" else float("-inf")
            elif sort_col == 2:
                # 修改时间：优先使用 epoch 秒（数值），缺失时回退到最早
                raw = item.data(0, self._SORT_MTIME_ROLE)
                if isinstance(raw, (int, float)):
                    return float(raw)
                return float("-inf")
            else:
                # 名称（列0）：先按文件类型分组，组内按名称（返回元组，同组可比）
                is_dir = self._is_dir_item(item)
                return get_file_type_sort_key(item.text(0), is_dir)

        items = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        dirs = [it for it in items if self._is_dir_item(it)]
        files = [it for it in items if not self._is_dir_item(it)]
        dirs.sort(key=_field_key, reverse=desc)
        files.sort(key=_field_key, reverse=desc)

        # 先取走所有顶层项，再按新顺序重新添加。
        # 注意：QTreeWidget 没有 takeTopLevelItems()，只能逐个 takeTopLevelItem(0)。
        while tree.topLevelItemCount() > 0:
            tree.takeTopLevelItem(0)
        for it in dirs + files:
            tree.addTopLevelItem(it)

    def _on_local_header_clicked(self, column: int):
        """本地树表头点击：切换该列的排序方向并重排

        同列再次点击切换 正序/倒序；点击新列则以正序排该列。
        """
        self._toggle_sort(self.local_tree, column)

    def _on_remote_header_clicked(self, column: int):
        """远程树表头点击：切换该列的排序方向并重排"""
        self._toggle_sort(self.remote_tree, column)

    def _toggle_sort(self, tree: QTreeWidget, column: int):
        """切换树的排序列与方向，并重排顶层项"""
        header = tree.header()
        if header.sortIndicatorSection() == column:
            # 同一列：切换正序/倒序
            order = (
                Qt.SortOrder.DescendingOrder
                if header.sortIndicatorOrder() == Qt.SortOrder.AscendingOrder
                else Qt.SortOrder.AscendingOrder
            )
        else:
            # 新列：默认正序
            order = Qt.SortOrder.AscendingOrder
        header.setSortIndicator(column, order)
        self._apply_sort(tree)

    def _on_local_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        """本地文件项双击：文件夹进入下一级"""
        if self._is_dir_item(item):
            rel_path = item.data(0, Qt.ItemDataRole.UserRole) or ""
            self._local_current = self._normalize_relative(rel_path)
            self._scan_local_files()
            self._update_path_display()

    def _on_remote_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        """远程文件项双击：文件夹进入下一级"""
        if self._is_dir_item(item):
            full_path = item.data(0, Qt.ItemDataRole.UserRole) or ""
            remote_prefix = (self._task.remote_prefix or "").rstrip('/') + '/'
            if full_path.startswith(remote_prefix):
                rel = full_path[len(remote_prefix):]
            else:
                rel = full_path
            self._remote_current = self._normalize_relative(rel)
            self._scan_remote_files()
            self._update_path_display()

    def _on_go_up(self):
        """返回上级目录"""
        if self._local_current or self._remote_current:
            if self._remote_current:
                self._remote_current = self._parent_relative(self._remote_current)
                self._scan_remote_files()
            if self._local_current:
                self._local_current = self._parent_relative(self._local_current)
                self._scan_local_files()
            self._update_path_display()

    def _on_local_context_menu(self, pos: QPoint):
        """本地文件树右键菜单：上传 / 删除"""
        item = self.local_tree.itemAt(pos)
        if not item:
            return
        self._selected_local = item

        menu = QMenu(self)
        is_dir = self._is_dir_item(item)

        upload_act = menu.addAction("上传" if not is_dir else "上传文件夹")
        delete_act = menu.addAction("删除")

        action = menu.exec(self.local_tree.viewport().mapToGlobal(pos))
        if action == upload_act:
            self._do_upload_selected()
        elif action == delete_act:
            self._do_delete_local_selected()

    def _on_remote_context_menu(self, pos: QPoint):
        """远程文件树右键菜单：下载 / 删除"""
        item = self.remote_tree.itemAt(pos)
        if not item:
            return
        self._selected_remote = item

        menu = QMenu(self)
        is_dir = self._is_dir_item(item)

        download_act = menu.addAction("下载" if not is_dir else "下载文件夹")
        delete_act = menu.addAction("删除")

        action = menu.exec(self.remote_tree.viewport().mapToGlobal(pos))
        if action == download_act:
            self._do_download_selected()
        elif action == delete_act:
            self._do_delete_remote_selected()

    def _suppress_watch(self):
        """上下文管理器：操作期间临时抑制实时监听，避免级联触发"""
        watcher = getattr(self._scheduler, "file_watcher", None) if self._scheduler else None

        class _SuppressCtx:
            def __init__(self, w):
                self._w = w

            def __enter__(self):
                if self._w:
                    self._w.set_suppress(True)
                return self

            def __exit__(self, *exc):
                if self._w:
                    self._w.set_suppress(False)
                return False

        return _SuppressCtx(watcher)

    def _remote_key_for_local_item(self, item: QTreeWidgetItem) -> str:
        """根据本地选中项计算远程 key"""
        rel_path = item.data(0, Qt.ItemDataRole.UserRole) or ""
        remote_prefix = (self._task.remote_prefix or "").rstrip('/')
        return FileUtils.normalize_path(os.path.join(remote_prefix, rel_path))

    def _remote_key_for_remote_item(self, item: QTreeWidgetItem) -> str:
        """远程选中项的完整远程 key（已含前缀）"""
        return item.data(0, Qt.ItemDataRole.UserRole) or ""

    def _local_path_for_local_item(self, item: QTreeWidgetItem) -> str:
        """本地选中项的绝对路径"""
        rel_path = item.data(0, Qt.ItemDataRole.UserRole) or ""
        return os.path.normpath(os.path.join(self._task.local_path, rel_path))

    def _local_path_for_remote_item(self, item: QTreeWidgetItem) -> str:
        """远程选中项对应的本地绝对路径"""
        full_path = item.data(0, Qt.ItemDataRole.UserRole) or ""
        remote_prefix = (self._task.remote_prefix or "").rstrip('/') + '/'
        if full_path.startswith(remote_prefix):
            rel = full_path[len(remote_prefix):]
        else:
            rel = full_path
        rel = FileUtils.normalize_path(rel)
        return os.path.normpath(os.path.join(self._task.local_path, rel))

    def _do_upload_selected(self):
        """上传本地选中项到远程"""
        item = self._selected_local
        if not item or not self._task or not self._s3_client:
            QMessageBox.warning(self, "提示", "请先选择任务并连接S3服务")
            return

        local_path = self._local_path_for_local_item(item)
        remote_key = self._remote_key_for_local_item(item)
        is_dir = self._is_dir_item(item)

        if not os.path.exists(local_path):
            QMessageBox.warning(self, "警告", f"本地路径不存在: {local_path}")
            return

        # 确认
        reply = QMessageBox.question(
            self, "确认上传",
            f"确定上传 {'文件夹' if is_dir else '文件'}:\n{local_path}\n→ {remote_key}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._run_in_thread(
            self._upload_worker, local_path, remote_key, is_dir,
            done_msg="上传完成", fail_msg="上传失败"
        )

    def _upload_worker(self, local_path, remote_key, is_dir):
        """上传工作线程任务

        返回 dict: {
            "uploaded": int,
            "failed": [{"path", "reason"}],
        }
        失败时抛出 RuntimeError 由 _run_in_thread 统一提示。
        """
        if is_dir:
            # 递归上传目录
            success = True
            failures = []
            for root, dirs, files in os.walk(local_path):
                for name in files:
                    src = os.path.join(root, name)
                    rel = os.path.relpath(src, local_path)
                    dst = FileUtils.normalize_path(os.path.join(remote_key, rel))
                    if not self._s3_client.upload_file(src, dst):
                        success = False
                        reason = ""
                        last = getattr(self._s3_client, "last_error", None)
                        if isinstance(last, dict):
                            reason = last.get("error") or ""
                        failures.append({"path": dst, "reason": reason or "上传失败"})
            if failures:
                # 抛出包含明细的异常，由 _on_op_fail 展示
                detail_lines = "\n".join(
                    f"• {f['path']}: {f['reason']}" for f in failures[:20]
                )
                more = len(failures) - 20
                more_msg = f"\n... 还有 {more} 个失败文件未展示，详见日志。" if more > 0 else ""
                raise RuntimeError(
                    f"上传文件夹失败，共 {len(failures)} 个文件失败:\n{detail_lines}{more_msg}"
                )
            return success
        else:
            if not self._s3_client.upload_file(local_path, remote_key):
                reason = ""
                last = getattr(self._s3_client, "last_error", None)
                if isinstance(last, dict):
                    reason = last.get("error") or ""
                raise RuntimeError(
                    f"上传失败: {local_path} -> {remote_key}\n原因: {reason or '未知错误'}"
                )
            return True

    def _do_download_selected(self):
        """下载远程选中项到本地"""
        item = self._selected_remote
        if not item or not self._task or not self._s3_client:
            QMessageBox.warning(self, "提示", "请先选择任务并连接S3服务")
            return

        remote_key = self._remote_key_for_remote_item(item)
        local_path = self._local_path_for_remote_item(item)
        is_dir = self._is_dir_item(item)

        # 确认
        reply = QMessageBox.question(
            self, "确认下载",
            f"确定下载 {'文件夹' if is_dir else '文件'}:\n{remote_key}\n→ {local_path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._run_in_thread(
            self._download_worker, remote_key, local_path, is_dir,
            done_msg="下载完成", fail_msg="下载失败"
        )

    def _download_worker(self, remote_key, local_path, is_dir):
        """下载工作线程任务

        失败时抛出包含明细的 RuntimeError。
        """
        if is_dir:
            # 递归下载目录：先列出前缀下所有文件
            prefix = remote_key.rstrip('/') + '/'
            remote_files = self._s3_client.list_files_recursive(prefix=prefix)
            success = True
            failures = []
            for fi in remote_files:
                if fi.is_dir:
                    continue
                # 计算本地目标路径
                rel = fi.path
                if rel.startswith(prefix):
                    rel = rel[len(prefix):]
                rel = FileUtils.normalize_path(rel)
                dst = os.path.normpath(os.path.join(local_path, rel))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if not self._s3_client.download_file(fi.path, dst):
                    success = False
                    reason = ""
                    last = getattr(self._s3_client, "last_error", None)
                    if isinstance(last, dict):
                        reason = last.get("error") or ""
                    failures.append({"path": fi.path, "reason": reason or "下载失败"})
            if failures:
                detail_lines = "\n".join(
                    f"• {f['path']}: {f['reason']}" for f in failures[:20]
                )
                more = len(failures) - 20
                more_msg = f"\n... 还有 {more} 个失败文件未展示，详见日志。" if more > 0 else ""
                raise RuntimeError(
                    f"下载文件夹失败，共 {len(failures)} 个文件失败:\n{detail_lines}{more_msg}"
                )
            return success
        else:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            if not self._s3_client.download_file(remote_key, local_path):
                reason = ""
                last = getattr(self._s3_client, "last_error", None)
                if isinstance(last, dict):
                    reason = last.get("error") or ""
                raise RuntimeError(
                    f"下载失败: {remote_key} -> {local_path}\n原因: {reason or '未知错误'}"
                )
            return True

    def _do_delete_local_selected(self):
        """删除本地选中项（同时删除远程对应项，符合本地→远程删除传播）"""
        item = self._selected_local
        if not item or not self._task:
            return

        local_path = self._local_path_for_local_item(item)
        remote_key = self._remote_key_for_local_item(item)
        is_dir = self._is_dir_item(item)

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定删除本地 {'文件夹' if is_dir else '文件'}:\n{local_path}\n\n"
            f"将同时删除远程对应项: {remote_key}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        def work():
            # 抑制实时监听，避免重复触发远程删除
            with self._suppress_watch():
                # 删除本地（delete_file 同时支持文件与目录）
                if not FileUtils.delete_file(local_path):
                    raise RuntimeError(f"删除本地失败: {local_path}")
                # 删除远程对应项
                if self._s3_client:
                    if is_dir:
                        prefix = remote_key.rstrip('/') + '/'
                        self._s3_client.delete_prefix(prefix)
                    else:
                        self._s3_client.delete_file(remote_key)
                return True

        self._run_in_thread(work, done_msg="删除完成", fail_msg="删除失败")

    def _do_delete_remote_selected(self):
        """删除远程选中项（不影响本地，符合远程→本地不同步删除）"""
        item = self._selected_remote
        if not item or not self._task or not self._s3_client:
            QMessageBox.warning(self, "提示", "请先选择任务并连接S3服务")
            return

        remote_key = self._remote_key_for_remote_item(item)
        is_dir = self._is_dir_item(item)

        reply = QMessageBox.question(
            self, "确认删除远程",
            f"确定删除远程 {'文件夹' if is_dir else '文件'}:\n{remote_key}\n\n"
            f"（不影响本地文件）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        def work():
            with self._suppress_watch():
                if is_dir:
                    prefix = remote_key.rstrip('/') + '/'
                    return self._s3_client.delete_prefix(prefix)
                else:
                    return self._s3_client.delete_file(remote_key)

        self._run_in_thread(work, done_msg="远程删除完成", fail_msg="远程删除失败")

    def _run_in_thread(self, fn, *args, done_msg="操作完成", fail_msg="操作失败"):
        """在后台线程执行文件操作，完成后通过信号在主线程刷新并提示"""
        self.status_message.emit("正在执行操作...", 0)

        def _runner():
            try:
                with self._suppress_watch():
                    fn(*args)
                self.op_done.emit()
            except Exception as e:
                self._last_error = str(e)
                self.op_failed.emit(str(e))

        t = threading.Thread(target=_runner, daemon=True)
        t.start()

    def _on_op_done(self):
        """后台操作完成（主线程槽）"""
        self.refresh()
        self.status_message.emit("操作完成", 3000)

    def _on_op_fail(self, error: str):
        """后台操作失败（主线程槽）"""
        QMessageBox.warning(self, "操作失败", error)
        self.status_message.emit("操作失败", 3000)
        self.refresh()
