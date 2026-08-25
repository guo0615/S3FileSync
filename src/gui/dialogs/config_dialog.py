"""
配置对话框
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget, QLabel,
    QLineEdit, QPushButton, QSpinBox, QCheckBox, QComboBox,
    QDialogButtonBox, QGroupBox, QFormLayout, QMessageBox
)
from PyQt6.QtCore import Qt
from typing import Optional

from ...utils.config_manager import ConfigManager, S3Config, SyncConfig, AppConfig
from ...utils.icon_utils import get_checkbox_checked_icon, get_checkbox_unchecked_icon


class ConfigDialog(QDialog):
    """配置对话框"""

    def __init__(self, parent=None, config_manager: Optional[ConfigManager] = None):
        """初始化"""
        super().__init__(parent)

        self.config_manager = config_manager or ConfigManager()

        self.setWindowTitle("设置")
        self.setMinimumSize(600, 500)
        self.setModal(True)

        self._init_ui()
        self._load_config()

    def _init_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # 标签页
        tab_widget = QTabWidget()

        # S3配置标签页
        s3_tab = self._create_s3_tab()
        tab_widget.addTab(s3_tab, "S3连接")

        # 同步配置标签页
        sync_tab = self._create_sync_tab()
        tab_widget.addTab(sync_tab, "同步设置")

        # 应用配置标签页
        app_tab = self._create_app_tab()
        tab_widget.addTab(app_tab, "应用设置")

        layout.addWidget(tab_widget)

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

            QTabWidget::pane {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
            }

            QTabBar::tab {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-bottom: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                padding: 8px 16px;
                margin-right: 2px;
                color: #000000;
            }

            QTabBar::tab:selected {
                background-color: #e3f2fd;
                border-color: #1976d2;
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

            QLineEdit, QSpinBox {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 4px;
                padding: 6px;
                color: #000000;
            }

            QLineEdit:focus, QSpinBox:focus {
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
            }

            QCheckBox::indicator:hover {
                border-color: #1976d2;
            }

            QCheckBox::indicator:checked {
                background-color: #ffffff;
                border-color: #1976d2;
                image: url(CHECKED_ICON);
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
        """.replace("CHECKED_ICON", checked_icon).replace("UNCHECKED_ICON", unchecked_icon))

    def _create_s3_tab(self) -> QWidget:
        """创建S3配置标签页"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(16)

        # 连接配置组
        connection_group = QGroupBox("连接配置")
        form_layout = QFormLayout(connection_group)
        form_layout.setSpacing(12)

        self.endpoint_edit = QLineEdit()
        self.endpoint_edit.setPlaceholderText("http://localhost:9000")
        form_layout.addRow("端点:", self.endpoint_edit)

        self.access_key_edit = QLineEdit()
        self.access_key_edit.setPlaceholderText("Access Key")
        form_layout.addRow("访问密钥:", self.access_key_edit)

        self.secret_key_edit = QLineEdit()
        self.secret_key_edit.setPlaceholderText("Secret Key")
        self.secret_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form_layout.addRow("秘密密钥:", self.secret_key_edit)

        self.bucket_edit = QLineEdit()
        self.bucket_edit.setPlaceholderText("Bucket Name")
        form_layout.addRow("存储桶:", self.bucket_edit)

        self.region_edit = QLineEdit()
        self.region_edit.setPlaceholderText("us-east-1")
        form_layout.addRow("区域:", self.region_edit)

        layout.addWidget(connection_group)

        # SSL配置组
        ssl_group = QGroupBox("SSL配置")
        form_layout = QFormLayout(ssl_group)

        self.use_ssl_check = QCheckBox("使用SSL")
        form_layout.addRow(self.use_ssl_check)

        self.verify_ssl_check = QCheckBox("验证SSL证书")
        form_layout.addRow(self.verify_ssl_check)

        layout.addWidget(ssl_group)

        # 测试连接按钮
        test_button = QPushButton("测试连接")
        test_button.clicked.connect(self._on_test_connection)
        layout.addWidget(test_button)

        layout.addStretch()

        return widget

    def _create_sync_tab(self) -> QWidget:
        """创建同步配置标签页"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(16)

        # 传输配置组
        transfer_group = QGroupBox("传输配置")
        form_layout = QFormLayout(transfer_group)
        form_layout.setSpacing(12)

        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 10)
        self.concurrent_spin.setValue(3)
        form_layout.addRow("最大并发数:", self.concurrent_spin)

        self.chunk_size_spin = QSpinBox()
        self.chunk_size_spin.setRange(1, 100)
        self.chunk_size_spin.setValue(8)
        self.chunk_size_spin.setSuffix(" MB")
        form_layout.addRow("分片大小:", self.chunk_size_spin)

        self.retry_times_spin = QSpinBox()
        self.retry_times_spin.setRange(0, 10)
        self.retry_times_spin.setValue(3)
        form_layout.addRow("重试次数:", self.retry_times_spin)

        self.retry_delay_spin = QSpinBox()
        self.retry_delay_spin.setRange(1, 60)
        self.retry_delay_spin.setValue(5)
        self.retry_delay_spin.setSuffix(" 秒")
        form_layout.addRow("重试延迟:", self.retry_delay_spin)

        layout.addWidget(transfer_group)

        # 冲突处理组
        conflict_group = QGroupBox("冲突处理")
        form_layout = QFormLayout(conflict_group)

        self.conflict_combo = QComboBox()
        self.conflict_combo.addItems(["重命名", "覆盖", "跳过", "手动解决"])
        form_layout.addRow("冲突策略:", self.conflict_combo)

        layout.addWidget(conflict_group)

        # 实时监听组
        watch_group = QGroupBox("实时同步")
        watch_layout = QFormLayout(watch_group)
        watch_layout.setSpacing(12)

        self.realtime_watch_check = QCheckBox("监听本地文件变更，实时同步到远程")
        self.realtime_watch_check.setToolTip("本地文件新建/修改/重命名后立即上传到远程；远程仍按定时同步")
        watch_layout.addRow(self.realtime_watch_check)

        layout.addWidget(watch_group)

        layout.addStretch()

        return widget

    def _create_app_tab(self) -> QWidget:
        """创建应用配置标签页"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(16)

        # 应用设置组
        app_group = QGroupBox("应用设置")
        form_layout = QFormLayout(app_group)
        form_layout.setSpacing(12)

        self.auto_start_check = QCheckBox("开机自启动")
        form_layout.addRow(self.auto_start_check)

        self.minimize_to_tray_check = QCheckBox("最小化到托盘")
        form_layout.addRow(self.minimize_to_tray_check)

        self.log_level_combo = QComboBox()
        self.log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        form_layout.addRow("日志级别:", self.log_level_combo)

        layout.addWidget(app_group)

        layout.addStretch()

        return widget

    def _load_config(self):
        """加载配置"""
        # S3配置
        s3_config = self.config_manager.get_s3_config()
        self.endpoint_edit.setText(s3_config.endpoint)
        self.access_key_edit.setText(s3_config.access_key)
        self.secret_key_edit.setText(s3_config.secret_key)
        self.bucket_edit.setText(s3_config.bucket)
        self.region_edit.setText(s3_config.region)
        self.use_ssl_check.setChecked(s3_config.use_ssl)
        self.verify_ssl_check.setChecked(s3_config.verify_ssl)

        # 同步配置
        sync_config = self.config_manager.get_sync_config()
        self.concurrent_spin.setValue(sync_config.max_concurrent)
        self.chunk_size_spin.setValue(sync_config.chunk_size // (1024 * 1024))
        self.retry_times_spin.setValue(sync_config.retry_times)
        self.retry_delay_spin.setValue(sync_config.retry_delay)

        conflict_index = {"rename": 0, "overwrite": 1, "skip": 2, "manual": 3}
        self.conflict_combo.setCurrentIndex(
            conflict_index.get(sync_config.conflict_strategy, 0)
        )
        self.realtime_watch_check.setChecked(sync_config.realtime_watch)

        # 应用配置
        app_config = self.config_manager.get_app_config()
        self.auto_start_check.setChecked(app_config.auto_start)
        self.minimize_to_tray_check.setChecked(app_config.minimize_to_tray)

        log_level_index = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
        self.log_level_combo.setCurrentIndex(
            log_level_index.get(app_config.log_level, 1)
        )

    def _on_test_connection(self):
        """测试连接"""
        # 创建临时S3配置
        s3_config = S3Config(
            endpoint=self.endpoint_edit.text(),
            access_key=self.access_key_edit.text(),
            secret_key=self.secret_key_edit.text(),
            bucket=self.bucket_edit.text(),
            region=self.region_edit.text(),
            use_ssl=self.use_ssl_check.isChecked(),
            verify_ssl=self.verify_ssl_check.isChecked()
        )

        # 测试连接
        from ...core.s3_client import S3Client
        client = S3Client(s3_config)

        if client.test_connection():
            QMessageBox.information(self, "成功", "S3连接测试成功!")
        else:
            QMessageBox.warning(self, "失败", "S3连接测试失败，请检查配置。")

        client.close()

    def _on_ok(self):
        """确定"""
        # 保存S3配置
        s3_config = S3Config(
            endpoint=self.endpoint_edit.text(),
            access_key=self.access_key_edit.text(),
            secret_key=self.secret_key_edit.text(),
            bucket=self.bucket_edit.text(),
            region=self.region_edit.text(),
            use_ssl=self.use_ssl_check.isChecked(),
            verify_ssl=self.verify_ssl_check.isChecked()
        )
        self.config_manager.update_s3_config(s3_config)

        # 保存同步配置
        conflict_strategies = ["rename", "overwrite", "skip", "manual"]
        sync_config = SyncConfig(
            max_concurrent=self.concurrent_spin.value(),
            chunk_size=self.chunk_size_spin.value() * 1024 * 1024,
            retry_times=self.retry_times_spin.value(),
            retry_delay=self.retry_delay_spin.value(),
            conflict_strategy=conflict_strategies[self.conflict_combo.currentIndex()],
            realtime_watch=self.realtime_watch_check.isChecked()
        )
        self.config_manager.update_sync_config(sync_config)

        # 保存应用配置
        log_levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
        app_config = AppConfig(
            auto_start=self.auto_start_check.isChecked(),
            minimize_to_tray=self.minimize_to_tray_check.isChecked(),
            log_level=log_levels[self.log_level_combo.currentIndex()]
        )
        self.config_manager.update_app_config(app_config)

        self.accept()
