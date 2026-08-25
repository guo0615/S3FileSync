"""
传输管理器
管理文件传输过程，实现断点续传
"""
import os
import json
import time
from typing import Optional, Callable, Dict, Any, List
from datetime import datetime
from dataclasses import dataclass, asdict
import logging
import threading

from .s3_client import S3Client
from ..models.transfer_state import TransferState, TransferStatus
from ..utils.file_utils import FileUtils


@dataclass
class PartInfo:
    """分片信息"""
    part_number: int
    etag: str
    size: int


@dataclass
class TransferCheckpoint:
    """传输检查点"""
    transfer_id: str
    file_path: str
    file_size: int
    transferred: int
    upload_id: Optional[str] = None  # S3上传ID(仅上传)
    parts: List[Dict] = None  # 已完成分片(仅上传)
    temp_file: Optional[str] = None  # 临时文件路径(仅下载)
    timestamp: str = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now().isoformat()
        if self.parts is None:
            self.parts = []


class TransferManager:
    """传输管理器"""

    def __init__(
        self,
        s3_client: S3Client,
        chunk_size: int = 8388608,  # 8MB
        logger: Optional[logging.Logger] = None
    ):
        """
        初始化传输管理器

        Args:
            s3_client: S3客户端
            chunk_size: 分片大小
            logger: 日志记录器
        """
        self.s3_client = s3_client
        self.chunk_size = chunk_size
        self.logger = logger or logging.getLogger(__name__)

        # 传输状态
        self._transfers: Dict[str, TransferState] = {}
        self._checkpoints: Dict[str, TransferCheckpoint] = {}
        self._locks: Dict[str, threading.Lock] = {}

        # 检查点目录
        self.checkpoint_dir = os.path.join(
            os.path.expanduser("~"),
            ".s3-sync-tool",
            "checkpoints"
        )
        os.makedirs(self.checkpoint_dir, exist_ok=True)

    def upload_with_resume(
        self,
        local_path: str,
        remote_key: str,
        callback: Optional[Callable[[int, int], None]] = None,
        extra_args: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        上传文件(支持断点续传)

        Args:
            local_path: 本地文件路径
            remote_key: 远程键
            callback: 进度回调
            extra_args: 额外参数

        Returns:
            是否成功
        """
        transfer_id = f"upload_{remote_key.replace('/', '_')}_{int(time.time())}"
        file_size = os.path.getsize(local_path)

        # 创建传输状态
        state = TransferState(
            transfer_id=transfer_id,
            file_path=local_path,
            total_size=file_size,
            status=TransferStatus.RUNNING,
            start_time=datetime.now()
        )
        self._transfers[transfer_id] = state
        self._locks[transfer_id] = threading.Lock()

        try:
            # 检查是否有检查点
            checkpoint = self._load_checkpoint(transfer_id)

            if checkpoint and checkpoint.file_path == local_path:
                # 从检查点恢复
                self.logger.info(f"从检查点恢复上传: {local_path}")
                return self._resume_upload(
                    checkpoint,
                    remote_key,
                    callback,
                    extra_args
                )
            else:
                # 新上传
                return self._start_upload(
                    transfer_id,
                    local_path,
                    remote_key,
                    callback,
                    extra_args
                )

        except Exception as e:
            self.logger.error(f"上传失败: {e}")
            state.status = TransferStatus.FAILED
            state.error = str(e)
            state.end_time = datetime.now()
            return False
        finally:
            # 清理
            if transfer_id in self._locks:
                del self._locks[transfer_id]

    def _start_upload(
        self,
        transfer_id: str,
        local_path: str,
        remote_key: str,
        callback: Optional[Callable[[int, int], None]],
        extra_args: Optional[Dict[str, Any]]
    ) -> bool:
        """开始新的上传"""
        state = self._transfers[transfer_id]
        file_size = state.total_size

        try:
            # 创建分片上传
            response = self.s3_client.client.create_multipart_upload(
                Bucket=self.s3_client.config.bucket,
                Key=remote_key,
                **(extra_args or {})
            )
            upload_id = response['UploadId']

            # 创建检查点
            checkpoint = TransferCheckpoint(
                transfer_id=transfer_id,
                file_path=local_path,
                file_size=file_size,
                transferred=0,
                upload_id=upload_id
            )
            self._checkpoints[transfer_id] = checkpoint

            # 分片上传
            parts = []
            part_number = 1
            offset = 0

            with open(local_path, 'rb') as f:
                while offset < file_size:
                    # 检查是否暂停
                    if state.status == TransferStatus.PAUSED:
                        self._save_checkpoint(checkpoint)
                        return False

                    # 读取分片
                    chunk = f.read(self.chunk_size)
                    if not chunk:
                        break

                    # 上传分片
                    part_response = self.s3_client.client.upload_part(
                        Bucket=self.s3_client.config.bucket,
                        Key=remote_key,
                        PartNumber=part_number,
                        UploadId=upload_id,
                        Body=chunk
                    )

                    # 记录分片信息
                    parts.append({
                        'PartNumber': part_number,
                        'ETag': part_response['ETag']
                    })

                    # 更新进度
                    offset += len(chunk)
                    state.update_progress(offset)
                    checkpoint.transferred = offset
                    checkpoint.parts = parts

                    # 回调
                    if callback:
                        callback(offset, file_size)

                    # 定期保存检查点
                    if part_number % 10 == 0:
                        self._save_checkpoint(checkpoint)

                    part_number += 1

            # 完成分片上传
            self.s3_client.client.complete_multipart_upload(
                Bucket=self.s3_client.config.bucket,
                Key=remote_key,
                UploadId=upload_id,
                MultipartUpload={'Parts': parts}
            )

            # 更新状态
            state.status = TransferStatus.COMPLETED
            state.end_time = datetime.now()

            # 删除检查点
            self._delete_checkpoint(transfer_id)

            self.logger.info(f"上传完成: {local_path} -> {remote_key}")
            return True

        except Exception as e:
            self.logger.error(f"上传失败: {e}")
            state.status = TransferStatus.FAILED
            state.error = str(e)
            state.end_time = datetime.now()
            self._save_checkpoint(self._checkpoints.get(transfer_id))
            return False

    def _resume_upload(
        self,
        checkpoint: TransferCheckpoint,
        remote_key: str,
        callback: Optional[Callable[[int, int], None]],
        extra_args: Optional[Dict[str, Any]]
    ) -> bool:
        """从检查点恢复上传"""
        transfer_id = checkpoint.transfer_id
        state = self._transfers[transfer_id]
        upload_id = checkpoint.upload_id

        try:
            # 列出已上传的分片
            response = self.s3_client.client.list_parts(
                Bucket=self.s3_client.config.bucket,
                Key=remote_key,
                UploadId=upload_id
            )

            existing_parts = {
                part['PartNumber']: part['ETag']
                for part in response.get('Parts', [])
            }

            # 继续上传剩余分片
            parts = checkpoint.parts
            part_number = len(parts) + 1
            offset = checkpoint.transferred

            with open(checkpoint.file_path, 'rb') as f:
                f.seek(offset)

                while offset < checkpoint.file_size:
                    # 检查是否暂停
                    if state.status == TransferStatus.PAUSED:
                        self._save_checkpoint(checkpoint)
                        return False

                    # 读取分片
                    chunk = f.read(self.chunk_size)
                    if not chunk:
                        break

                    # 上传分片
                    part_response = self.s3_client.client.upload_part(
                        Bucket=self.s3_client.config.bucket,
                        Key=remote_key,
                        PartNumber=part_number,
                        UploadId=upload_id,
                        Body=chunk
                    )

                    # 记录分片信息
                    parts.append({
                        'PartNumber': part_number,
                        'ETag': part_response['ETag']
                    })

                    # 更新进度
                    offset += len(chunk)
                    state.update_progress(offset)
                    checkpoint.transferred = offset
                    checkpoint.parts = parts

                    # 回调
                    if callback:
                        callback(offset, checkpoint.file_size)

                    # 定期保存检查点
                    if part_number % 10 == 0:
                        self._save_checkpoint(checkpoint)

                    part_number += 1

            # 完成分片上传
            self.s3_client.client.complete_multipart_upload(
                Bucket=self.s3_client.config.bucket,
                Key=remote_key,
                UploadId=upload_id,
                MultipartUpload={'Parts': parts}
            )

            # 更新状态
            state.status = TransferStatus.COMPLETED
            state.end_time = datetime.now()

            # 删除检查点
            self._delete_checkpoint(transfer_id)

            self.logger.info(f"恢复上传完成: {checkpoint.file_path} -> {remote_key}")
            return True

        except Exception as e:
            self.logger.error(f"恢复上传失败: {e}")
            state.status = TransferStatus.FAILED
            state.error = str(e)
            state.end_time = datetime.now()
            self._save_checkpoint(checkpoint)
            return False

    def download_with_resume(
        self,
        remote_key: str,
        local_path: str,
        callback: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """
        下载文件(支持断点续传)

        Args:
            remote_key: 远程键
            local_path: 本地文件路径
            callback: 进度回调

        Returns:
            是否成功
        """
        transfer_id = f"download_{remote_key.replace('/', '_')}_{int(time.time())}"

        # 获取文件大小
        metadata = self.s3_client.get_file_metadata(remote_key)
        if not metadata:
            return False

        file_size = metadata['size']

        # 创建传输状态
        state = TransferState(
            transfer_id=transfer_id,
            file_path=local_path,
            total_size=file_size,
            status=TransferStatus.RUNNING,
            start_time=datetime.now()
        )
        self._transfers[transfer_id] = state
        self._locks[transfer_id] = threading.Lock()

        try:
            # 检查是否有检查点
            checkpoint = self._load_checkpoint(transfer_id)

            if checkpoint and checkpoint.file_path == local_path:
                # 从检查点恢复
                self.logger.info(f"从检查点恢复下载: {local_path}")
                return self._resume_download(
                    checkpoint,
                    remote_key,
                    callback
                )
            else:
                # 新下载
                return self._start_download(
                    transfer_id,
                    remote_key,
                    local_path,
                    file_size,
                    callback
                )

        except Exception as e:
            self.logger.error(f"下载失败: {e}")
            state.status = TransferStatus.FAILED
            state.error = str(e)
            state.end_time = datetime.now()
            return False
        finally:
            # 清理
            if transfer_id in self._locks:
                del self._locks[transfer_id]

    def _finalize_download(self, temp_file: str, target_path: str, remote_key: str) -> bool:
        """下载完成后的落盘：把临时文件替换为目标文件

        Windows 上目标文件若被其他进程以写方式打开（如持续写入的日志文件、
        或本工具并发上传的句柄），os.replace 会抛 WinError 5 拒绝访问。
        这里做多次重试；仍失败时静默跳过本次落盘并清理临时文件，
        下次同步会重新下载，不让同步因此报错刷屏。

        Args:
            temp_file: 已下载完成的临时文件
            target_path: 目标文件路径
            remote_key: 远程键（仅用于日志）

        Returns:
            是否成功落盘（False 表示目标被占用，本次跳过）
        """
        import time as _t
        last_exc = None
        # 重试几次，等待文件占用释放。os.replace 本身可覆盖已存在文件，
        # 不要先删除目标（若删除后替换失败会导致目标文件丢失）。
        for attempt in range(5):
            try:
                os.replace(temp_file, target_path)
                return True
            except OSError as e:
                last_exc = e
                self.logger.debug(
                    f"替换下载文件第{attempt + 1}次失败（目标可能被占用）: "
                    f"{temp_file} -> {target_path}, {e}"
                )
                _t.sleep(0.5)

        # 多次重试仍失败：目标文件持续被占用（如活跃日志文件被外部程序打开），
        # 静默跳过本次，保留 tmp 供下次同步重试。不抛异常不上报，避免刷屏。
        self.logger.warning(
            f"下载文件落盘跳过（目标文件持续被占用，将在下次同步重试）: "
            f"{target_path}，错误: {last_exc}"
        )
        # 删除不完整的临时文件，避免残留占用
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except OSError:
            pass
        return False

    def _start_download(
        self,
        transfer_id: str,
        remote_key: str,
        local_path: str,
        file_size: int,
        callback: Optional[Callable[[int, int], None]]
    ) -> bool:
        """开始新的下载"""
        state = self._transfers[transfer_id]

        # 确保目录存在
        local_dir = os.path.dirname(local_path)
        if local_dir:
            os.makedirs(local_dir, exist_ok=True)

        # 创建临时文件
        temp_file = local_path + ".tmp"

        try:
            # 创建检查点
            checkpoint = TransferCheckpoint(
                transfer_id=transfer_id,
                file_path=local_path,
                file_size=file_size,
                transferred=0,
                temp_file=temp_file
            )
            self._checkpoints[transfer_id] = checkpoint

            # 分片下载
            offset = 0

            with open(temp_file, 'wb') as f:
                while offset < file_size:
                    # 检查是否暂停
                    if state.status == TransferStatus.PAUSED:
                        self._save_checkpoint(checkpoint)
                        return False

                    # 计算范围
                    end = min(offset + self.chunk_size, file_size)
                    range_header = f"bytes={offset}-{end-1}"

                    # 下载分片
                    response = self.s3_client.client.get_object(
                        Bucket=self.s3_client.config.bucket,
                        Key=remote_key,
                        Range=range_header
                    )

                    # 写入文件
                    chunk = response['Body'].read()
                    f.write(chunk)

                    # 更新进度
                    offset += len(chunk)
                    state.update_progress(offset)
                    checkpoint.transferred = offset

                    # 回调
                    if callback:
                        callback(offset, file_size)

                    # 定期保存检查点
                    if (offset // self.chunk_size) % 10 == 0:
                        self._save_checkpoint(checkpoint)

            # 重命名临时文件（含重试与占用兜底：目标文件被占用时静默跳过）
            if not self._finalize_download(temp_file, local_path, remote_key):
                # 目标被占用，本次静默跳过：清理状态与检查点，不报错
                state.status = TransferStatus.COMPLETED
                state.end_time = datetime.now()
                self._delete_checkpoint(transfer_id)
                return True

            # 更新状态
            state.status = TransferStatus.COMPLETED
            state.end_time = datetime.now()

            # 删除检查点
            self._delete_checkpoint(transfer_id)

            self.logger.info(f"下载完成: {remote_key} -> {local_path}")
            return True

        except Exception as e:
            self.logger.error(f"下载失败: {e}")
            state.status = TransferStatus.FAILED
            state.error = str(e)
            state.end_time = datetime.now()
            self._save_checkpoint(checkpoint)
            return False

    def _resume_download(
        self,
        checkpoint: TransferCheckpoint,
        remote_key: str,
        callback: Optional[Callable[[int, int], None]]
    ) -> bool:
        """从检查点恢复下载"""
        transfer_id = checkpoint.transfer_id
        state = self._transfers[transfer_id]
        temp_file = checkpoint.temp_file

        try:
            # 检查临时文件是否存在
            if not os.path.exists(temp_file):
                self.logger.error(f"临时文件不存在: {temp_file}")
                return False

            # 继续下载
            offset = checkpoint.transferred

            with open(temp_file, 'ab') as f:
                while offset < checkpoint.file_size:
                    # 检查是否暂停
                    if state.status == TransferStatus.PAUSED:
                        self._save_checkpoint(checkpoint)
                        return False

                    # 计算范围
                    end = min(offset + self.chunk_size, checkpoint.file_size)
                    range_header = f"bytes={offset}-{end-1}"

                    # 下载分片
                    response = self.s3_client.client.get_object(
                        Bucket=self.s3_client.config.bucket,
                        Key=remote_key,
                        Range=range_header
                    )

                    # 写入文件
                    chunk = response['Body'].read()
                    f.write(chunk)

                    # 更新进度
                    offset += len(chunk)
                    state.update_progress(offset)
                    checkpoint.transferred = offset

                    # 回调
                    if callback:
                        callback(offset, checkpoint.file_size)

                    # 定期保存检查点
                    if (offset // self.chunk_size) % 10 == 0:
                        self._save_checkpoint(checkpoint)

            # 重命名临时文件（含重试与占用兜底：目标文件被占用时静默跳过）
            if not self._finalize_download(temp_file, checkpoint.file_path, remote_key):
                # 目标被占用，本次静默跳过：清理状态与检查点，不报错
                state.status = TransferStatus.COMPLETED
                state.end_time = datetime.now()
                self._delete_checkpoint(transfer_id)
                return True

            # 更新状态
            state.status = TransferStatus.COMPLETED
            state.end_time = datetime.now()

            # 删除检查点
            self._delete_checkpoint(transfer_id)

            self.logger.info(f"恢复下载完成: {remote_key} -> {checkpoint.file_path}")
            return True

        except Exception as e:
            self.logger.error(f"恢复下载失败: {e}")
            state.status = TransferStatus.FAILED
            state.error = str(e)
            state.end_time = datetime.now()
            self._save_checkpoint(checkpoint)
            return False

    def pause_transfer(self, transfer_id: str) -> bool:
        """
        暂停传输

        Args:
            transfer_id: 传输ID

        Returns:
            是否成功
        """
        if transfer_id not in self._transfers:
            return False

        state = self._transfers[transfer_id]
        state.status = TransferStatus.PAUSED

        # 保存检查点
        if transfer_id in self._checkpoints:
            self._save_checkpoint(self._checkpoints[transfer_id])

        self.logger.info(f"传输已暂停: {transfer_id}")
        return True

    def resume_transfer(self, transfer_id: str) -> bool:
        """
        恢复传输

        Args:
            transfer_id: 传输ID

        Returns:
            是否成功
        """
        if transfer_id not in self._transfers:
            return False

        state = self._transfers[transfer_id]
        state.status = TransferStatus.RUNNING

        self.logger.info(f"传输已恢复: {transfer_id}")
        return True

    def cancel_transfer(self, transfer_id: str) -> bool:
        """
        取消传输

        Args:
            transfer_id: 传输ID

        Returns:
            是否成功
        """
        if transfer_id not in self._transfers:
            return False

        state = self._transfers[transfer_id]
        state.status = TransferStatus.CANCELLED
        state.end_time = datetime.now()

        # 删除检查点
        self._delete_checkpoint(transfer_id)

        # 删除临时文件
        if transfer_id in self._checkpoints:
            checkpoint = self._checkpoints[transfer_id]
            if checkpoint.temp_file and os.path.exists(checkpoint.temp_file):
                os.remove(checkpoint.temp_file)

        self.logger.info(f"传输已取消: {transfer_id}")
        return True

    def get_transfer_state(self, transfer_id: str) -> Optional[TransferState]:
        """
        获取传输状态

        Args:
            transfer_id: 传输ID

        Returns:
            传输状态
        """
        return self._transfers.get(transfer_id)

    def _save_checkpoint(self, checkpoint: TransferCheckpoint):
        """保存检查点"""
        if not checkpoint:
            return

        try:
            checkpoint_file = os.path.join(
                self.checkpoint_dir,
                f"{checkpoint.transfer_id}.json"
            )

            with open(checkpoint_file, 'w') as f:
                json.dump(asdict(checkpoint), f, indent=2)

            self.logger.debug(f"检查点已保存: {checkpoint.transfer_id}")

        except Exception as e:
            self.logger.error(f"保存检查点失败: {e}")

    def _load_checkpoint(self, transfer_id: str) -> Optional[TransferCheckpoint]:
        """加载检查点"""
        try:
            checkpoint_file = os.path.join(
                self.checkpoint_dir,
                f"{transfer_id}.json"
            )

            if not os.path.exists(checkpoint_file):
                return None

            with open(checkpoint_file, 'r') as f:
                data = json.load(f)

            checkpoint = TransferCheckpoint(**data)
            self._checkpoints[transfer_id] = checkpoint
            return checkpoint

        except Exception as e:
            self.logger.error(f"加载检查点失败: {e}")
            return None

    def _delete_checkpoint(self, transfer_id: str):
        """删除检查点"""
        try:
            checkpoint_file = os.path.join(
                self.checkpoint_dir,
                f"{transfer_id}.json"
            )

            if os.path.exists(checkpoint_file):
                os.remove(checkpoint_file)

            if transfer_id in self._checkpoints:
                del self._checkpoints[transfer_id]

            self.logger.debug(f"检查点已删除: {transfer_id}")

        except Exception as e:
            self.logger.error(f"删除检查点失败: {e}")
