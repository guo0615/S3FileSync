"""
文件同步引擎
实现文件同步逻辑，处理双向同步冲突
"""
import os
import threading
from typing import Dict, List, Optional, Tuple, Callable, Any
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
import logging

from .s3_client import S3Client
from ..models.file_info import FileInfo
from ..models.sync_task import SyncTask, SyncMode, ConflictStrategy
from ..utils.config_manager import SyncConfig
from ..utils.file_utils import FileUtils
from ..utils.hash_utils import HashUtils


class SyncCancelled(Exception):
    """同步被取消时抛出的异常"""
    pass


class FileAction(Enum):
    """文件操作类型"""
    UPLOAD = "upload"           # 上传到远程
    DOWNLOAD = "download"       # 从远程下载
    DELETE_LOCAL = "delete_local"   # 删除本地文件
    DELETE_REMOTE = "delete_remote" # 删除远程文件
    CONFLICT = "conflict"       # 冲突
    SKIP = "skip"               # 跳过


@dataclass
class FailedFile:
    """同步失败的文件信息（用于弹窗展示失败原因）"""
    path: str                       # 相对路径
    action: str                     # 失败的操作类型: upload/download/delete_local/delete_remote/conflict
    reason: str                     # 失败原因（异常信息）
    local_path: str = ""            # 本地绝对路径（可选，便于用户定位）
    remote_path: str = ""           # 远程 key（可选）

    def to_dict(self) -> Dict[str, str]:
        return {
            "path": self.path,
            "action": self.action,
            "reason": self.reason,
            "local_path": self.local_path,
            "remote_path": self.remote_path,
        }


@dataclass
class SyncPlan:
    """同步计划"""
    actions: List[Tuple[FileAction, FileInfo, Optional[FileInfo]]] = field(default_factory=list)
    # (操作, 本地文件, 远程文件)

    conflicts: List[Tuple[FileInfo, FileInfo]] = field(default_factory=list)
    # (本地文件, 远程文件)

    def add_action(
        self,
        action: FileAction,
        local_file: Optional[FileInfo] = None,
        remote_file: Optional[FileInfo] = None
    ):
        """添加操作"""
        self.actions.append((action, local_file, remote_file))

    def add_conflict(self, local_file: FileInfo, remote_file: FileInfo):
        """添加冲突"""
        self.conflicts.append((local_file, remote_file))
        self.actions.append((FileAction.CONFLICT, local_file, remote_file))


@dataclass
class SyncResult:
    """同步结果"""
    success: bool
    uploaded: int = 0
    downloaded: int = 0
    deleted_local: int = 0
    deleted_remote: int = 0
    conflicts: int = 0
    errors: int = 0
    error_messages: List[str] = field(default_factory=list)
    # 每个失败文件的详细信息（路径、操作、原因），供 UI 弹窗展示
    failed_files: List[FailedFile] = field(default_factory=list)
    # 是否被用户取消
    cancelled: bool = False
    start_time: datetime = None
    end_time: datetime = None

    def add_failure(self, path: str, action: str, reason: str,
                    local_path: str = "", remote_path: str = "") -> None:
        """记录一个失败文件"""
        self.failed_files.append(FailedFile(
            path=path,
            action=action,
            reason=reason,
            local_path=local_path,
            remote_path=remote_path,
        ))


