"""
主窗口
"""
import sys
from typing import Optional
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QMenuBar, QMenu, QToolBar, QStatusBar,
    QMessageBox, QFileDialog, QSplashScreen, QLabel
)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QKeySequence, QPixmap, QFont
import logging

from ..core.s3_client import S3Client
from ..core.sync_engine import SyncEngine
from ..core.scheduler import SyncScheduler
from ..utils.config_manager import ConfigManager
from .widgets.task_list import TaskListWidget
from .widgets.file_browser import FileBrowserWidget
from .widgets.sync_panel import SyncPanelWidget
from .dialogs.config_dialog import ConfigDialog
from .dialogs.task_dialog import TaskDialog


class InitWorker(QThread):
    """后台初始化工作线程"""
    finished = pyqtSignal(bool, str)  # 完成信号 (成功, 消息)
    progress = pyqtSignal(str)  # 进度信号

    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.config_manager = config_manager
        self.s3_client = None
        self.sync_engine = None
        self.scheduler = None

    def run(self):
        """执行初始化"""
        try:
            # 步骤1: 创建S3客户端
            self.progress.emit("正在连接S3服务...")
            s3_config = self.config_manager.get_s3_config()
            self.s3_client = S3Client(s3_config)

            # 步骤2: 测试连接（异步，不阻塞）
            self.progress.emit("正在测试连接...")
            # 不立即测试连接，延迟到实际使用时

            # 步骤3: 创建同步引擎
            self.progress.emit("正在初始化同步引擎...")
            sync_config = self.config_manager.get_sync_config()
            self.sync_engine = SyncEngine(self.s3_client, sync_config)

            # 步骤4: 创建调度器
            self.progress.emit("正在初始化调度器...")
            self.scheduler = SyncScheduler(self.sync_engine)

            # 步骤5: 加载任务
            self.progress.emit("正在加载任务...")
            tasks_data = self.config_manager.get_tasks()
            for task_data in tasks_data:
                from ..models.sync_task import SyncTask
                task = SyncTask.from_dict(task_data)
                self.scheduler.add_task(task)

            self.finished.emit(True, "初始化完成")

        except Exception as e:
            self.finished.emit(False, str(e))


