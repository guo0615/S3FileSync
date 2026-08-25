"""
定时调度器
管理定时同步任务
"""
from typing import Dict, List, Optional, Callable
from datetime import datetime
from enum import Enum
import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR

from .sync_engine import SyncEngine
from .file_watcher import FileWatcher, watchdog_available
from ..models.sync_task import SyncTask
from ..models.transfer_state import TransferStatus


class TaskStatus(Enum):
    """任务状态"""
    IDLE = "idle"           # 空闲
    RUNNING = "running"     # 运行中
    PAUSED = "paused"       # 已暂停
    ERROR = "error"         # 错误


class SyncScheduler:
    """同步调度器"""

    def __init__(
        self,
        sync_engine: SyncEngine,
        logger: Optional[logging.Logger] = None
    ):
        """
        初始化调度器

        Args:
            sync_engine: 同步引擎
            logger: 日志记录器
        """
        self.sync_engine = sync_engine
        self.logger = logger or logging.getLogger(__name__)

        # APScheduler调度器
        self._scheduler = BackgroundScheduler()
        self._scheduler.add_listener(
            self._job_executed_listener,
            EVENT_JOB_EXECUTED | EVENT_JOB_ERROR
        )

        # 任务管理
        self._tasks: Dict[str, SyncTask] = {}
        self._task_status: Dict[str, TaskStatus] = {}
        self._task_prev_status: Dict[str, TaskStatus] = {}  # 进入 RUNNING 前的状态（供 finish_task 恢复）
        self._task_callbacks: Dict[str, Callable] = {}
        self._task_progress_callbacks: Dict[str, Callable] = {}  # 任务进度回调

        # 本地文件实时监听（本地变更 -> 实时上传远程）
        self.file_watcher = FileWatcher(sync_engine, logger)
        self._watch_enabled = False

        # 锁
        self._lock = threading.Lock()

        # 启动调度器
        self._scheduler.start()
        self.logger.info("调度器已启动")

    def set_realtime_watch(self, enabled: bool):
        """
        启用/禁用本地文件实时监听

        Args:
            enabled: 是否启用
        """
        with self._lock:
            if enabled == self._watch_enabled:
                return
            self._watch_enabled = enabled

            if enabled:
                for task in self._tasks.values():
                    if task.enabled:
                        self.file_watcher.add_watch(task)
                self.logger.info("已启用本地文件实时监听")
            else:
                self.file_watcher.stop()
                self.logger.info("已禁用本地文件实时监听")

    def add_task(
        self,
        task: SyncTask,
        callback: Optional[Callable] = None,
        progress_callback: Optional[Callable] = None
    ) -> str:
        """
        添加同步任务

        Args:
            task: 同步任务
            callback: 任务执行完成回调
            progress_callback: 任务执行进度回调
                callback(task_id, current, total, message)

        Returns:
            任务ID
        """
        with self._lock:
            # 添加任务
            self._tasks[task.id] = task
            self._task_status[task.id] = TaskStatus.IDLE

            if callback:
                self._task_callbacks[task.id] = callback
            if progress_callback:
                self._task_progress_callbacks[task.id] = progress_callback

            # 如果任务启用，添加到调度器
            if task.enabled:
                self._schedule_task(task)

            # 实时监听：若全局监听已开启且任务启用，则开始监听本地目录
            if self._watch_enabled and task.enabled:
                self.file_watcher.add_watch(task)

            self.logger.info(f"添加任务: {task.name} ({task.id})")
            return task.id

    def remove_task(self, task_id: str) -> bool:
        """
        移除同步任务

        Args:
            task_id: 任务ID

        Returns:
            是否成功
        """
        with self._lock:
            if task_id not in self._tasks:
                return False

            task = self._tasks[task_id]

            # 从调度器移除
            if self._scheduler.get_job(task_id):
                self._scheduler.remove_job(task_id)

            # 停止实时监听
            if self._watch_enabled:
                self.file_watcher.remove_watch(task.local_path)

            # 删除任务
            del self._tasks[task_id]
            if task_id in self._task_status:
                del self._task_status[task_id]
            if task_id in self._task_prev_status:
                del self._task_prev_status[task_id]
            if task_id in self._task_callbacks:
                del self._task_callbacks[task_id]
            if task_id in self._task_progress_callbacks:
                del self._task_progress_callbacks[task_id]

            self.logger.info(f"移除任务: {task_id}")
            return True

    def pause_task(self, task_id: str) -> bool:
        """
        暂停任务

        - 暂停 APScheduler 定时触发
        - 停止实时监听
        - 若任务正在同步，通知同步引擎暂停（当前文件传完后停下等待恢复）

        Args:
            task_id: 任务ID

        Returns:
            是否成功
        """
        with self._lock:
            if task_id not in self._tasks:
                return False

            # 暂停调度器中的任务
            if self._scheduler.get_job(task_id):
                self._scheduler.pause_job(task_id)

            # 停止实时监听
            if self._watch_enabled:
                self.file_watcher.remove_watch(self._tasks[task_id].local_path)

            # 更新状态
            self._task_status[task_id] = TaskStatus.PAUSED
            self._tasks[task_id].enabled = False

        # 通知同步引擎暂停正在运行的同步（在锁外调用，避免持锁期间阻塞）
        try:
            self.sync_engine.pause_sync(task_id)
        except Exception as e:
            self.logger.warning(f"暂停同步引擎失败: {e}")

        self.logger.info(f"暂停任务: {task_id}")
        return True

    def resume_task(self, task_id: str) -> bool:
        """
        恢复任务

        - 恢复 APScheduler 定时触发
        - 恢复实时监听
        - 若同步正被暂停，通知同步引擎恢复继续

        Args:
            task_id: 任务ID

        Returns:
            是否成功
        """
        with self._lock:
            if task_id not in self._tasks:
                return False

            # 恢复调度器中的任务
            if self._scheduler.get_job(task_id):
                self._scheduler.resume_job(task_id)
            else:
                # 重新调度
                task = self._tasks[task_id]
                self._schedule_task(task)

            # 恢复实时监听
            if self._watch_enabled:
                self.file_watcher.add_watch(self._tasks[task_id])

            # 更新状态
            self._task_status[task_id] = TaskStatus.IDLE
            self._tasks[task_id].enabled = True

        # 通知同步引擎恢复被暂停的同步（在锁外调用）
        try:
            self.sync_engine.resume_sync(task_id)
        except Exception as e:
            self.logger.warning(f"恢复同步引擎失败: {e}")

        self.logger.info(f"恢复任务: {task_id}")
        return True

    def cancel_task_sync(self, task_id: str) -> bool:
        """
        取消正在运行的同步（立即终止）

        Args:
            task_id: 任务ID

        Returns:
            是否成功
        """
        try:
            self.sync_engine.cancel_sync(task_id)
            self.logger.info(f"已请求取消任务同步: {task_id}")
            return True
        except Exception as e:
            self.logger.error(f"取消任务同步失败: {e}")
            return False

    def run_task_now(self, task_id: str) -> bool:
        """
        立即运行任务

        注意：在锁外调用 _execute_task，因为 _execute_task 内部会通过
        try_start_task 再次获取 self._lock（否则死锁）。若任务正在运行，
        本次调用会被 try_start_task 拒绝并返回 False。

        Args:
            task_id: 任务ID

        Returns:
            是否成功启动执行（False 表示任务不存在或已在运行）
        """
        with self._lock:
            if task_id not in self._tasks:
                return False

        # 在锁外触发执行（内部原子化获取执行权）
        return self._execute_task(task_id)

    def get_task_status(self, task_id: str) -> TaskStatus:
        """
        获取任务状态

        Args:
            task_id: 任务ID

        Returns:
            任务状态
        """
        return self._task_status.get(task_id, TaskStatus.IDLE)

    def get_all_tasks(self) -> List[SyncTask]:
        """获取所有任务"""
        return list(self._tasks.values())

    def get_task(self, task_id: str) -> Optional[SyncTask]:
        """获取任务"""
        return self._tasks.get(task_id)

    def update_task(self, task: SyncTask) -> bool:
        """
        更新任务

        Args:
            task: 同步任务

        Returns:
            是否成功
        """
        with self._lock:
            if task.id not in self._tasks:
                return False

            old_task = self._tasks[task.id]

            # 移除旧的调度
            if self._scheduler.get_job(task.id):
                self._scheduler.remove_job(task.id)

            # 停止旧的实时监听（路径可能变化）
            if self._watch_enabled and old_task.local_path:
                self.file_watcher.remove_watch(old_task.local_path)

            # 更新任务
            self._tasks[task.id] = task

            # 如果任务启用，重新调度
            if task.enabled:
                self._schedule_task(task)

            # 重新监听（若全局监听开启）
            if self._watch_enabled and task.enabled:
                self.file_watcher.add_watch(task)

            self.logger.info(f"更新任务: {task.name} ({task.id})")
            return True

    def _schedule_task(self, task: SyncTask):
        """调度任务"""
        try:
            # 创建间隔触发器
            trigger = IntervalTrigger(seconds=task.interval)

            # 添加任务到调度器
            self._scheduler.add_job(
                self._execute_task,
                trigger=trigger,
                id=task.id,
                args=[task.id],
                replace_existing=True
            )

            # 计算下次执行时间
            task.calculate_next_sync()

            self.logger.info(
                f"调度任务: {task.name}, 间隔: {task.interval}秒, "
                f"下次执行: {task.next_sync}"
            )

        except Exception as e:
            self.logger.error(f"调度任务失败: {e}")

    def try_start_task(self, task_id: str) -> bool:
        """
        原子化地尝试启动任务：若任务未在运行，则标记为 RUNNING 并返回 True；
        若任务已在运行，返回 False（调用方应跳过本次执行）。

        该方法在 self._lock 下完成"检查状态 + 设置状态"，保证并发环境下
        （APScheduler 线程池 + 手动 run_task_now / 手动同步）同一任务
        不会出现两个执行实例同时运行。

        Args:
            task_id: 任务ID

        Returns:
            是否成功获取执行权
        """
        with self._lock:
            if task_id not in self._tasks:
                return False
            current = self._task_status.get(task_id, TaskStatus.IDLE)
            if current == TaskStatus.RUNNING:
                # 已有实例正在运行，拒绝再次启动
                self.logger.warning(
                    f"任务正在运行，跳过本次执行: {self._tasks[task_id].name} ({task_id})"
                )
                return False
            # 记录进入运行前状态，供 finish_task 恢复
            self._task_prev_status[task_id] = current
            self._task_status[task_id] = TaskStatus.RUNNING
            return True

    def finish_task(self, task_id: str, status: Optional[TaskStatus] = None) -> None:
        """
        标记任务执行结束，恢复任务状态（原子化）。

        Args:
            task_id: 任务ID
            status: 结束后状态；None 时恢复为进入运行前的状态
        """
        with self._lock:
            if task_id not in self._tasks:
                return
            if status is None:
                status = self._task_prev_status.pop(task_id, TaskStatus.IDLE)
            else:
                self._task_prev_status.pop(task_id, None)
            self._task_status[task_id] = status

    def _execute_task(self, task_id: str) -> bool:
        """执行任务（幂等：同一任务同时只会有一个实例在运行）

        Returns:
            是否实际启动了执行（False 表示任务不存在或已在运行/被跳过）
        """
        task = self._tasks.get(task_id)
        if not task:
            return False

        # 原子化获取执行权：若任务已在运行（定时或手动），直接跳过
        if not self.try_start_task(task_id):
            return False

        # 同步期间抑制本地实时监听：
        # 下载落地/重命名文件会触发 FileWatcher，若不抑制会把刚下载的内容
        # 再次实时上传，与定时同步互相追赶形成重复同步死循环，导致程序占用异常增大。
        watcher = getattr(self, "file_watcher", None)
        if watcher:
            watcher.set_suppress(True)

        try:
            self.logger.info(f"开始执行任务: {task.name}")

            # 扫描文件
            local_files = self.sync_engine.scan_local(task.local_path)
            remote_files = self.sync_engine.scan_remote(task.remote_prefix)

            # 检测变更
            plan = self.sync_engine.detect_changes(
                local_files,
                remote_files,
                task.sync_mode
            )

            # 构造进度回调（若有注册），把进度传给 UI（通过 callback 链）
            progress_cb = self._task_progress_callbacks.get(task_id)

            # 字节级统计（供 byte_callback）
            byte_state = [0.0, 0.0, 0.0]  # [transferred, total_bytes, speed]

            def _progress_callback(cur, total, message):
                if progress_cb:
                    try:
                        progress_cb(
                            task_id, cur, total, message,
                            byte_state[0], byte_state[1], byte_state[2],
                        )
                    except Exception as e:
                        self.logger.warning(f"任务进度回调异常: {e}")

            def _byte_callback(transferred, total, speed):
                byte_state[0] = transferred
                byte_state[1] = total
                byte_state[2] = speed
                if progress_cb:
                    try:
                        # current=-1 标记为字节级进度，UI 端以 transferred/total 算百分比
                        progress_cb(
                            task_id, -1, 100, "同步中",
                            transferred, total, speed,
                        )
                    except Exception as e:
                        self.logger.warning(f"任务进度回调异常: {e}")

            # 执行同步（文件粒度 + 字节粒度回调）
            result = self.sync_engine.execute_sync(
                plan,
                task,
                _progress_callback if progress_cb else None,
                _byte_callback if progress_cb else None,
            )

            # 更新任务时间
            task.last_sync = datetime.now()
            task.calculate_next_sync()

            # 回调
            if task_id in self._task_callbacks:
                callback = self._task_callbacks[task_id]
                callback(task_id, result)

            self.logger.info(
                f"任务执行完成: {task.name}, "
                f"上传: {result.uploaded}, 下载: {result.downloaded}, "
                f"错误: {result.errors}"
                f"{', 已取消' if result.cancelled else ''}"
            )
            return True

        except Exception as e:
            self.logger.error(f"任务执行失败: {task.name}, {e}")
            self.finish_task(task_id, TaskStatus.ERROR)

            # 回调
            if task_id in self._task_callbacks:
                callback = self._task_callbacks[task_id]
                callback(task_id, None, str(e))
            return False

        finally:
            # 恢复本地实时监听（与开头 set_suppress(True) 对应，计数式归零）
            if watcher:
                try:
                    watcher.set_suppress(False)
                except Exception:
                    pass

            # 无论正常/异常/取消结束，都恢复任务状态并清理同步控制
            if self._task_status.get(task_id) == TaskStatus.RUNNING:
                # 正常路径未 finish（取消/异常已处理），这里兜底恢复
                # 若已被 pause_task 置为 PAUSED（用户暂停），保持 PAUSED
                self.finish_task(task_id)
            # 清理同步引擎中残留的暂停/取消控制
            try:
                self.sync_engine._clear_control(task_id)
            except Exception:
                pass

    def is_task_running(self, task_id: str) -> bool:
        """
        查询任务是否正在运行（原子化）。

        Args:
            task_id: 任务ID

        Returns:
            任务是否处于 RUNNING 状态
        """
        with self._lock:
            return self._task_status.get(task_id) == TaskStatus.RUNNING

    def _job_executed_listener(self, event):
        """任务执行监听器"""
        if event.exception:
            self.logger.error(f"任务执行异常: {event.job_id}, {event.exception}")
        else:
            self.logger.debug(f"任务执行完成: {event.job_id}")

    def shutdown(self):
        """关闭调度器"""
        # 停止实时监听
        try:
            self.file_watcher.stop()
        except Exception:
            pass

        self._scheduler.shutdown(wait=True)
        self.logger.info("调度器已关闭")

    def __del__(self):
        """析构函数"""
        try:
            self.shutdown()
        except:
            pass
