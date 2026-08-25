"""
S3客户端模块
封装boto3 S3操作，提供统一的文件传输接口
"""
import os
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from botocore.client import Config
from typing import List, Optional, Dict, Any, Callable
from datetime import datetime
import logging

from ..utils.config_manager import S3Config
from ..models.file_info import FileInfo
from ..utils.hash_utils import HashUtils
from ..utils.file_utils import FileUtils


class S3Client:
    """S3客户端"""

    def __init__(self, config: S3Config, logger: Optional[logging.Logger] = None):
        """
        初始化S3客户端

        Args:
            config: S3配置
            logger: 日志记录器
        """
        self.config = config
        self.logger = logger or logging.getLogger(__name__)
        self._client = None
        self._resource = None
        self._connected = False
        self._initialized = False

        # 最近一次操作的错误信息（供调用方在收到 False 返回时获取失败原因）
        # 形如: {"op": "upload", "path": "remote_key", "error": "..."}
        self.last_error: Optional[Dict[str, Any]] = None

        # 延迟初始化，不立即创建客户端

    def _record_error(self, op: str, path: str, error: Any) -> None:
        """记录最近一次操作失败的原因，便于上层显示给用户"""
        self.last_error = {
            "op": op,
            "path": path,
            "error": str(error),
            "type": type(error).__name__,
        }

    def _ensure_initialized(self):
        """确保客户端已初始化"""
        if not self._initialized:
            self._init_client()
            self._initialized = True

    def _init_client(self):
        """初始化S3客户端"""
        try:
            # 创建S3客户端（使用连接池优化）
            self._client = boto3.client(
                's3',
                endpoint_url=self.config.endpoint,
                aws_access_key_id=self.config.access_key,
                aws_secret_access_key=self.config.secret_key,
                region_name=self.config.region,
                config=Config(
                    signature_version='s3v4',
                    s3={'addressing_style': 'path'},
                    max_pool_connections=50,  # 增加连接池大小
                    retries={'max_attempts': 3}  # 自动重试
                ),
                use_ssl=self.config.use_ssl,
                verify=self.config.verify_ssl
            )

            # 创建S3资源
            self._resource = boto3.resource(
                's3',
                endpoint_url=self.config.endpoint,
                aws_access_key_id=self.config.access_key,
                aws_secret_access_key=self.config.secret_key,
                region_name=self.config.region,
                config=Config(
                    signature_version='s3v4',
                    s3={'addressing_style': 'path'},
                    max_pool_connections=50
                ),
                use_ssl=self.config.use_ssl,
                verify=self.config.verify_ssl
            )

            self._connected = True
            self.logger.info(f"S3客户端初始化成功: {self.config.endpoint}")

        except Exception as e:
            self.logger.error(f"S3客户端初始化失败: {e}")
            self._connected = False

    def test_connection(self) -> bool:
        """
        测试S3连接

        Returns:
            是否连接成功
        """
        self._ensure_initialized()
        try:
            # 尝试列出存储桶
            self._client.head_bucket(Bucket=self.config.bucket)
            self.logger.info(f"S3连接测试成功: {self.config.bucket}")
            return True
        except ClientError as e:
            error_code = int(e.response.get('Error', {}).get('Code', 500))
            if error_code == 404:
                self.logger.error(f"存储桶不存在: {self.config.bucket}")
            else:
                self.logger.error(f"S3连接测试失败: {e}")
            return False
        except NoCredentialsError:
            self.logger.error("S3凭证无效")
            return False
        except Exception as e:
            self.logger.error(f"S3连接测试异常: {e}")
            return False

    def create_bucket(self, bucket_name: Optional[str] = None) -> bool:
        """
        创建存储桶

        Args:
            bucket_name: 存储桶名称，如果为None则使用配置中的名称

        Returns:
            是否成功
        """
        bucket = bucket_name or self.config.bucket
        try:
            self._client.create_bucket(Bucket=bucket)
            self.logger.info(f"创建存储桶成功: {bucket}")
            return True
        except ClientError as e:
            self.logger.error(f"创建存储桶失败: {e}")
            return False

    @staticmethod
    def _to_local_naive(dt):
        """
        将 aware datetime（如 boto3 返回的 UTC LastModified）转换为 naive datetime，
        并统一以 UTC 墙钟时间显示，与远程文件服务器的文件时间保持一致。

        说明：
        - S3 的 LastModified 是 UTC 存储、在对象头中即为 UTC 墙钟时间（绝大多数自建
          S3/MinIO 服务器也运行在 UTC）。若换算成客户端本地时区（如北京 +8），显示会
          比服务器文件时间差 8 小时。
        - 这里不换算时区，仅剥离 tzinfo，保留 UTC 墙钟时间用于界面显示。
        - 同步比对时，本地 mtime 会通过 FileUtils.get_file_mtime_utc() 转为同一 UTC
          口径（见 sync_engine），保证本地/远程在同一个时间基准下比较。
        """
        if dt is None:
            return None
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        if dt.tzinfo is not None:
            # 仅剥离时区，保留原墙钟时间（UTC 服务器时间）
            return dt.replace(tzinfo=None)
        return dt

    def list_files(self, prefix: str = '', delimiter: str = '/') -> List[FileInfo]:
        """
        列出远程文件（单层，含子目录占位）

        用于文件浏览器等需要逐级浏览的场景：返回当前层级的文件和子目录。

        Args:
            prefix: 前缀
            delimiter: 分隔符

        Returns:
            文件信息列表
        """
        self._ensure_initialized()
        files = []

        # 规范化前缀：以 / 结尾的前缀才会被S3当作"目录"，列出其中的内容；
        # 否则如 prefix="444" 会把整个 "444/" 折叠成单个CommonPrefix，看不到里面的文件。
        if prefix and not prefix.endswith('/'):
            prefix = prefix + '/'

        try:
            paginator = self._client.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(
                Bucket=self.config.bucket,
                Prefix=prefix,
                Delimiter=delimiter
            )

            for page in page_iterator:
                # 处理文件
                for obj in page.get('Contents', []):
                    file_info = FileInfo(
                        path=obj['Key'],
                        size=obj['Size'],
                        mtime=self._to_local_naive(obj['LastModified']),
                        hash=obj.get('ETag', '').strip('"'),
                        is_dir=False,
                        metadata=obj.get('Metadata', {})
                    )
                    files.append(file_info)

                # 处理目录
                for prefix_obj in page.get('CommonPrefixes', []):
                    dir_info = FileInfo(
                        path=prefix_obj['Prefix'],
                        size=0,
                        mtime=datetime.now(),
                        is_dir=True
                    )
                    files.append(dir_info)

            self.logger.info(f"列出文件成功: {prefix}, 共{len(files)}个文件")
            return files

        except ClientError as e:
            self.logger.error(f"列出文件失败: {e}")
            return []

    def list_files_recursive(self, prefix: str = '', delimiter: str = '/') -> List[FileInfo]:
        """
        递归列出远程文件（含所有子目录下的文件）

        用于同步引擎的完整扫描比对：子目录内容不会因 Delimiter 折叠而丢失。

        Args:
            prefix: 前缀
            delimiter: 分隔符

        Returns:
            文件信息列表（仅文件，不含目录占位）
        """
        self._ensure_initialized()
        files = []

        # 规范化前缀
        if prefix and not prefix.endswith('/'):
            prefix = prefix + '/'

        try:
            paginator = self._client.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(
                Bucket=self.config.bucket,
                Prefix=prefix,
                Delimiter=delimiter
            )

            sub_dirs = []

            for page in page_iterator:
                # 处理文件
                for obj in page.get('Contents', []):
                    file_info = FileInfo(
                        path=obj['Key'],
                        size=obj['Size'],
                        mtime=self._to_local_naive(obj['LastModified']),
                        hash=obj.get('ETag', '').strip('"'),
                        is_dir=False,
                        metadata=obj.get('Metadata', {})
                    )
                    files.append(file_info)

                # 收集子目录，稍后递归
                for prefix_obj in page.get('CommonPrefixes', []):
                    sub_dirs.append(prefix_obj['Prefix'])

            # 递归列出所有子目录
            for sub_dir in sub_dirs:
                try:
                    sub_files = self.list_files_recursive(prefix=sub_dir, delimiter=delimiter)
                    files.extend(sub_files)
                except Exception as e:
                    self.logger.warning(f"递归列出子目录失败: {sub_dir}, {e}")

            self.logger.info(f"递归列出文件成功: {prefix}, 共{len(files)}个文件")
            return files

        except ClientError as e:
            self.logger.error(f"递归列出文件失败: {e}")
            return []

    def upload_file(
        self,
        local_path: str,
        remote_key: str,
        callback: Optional[Callable[[int, int], None]] = None,
        metadata: Optional[Dict[str, str]] = None
    ) -> bool:
        """
        上传文件

        Args:
            local_path: 本地文件路径
            remote_key: 远程键
            callback: 进度回调函数 callback(transferred, total)
            metadata: 元数据

        Returns:
            是否成功
        """
        try:
            file_size = os.path.getsize(local_path)
            extra_args = {}

            if metadata:
                extra_args['Metadata'] = metadata

            # 使用分片上传以支持大文件和进度回调
            if file_size > 8 * 1024 * 1024:  # 大于8MB使用分片上传
                return self._upload_multipart(local_path, remote_key, callback, extra_args)
            else:
                return self._upload_simple(local_path, remote_key, callback, extra_args)

        except Exception as e:
            self.logger.error(f"上传文件失败: {local_path} -> {remote_key}, {e}")
            self._record_error("upload", f"{local_path} -> {remote_key}", e)
            return False

    @staticmethod
    def _boto_callback_adapter(
        callback: Optional[Callable[[int, int], None]],
        total: int
    ):
        """把双参数进度回调 callback(transferred, total) 适配为 boto3 单参数 Callback(bytes)

        boto3 的简单上传/下载（upload_file/download_file）会自动生成
        S3TransferConfig，其 Callback 约定为单参数 Callback(int)，仅接收
        已传输的累计字节数。而本项目的进度契约统一为双参数
        callback(transferred, total)。这里做一层适配，保持接口一致。
        """
        if callback is None:
            return None

        def _adapter(bytes_transferred: int) -> None:
            callback(int(bytes_transferred), total)

        return _adapter

    def _upload_simple(
        self,
        local_path: str,
        remote_key: str,
        callback: Optional[Callable[[int, int], None]],
        extra_args: Dict[str, Any]
    ) -> bool:
        """简单上传(小文件)"""
        try:
            file_size = os.path.getsize(local_path)
            self._client.upload_file(
                local_path,
                self.config.bucket,
                remote_key,
                ExtraArgs=extra_args,
                # boto3 Callback 是单参数(bytes)，适配为项目统一的双参数契约
                Callback=self._boto_callback_adapter(callback, file_size)
            )
            self.logger.info(f"上传文件成功: {local_path} -> {remote_key}")
            return True
        except Exception as e:
            self.logger.error(f"简单上传失败: {e}")
            self._record_error("upload", f"{local_path} -> {remote_key}", e)
            return False

    def _upload_multipart(
        self,
        local_path: str,
        remote_key: str,
        callback: Optional[Callable[[int, int], None]],
        extra_args: Dict[str, Any]
    ) -> bool:
        """分片上传(大文件)"""
        try:
            from .transfer_manager import TransferManager
            manager = TransferManager(self)
            result = manager.upload_with_resume(
                local_path,
                remote_key,
                callback,
                extra_args
            )
            return result
        except Exception as e:
            self.logger.error(f"分片上传失败: {e}")
            self._record_error("upload", f"{local_path} -> {remote_key}", e)
            return False

    def download_file(
        self,
        remote_key: str,
        local_path: str,
        callback: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """
        下载文件

        Args:
            remote_key: 远程键
            local_path: 本地文件路径
            callback: 进度回调函数 callback(transferred, total)

        Returns:
            是否成功
        """
        try:
            # 确保本地目录存在
            local_dir = os.path.dirname(local_path)
            if local_dir:
                os.makedirs(local_dir, exist_ok=True)

            # 获取文件大小
            response = self._client.head_object(
                Bucket=self.config.bucket,
                Key=remote_key
            )
            file_size = response['ContentLength']

            # 大文件使用分片下载
            if file_size > 8 * 1024 * 1024:
                return self._download_multipart(remote_key, local_path, callback)
            else:
                return self._download_simple(remote_key, local_path, callback, file_size)

        except Exception as e:
            self.logger.error(f"下载文件失败: {remote_key} -> {local_path}, {e}")
            self._record_error("download", f"{remote_key} -> {local_path}", e)
            return False

    def _download_simple(
        self,
        remote_key: str,
        local_path: str,
        callback: Optional[Callable[[int, int], None]],
        file_size: int = 0
    ) -> bool:
        """简单下载(小文件)

        Args:
            remote_key: 远程键
            local_path: 本地路径
            callback: 双参数进度回调 callback(transferred, total)
            file_size: 文件大小（由 download_file 传入，供进度回调 total 使用）
        """
        try:
            self._client.download_file(
                self.config.bucket,
                remote_key,
                local_path,
                # boto3 Callback 是单参数(bytes)，适配为项目统一的双参数契约
                Callback=self._boto_callback_adapter(callback, file_size)
            )
            self.logger.info(f"下载文件成功: {remote_key} -> {local_path}")
            return True
        except Exception as e:
            self.logger.error(f"简单下载失败: {e}")
            self._record_error("download", f"{remote_key} -> {local_path}", e)
            return False

    def _download_multipart(
        self,
        remote_key: str,
        local_path: str,
        callback: Optional[Callable[[int, int], None]]
    ) -> bool:
        """分片下载(大文件)"""
        try:
            from .transfer_manager import TransferManager
            manager = TransferManager(self)
            result = manager.download_with_resume(
                remote_key,
                local_path,
                callback
            )
            return result
        except Exception as e:
            self.logger.error(f"分片下载失败: {e}")
            self._record_error("download", f"{remote_key} -> {local_path}", e)
            return False

    def delete_file(self, remote_key: str) -> bool:
        """
        删除文件

        Args:
            remote_key: 远程键

        Returns:
            是否成功
        """
        try:
            self._client.delete_object(
                Bucket=self.config.bucket,
                Key=remote_key
            )
            self.logger.info(f"删除文件成功: {remote_key}")
            return True
        except ClientError as e:
            self.logger.error(f"删除文件失败: {e}")
            self._record_error("delete_remote", remote_key, e)
            return False
        except Exception as e:
            self.logger.error(f"删除文件失败: {remote_key}, {e}")
            self._record_error("delete_remote", remote_key, e)
            return False

    def delete_files(self, remote_keys: List[str]) -> bool:
        """
        批量删除文件

        Args:
            remote_keys: 远程键列表

        Returns:
            是否成功
        """
        try:
            objects = [{'Key': key} for key in remote_keys]
            self._client.delete_objects(
                Bucket=self.config.bucket,
                Delete={'Objects': objects}
            )
            self.logger.info(f"批量删除文件成功: {len(remote_keys)}个文件")
            return True
        except ClientError as e:
            self.logger.error(f"批量删除文件失败: {e}")
            return False

    def delete_prefix(self, remote_prefix: str) -> bool:
        """
        递归删除指定前缀下的所有对象（用于删除整棵远程目录）

        Args:
            remote_prefix: 远程前缀

        Returns:
            是否成功
        """
        try:
            keys = []
            # 收集前缀下所有对象
            paginator = self._client.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(
                Bucket=self.config.bucket,
                Prefix=remote_prefix
            )
            for page in page_iterator:
                for obj in page.get('Contents', []):
                    keys.append(obj['Key'])

            if not keys:
                self.logger.info(f"前缀下无对象可删除: {remote_prefix}")
                return True

            # 每批最多1000个
            for i in range(0, len(keys), 1000):
                batch = keys[i:i + 1000]
                self._client.delete_objects(
                    Bucket=self.config.bucket,
                    Delete={'Objects': [{'Key': k} for k in batch]}
                )

            self.logger.info(f"递归删除前缀成功: {remote_prefix}, 共{len(keys)}个对象")
            return True
        except ClientError as e:
            self.logger.error(f"递归删除前缀失败: {remote_prefix}, {e}")
            return False

    def get_file_metadata(self, remote_key: str) -> Optional[Dict[str, Any]]:
        """
        获取文件元数据

        Args:
            remote_key: 远程键

        Returns:
            元数据字典
        """
        try:
            response = self._client.head_object(
                Bucket=self.config.bucket,
                Key=remote_key
            )
            return {
                'size': response['ContentLength'],
                'last_modified': response['LastModified'],
                'etag': response['ETag'].strip('"'),
                'content_type': response.get('ContentType', ''),
                'metadata': response.get('Metadata', {})
            }
        except ClientError as e:
            self.logger.error(f"获取文件元数据失败: {e}")
            return None

    def copy_file(
        self,
        src_key: str,
        dst_key: str,
        src_bucket: Optional[str] = None
    ) -> bool:
        """
        复制文件

        Args:
            src_key: 源文件键
            dst_key: 目标文件键
            src_bucket: 源存储桶，如果为None则使用当前存储桶

        Returns:
            是否成功
        """
        try:
            src_bucket = src_bucket or self.config.bucket
            copy_source = {
                'Bucket': src_bucket,
                'Key': src_key
            }

            self._client.copy_object(
                Bucket=self.config.bucket,
                CopySource=copy_source,
                Key=dst_key
            )
            self.logger.info(f"复制文件成功: {src_key} -> {dst_key}")
            return True
        except ClientError as e:
            self.logger.error(f"复制文件失败: {e}")
            return False

    def file_exists(self, remote_key: str) -> bool:
        """
        检查文件是否存在

        Args:
            remote_key: 远程键

        Returns:
            是否存在
        """
        try:
            self._client.head_object(
                Bucket=self.config.bucket,
                Key=remote_key
            )
            return True
        except ClientError:
            return False

    def get_file_url(self, remote_key: str, expires: int = 3600) -> Optional[str]:
        """
        获取文件预签名URL

        Args:
            remote_key: 远程键
            expires: 过期时间(秒)

        Returns:
            预签名URL
        """
        try:
            url = self._client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.config.bucket,
                    'Key': remote_key
                },
                ExpiresIn=expires
            )
            return url
        except ClientError as e:
            self.logger.error(f"生成预签名URL失败: {e}")
            return None

    def close(self):
        """关闭连接"""
        if self._client:
            self._client = None
        if self._resource:
            self._resource = None
        self._connected = False
        self.logger.info("S3连接已关闭")

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._connected

    @property
    def client(self):
        """获取boto3客户端"""
        return self._client

    @property
    def resource(self):
        """获取boto3资源"""
        return self._resource