class MainWindow(QMainWindow):
    """主窗口"""

    # 跨线程任务完成信号：调度器在后台线程执行回调时，通过此信号负载到 GUI 线程，
    # 避免在非 GUI 线程操作 statusBar(启动 QTimer)/刷新控件(QTreeWidget) 而崩溃。
    # 参数: (task_id, uploaded, downloaded, errors, error, failed_files_json)
    task_executed = pyqtSignal(str, int, int, int, str, str)

    # 定时任务进度信号（调度器后台线程触发 -> GUI 线程更新同步面板）
    # 参数: (task_id, current, total, message, transferred_bytes, total_bytes, speed)
    task_progress = pyqtSignal(str, int, int, str, float, float, float)

    # 实时同步失败信号（由 file_watcher 后台线程触发，负载到 GUI 线程弹窗）
    # 参数: failures_json (List[{"action","path","reason"}] 序列化为 JSON 字符串)
    realtime_sync_failed = pyqtSignal(str)

    def __init__(self, config_manager: Optional[ConfigManager] = None):
        """
        初始化主窗口

        Args:
            config_manager: 配置管理器
        """
        super().__init__()

        # 配置管理器
        self.config_manager = config_manager or ConfigManager()

        # 日志记录器
        self.logger = logging.getLogger(__name__)

        # 核心组件（延迟初始化）
        self.s3_client: Optional[S3Client] = None
        self.sync_engine: Optional[SyncEngine] = None
        self.scheduler: Optional[SyncScheduler] = None
        self._init_worker: Optional[InitWorker] = None

        # 当前手动同步的任务ID（用于结束后释放调度器执行权）
        self._manual_sync_task_id: Optional[str] = None

        # 当前正在同步的任务ID（手动或定时，用于暂停/恢复/取消按钮定位）
        self._active_task_id: Optional[str] = None

        # 初始化UI（快速显示）
        self._init_ui()

        # 跨线程任务完成信号 -> GUI 线程槽
        self.task_executed.connect(self._handle_task_executed)

        # 定时任务进度信号 -> GUI 线程槽
        self.task_progress.connect(self._handle_task_progress)

        # 实时同步失败信号 -> GUI 线程槽
        self.realtime_sync_failed.connect(self._handle_realtime_sync_failed)

        # 异步初始化核心组件
        self._init_core_async()

    def _init_ui(self):
        """初始化UI"""
        # 窗口设置
        self.setWindowTitle("S3文件同步工具")
        # 允许窗口自由缩放（缩小/放大），最小尺寸设得合理一些，
        # 避免在小屏幕或布局内容撑大的情况下无法调整窗口大小。
        self.setMinimumSize(900, 600)

        # 创建菜单栏
        self._create_menu_bar()

        # 创建工具栏
        self._create_tool_bar()

        # 创建中心部件
        self._create_central_widget()

        # 创建状态栏
        self._create_status_bar()

        # 应用样式
        self._apply_style()

        # 显示加载提示
        self.statusBar().showMessage("正在初始化...")

    def _create_menu_bar(self):
        """创建菜单栏"""
        menu_bar = self.menuBar()

        # 文件菜单
        file_menu = menu_bar.addMenu("文件(&F)")

        new_task_action = QAction("新建任务(&N)", self)
        new_task_action.setShortcut(QKeySequence.StandardKey.New)
        new_task_action.triggered.connect(self._on_new_task)
        file_menu.addAction(new_task_action)

        open_config_action = QAction("打开配置(&O)", self)
        open_config_action.triggered.connect(self._on_open_config)
        file_menu.addAction(open_config_action)

        save_config_action = QAction("保存配置(&S)", self)
        save_config_action.setShortcut(QKeySequence.StandardKey.Save)
        save_config_action.triggered.connect(self._on_save_config)
        file_menu.addAction(save_config_action)

        file_menu.addSeparator()

        exit_action = QAction("退出(&X)", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # 编辑菜单
        edit_menu = menu_bar.addMenu("编辑(&E)")

        settings_action = QAction("设置(&S)", self)
        settings_action.triggered.connect(self._on_settings)
        edit_menu.addAction(settings_action)

        # 视图菜单
        view_menu = menu_bar.addMenu("视图(&V)")

        refresh_action = QAction("刷新(&R)", self)
        refresh_action.setShortcut(QKeySequence.StandardKey.Refresh)
        refresh_action.triggered.connect(self._on_refresh)
        view_menu.addAction(refresh_action)

        # 帮助菜单
        help_menu = menu_bar.addMenu("帮助(&H)")

        about_action = QAction("关于(&A)", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _create_tool_bar(self):
        """创建工具栏"""
        toolbar = self.addToolBar("主工具栏")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(24, 24))

        # 新建任务
        new_task_action = QAction("新建任务", self)
        new_task_action.setToolTip("创建新的同步任务")
        new_task_action.triggered.connect(self._on_new_task)
        toolbar.addAction(new_task_action)

        toolbar.addSeparator()

        # 开始同步
        start_sync_action = QAction("开始同步", self)
        start_sync_action.setToolTip("开始同步选中的任务")
        start_sync_action.triggered.connect(self._on_start_sync)
        toolbar.addAction(start_sync_action)

        # 暂停同步
        pause_sync_action = QAction("暂停", self)
        pause_sync_action.setToolTip("暂停当前同步")
        pause_sync_action.triggered.connect(self._on_pause_sync)
        toolbar.addAction(pause_sync_action)

        # 恢复同步
        resume_sync_action = QAction("恢复", self)
        resume_sync_action.setToolTip("恢复被暂停的同步")
        resume_sync_action.triggered.connect(self._on_resume_sync)
        toolbar.addAction(resume_sync_action)

        # 取消同步
        cancel_sync_action = QAction("取消", self)
        cancel_sync_action.setToolTip("取消当前同步")
        cancel_sync_action.triggered.connect(self._on_cancel_sync)
        toolbar.addAction(cancel_sync_action)

        toolbar.addSeparator()

        # 设置
        settings_action = QAction("设置", self)
        settings_action.setToolTip("打开设置对话框")
        settings_action.triggered.connect(self._on_settings)
        toolbar.addAction(settings_action)

        # 日志
        log_action = QAction("日志", self)
        log_action.setToolTip("查看同步日志")
        log_action.triggered.connect(self._on_view_log)
        toolbar.addAction(log_action)

    def _create_central_widget(self):
        """创建中心部件"""
        # 主分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)
        # 允许子部件折叠到很小，保证窗口可自由缩放
        splitter.setChildrenCollapsible(True)

        # 左侧：任务列表面板
        self.task_list_widget = TaskListWidget()
        self.task_list_widget.task_selected.connect(self._on_task_selected)
        self.task_list_widget.task_activated.connect(self._on_task_activated)
        self.task_list_widget.task_delete_requested.connect(self._on_task_delete)
        self.task_list_widget.task_toggle_requested.connect(self._on_task_toggle)
        splitter.addWidget(self.task_list_widget)

        # 右侧：文件浏览器和同步状态
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # 文件浏览器
        self.file_browser_widget = FileBrowserWidget()
        self.file_browser_widget.status_message.connect(self._on_browser_status)
        right_layout.addWidget(self.file_browser_widget, stretch=3)

        # 同步状态面板
        self.sync_panel_widget = SyncPanelWidget()
        # 面板上的 暂停/恢复/取消 按钮 -> 主窗口处理
        self.sync_panel_widget.pause_requested.connect(self._on_pause_sync)
        self.sync_panel_widget.resume_requested.connect(self._on_resume_sync)
        self.sync_panel_widget.cancel_requested.connect(self._on_cancel_sync)
        right_layout.addWidget(self.sync_panel_widget, stretch=1)

        splitter.addWidget(right_widget)

        # 设置分割比例（仅作为初始比例，不限制缩放）
        splitter.setSizes([300, 900])

        self.setCentralWidget(splitter)

    def _create_status_bar(self):
        """创建状态栏"""
        status_bar = self.statusBar()
        status_bar.showMessage("就绪")

    def _apply_style(self):
        """应用样式"""
        # 现代化样式
        style = """
        QMainWindow {
            background-color: #f5f5f5;
        }

        /* 菜单栏样式 - 第一排 */
        QMenuBar {
            background-color: #ffffff;
            border-bottom: 1px solid #e0e0e0;
            padding: 6px;
            color: #000000;
            font-size: 13px;
        }

        QMenuBar::item {
            padding: 6px 12px;
            background-color: transparent;
            border-radius: 4px;
            color: #000000;
        }

        QMenuBar::item:selected {
            background-color: #e3f2fd;
            color: #1976d2;
        }

        QMenuBar::item:pressed {
            background-color: #bbdefb;
        }

        /* 工具栏样式 - 第二排 */
        QToolBar {
            background-color: #ffffff;
            border-bottom: 1px solid #e0e0e0;
            padding: 8px;
            spacing: 8px;
        }

        QToolBar QToolButton {
            background-color: #1976d2;
            border: none;
            border-radius: 4px;
            padding: 8px 16px;
            min-width: 80px;
            color: #ffffff;
            font-size: 13px;
            font-weight: bold;
        }

        QToolBar QToolButton:hover {
            background-color: #1565c0;
        }

        QToolBar QToolButton:pressed {
            background-color: #0d47a1;
        }

        QToolBar QSeparator {
            background-color: #e0e0e0;
            width: 1px;
            height: 24px;
            margin: 0 4px;
        }

        /* 状态栏样式 */
        QStatusBar {
            background-color: #ffffff;
            border-top: 1px solid #e0e0e0;
            padding: 4px;
            color: #424242;
        }

        /* 分割器样式 */
        QSplitter::handle {
            background-color: #e0e0e0;
        }

        QSplitter::handle:horizontal {
            width: 2px;
        }

        QSplitter::handle:vertical {
            height: 2px;
        }

        /* 对话框样式 */
        QDialog {
            color: #000000;
        }

        QDialog QLabel {
            color: #000000;
        }

        QDialog QLineEdit {
            color: #000000;
        }

        QDialog QComboBox {
            color: #000000;
        }

        QDialog QSpinBox {
            color: #000000;
        }

        QDialog QCheckBox {
            color: #000000;
        }

        QDialog QGroupBox {
            color: #000000;
        }

        QDialog QTabWidget::pane {
            color: #000000;
        }

        QDialog QTabBar::tab {
            color: #000000;
        }

        /* 消息框样式 */
        QMessageBox {
            background-color: #ffffff;
            color: #000000;
        }

        QMessageBox QLabel {
            background-color: #ffffff;
            color: #000000;
        }

        QMessageBox QPushButton {
            background-color: #ffffff;
            border: 1px solid #e0e0e0;
            border-radius: 4px;
            padding: 6px 12px;
            color: #000000;
        }

        QMessageBox QPushButton:hover {
            background-color: #e3f2fd;
            border-color: #1976d2;
        }

        /* 白色滚动条样式 */
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
        """

        self.setStyleSheet(style)

    def _init_core_async(self):
        """异步初始化核心组件"""
        self._init_worker = InitWorker(self.config_manager)
        self._init_worker.progress.connect(self._on_init_progress)
        self._init_worker.finished.connect(self._on_init_finished)
        self._init_worker.start()

    def _on_init_progress(self, message: str):
        """初始化进度回调"""
        self.statusBar().showMessage(message)

    def _on_init_finished(self, success: bool, message: str):
        """初始化完成回调"""
        if success and self._init_worker:
            # 获取初始化的组件
            self.s3_client = self._init_worker.s3_client
            self.sync_engine = self._init_worker.sync_engine
            self.scheduler = self._init_worker.scheduler

            # 注入S3客户端到文件浏览器（用于显示远程文件）
            self.file_browser_widget.set_s3_client(self.s3_client)
            # 注入同步引擎与调度器（用于文件浏览器右键操作）
            self.file_browser_widget.set_sync_engine(self.sync_engine)
            self.file_browser_widget.set_scheduler(self.scheduler)

            # 设置任务执行回调
            if self.scheduler:
                for task in self.scheduler.get_all_tasks():
                    # 重新添加回调（完成 + 进度）
                    self.scheduler._task_callbacks[task.id] = self._on_task_executed
                    self.scheduler._task_progress_callbacks[task.id] = self._on_task_progress

                # 订阅实时同步失败事件（后台线程触发 -> 信号 -> GUI 线程弹窗）
                if self.scheduler.file_watcher:
                    self.scheduler.file_watcher.set_failure_callback(
                        self._on_realtime_sync_failed
                    )

                # 启用本地文件实时监听（若配置开启）
                sync_config = self.config_manager.get_sync_config()
                if sync_config.realtime_watch:
                    self.scheduler.set_realtime_watch(True)
                    self.logger.info("已根据配置启用本地文件实时监听")

            # 加载任务列表
            self._refresh_task_list()

            self.statusBar().showMessage("就绪", 3000)
            self.logger.info("核心组件初始化完成")
        else:
            self.logger.error(f"核心组件初始化失败: {message}")
            self.statusBar().showMessage(f"初始化失败: {message}")
            QMessageBox.warning(
                self,
                "初始化失败",
                f"核心组件初始化失败: {message}\n部分功能可能不可用，请检查配置。"
            )

        # 清理工作线程
        if self._init_worker:
            self._init_worker = None

    def _on_realtime_sync_failed(self, failures):
        """实时同步失败回调（由 file_watcher 在后台线程调用）

        将失败列表序列化为 JSON，通过信号负载到 GUI 线程弹窗。
        """
        import json
        try:
            payload = json.dumps(failures, ensure_ascii=False)
        except (TypeError, ValueError):
            payload = "[]"
        self.realtime_sync_failed.emit(payload)

    def _handle_realtime_sync_failed(self, failures_json: str):
        """实时同步失败的 GUI 线程处理：仅记录日志（不再弹窗）"""
        import json
        failures = []
        if failures_json:
            try:
                failures = json.loads(failures_json) or []
            except (ValueError, TypeError):
                failures = []

        if not failures:
            return

        # 需求：同步报错不再弹窗打扰，明细写入日志即可
        self._show_failed_files_dialog(
            title="实时同步失败",
            intro=f"共 {len(failures)} 个文件实时同步失败",
            failed_files=failures,
        )

    def _init_core(self):
        """同步初始化核心组件（用于重新初始化）"""
        try:
            # 先关闭旧的组件
            self._shutdown_core()

            # 创建S3客户端
            s3_config = self.config_manager.get_s3_config()
            self.s3_client = S3Client(s3_config)

            # 创建同步引擎
            sync_config = self.config_manager.get_sync_config()
            self.sync_engine = SyncEngine(self.s3_client, sync_config)

            # 创建调度器
            self.scheduler = SyncScheduler(self.sync_engine)

            # 注入S3客户端到文件浏览器（用于显示远程文件）
            self.file_browser_widget.set_s3_client(self.s3_client)
            # 注入同步引擎与调度器（用于文件浏览器右键操作）
            self.file_browser_widget.set_sync_engine(self.sync_engine)
            self.file_browser_widget.set_scheduler(self.scheduler)

            # 加载任务
            tasks_data = self.config_manager.get_tasks()
            for task_data in tasks_data:
                from ..models.sync_task import SyncTask
                task = SyncTask.from_dict(task_data)
                self.scheduler.add_task(
                    task,
                    self._on_task_executed,
                    self._on_task_progress,
                )

            # 订阅实时同步失败事件（后台线程触发 -> 信号 -> GUI 线程弹窗）
            if self.scheduler and self.scheduler.file_watcher:
                self.scheduler.file_watcher.set_failure_callback(
                    self._on_realtime_sync_failed
                )

            # 启用本地文件实时监听（若配置开启）
            sync_config = self.config_manager.get_sync_config()
            if sync_config.realtime_watch:
                self.scheduler.set_realtime_watch(True)
                self.logger.info("已根据配置启用本地文件实时监听")

            self.logger.info("核心组件初始化完成")

        except Exception as e:
            self.logger.error(f"核心组件初始化失败: {e}")
            QMessageBox.critical(
                self,
                "初始化失败",
                f"核心组件初始化失败: {e}\n请检查配置后重启应用。"
            )

    def _shutdown_core(self):
        """关闭核心组件"""
        try:
            # 关闭调度器
            if self.scheduler:
                # 先取消所有正在运行/暂停中的任务同步：
                # 定时同步被暂停时，同步线程会阻塞在暂停等待中（既未恢复也未取消），
                # 若直接 shutdown(wait=True) 会永久等待该线程，导致关闭窗口时程序卡死崩溃。
                # 这里先设置 cancel 标记并清掉 pause 事件，让同步线程退出阻塞、
                # 抛 SyncCancelled 正常收尾。
                for task in self.scheduler.get_all_tasks():
                    try:
                        if self.scheduler.is_task_running(task.id):
                            self.scheduler.cancel_task_sync(task.id)
                    except Exception as e:
                        self.logger.warning(f"取消任务同步失败: {task.name}: {e}")

                # 短暂等待同步线程完成清理，避免 shutdown(wait=True) 被暂停中的线程阻塞
                import time as _t
                _t.sleep(1.5)

                self.scheduler.shutdown()
                self.scheduler = None

            # 关闭S3客户端
            if self.s3_client:
                self.s3_client.close()
                self.s3_client = None

            # 清空同步引擎
            self.sync_engine = None

            self.logger.info("核心组件已关闭")

        except Exception as e:
            self.logger.error(f"关闭核心组件失败: {e}")

    def _refresh_task_list(self):
        """刷新任务列表"""
        if self.scheduler:
            tasks = self.scheduler.get_all_tasks()
            # 收集运行中的任务ID，供列表显示"同步中"状态
            running_ids = set()
            for task in tasks:
                if self.scheduler.is_task_running(task.id):
                    running_ids.add(task.id)
            self.task_list_widget.update_tasks(tasks, running_ids=running_ids)

    # 菜单和工具栏事件处理
    def _on_new_task(self):
        """新建任务"""
        dialog = TaskDialog(self)
        if dialog.exec():
            task = dialog.get_task()
            if self.scheduler:
                self.scheduler.add_task(
                    task,
                    self._on_task_executed,
                    self._on_task_progress,
                )
                self._save_tasks()
                self._refresh_task_list()
                self.logger.info(f"新建任务成功: {task.name} ({task.id})")
            else:
                self.logger.warning("新建任务失败: 调度器未初始化")

    def _on_open_config(self):
        """打开配置"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "打开配置文件",
            "",
            "YAML文件 (*.yaml *.yml);;所有文件 (*)"
        )
        if file_path:
            if self.config_manager.import_config(file_path):
                QMessageBox.information(self, "成功", "配置导入成功")
                self.logger.info(f"配置导入成功: {file_path}")
                self._init_core()
                self._load_config()
            else:
                QMessageBox.warning(self, "失败", "配置导入失败")
                self.logger.error(f"配置导入失败: {file_path}")

    def _on_save_config(self):
        """保存配置"""
        if self.config_manager.save_config():
            self.statusBar().showMessage("配置已保存", 3000)
            self.logger.info("配置保存成功")
        else:
            QMessageBox.warning(self, "失败", "配置保存失败")
            self.logger.error("配置保存失败")

    def _on_settings(self):
        """打开设置"""
        dialog = ConfigDialog(self, self.config_manager)
        if dialog.exec():
            try:
                # 显示重新初始化提示
                self.statusBar().showMessage("正在重新初始化核心组件...")
                self.logger.info("设置已修改，正在重新初始化核心组件...")

                # 重新初始化核心组件
                self._init_core()

                # 刷新任务列表
                self._refresh_task_list()

                self.statusBar().showMessage("配置已更新", 3000)
                self.logger.info("配置更新成功")

            except Exception as e:
                self.logger.error(f"重新初始化失败: {e}")
                QMessageBox.warning(
                    self,
                    "初始化失败",
                    f"配置已保存，但重新初始化失败: {e}\n请重启应用以应用新配置。"
                )

    def _on_refresh(self):
        """刷新"""
        self._refresh_task_list()
        self.file_browser_widget.refresh()
        self.statusBar().showMessage("已刷新", 3000)

    def _on_about(self):
        """关于"""
        QMessageBox.about(
            self,
            "关于",
            "<h3>S3文件同步工具</h3>"
            "<p>版本: 1.0.0</p>"
            "<p>基于S3协议的文件同步工具，支持双向同步、断点续传、定时调度等功能。</p>"
            "<p>© 2026 S3 Sync Tool</p>"
        )

    def _on_start_sync(self):
        """开始同步"""
        task = self.task_list_widget.get_selected_task()
        if not task:
            QMessageBox.warning(self, "警告", "请先选择一个任务")
            return

        if not self.sync_engine:
            QMessageBox.warning(self, "警告", "同步引擎未初始化，请检查配置")
            return

        # 检查任务路径是否有效
        if not task.local_path:
            QMessageBox.warning(self, "警告", "任务本地路径未设置")
            return

        if not task.remote_prefix:
            QMessageBox.warning(self, "警告", "任务远程路径未设置")
            return

        # 检查本地路径是否存在
        import os
        if not os.path.exists(task.local_path):
            QMessageBox.warning(self, "警告", f"本地路径不存在: {task.local_path}")
            return

        # 互斥检查：若该任务正在运行（定时同步或上次手动同步尚未完成），
        # 则拒绝再次启动，避免同一任务并发执行
        if self.scheduler and self.scheduler.is_task_running(task.id):
            QMessageBox.warning(
                self,
                "任务正在运行",
                f"任务 '{task.name}' 正在同步中，请等待本次同步完成后再试。"
            )
            self.logger.warning(
                f"手动同步被拒绝，任务正在运行: {task.name} ({task.id})"
            )
            return

        # 已暂停/已禁用的任务：提示先恢复
        if self.scheduler and not task.enabled:
            QMessageBox.warning(
                self,
                "任务已暂停",
                f"任务 '{task.name}' 已暂停/禁用，请先点击「恢复」再开始同步。"
            )
            self.logger.warning(
                f"手动同步被拒绝，任务已暂停: {task.name} ({task.id})"
            )
            return

        self.logger.info(f"开始同步任务: {task.name} ({task.id})")

        try:
            # 更新UI状态
            self.sync_panel_widget.set_task_name(task.name)
            self.sync_panel_widget.update_status("正在准备同步...")

            # 原子化获取执行权（与定时同步互斥）
            if self.scheduler and not self.scheduler.try_start_task(task.id):
                QMessageBox.warning(
                    self,
                    "任务正在运行",
                    f"任务 '{task.name}' 正在同步中，请等待本次同步完成后再试。"
                )
                self.logger.warning(
                    f"手动同步被拒绝（获取执行权失败）: {task.name} ({task.id})"
                )
                return

            # 记录当前手动同步的任务ID，用于结束后释放执行权
            self._manual_sync_task_id = task.id
            self._active_task_id = task.id

            # 在后台线程执行同步
            from PyQt6.QtCore import QThread, pyqtSignal
            import time as _time

            class SyncWorker(QThread):
                """同步工作线程"""
                # (message, current, total, transferred_bytes, total_bytes, speed)
                progress = pyqtSignal(str, int, int, float, float, float)
                # (success, message, failed_files_json)
                finished = pyqtSignal(bool, str, str)

                def __init__(self, sync_engine, task, logger=None, watcher=None):
                    super().__init__()
                    self.sync_engine = sync_engine
                    self.task = task
                    self.logger = logger or logging.getLogger(__name__)
                    self.watcher = watcher  # FileWatcher，同步期间抑制实时监听

                def run(self):
                    import json
                    # 同步期间抑制本地实时监听：避免下载落地文件触发实时上传，
                    # 与定时同步互相追赶形成重复同步死循环
                    if self.watcher:
                        self.watcher.set_suppress(True)
                    try:
                        self.logger.info(f"开始同步任务: {self.task.name}")

                        # 扫描文件
                        self.progress.emit("正在扫描本地文件...", 0, 100, 0, 0, 0)
                        self.logger.debug(f"扫描本地路径: {self.task.local_path}")
                        local_files = self.sync_engine.scan_local(self.task.local_path)

                        self.progress.emit("正在扫描远程文件...", 10, 100, 0, 0, 0)
                        self.logger.debug(f"扫描远程路径: {self.task.remote_prefix}")
                        remote_files = self.sync_engine.scan_remote(self.task.remote_prefix)

                        # 检测变更（以修改时间最新者作为同步源）
                        self.progress.emit("正在检测变更...", 20, 100, 0, 0, 0)
                        plan = self.sync_engine.detect_changes(
                            local_files,
                            remote_files,
                            self.task.sync_mode
                        )

                        # 执行同步
                        total_actions = len(plan.actions)
                        self.logger.info(f"检测到 {total_actions} 个文件操作")

                        if total_actions > 0:
                            # 预先计算所有待传输文件的总字节数（仅统计会真正传输的操作）
                            total_bytes = 0.0
                            for action, lf, rf in plan.actions:
                                if action.value in ("upload", "download"):
                                    fi = lf or rf
                                    if fi is not None:
                                        total_bytes += float(getattr(fi, "size", 0) or 0)

                            # 字节级进度状态（由 byte_callback 更新，来自 execute_sync 真实统计）
                            byte_state = {
                                "transferred": 0.0,
                                "speed": 0.0,
                            }
                            last_progress = [_time.time()]

                            def progress_callback(current, total, message):
                                # 文件粒度进度（百分比）
                                progress_value = 20 + int((current / total) * 80)
                                now = _time.time()
                                # 限频，避免 UI 刷新过频繁
                                if now - last_progress[0] >= 0.2 or current >= total:
                                    last_progress[0] = now
                                    self.progress.emit(
                                        message,
                                        progress_value,
                                        100,
                                        byte_state["transferred"],
                                        total_bytes,
                                        byte_state["speed"],
                                    )
                                self.logger.debug(f"同步进度: {current}/{total} - {message}")

                            def byte_cb(transferred, total, speed):
                                # 字节级进度：更新速率与剩余时间依据
                                byte_state["transferred"] = transferred
                                byte_state["speed"] = speed
                                now = _time.time()
                                if now - last_progress[0] >= 0.2:
                                    last_progress[0] = now
                                    progress_value = 20 + int(
                                        (transferred / max(total, 1)) * 80) if total else 20
                                    self.progress.emit(
                                        "同步中",
                                        progress_value,
                                        100,
                                        transferred,
                                        total,
                                        speed,
                                    )

                            result = self.sync_engine.execute_sync(
                                plan,
                                self.task,
                                progress_callback,
                                byte_cb,
                            )

                            # 序列化失败文件列表（避免跨线程传递对象引用）
                            failed_files_json = json.dumps(
                                [f.to_dict() for f in result.failed_files],
                                ensure_ascii=False,
                            )

                            # 完成
                            message = (
                                f"上传: {result.uploaded}, 下载: {result.downloaded}, "
                                f"错误: {result.errors}"
                            )
                            if result.cancelled:
                                message = "同步已取消"
                            self.logger.info(f"同步完成: {message}")
                            self.progress.emit(
                                "同步完成" if not result.cancelled else "同步已取消",
                                100,
                                100,
                                byte_state["transferred"],
                                max(total_bytes, 1),
                                0,
                            )
                            # success 仅由 result.errors == 0 决定（部分失败/取消时为 False）
                            self.finished.emit(
                                result.success,
                                message,
                                failed_files_json,
                            )
                        else:
                            # 没有需要同步的文件
                            self.logger.info("没有需要同步的文件")
                            self.progress.emit("没有需要同步的文件", 100, 100, 0, 0, 0)
                            self.finished.emit(True, "没有需要同步的文件", "[]")

                    except Exception as e:
                        import traceback
                        error_msg = f"{str(e)}\n{traceback.format_exc()}"
                        self.logger.error(f"同步失败: {error_msg}")
                        self.progress.emit("同步失败", 100, 100, 0, 0, 0)
                        self.finished.emit(False, error_msg, "[]")
                    finally:
                        # 恢复本地实时监听（计数式，归零才恢复）
                        if self.watcher:
                            try:
                                self.watcher.set_suppress(False)
                            except Exception:
                                pass

            # 创建并启动工作线程
            self._sync_worker = SyncWorker(
                self.sync_engine, task, self.logger,
                watcher=self.scheduler.file_watcher if self.scheduler else None
            )
            self._sync_worker.progress.connect(self._on_sync_progress)
            self._sync_worker.finished.connect(self._on_sync_finished)
            self._sync_worker.start()

        except Exception as e:
            import traceback
            error_msg = f"启动同步失败: {str(e)}\n{traceback.format_exc()}"
            self.logger.error(error_msg)
            # 若已获取执行权，需释放，避免任务状态卡在 RUNNING 导致后续无法同步
            if self.scheduler and getattr(self, "_manual_sync_task_id", None):
                self.scheduler.finish_task(self._manual_sync_task_id)
                self._manual_sync_task_id = None
            self.statusBar().showMessage("启动同步失败，详见日志", 5000)

    def _on_sync_progress(self, message: str, current: int, total: int,
                          transferred_bytes: float = 0.0,
                          total_bytes: float = 0.0,
                          speed: float = 0.0):
        """同步进度回调

        Args:
            message: 状态消息
            current: 当前进度百分比
            total: 总进度百分比（通常为100）
            transferred_bytes: 已传输字节数
            total_bytes: 总传输字节数
            speed: 实时传输速度(bytes/s)
        """
        from ..models.transfer_state import TransferState, TransferStatus as TS

        # 创建传输状态（使用真实字节数与速度）
        state = TransferState(
            transfer_id="sync",
            file_path=message,
            total_size=int(total_bytes) or 1,
            transferred_size=int(transferred_bytes),
            progress=current,
            status=TS.RUNNING,
            speed=speed,
        )

        self.sync_panel_widget.update_state(state)
        self.sync_panel_widget.update_status(message)

    def _on_sync_finished(self, success: bool, message: str, failed_files_json: str = ""):
        """同步完成回调

        Args:
            success: 是否成功
            message: 摘要信息（可能为"同步已取消"）
            failed_files_json: 失败文件列表的 JSON 字符串（由 SyncWorker 序列化）
        """
        from ..models.transfer_state import TransferState, TransferStatus as TS
        import json

        is_cancelled = "已取消" in (message or "")

        # 更新状态
        status = TS.CANCELLED if is_cancelled else (TS.COMPLETED if success else TS.FAILED)
        state = TransferState(
            transfer_id="sync",
            file_path="",
            total_size=100,
            transferred_size=100,
            progress=100,
            status=status,
            error=None if success else message
        )

        self.sync_panel_widget.update_state(state)

        if success:
            self.statusBar().showMessage(f"同步完成: {message}", 5000)
            self.logger.info(f"同步完成: {message}")
        elif is_cancelled:
            self.statusBar().showMessage(f"{message}", 5000)
            self.logger.warning(f"{message}")
        else:
            self.statusBar().showMessage(f"同步失败: {message}", 5000)
            self.logger.error(f"同步失败: {message}")

        # 解析失败文件列表并展示弹窗
        failed_files = []
        if failed_files_json:
            try:
                failed_files = json.loads(failed_files_json) or []
            except (ValueError, TypeError):
                failed_files = []

        if failed_files:
            # 需求：同步报错不弹窗，仅记录日志（状态栏已在上方给出简短提示）
            self._show_failed_files_dialog(
                title="部分文件同步失败",
                intro=f"共 {len(failed_files)} 个文件同步失败，摘要: {message}",
                failed_files=failed_files,
            )
        elif not success and not is_cancelled:
            # 整体失败（异常），但无具体失败文件：仅记日志，不再弹窗
            self.logger.error(f"同步失败: {message}")

        # 释放手动同步的执行权（若本次同步由 _on_start_sync 发起）
        if self.scheduler and getattr(self, "_manual_sync_task_id", None):
            task_id = self._manual_sync_task_id
            # 仅当该任务当前确实在 RUNNING（由手动同步持有）时才释放，
            # 避免误释放随后由定时同步获取的执行权
            if self.scheduler.is_task_running(task_id):
                self.scheduler.finish_task(task_id)
                self.logger.debug(f"手动同步结束，释放任务执行权: {task_id}")
            self._manual_sync_task_id = None
            if self._active_task_id == task_id:
                self._active_task_id = None

        # 刷新任务列表
        self._refresh_task_list()

    def _get_sync_target_task(self):
        """确定当前操作针对的任务

        优先：当前正在同步的任务（_active_task_id，手动或定时）；
        其次：任务列表中选中的任务。
        """
        task_id = getattr(self, "_active_task_id", None)
        if task_id and self.scheduler:
            task = self.scheduler.get_task(task_id)
            if task:
                return task
        return self.task_list_widget.get_selected_task()

    def _on_pause_sync(self):
        """暂停同步（工具栏或面板按钮触发）

        - 暂停选中/当前任务的定时调度
        - 若正在同步，通知同步引擎暂停（当前文件传完后停下等待恢复）
        """
        task = self._get_sync_target_task()
        if not task:
            QMessageBox.warning(self, "提示", "请先选择任务")
            return
        if not self.scheduler:
            return

        self.scheduler.pause_task(task.id)
        self.statusBar().showMessage(f"已暂停: {task.name}", 3000)
        self.logger.info(f"暂停同步: {task.name}")
        # 更新同步面板：已暂停（恢复/取消可用）
        from ..models.transfer_state import TransferState, TransferStatus as TS
        self.sync_panel_widget.update_state(TransferState(
            transfer_id=task.id,
            file_path="",
            total_size=0,
            transferred_size=0,
            progress=self.sync_panel_widget.progress_bar.value(),
            status=TS.PAUSED,
        ))
        self._refresh_task_list()

    def _on_resume_sync(self):
        """恢复同步（工具栏或面板按钮触发）"""
        task = self._get_sync_target_task()
        if not task:
            QMessageBox.warning(self, "提示", "请先选择任务")
            return
        if not self.scheduler:
            return

        self.scheduler.resume_task(task.id)
        self.statusBar().showMessage(f"已恢复: {task.name}", 3000)
        self.logger.info(f"恢复同步: {task.name}")
        # 更新同步面板：同步中（暂停/取消可用）
        from ..models.transfer_state import TransferState, TransferStatus as TS
        self.sync_panel_widget.update_state(TransferState(
            transfer_id=task.id,
            file_path="",
            total_size=0,
            transferred_size=0,
            progress=self.sync_panel_widget.progress_bar.value(),
            status=TS.RUNNING,
        ))
        self._refresh_task_list()

    def _on_cancel_sync(self):
        """取消同步（工具栏或面板按钮触发）"""
        task = self._get_sync_target_task()
        if not task:
            QMessageBox.warning(self, "提示", "请先选择任务")
            return

        # 确认
        reply = QMessageBox.question(
            self,
            "确认取消",
            f"确定要取消任务 '{task.name}' 的当前同步吗？\n已传输的内容会保留。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        if self.scheduler:
            self.scheduler.cancel_task_sync(task.id)
        elif self.sync_engine:
            self.sync_engine.cancel_sync(task.id)

        self.statusBar().showMessage(f"已请求取消: {task.name}", 3000)
        self.logger.info(f"请求取消同步: {task.name}")
        self.sync_panel_widget.update_status("正在取消...")
        self._refresh_task_list()

    def _on_view_log(self):
        """查看日志"""
        from .dialogs.log_viewer_dialog import LogViewerDialog
        from ..utils.logger import _resolve_log_path

        # 获取日志文件路径（解析为绝对路径，与logger.py保持一致）
        app_config = self.config_manager.get_app_config()
        log_file = _resolve_log_path(app_config.log_file)

        # 创建并显示日志查看对话框
        self._log_viewer = LogViewerDialog(self, log_file)
        self._log_viewer.show()

    def _on_task_selected(self, task):
        """任务选中"""
        # 更新文件浏览器
        self.file_browser_widget.set_task(task)
        # 刷新文件列表
        if task:
            self.file_browser_widget.refresh()

    def _on_browser_status(self, message: str, timeout: int):
        """文件浏览器状态消息"""
        self.statusBar().showMessage(message, timeout or 0)

    def _on_task_activated(self, task):
        """任务激活(双击)"""
        # 新建或编辑任务
        dialog = TaskDialog(self, task)
        if dialog.exec():
            updated_task = dialog.get_task()
            if self.scheduler:
                if task is None:
                    # 新建任务
                    self.scheduler.add_task(
                        updated_task,
                        self._on_task_executed,
                        self._on_task_progress,
                    )
                    self.logger.info(f"新建任务成功: {updated_task.name}")
                else:
                    # 更新任务
                    self.scheduler.update_task(updated_task)
                    self.logger.info(f"更新任务成功: {updated_task.name}")
                self._save_tasks()
                self._refresh_task_list()

    def _on_task_toggle(self, task, enabled: bool):
        """任务启用/禁用请求（右键菜单触发）

        真正暂停/恢复调度器定时触发与实时监听，并保存配置。
        """
        if not self.scheduler:
            QMessageBox.warning(self, "警告", "调度器未初始化，请检查配置")
            return

        if enabled:
            self.scheduler.resume_task(task.id)
            self.logger.info(f"任务已启用: {task.name}")
            self.statusBar().showMessage(f"已启用任务: {task.name}", 3000)
        else:
            self.scheduler.pause_task(task.id)
            self.logger.info(f"任务已禁用: {task.name}")
            self.statusBar().showMessage(f"已禁用任务: {task.name}", 3000)

        # 保存任务配置
        self._save_tasks()
        self._refresh_task_list()

    def _on_task_delete(self, task_id: str):
        """删除任务"""
        try:
            if not self.scheduler:
                QMessageBox.warning(self, "警告", "调度器未初始化，请检查配置")
                return

            # 获取任务信息用于确认
            task = self.scheduler.get_task(task_id)
            if not task:
                QMessageBox.warning(self, "警告", "任务不存在或已被删除")
                return

            # 确认删除
            reply = QMessageBox.question(
                self,
                "确认删除",
                f"确定要删除任务 '{task.name}' 吗？\n此操作不可恢复。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )

            if reply != QMessageBox.StandardButton.Yes:
                return

            # 从调度器移除任务
            success = self.scheduler.remove_task(task_id)
            if not success:
                QMessageBox.warning(self, "警告", "删除任务失败，任务不存在")
                return

            # 保存任务列表
            self._save_tasks()

            # 刷新任务列表
            self._refresh_task_list()

            # 清空文件浏览器
            self.file_browser_widget.set_task(None)

            self.statusBar().showMessage(f"已删除任务: {task.name}", 3000)
            self.logger.info(f"删除任务成功: {task.name} ({task_id})")

        except Exception as e:
            import traceback
            error_msg = f"删除任务失败: {str(e)}\n{traceback.format_exc()}"
            self.logger.error(error_msg)
            QMessageBox.critical(self, "错误", error_msg)

    def _on_task_progress(self, task_id: str, current: int, total: int, message: str,
                          transferred: float = 0.0, total_bytes: float = 0.0,
                          speed: float = 0.0):
        """定时任务进度回调（在调度器后台线程中调用）

        通过 task_progress 信号负载到 GUI 线程更新同步面板，避免跨线程操作 GUI。
        """
        self.task_progress.emit(
            task_id, current, total, message,
            transferred, total_bytes, speed,
        )

    def _handle_task_progress(self, task_id: str, current: int, total: int, message: str,
                              transferred: float = 0.0, total_bytes: float = 0.0,
                              speed: float = 0.0):
        """定时任务进度的 GUI 线程处理：更新同步面板显示"""
        if not self.scheduler:
            return
        task = self.scheduler.get_task(task_id)
        if not task:
            return

        # 记录当前正在同步的任务（供暂停/恢复/取消按钮定位）
        self._active_task_id = task_id

        # 更新同步面板：任务名 + 状态 + 进度（RUNNING 状态启用暂停/取消按钮）
        if getattr(self, "sync_panel_widget", None) is not None:
            from ..models.transfer_state import TransferState, TransferStatus as TS
            self.sync_panel_widget.set_task_name(task.name)

            # 计算进度百分比：
            # - current == -1：字节级进度，用 transferred/total_bytes
            # - 否则用文件级 current/total
            if current == -1 and total_bytes > 0:
                progress_value = int((transferred / total_bytes) * 100)
                state = TransferState(
                    transfer_id=task_id,
                    file_path="",
                    total_size=int(total_bytes),
                    transferred_size=int(transferred),
                    progress=progress_value,
                    status=TS.RUNNING,
                    speed=speed,
                )
            else:
                progress_value = int((current / total) * 100) if total > 0 else 0
                state = TransferState(
                    transfer_id=task_id,
                    file_path="",
                    total_size=int(total_bytes),
                    transferred_size=int(transferred),
                    progress=progress_value,
                    status=TS.RUNNING,
                    speed=speed,
                )
            self.sync_panel_widget.update_state(state)
            self.sync_panel_widget.update_status(message)

        # 刷新任务列表（显示"同步中"）
        self._refresh_task_list()

    def _on_task_executed(self, task_id, result=None, error=None):
        """任务执行完成回调（可能在调度器后台线程中调用）

        注意：此处不能直接操作 GUI（statusBar.showMessage 带超时会启动 QTimer、
        _refresh_task_list 会刷新 QTreeWidget），否则触发
        "QObject::startTimer: Timers cannot be started from another thread"。
        通过 task_executed 信号负载到 GUI 线程处理。
        """
        import json

        if result is not None:
            failed_files_json = json.dumps(
                [f.to_dict() for f in getattr(result, "failed_files", [])],
                ensure_ascii=False,
            )
            # 若被取消，通过 error 字段传达"已取消"状态
            effective_error = error or ""
            if getattr(result, "cancelled", False):
                effective_error = effective_error or "同步已取消"
            self.task_executed.emit(
                task_id,
                getattr(result, 'uploaded', 0),
                getattr(result, 'downloaded', 0),
                getattr(result, 'errors', 0),
                effective_error,
                failed_files_json,
            )
        else:
            self.task_executed.emit(task_id, 0, 0, 0, error or "", "[]")

    def _handle_task_executed(
        self,
        task_id: str,
        uploaded: int,
        downloaded: int,
        errors: int,
        error: str,
        failed_files_json: str = "[]"
    ):
        """任务执行完成的 GUI 线程处理（由 task_executed 信号触发，在 GUI 线程运行）"""
        import json
        from ..models.transfer_state import TransferState, TransferStatus as TS

        # 定时任务完成：清除当前同步任务标记
        if getattr(self, "_active_task_id", None) == task_id:
            self._active_task_id = None

        # 更新同步面板为完成/失败状态（面板可能未初始化，做防御性检查）
        if getattr(self, "sync_panel_widget", None) is not None:
            is_cancelled = "已取消" in (error or "")
            done_status = (
                TS.CANCELLED if is_cancelled
                else TS.COMPLETED if not error
                else TS.FAILED
            )
            done_state = TransferState(
                transfer_id="sync",
                file_path="",
                total_size=0,
                transferred_size=0,
                progress=100,
                status=done_status,
                error=error or None,
            )
            self.sync_panel_widget.update_state(done_state)

        is_cancelled = "已取消" in (error or "")

        if is_cancelled:
            self.statusBar().showMessage(f"{error}", 5000)
        elif error:
            self.statusBar().showMessage(f"任务执行失败: {error}", 5000)
        elif uploaded or downloaded or errors:
            self.statusBar().showMessage(
                f"同步完成: 上传{uploaded}, 下载{downloaded}, 错误{errors}",
                5000
            )
        else:
            self.statusBar().showMessage("同步完成: 无变更", 5000)

        # 解析失败文件列表
        failed_files = []
        if failed_files_json:
            try:
                failed_files = json.loads(failed_files_json) or []
            except (ValueError, TypeError):
                failed_files = []

        # 有失败文件时记录日志（需求：不再弹窗打扰）
        if failed_files and not is_cancelled:
            task_name = ""
            if self.scheduler:
                task = self.scheduler.get_task(task_id)
                if task:
                    task_name = task.name
            title = f"任务 '{task_name or task_id}' 同步失败" if task_name else "同步失败"
            self._show_failed_files_dialog(
                title=title,
                intro=f"共 {len(failed_files)} 个文件同步失败",
                failed_files=failed_files,
            )
        elif error and not is_cancelled:
            # 任务执行异常（非文件级失败）：仅记日志，不再弹窗
            task_name = ""
            if self.scheduler:
                task = self.scheduler.get_task(task_id)
                if task:
                    task_name = task.name
            title = f"任务 '{task_name or task_id}' 执行失败" if task_name else "任务执行失败"
            self.logger.error(f"{title}: {error}")

        # 刷新UI（安全：在 GUI 线程）
        self._refresh_task_list()

    def _show_failed_files_dialog(self, title: str, intro: str, failed_files):
        """记录失败文件清单及原因到日志（不再弹窗打扰用户）

        需求：同步报错不弹窗提醒，保留日志记录即可。完整明细写入日志，
        用户可在「日志」窗口查看。

        Args:
            title: 日志标题
            intro: 引言文本
            failed_files: 失败文件列表，每个元素为 dict {
                path, action, reason, local_path, remote_path
            }
        """
        if not failed_files:
            return

        # 限制展示数量，避免日志过长
        max_show = 20
        shown = failed_files[:max_show]
        more_count = len(failed_files) - len(shown)

        # 操作类型中文映射
        action_map = {
            "upload": "上传",
            "download": "下载",
            "delete_local": "删除本地",
            "delete_remote": "删除远程",
            "conflict": "冲突处理",
            "skip": "跳过",
        }

        lines = [intro, ""]
        for f in shown:
            action_cn = action_map.get(str(f.get("action", "")), str(f.get("action", "")))
            path = f.get("path", "")
            reason = f.get("reason", "未知原因")
            lines.append(f"• [{action_cn}] {path}")
            lines.append(f"    原因: {reason}")

        if more_count > 0:
            lines.append("")
            lines.append(f"... 还有 {more_count} 个失败文件未展示，请查看日志获取完整列表。")

        detail = "\n".join(lines)

        # 仅记录日志，不弹窗
        self.logger.error(f"{title}:\n{detail}")

    def _save_tasks(self):
        """保存任务列表"""
        if self.scheduler:
            tasks = self.scheduler.get_all_tasks()
            tasks_data = [task.to_dict() for task in tasks]
            self.config_manager.update_tasks(tasks_data)

    def closeEvent(self, event):
        """关闭事件"""
        self.logger.info("应用正在关闭...")

        # 保存配置
        self._save_tasks()

        # 关闭核心组件
        self._shutdown_core()

        self.logger.info("应用已关闭")
        event.accept()


def run_app():
    """运行应用"""
    app = QApplication(sys.argv)
    app.setApplicationName("S3文件同步工具")
    app.setOrganizationName("S3 Sync Tool")

    # 设置应用样式
    app.setStyle("Fusion")

    # 全局消息框样式：确保所有 QMessageBox（含后台线程触发的同步失败弹窗）
    # 均为白底黑字，避免系统深色主题下看不清内容。
    app.setStyleSheet("""
        QMessageBox {
            background-color: #ffffff;
            color: #000000;
        }
        QMessageBox QLabel {
            background-color: #ffffff;
            color: #000000;
        }
        QMessageBox QPushButton {
            background-color: #ffffff;
            border: 1px solid #e0e0e0;
            border-radius: 4px;
            padding: 6px 12px;
            color: #000000;
        }
        QMessageBox QPushButton:hover {
            background-color: #e3f2fd;
            border-color: #1976d2;
        }
    """)

    # 创建启动画面
    splash = QSplashScreen()
    splash_pixmap = QPixmap(400, 200)
    splash_pixmap.fill(Qt.GlobalColor.white)

    # 在启动画面上绘制文字
    from PyQt6.QtGui import QPainter, QColor
    painter = QPainter(splash_pixmap)
    painter.setPen(QColor(25, 118, 210))  # 蓝色
    font = QFont("Arial", 20, QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(splash_pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "S3文件同步工具\n正在启动...")
    painter.end()

    splash.setPixmap(splash_pixmap)
    splash.show()

    # 处理事件，确保启动画面显示
    app.processEvents()

    # 创建主窗口
    window = MainWindow()
    window.show()

    # 关闭启动画面
    splash.finish(window)

    # 运行应用
    sys.exit(app.exec())
