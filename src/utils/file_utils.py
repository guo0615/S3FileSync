"""
文件工具
"""
import os
import shutil
from pathlib import Path
from typing import List, Optional, Generator
from datetime import datetime


class FileUtils:
    """文件工具类"""

    @staticmethod
    def ensure_dir(path: str) -> bool:
        """
        确保目录存在，如果不存在则创建

        Args:
            path: 目录路径

        Returns:
            是否成功
        """
        try:
            os.makedirs(path, exist_ok=True)
            return True
        except Exception:
            return False

    @staticmethod
    def get_file_size(path: str) -> int:
        """
        获取文件大小

        Args:
            path: 文件路径

        Returns:
            文件大小(字节)
        """
        try:
            return os.path.getsize(path)
        except Exception:
            return 0

    @staticmethod
    def get_file_mtime(path: str) -> datetime:
        """
        获取文件修改时间

        Args:
            path: 文件路径

        Returns:
            修改时间
        """
        try:
            mtime = os.path.getmtime(path)
            return datetime.fromtimestamp(mtime)
        except Exception:
            return datetime.now()

    @staticmethod
    def get_file_mtime_utc(path: str) -> datetime:
        """
        获取文件修改时间（UTC 墙钟时间口径，naive）

        与远程 S3 LastModified（UTC 服务器时间）使用同一时间基准，
        保证本地/远程修改时间可以正确比较，避免因时区差异导致的偏差。

        Args:
            path: 文件路径

        Returns:
            UTC 口径的修改时间（naive datetime）
        """
        from datetime import timezone
        try:
            mtime = os.path.getmtime(path)
            return datetime.fromtimestamp(mtime, tz=timezone.utc).replace(tzinfo=None)
        except Exception:
            return datetime.now()

    @staticmethod
    def to_local_display(dt: datetime) -> datetime:
        """
        将 UTC 墙钟口径的 naive datetime 转换为本机时区显示用 datetime。

        后台存储/比较统一用 UTC naive 口径（远程 _to_local_naive、本地
        get_file_mtime_utc 均为 UTC naive），但界面显示需与 MinIO 控制台一致：
        MinIO 控制台按查看者浏览器时区显示 LastModified（北京时间 +8），
        所以这里把 UTC naive 当作 UTC 时间，转换到本机时区用于显示。

        仅影响显示，不影响同步比较（比较仍用 UTC naive 原值）。

        Args:
            dt: UTC naive datetime

        Returns:
            本机时区的 naive datetime（仅用于显示）
        """
        from datetime import timezone
        if dt is None:
            return None
        # 若已是 aware，直接转本地时区
        if dt.tzinfo is not None:
            return dt.astimezone().replace(tzinfo=None)
        # naive 视为 UTC，附加 tz 后转本机时区，再剥离 tzinfo
        return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)

    @staticmethod
    def list_files(
        directory: str,
        recursive: bool = True,
        include_dirs: bool = True,
        exclude_patterns: Optional[List[str]] = None
    ) -> Generator[str, None, None]:
        """
        列出目录下的所有文件

        Args:
            directory: 目录路径
            recursive: 是否递归
            include_dirs: 是否包含目录
            exclude_patterns: 排除模式列表

        Yields:
            文件路径
        """
        exclude_patterns = exclude_patterns or []

        def should_exclude(path: str) -> bool:
            """检查是否应该排除

            规则：
            - 模式含通配符（* ? [）时，用 fnmatch 对"文件名"做通配匹配，
              如 `*.tmp` 可命中 `audit.jsonl.tmp`、`xxx.tmp.tmp`；
            - 纯文本模式（如 .git、__pycache__）沿用整段子串匹配，
              用于排除目录名。
            """
            import fnmatch
            name = os.path.basename(path.rstrip('/\\'))
            for pattern in exclude_patterns:
                if any(ch in pattern for ch in '*?['):
                    # 通配符模式：对文件名匹配
                    if fnmatch.fnmatch(name, pattern):
                        return True
                else:
                    # 纯文本模式：整段子串匹配（目录名）
                    if pattern in path:
                        return True
            return False

        if recursive:
            for root, dirs, files in os.walk(directory):
                # 排除目录
                dirs[:] = [d for d in dirs if not should_exclude(d)]

                if include_dirs:
                    for d in dirs:
                        dir_path = os.path.join(root, d)
                        if not should_exclude(dir_path):
                            yield dir_path

                for f in files:
                    file_path = os.path.join(root, f)
                    if not should_exclude(file_path):
                        yield file_path
        else:
            for item in os.listdir(directory):
                item_path = os.path.join(directory, item)
                if not should_exclude(item_path):
                    if os.path.isfile(item_path):
                        yield item_path
                    elif include_dirs and os.path.isdir(item_path):
                        yield item_path

    @staticmethod
    def get_relative_path(file_path: str, base_path: str) -> str:
        """
        获取相对路径

        Args:
            file_path: 文件路径
            base_path: 基础路径

        Returns:
            相对路径
        """
        try:
            return os.path.relpath(file_path, base_path)
        except Exception:
            return file_path

    @staticmethod
    def normalize_path(path: str) -> str:
        """
        规范化路径(统一使用正斜杠)

        Args:
            path: 路径

        Returns:
            规范化后的路径
        """
        return path.replace('\\', '/')

    @staticmethod
    def delete_file(path: str) -> bool:
        """
        删除文件

        Args:
            path: 文件路径

        Returns:
            是否成功
        """
        try:
            if os.path.isfile(path):
                os.remove(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)
            return True
        except Exception:
            return False

    @staticmethod
    def copy_file(src: str, dst: str) -> bool:
        """
        复制文件

        Args:
            src: 源文件路径
            dst: 目标文件路径

        Returns:
            是否成功
        """
        try:
            # 确保目标目录存在
            dst_dir = os.path.dirname(dst)
            if dst_dir:
                os.makedirs(dst_dir, exist_ok=True)

            shutil.copy2(src, dst)
            return True
        except Exception:
            return False

    @staticmethod
    def move_file(src: str, dst: str) -> bool:
        """
        移动文件

        Args:
            src: 源文件路径
            dst: 目标文件路径

        Returns:
            是否成功
        """
        try:
            # 确保目标目录存在
            dst_dir = os.path.dirname(dst)
            if dst_dir:
                os.makedirs(dst_dir, exist_ok=True)

            shutil.move(src, dst)
            return True
        except Exception:
            return False

    @staticmethod
    def create_temp_file(prefix: str = "s3_sync_", suffix: str = ".tmp") -> str:
        """
        创建临时文件

        Args:
            prefix: 文件名前缀
            suffix: 文件名后缀

        Returns:
            临时文件路径
        """
        import tempfile
        fd, path = tempfile.mkstemp(suffix=suffix, prefix=prefix)
        os.close(fd)
        return path

    @staticmethod
    def create_temp_dir(prefix: str = "s3_sync_") -> str:
        """
        创建临时目录

        Args:
            prefix: 目录名前缀

        Returns:
            临时目录路径
        """
        import tempfile
        return tempfile.mkdtemp(prefix=prefix)

    @staticmethod
    def format_size(size: int) -> str:
        """
        格式化文件大小

        Args:
            size: 文件大小(字节)

        Returns:
            格式化后的字符串
        """
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return f"{size:.2f} PB"

    @staticmethod
    def format_speed(speed: float) -> str:
        """
        格式化传输速度

        Args:
            speed: 速度(字节/秒)

        Returns:
            格式化后的字符串
        """
        return FileUtils.format_size(int(speed)) + "/s"