class SyncEngine:
    """文件同步引擎"""

    def __init__(
        self,
        s3_client: S3Client,
        config: SyncConfig,
        logger: Optional[logging.Logger] = None
    ):
        """
        初始化同步引擎

        Args:
            s3_client: S3客户端
            config: 同步配置
            logger: 日志记录器
        """
        self.s3_client = s3_client
        self.config = config
        self.logger = logger or logging.getLogger(__name__)

        # 同步历史记录
        self._sync_history: Dict[str, FileInfo] = {}  # path -> FileInfo

        # 同步控制（按任务 id 管理暂停/取消状态）
        # 每个任务: {"pause": threading.Event(已设置=暂停中), "cancel": bool}
        self._controls: Dict[str, Dict[str, Any]] = {}
        self._controls_lock = threading.Lock()

    # ---- 同步控制（暂停/恢复/取消） ----

    def _get_control(self, task_id: str) -> Dict[str, Any]:
        """获取（或创建）任务的同步控制对象"""
        with self._controls_lock:
            if task_id not in self._controls:
                self._controls[task_id] = {
                    "pause": threading.Event(),  # 默认不暂停
                    "cancel": False,
                }
            return self._controls[task_id]

    def _clear_control(self, task_id: str):
        """清理任务的同步控制（同步结束后调用）"""
        with self._controls_lock:
            self._controls.pop(task_id, None)

    def pause_sync(self, task_id: str) -> bool:
        """暂停指定任务的同步（当前文件传输完或检查点时暂停）"""
        control = self._get_control(task_id)
        control["pause"].set()
        self.logger.info(f"请求暂停同步: {task_id}")
        return True

    def resume_sync(self, task_id: str) -> bool:
        """恢复指定任务的同步"""
        with self._controls_lock:
            control = self._controls.get(task_id)
        if control is None:
            return False
        control["pause"].clear()
        self.logger.info(f"请求恢复同步: {task_id}")
        return True

    def cancel_sync(self, task_id: str) -> bool:
        """取消指定任务的同步（尽快终止）"""
        control = self._get_control(task_id)
        control["cancel"] = True
        control["pause"].clear()  # 若处于暂停中，解除阻塞以便退出
        self.logger.info(f"请求取消同步: {task_id}")
        return True

    def is_sync_paused(self, task_id: str) -> bool:
        """查询任务同步是否处于暂停状态"""
        with self._controls_lock:
            control = self._controls.get(task_id)
        return bool(control and control["pause"].is_set())

    def _check_control(self, task_id: str, control: Dict[str, Any]):
        """在每次文件操作前后检查暂停/取消状态

        - 取消: 抛 SyncCancelled 终止整个同步
        - 暂停: 阻塞等待恢复（或取消）
        """
        if control["cancel"]:
            raise SyncCancelled("同步已取消")

        # 暂停：阻塞等待恢复，期间若取消则退出
        pause_event = control["pause"]
        if pause_event.is_set():
            # 等待恢复或取消
            while pause_event.is_set() and not control["cancel"]:
                pause_event.wait(0.5)
            if control["cancel"]:
                raise SyncCancelled("同步已取消")
            self.logger.debug(f"同步已恢复: {task_id}")

    def scan_local(self, local_path: str, exclude_patterns: Optional[List[str]] = None) -> Dict[str, FileInfo]:
        """
        扫描本地文件

        Args:
            local_path: 本地路径
            exclude_patterns: 排除模式

        Returns:
            文件信息字典 {相对路径: FileInfo}
        """
        files = {}
        # 排除模式：目录名（.git、__pycache__ 等）与临时文件后缀
        # （*.tmp 等，多为其他程序原子写入产生的中间文件，不应参与同步）
        exclude_patterns = exclude_patterns or [
            '.git', '__pycache__', '.DS_Store',
            '*.tmp', '*.part', '*.temp', '*.swp', '*.bak', '*.lock',
        ]

        try:
            for file_path in FileUtils.list_files(local_path, recursive=True, exclude_patterns=exclude_patterns):
                if os.path.isfile(file_path):
                    # 获取相对路径
                    rel_path = FileUtils.get_relative_path(file_path, local_path)
                    rel_path = FileUtils.normalize_path(rel_path)

                    # 获取文件信息（mtime 使用 UTC 墙钟时间口径，与远程 S3 服务器时间一致）
                    file_info = FileInfo(
                        path=rel_path,
                        size=FileUtils.get_file_size(file_path),
                        mtime=FileUtils.get_file_mtime_utc(file_path),
                        hash=HashUtils.calculate_file_md5(file_path),
                        is_dir=False
                    )
                    files[rel_path] = file_info

            self.logger.info(f"扫描本地文件完成: {local_path}, 共{len(files)}个文件")
            return files

        except Exception as e:
            self.logger.error(f"扫描本地文件失败: {e}")
            return {}

    def scan_remote(self, remote_prefix: str) -> Dict[str, FileInfo]:
        """
        扫描远程文件

        Args:
            remote_prefix: 远程前缀

        Returns:
            文件信息字典 {相对路径: FileInfo}
        """
        files = {}

        try:
            # 使用递归列出，确保子目录中的文件不会因 Delimiter 折叠而丢失
            remote_files = self.s3_client.list_files_recursive(prefix=remote_prefix)

            for file_info in remote_files:
                if not file_info.is_dir:
                    # 获取相对路径(移除前缀)
                    rel_path = file_info.path
                    norm_prefix = remote_prefix
                    if norm_prefix and not norm_prefix.endswith('/'):
                        norm_prefix = norm_prefix + '/'
                    if norm_prefix and rel_path.startswith(norm_prefix):
                        rel_path = rel_path[len(norm_prefix):]

                    rel_path = FileUtils.normalize_path(rel_path).lstrip('/')
                    # 跳过临时文件后缀（与本地扫描保持一致），
                    # 避免远程残留的 .tmp/.part 等中间文件被下载回本地
                    if self._is_temp_file(rel_path):
                        continue
                    file_info.path = rel_path
                    files[rel_path] = file_info

            self.logger.info(f"扫描远程文件完成: {remote_prefix}, 共{len(files)}个文件")
            return files

        except Exception as e:
            self.logger.error(f"扫描远程文件失败: {e}")
            return {}

    @staticmethod
    def _is_temp_file(rel_path: str) -> bool:
        """判断相对路径是否为临时文件（应跳过同步）

        匹配规则与 scan_local 的排除模式保持一致：
        文件名以 .tmp/.part/.temp/.swp/.bak/.lock 结尾。

        Args:
            rel_path: 相对路径

        Returns:
            是否为临时文件
        """
        import fnmatch
        name = os.path.basename(rel_path.replace('\\', '/'))
        return any(fnmatch.fnmatch(name, p) for p in (
            '*.tmp', '*.part', '*.temp', '*.swp', '*.bak', '*.lock'
        ))

    def detect_changes(
        self,
        local_files: Dict[str, FileInfo],
        remote_files: Dict[str, FileInfo],
        sync_mode: SyncMode = SyncMode.BIDIRECTIONAL
    ) -> SyncPlan:
        """
        检测变更并生成同步计划

        原则：以文件的修改时间（同一 UTC 口径）最新的那一端作为同步源。
        - 仅一侧存在：另一侧缺失即为"删除"或"新建"，都直接以存在侧为准传播；
        - 两侧都存在且内容不同：修改时间较新者作为源上传/下载，时间相等才视为冲突。

        不再依赖内存中的同步历史 _sync_history：首次运行、重启后历史丢失时，
        不会再把"本地缺失的远程文件"误判为"远程已删除"而错误清空本地/远程。

        Args:
            local_files: 本地文件字典
            remote_files: 远程文件字典
            sync_mode: 同步模式

        Returns:
            同步计划
        """
        plan = SyncPlan()

        # 获取所有文件路径
        all_paths = set(local_files.keys()) | set(remote_files.keys())

        for path in all_paths:
            local_file = local_files.get(path)
            remote_file = remote_files.get(path)

            # 根据同步模式处理
            if sync_mode == SyncMode.UPLOAD:
                # 仅上传模式
                self._plan_upload_mode(plan, path, local_file, remote_file)
            elif sync_mode == SyncMode.DOWNLOAD:
                # 仅下载模式
                self._plan_download_mode(plan, path, local_file, remote_file)
            else:
                # 双向同步模式
                self._plan_bidirectional_mode(plan, path, local_file, remote_file)

        self.logger.info(
            f"检测变更完成: {len(plan.actions)}个操作, {len(plan.conflicts)}个冲突"
        )
        return plan

    def _plan_upload_mode(
        self,
        plan: SyncPlan,
        path: str,
        local_file: Optional[FileInfo],
        remote_file: Optional[FileInfo]
    ):
        """仅上传模式的计划（以修改时间为准，本地较新才上传）"""
        if local_file and not remote_file:
            # 本地有，远程没有 -> 上传
            plan.add_action(FileAction.UPLOAD, local_file, None)
        elif local_file and remote_file:
            # 两边都有，本地较新则上传
            if self._should_upload(local_file, remote_file):
                plan.add_action(FileAction.UPLOAD, local_file, remote_file)
        elif not local_file and remote_file:
            # 本地没有，远程有 -> 删除远程
            plan.add_action(FileAction.DELETE_REMOTE, None, remote_file)

    def _plan_download_mode(
        self,
        plan: SyncPlan,
        path: str,
        local_file: Optional[FileInfo],
        remote_file: Optional[FileInfo]
    ):
        """仅下载模式的计划（以修改时间为准，远程较新才下载）

        远程→本地方向不同步删除事件：远程缺失但本地存在时不删除本地文件，
        仅跳过（本地保留）。只处理远程的新增与修改。
        """
        if remote_file and not local_file:
            # 远程有，本地没有 -> 下载
            plan.add_action(FileAction.DOWNLOAD, None, remote_file)
        elif remote_file and local_file:
            # 两边都有，远程较新则下载
            if self._should_download(local_file, remote_file):
                plan.add_action(FileAction.DOWNLOAD, local_file, remote_file)
        # 远程没有、本地有 -> 不删除本地（需求：远程→本地不同步删除）

    def _plan_bidirectional_mode(
        self,
        plan: SyncPlan,
        path: str,
        local_file: Optional[FileInfo],
        remote_file: Optional[FileInfo]
    ):
        """双向同步模式的计划（以修改时间最新者作为同步源）

        删除事件不同步：本地删除→远程由实时监听(watchdog)处理；
        远程删除→本地不删除（需求：远程→本地不同步删除）。
        这里仅处理新增与修改：单侧存在即向缺失侧传播，两侧存在以较新者为源。
        """
        if local_file and not remote_file:
            # 本地有，远程没有 -> 上传（本地是唯一存在的来源，内容为准）
            # 注意：本地删除的远程传播由实时监听处理，此处不会误删远程
            plan.add_action(FileAction.UPLOAD, local_file, None)

        elif not local_file and remote_file:
            # 远程有，本地没有 -> 下载（远程是唯一存在的来源，内容为准）
            plan.add_action(FileAction.DOWNLOAD, None, remote_file)

        elif local_file and remote_file:
            # 两边都有，以修改时间最新者作为源
            if local_file.hash == remote_file.hash:
                # 内容相同 -> 跳过
                plan.add_action(FileAction.SKIP, local_file, remote_file)
            elif self._mtime_newer(local_file.mtime, remote_file.mtime):
                # 本地较新 -> 上传
                plan.add_action(FileAction.UPLOAD, local_file, remote_file)
            elif self._mtime_newer(remote_file.mtime, local_file.mtime):
                # 远程较新 -> 下载
                plan.add_action(FileAction.DOWNLOAD, local_file, remote_file)
            else:
                # 修改时间相同但内容不同 -> 冲突
                plan.add_conflict(local_file, remote_file)

    @staticmethod
    def _mtime_newer(a, b) -> bool:
        """a 是否严格晚于 b（含 None 容错）"""
        if a is None:
            return False
        if b is None:
            return True
        try:
            return a > b
        except TypeError:
            # 类型不一致（naive/aware 混用）时按字符串比较兜底
            return str(a) > str(b)

    def _should_upload(self, local_file: FileInfo, remote_file: FileInfo) -> bool:
        """判断是否需要上传（内容不同且本地较新）"""
        if local_file.hash == remote_file.hash:
            return False
        return self._mtime_newer(local_file.mtime, remote_file.mtime)

    def _should_download(self, local_file: FileInfo, remote_file: FileInfo) -> bool:
        """判断是否需要下载（内容不同且远程较新）"""
        if local_file.hash == remote_file.hash:
            return False
        return self._mtime_newer(remote_file.mtime, local_file.mtime)

    def execute_sync(
        self,
        plan: SyncPlan,
        task: SyncTask,
        callback: Optional[Callable[[int, int, str], None]] = None,
        byte_callback: Optional[Callable[[float, float, float], None]] = None
    ) -> SyncResult:
        """
        执行同步计划

        Args:
            plan: 同步计划
            task: 同步任务
            callback: 进度回调 callback(current, total, message)
            byte_callback: 字节级进度回调 byte_callback(transferred, total, speed)
                （在文件传输过程中周期性触发，供 UI 显示速率/剩余时间）

        Returns:
            同步结果
        """
        result = SyncResult(
            success=True,
            start_time=datetime.now()
        )

        total_actions = len(plan.actions)
        current_action = 0

        # 字节级进度统计（供 byte_callback）
        import time as _t
        total_bytes = 0.0
        for action, lf, rf in plan.actions:
            if action.value in ("upload", "download"):
                fi = lf or rf
                if fi is not None:
                    total_bytes += float(getattr(fi, "size", 0) or 0)
        transferred_bytes = [0.0]
        byte_start = _t.time()
        last_byte_emit = [byte_start]

        # 获取同步控制（支持暂停/取消）
        control = self._get_control(task.id)
        cancelled = False

        for action, local_file, remote_file in plan.actions:
            current_action += 1

            # 每次操作前检查暂停/取消状态
            try:
                self._check_control(task.id, control)
            except SyncCancelled:
                cancelled = True
                self.logger.warning(f"同步已取消: {task.name}")
                break

            # 解析当前操作的相对路径（用于失败时记录）
            rel_path = ""
            if local_file and local_file.path:
                rel_path = local_file.path
            elif remote_file and remote_file.path:
                rel_path = remote_file.path

            # 回调
            if callback:
                message = f"{action.value}: {rel_path}"
                callback(current_action, total_actions, message)

            try:
                if action == FileAction.UPLOAD:
                    # 上传（携带字节级进度回调）
                    prev_bytes = transferred_bytes[0]  # 本文件开始前的累计字节

                    def _byte_progress(transferred, total):
                        # 在文件传输过程中也检查暂停/取消
                        try:
                            self._check_control(task.id, control)
                        except SyncCancelled:
                            raise
                        # 累计字节 = 之前文件的字节 + 本文件已传输字节
                        transferred_bytes[0] = prev_bytes + float(transferred)
                        if byte_callback:
                            now = _t.time()
                            if now - last_byte_emit[0] >= 0.2 or transferred >= total:
                                speed = transferred_bytes[0] / max(now - byte_start, 1e-6)
                                last_byte_emit[0] = now
                                byte_callback(
                                    transferred_bytes[0],
                                    total_bytes,
                                    speed,
                                )
                        if callback:
                            message = f"上传中: {local_file.path}"
                            callback(current_action, total_actions, message)

                    success = self._execute_upload(task, local_file, progress_cb=_byte_progress)
                    # 上传完成后，确保字节累计到位（简单上传可能无逐字节回调）
                    if success:
                        transferred_bytes[0] = prev_bytes + float(
                            getattr(local_file, "size", 0) or 0)
                    else:
                        transferred_bytes[0] = prev_bytes
                    if success:
                        result.uploaded += 1
                        self._sync_history[local_file.path] = local_file
                    else:
                        result.errors += 1
                        local_path = os.path.join(task.local_path, local_file.path)
                        remote_key = FileUtils.normalize_path(
                            os.path.join(task.remote_prefix, local_file.path))
                        reason = self._extract_s3_error("upload", local_file.path)
                        result.add_failure(
                            path=local_file.path,
                            action=action.value,
                            reason=reason,
                            local_path=local_path,
                            remote_path=remote_key,
                        )
                        result.error_messages.append(f"上传失败: {local_file.path} - {reason}")
                        self.logger.error(
                            f"上传失败: {local_file.path} -> {remote_key}, 原因: {reason}"
                        )

                elif action == FileAction.DOWNLOAD:
                    # 下载（携带字节级进度回调）
                    prev_bytes_dl = transferred_bytes[0]  # 本文件开始前的累计字节

                    def _byte_progress_dl(transferred, total):
                        try:
                            self._check_control(task.id, control)
                        except SyncCancelled:
                            raise
                        # 累计字节 = 之前文件的字节 + 本文件已传输字节
                        transferred_bytes[0] = prev_bytes_dl + float(transferred)
                        if byte_callback:
                            now = _t.time()
                            if now - last_byte_emit[0] >= 0.2 or transferred >= total:
                                speed = transferred_bytes[0] / max(now - byte_start, 1e-6)
                                last_byte_emit[0] = now
                                byte_callback(
                                    transferred_bytes[0],
                                    total_bytes,
                                    speed,
                                )
                        if callback:
                            message = f"下载中: {remote_file.path}"
                            callback(current_action, total_actions, message)

                    success = self._execute_download(task, remote_file, progress_cb=_byte_progress_dl)
                    # 下载完成后，确保字节累计到位
                    if success:
                        transferred_bytes[0] = prev_bytes_dl + float(
                            getattr(remote_file, "size", 0) or 0)
                    else:
                        transferred_bytes[0] = prev_bytes_dl
                    if success:
                        result.downloaded += 1
                        self._sync_history[remote_file.path] = remote_file
                    else:
                        result.errors += 1
                        local_path = os.path.join(task.local_path, remote_file.path)
                        remote_key = FileUtils.normalize_path(
                            os.path.join(task.remote_prefix, remote_file.path))
                        reason = self._extract_s3_error("download", remote_file.path)
                        result.add_failure(
                            path=remote_file.path,
                            action=action.value,
                            reason=reason,
                            local_path=local_path,
                            remote_path=remote_key,
                        )
                        result.error_messages.append(f"下载失败: {remote_file.path} - {reason}")
                        self.logger.error(
                            f"下载失败: {remote_key} -> {local_path}, 原因: {reason}"
                        )

                elif action == FileAction.DELETE_LOCAL:
                    # 删除本地
                    success = self._execute_delete_local(task, local_file)
                    if success:
                        result.deleted_local += 1
                        if local_file.path in self._sync_history:
                            del self._sync_history[local_file.path]
                    else:
                        result.errors += 1
                        local_path = os.path.join(task.local_path, local_file.path)
                        reason = self._extract_delete_local_error(local_path)
                        result.add_failure(
                            path=local_file.path,
                            action=action.value,
                            reason=reason,
                            local_path=local_path,
                        )
                        result.error_messages.append(f"删除本地失败: {local_file.path} - {reason}")
                        self.logger.error(
                            f"删除本地失败: {local_path}, 原因: {reason}"
                        )

                elif action == FileAction.DELETE_REMOTE:
                    # 删除远程
                    success = self._execute_delete_remote(task, remote_file)
                    if success:
                        result.deleted_remote += 1
                        if remote_file.path in self._sync_history:
                            del self._sync_history[remote_file.path]
                    else:
                        result.errors += 1
                        remote_key = FileUtils.normalize_path(
                            os.path.join(task.remote_prefix, remote_file.path))
                        reason = self._extract_s3_error("delete_remote", remote_file.path)
                        result.add_failure(
                            path=remote_file.path,
                            action=action.value,
                            reason=reason,
                            remote_path=remote_key,
                        )
                        result.error_messages.append(f"删除远程失败: {remote_file.path} - {reason}")
                        self.logger.error(
                            f"删除远程失败: {remote_key}, 原因: {reason}"
                        )

                elif action == FileAction.CONFLICT:
                    # 冲突处理
                    success = self._execute_conflict(task, local_file, remote_file)
                    if success:
                        result.conflicts += 1
                    else:
                        result.errors += 1
                        reason = self._extract_s3_error("conflict", local_file.path or "")
                        result.add_failure(
                            path=local_file.path or remote_file.path,
                            action=action.value,
                            reason=reason or "冲突处理失败",
                        )
                        result.error_messages.append(
                            f"冲突处理失败: {local_file.path} - {reason}"
                        )
                        self.logger.error(
                            f"冲突处理失败: {local_file.path}, 原因: {reason}"
                        )

                elif action == FileAction.SKIP:
                    # 跳过
                    pass

            except SyncCancelled:
                # 同步被取消：终止整个同步
                cancelled = True
                self.logger.warning(f"同步已取消: {task.name}")
                break
            except Exception as e:
                self.logger.error(f"执行操作失败: {action.value}, {e}", exc_info=True)
                result.errors += 1
                err_msg = f"{action.value}: {str(e)}"
                result.error_messages.append(err_msg)
                result.add_failure(
                    path=rel_path,
                    action=action.value,
                    reason=str(e),
                )

        # 更新结果
        result.end_time = datetime.now()
        result.success = result.errors == 0 and not cancelled
        # 若被取消，标记错误计数以便 UI 知道同步未正常完成
        if cancelled:
            result.cancelled = True

        # 清理同步控制（暂停/取消标记）
        self._clear_control(task.id)

        self.logger.info(
            f"同步完成: 上传{result.uploaded}, 下载{result.downloaded}, "
            f"删除本地{result.deleted_local}, 删除远程{result.deleted_remote}, "
            f"冲突{result.conflicts}, 错误{result.errors}"
            f"{', 已取消' if cancelled else ''}"
        )
        if result.failed_files:
            self.logger.warning(
                f"失败文件明细:\n" + "\n".join(
                    f"  - [{f.action}] {f.path} | 原因: {f.reason}"
                    for f in result.failed_files
                )
            )

        return result

    def _extract_s3_error(self, op: str, path: str) -> str:
        """从 S3Client.last_error 中提取失败原因，找不到则返回通用描述"""
        last = getattr(self.s3_client, "last_error", None)
        if isinstance(last, dict) and last.get("path"):
            err = last.get("error") or "未知错误"
            err_type = last.get("type") or ""
            if err_type and err_type not in err:
                return f"{err_type}: {err}"
            return err
        return f"{op} '{path}' 失败（详见日志）"

    def _extract_delete_local_error(self, local_path: str) -> str:
        """删除本地文件失败的原因（FileUtils.delete_file 未保留原因，返回通用描述）"""
        import sys
        # 取最近一次异常（若有）
        exc = sys.exc_info()
        if exc and exc[1] is not None:
            return f"{type(exc[1]).__name__}: {exc[1]}"
        return f"无法删除本地文件: {local_path}"

    def _execute_upload(
        self,
        task: SyncTask,
        local_file: FileInfo,
        progress_cb: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """执行上传

        Args:
            task: 同步任务
            local_file: 本地文件信息
            progress_cb: 字节级进度回调 callback(transferred, total)
        """
        local_path = os.path.join(task.local_path, local_file.path)
        remote_key = FileUtils.normalize_path(os.path.join(task.remote_prefix, local_file.path))

        if progress_cb is not None:
            return self.s3_client.upload_file(local_path, remote_key, callback=progress_cb)
        return self.s3_client.upload_file(local_path, remote_key)

    def _execute_download(
        self,
        task: SyncTask,
        remote_file: FileInfo,
        progress_cb: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """执行下载

        Args:
            task: 同步任务
            remote_file: 远程文件信息
            progress_cb: 字节级进度回调 callback(transferred, total)
        """
        remote_key = FileUtils.normalize_path(os.path.join(task.remote_prefix, remote_file.path))
        local_path = os.path.join(task.local_path, remote_file.path)

        if progress_cb is not None:
            return self.s3_client.download_file(remote_key, local_path, callback=progress_cb)
        return self.s3_client.download_file(remote_key, local_path)

    def _execute_delete_local(self, task: SyncTask, local_file: FileInfo) -> bool:
        """执行删除本地文件"""
        local_path = os.path.join(task.local_path, local_file.path)
        return FileUtils.delete_file(local_path)

    def _execute_delete_remote(self, task: SyncTask, remote_file: FileInfo) -> bool:
        """执行删除远程文件"""
        remote_key = FileUtils.normalize_path(os.path.join(task.remote_prefix, remote_file.path))
        return self.s3_client.delete_file(remote_key)

    def _execute_conflict(
        self,
        task: SyncTask,
        local_file: FileInfo,
        remote_file: FileInfo
    ) -> bool:
        """执行冲突处理"""
        strategy = task.conflict_strategy

        if strategy == ConflictStrategy.RENAME:
            # 重命名策略：创建冲突副本
            return self._handle_conflict_rename(task, local_file, remote_file)

        elif strategy == ConflictStrategy.OVERWRITE:
            # 覆盖策略：较新的覆盖较旧的
            return self._handle_conflict_overwrite(task, local_file, remote_file)

        elif strategy == ConflictStrategy.SKIP:
            # 跳过策略：记录到日志
            self.logger.warning(f"跳过冲突文件: {local_file.path}")
            return True

        elif strategy == ConflictStrategy.MANUAL:
            # 手动解决：记录到日志，等待用户处理
            self.logger.warning(f"需要手动解决冲突: {local_file.path}")
            return True

        return False

    def _handle_conflict_rename(
        self,
        task: SyncTask,
        local_file: FileInfo,
        remote_file: FileInfo
    ) -> bool:
        """处理冲突：重命名"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 重命名本地文件
        local_path = os.path.join(task.local_path, local_file.path)
        name, ext = os.path.splitext(local_path)
        conflict_local = f"{name}_conflict_{timestamp}{ext}"

        if not FileUtils.move_file(local_path, conflict_local):
            self.logger.error(f"重命名本地文件失败: {local_path}")
            return False

        # 下载远程文件
        remote_key = FileUtils.normalize_path(os.path.join(task.remote_prefix, remote_file.path))
        if not self.s3_client.download_file(remote_key, local_path):
            self.logger.error(f"下载远程文件失败: {remote_key}")
            return False

        self.logger.info(f"冲突已解决(重命名): {local_file.path}")
        return True

    def _handle_conflict_overwrite(
        self,
        task: SyncTask,
        local_file: FileInfo,
        remote_file: FileInfo
    ) -> bool:
        """处理冲突：覆盖"""
        if local_file.mtime > remote_file.mtime:
            # 本地较新，上传覆盖远程
            return self._execute_upload(task, local_file)
        else:
            # 远程较新，下载覆盖本地
            return self._execute_download(task, remote_file)

    def get_sync_history(self) -> Dict[str, FileInfo]:
        """获取同步历史"""
        return self._sync_history

    def clear_sync_history(self):
        """清空同步历史"""
        self._sync_history.clear()
