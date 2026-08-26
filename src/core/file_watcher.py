"""
本地文件实时监听器
基于 watchdog 监听本地目录文件变更，实时触发同步到远程。
"""
import os
import time
import threading
import logging
from typing import Dict, Optional, Callable, Set, List

from ..models.sync_task import SyncMode

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    _WATCHDOG_AVAILABLE = True
except Exception:  # 沙盒/离线环境可能没有 watchdog
    Observer = None
    FileSystemEventHandler = object
    _WATCHDOG_AVAILABLE = False


class LocalFileHandler(FileSystemEventHandler):
    """本地文件系统事件处理器"""

    def __init__(self, on_change: Callable[[str, list, list], None], debounce: float = 1.0):
        """
        初始化

        Args:
            on_change: 变更回调 on_change(created_or_modified_paths, deleted_paths, moved_pairs)
            debounce: 防抖时间(秒)，合并短时间内多次事件
        """
        super().__init__()
        self._on_change = on_change
        self._debounce = debounce
        self._pending: Set[str] = set()       # 新建/修改/移动目标的路径
        self._deleted: Set[str] = set()       # 删除/移动源的路径
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None

    def _schedule(self, path: str, deleted: bool = False):
        """合并事件并延时触发"""
        with self._lock:
            if deleted:
                self._deleted.add(path)
                self._pending.discard(path)  # 删除优先：同一路径既有修改又有删除时按删除处理
            else:
                self._pending.add(path)
                if path in self._deleted:
                    self._deleted.discard(path)
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self):
        """触发一次变更回调（合并的路径列表）"""
        with self._lock:
            paths = list(self._pending)
            deleted = list(self._deleted)
            self._pending.clear()
            self._deleted.clear()
            self._timer = None
        if (paths or deleted) and self._on_change:
            try:
                self._on_change(paths, deleted)
            except Exception as e:
                logging.getLogger(__name__).error(f"实时同步回调失败: {e}")

    def on_created(self, event):
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._schedule(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._schedule(event.src_path, deleted=True)  # 移动源视为删除
            self._schedule(event.dest_path)               # 移动目标视为新建

    def on_deleted(self, event):
        if not event.is_directory:
            self._schedule(event.src_path, deleted=True)
        else:
            self._schedule(event.src_path, deleted=True)  # 目录删除也记录（用于删除整棵远程目录）


class FileWatcher:
    """本地文件监听器，监听目录变更并回调"""

    def __init__(
        self,
        sync_engine=None,
        logger: Optional[logging.Logger] = None,
        debounce: float = 1.0
    ):
        """
        初始化

        Args:
            sync_engine: 同步引擎（用于实时同步）
            logger: 日志记录器
            debounce: 事件防抖时间(秒)
        """
        self.sync_engine = sync_engine
        self.logger = logger or logging.getLogger(__name__)
        self.debounce = debounce

        self._observer = None
        self._handler = None
        self._watched: Dict[str, str] = {}  # local_path -> task.remote_prefix
        self._task_map: Dict[str, object] = {}  # local_path -> task
        self._watch_handles: Dict[str, object] = {}  # local_path -> watchdog Watch对象
        self._lock = threading.Lock()
        self._sync_lock = threading.Lock()  # 防止并发同步
        self._suppress = False  # 临时抑制监听回调（同步/文件操作期间避免级联）
        self._suppress_count = 0  # 抑制计数：支持嵌套抑制，归零才恢复监听

        # 实时同步失败回调（由调用方设置，用于 UI 通知）
        # 签名: on_failed(failures: List[Dict[str, str]])
        # 每项 {"action": "upload"/"delete_remote", "path": str, "reason": str}
        self._on_failed: Optional[Callable[[List[Dict[str, str]]], None]] = None

    @property
    def is_available(self) -> bool:
        """watchdog 是否可用"""
        return _WATCHDOG_AVAILABLE

    @staticmethod
    def _is_temp_path(path: str) -> bool:
        """判断路径是否为临时文件（应跳过实时同步）

        与 sync_engine 的排除规则保持一致：文件名以
        .tmp/.part/.temp/.swp/.bak/.lock 结尾。

        Args:
            path: 文件路径

        Returns:
            是否为临时文件
        """
        import fnmatch
        name = os.path.basename(path.replace('\\', '/'))
        return any(fnmatch.fnmatch(name, p) for p in (
            '*.tmp', '*.part', '*.temp', '*.swp', '*.bak', '*.lock'
        ))

    def set_failure_callback(self, callback: Callable[[List[Dict[str, str]]], None]):
        """设置实时同步失败回调（供 UI 层订阅以弹窗提示用户）"""
        self._on_failed = callback

    def add_watch(self, task) -> bool:
        """
        监听任务的本地目录

        Args:
            task: SyncTask

        Returns:
            是否成功
        """
        if not _WATCHDOG_AVAILABLE:
            self.logger.warning("watchdog 不可用，无法实时监听")
            return False

        local_path = task.local_path
        if not local_path or not os.path.isdir(local_path):
            self.logger.warning(f"本地目录不存在，无法监听: {local_path}")
            return False

        with self._lock:
            # 同一目录只监听一次
            if local_path in self._watched:
                self._task_map[local_path] = task
                return True

            try:
                if self._observer is None:
                    self._observer = Observer()
                    self._observer.daemon = True
                    self._observer.start()

                handler = LocalFileHandler(
                    on_change=self._on_files_changed,
                    debounce=self.debounce
                )
                watch_handle = self._observer.schedule(handler, local_path, recursive=True)
                self._watched[local_path] = task.remote_prefix
                self._task_map[local_path] = task
                self._watch_handles[local_path] = watch_handle
                self.logger.info(f"开始监听本地目录: {local_path} (远程: {task.remote_prefix})")
                return True

            except Exception as e:
                self.logger.error(f"监听目录失败: {local_path}, {e}")
                return False

    def remove_watch(self, local_path: str) -> bool:
        """
        停止监听目录

        Args:
            local_path: 本地路径

        Returns:
            是否成功
        """
        with self._lock:
            if local_path not in self._watched:
                return False
            try:
                # 仅移除该目录的监听，不影响其他目录
                handle = self._watch_handles.get(local_path)
                if handle and self._observer:
                    try:
                        self._observer.remove_watch(handle)
                    except Exception:
                        pass
                del self._watched[local_path]
                if local_path in self._task_map:
                    del self._task_map[local_path]
                if local_path in self._watch_handles:
                    del self._watch_handles[local_path]
                self.logger.info(f"停止监听本地目录: {local_path}")
                return True
            except Exception as e:
                self.logger.error(f"停止监听失败: {local_path}, {e}")
                return False

    def set_suppress(self, on: bool):
        """临时抑制/恢复监听回调

        计数式抑制：on=True 递增抑制计数，on=False 递减；
        计数归零才真正恢复监听。支持并发/嵌套抑制（如手动同步、
        定时同步、文件浏览器操作同时进行时不会互相打断抑制状态）。
        """
        with self._lock:
            if on:
                self._suppress_count += 1
                self._suppress = True
            else:
                if self._suppress_count > 0:
                    self._suppress_count -= 1
                self._suppress = self._suppress_count > 0

    def _on_files_changed(self, paths, deleted_paths):
        """文件变更回调：实时同步对应任务（上传变更 + 删除远程对应项）"""
        if self._suppress:
            # 文件浏览器手动操作期间不触发实时同步，避免级联
            return
        # 找到变更文件所属的任务（取最近的父目录）
        changed = set(os.path.normpath(p) for p in paths)
        deleted = set(os.path.normpath(p) for p in (deleted_paths or []))

        # 按监听目录分组
        for local_path, remote_prefix in list(self._watched.items()):
            base = os.path.normpath(local_path)
            rel_paths = []
            rel_deleted = []
            for p in changed:
                try:
                    rel = os.path.relpath(p, base)
                    if not rel.startswith('..'):
                        rel_norm = FileUtils.normalize_path(rel)
                        # 跳过临时文件（*.tmp/.part/.temp 等，多为程序原子写入中间文件）
                        if self._is_temp_path(p):
                            continue
                        rel_paths.append(rel_norm)
                except ValueError:
                    continue
            for p in deleted:
                try:
                    rel = os.path.relpath(p, base)
                    if not rel.startswith('..'):
                        rel_norm = FileUtils.normalize_path(rel)
                        if self._is_temp_path(p):
                            continue
                        rel_deleted.append(rel_norm)
                except ValueError:
                    continue

            if rel_paths or rel_deleted:
                task = self._task_map.get(local_path)
                if task:
                    self._sync_task(task, rel_paths, rel_deleted)

    def _sync_task(self, task, rel_paths, rel_deleted=None):
        """实时同步单个任务

        按任务的同步模式决定行为：
        - DOWNLOAD（仅下载）：本地变更不触发任何写远程操作，
          不做上传也不删除远程（远程→本地由定时/手动同步完成）。
        - UPLOAD（仅上传）：上传本地新增/修改 + 传播删除（本地删除 -> 删远程）。
        - BIDIRECTIONAL（双向）：上传本地变更 + 删除远程对应文件。

        需与 _on_files_changed 的监听范围配合：DOWNLOAD 任务在
        add_watch 时即跳过监听，此处双重保险。
        """
        if not self._sync_lock.acquire(blocking=False):
            self.logger.debug("已有实时同步进行中，跳过本次触发")
            return

        rel_deleted = rel_deleted or []

        # 仅下载模式：本地文件监听不应产生任何写远程操作（不传、不删）
        if getattr(task, "sync_mode", None) == SyncMode.DOWNLOAD:
            self.logger.debug(
                f"任务为仅下载模式，跳过本地变更实时同步: {getattr(task, 'name', '')} "
                f"(变更{len(rel_paths)}个, 删除{len(rel_deleted)}个)"
            )
            self._sync_lock.release()
            return

        try:
            local_path = task.local_path
            remote_prefix = task.remote_prefix or ""

            uploaded = 0
            deleted_remote = 0
            failed = 0
            failures: List[Dict[str, str]] = []  # 收集失败明细供 UI 展示

            s3_client = self.sync_engine.s3_client

            # 1. 处理删除：本地文件被删 -> 删除远程对应对象
            #    仅上传/双向模式均传播删除（仅下载模式在此前已提前 return）
            for rel in rel_deleted:
                remote_key = FileUtils.normalize_path(os.path.join(remote_prefix, rel))
                try:
                    # 先按"可能为目录"尝试：补斜杠并递归删除前缀下所有对象
                    prefix_for_dir = remote_key
                    if not prefix_for_dir.endswith('/'):
                        prefix_for_dir = prefix_for_dir + '/'
                    if s3_client.delete_prefix(prefix_for_dir):
                        deleted_remote += 1
                        self.logger.info(f"实时同步删除远程(目录): {prefix_for_dir}")
                    else:
                        # 目录前缀下可能无对象，再尝试按文件删除
                        if s3_client.delete_file(remote_key):
                            deleted_remote += 1
                            self.logger.info(f"实时同步删除远程: {remote_key}")
                        else:
                            failed += 1
                            reason = self._extract_s3_error(s3_client, remote_key, "删除远程")
                            self.logger.error(f"实时同步删除远程失败: {remote_key}, 原因: {reason}")
                            failures.append({
                                "action": "delete_remote",
                                "path": remote_key,
                                "reason": reason,
                            })
                except Exception as e:
                    # 尝试按文件删除兜底
                    try:
                        if s3_client.delete_file(remote_key):
                            deleted_remote += 1
                            self.logger.info(f"实时同步删除远程: {remote_key}")
                        else:
                            failed += 1
                            reason = f"{type(e).__name__}: {e}"
                            self.logger.error(
                                f"实时同步删除远程失败: {remote_key}, 原因: {reason}"
                            )
                            failures.append({
                                "action": "delete_remote",
                                "path": remote_key,
                                "reason": reason,
                            })
                    except Exception as inner:
                        failed += 1
                        reason = f"{type(inner).__name__}: {inner}"
                        self.logger.error(
                            f"实时同步删除远程失败: {remote_key}, 原因: {reason}"
                        )
                        failures.append({
                            "action": "delete_remote",
                            "path": remote_key,
                            "reason": reason,
                        })

            # 2. 处理上传：本地新建/修改/移动目标 -> 上传远程
            for rel in rel_paths:
                abs_path = os.path.join(local_path, rel)
                if os.path.isfile(abs_path):
                    remote_key = FileUtils.normalize_path(os.path.join(remote_prefix, rel))
                    ok = s3_client.upload_file(abs_path, remote_key)
                    if ok:
                        uploaded += 1
                        self.logger.info(f"实时同步上传: {rel} -> {remote_key}")
                    else:
                        failed += 1
                        reason = self._extract_s3_error(s3_client, remote_key, "上传")
                        self.logger.error(
                            f"实时同步上传失败: {rel} -> {remote_key}, 原因: {reason}"
                        )
                        failures.append({
                            "action": "upload",
                            "path": rel,
                            "reason": reason,
                        })
                elif os.path.isdir(abs_path):
                    # 新目录本身无需上传，跳过
                    pass

            if uploaded or deleted_remote or failed:
                self.logger.info(
                    f"实时同步完成: 上传{uploaded}, 删除远程{deleted_remote}, 失败{failed}"
                )

            # 有失败时通知 UI（通过回调，由调用方转发到 GUI 线程弹窗）
            if failures and self._on_failed:
                try:
                    self._on_failed(failures)
                except Exception as e:
                    self.logger.error(f"实时同步失败回调执行异常: {e}")

        finally:
            self._sync_lock.release()

    @staticmethod
    def _extract_s3_error(s3_client, path: str, action_cn: str) -> str:
        """从 S3Client.last_error 中提取失败原因"""
        last = getattr(s3_client, "last_error", None)
        if isinstance(last, dict):
            err = last.get("error") or ""
            err_type = last.get("type") or ""
            if err:
                if err_type and err_type not in err:
                    return f"{err_type}: {err}"
                return err
        return f"{action_cn} '{path}' 失败（详见日志）"

    def stop(self):
        """停止监听"""
        with self._lock:
            if self._observer:
                try:
                    self._observer.stop()
                    self._observer.join(timeout=5)
                except Exception:
                    pass
                self._observer = None
            self._watched.clear()
            self._task_map.clear()
            self.logger.info("文件监听已停止")


# 运行时导入 FileUtils（避免循环依赖）
from ..utils.file_utils import FileUtils


def watchdog_available() -> bool:
    """watchdog 是否可用"""
    return _WATCHDOG_AVAILABLE
