"""
哈希计算工具
"""
import hashlib
from typing import Optional


class HashUtils:
    """哈希工具类"""

    @staticmethod
    def calculate_file_md5(file_path: str, chunk_size: int = 8192) -> str:
        """
        计算文件的MD5哈希值

        Args:
            file_path: 文件路径
            chunk_size: 分块大小

        Returns:
            MD5哈希值(十六进制字符串)
        """
        md5_hash = hashlib.md5()

        try:
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    md5_hash.update(chunk)
            return md5_hash.hexdigest()
        except Exception:
            return ""

    @staticmethod
    def calculate_file_sha256(file_path: str, chunk_size: int = 8192) -> str:
        """
        计算文件的SHA256哈希值

        Args:
            file_path: 文件路径
            chunk_size: 分块大小

        Returns:
            SHA256哈希值(十六进制字符串)
        """
        sha256_hash = hashlib.sha256()

        try:
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    sha256_hash.update(chunk)
            return sha256_hash.hexdigest()
        except Exception:
            return ""

    @staticmethod
    def calculate_data_md5(data: bytes) -> str:
        """
        计算数据的MD5哈希值

        Args:
            data: 字节数据

        Returns:
            MD5哈希值(十六进制字符串)
        """
        md5_hash = hashlib.md5()
        md5_hash.update(data)
        return md5_hash.hexdigest()

    @staticmethod
    def calculate_data_sha256(data: bytes) -> str:
        """
        计算数据的SHA256哈希值

        Args:
            data: 字节数据

        Returns:
            SHA256哈希值(十六进制字符串)
        """
        sha256_hash = hashlib.sha256()
        sha256_hash.update(data)
        return sha256_hash.hexdigest()

    @staticmethod
    def calculate_etag(file_path: str, chunk_size: int = 8388608) -> str:
        """
        计算S3 ETag(模拟AWS S3的分片上传ETag计算方式)

        用于让本地文件的"内容指纹"与远端 list_objects_v2 返回的 ETag 直接可比，
        无需额外请求即可判断内容是否一致（见 sync_engine.scan_local）。

        注意：计算口径必须与实际上传方式严格一致，否则本地指纹与远端 ETag
        对不上，大文件每次都会退化到按修改时间比较：
        - 小于等于分片大小：S3 单次 PUT，ETag = 整个文件的 MD5；
        - 大于分片大小：S3 分片上传，ETag = 各分片 MD5 拼接后再取 MD5，形如 "<md5>-<分片数>"。
        因此这里的分片阈值必须与 S3Client.upload_file 走分片上传的阈值
        （8MB）以及 TransferManager.chunk_size 默认值保持一致。

        Args:
            file_path: 文件路径
            chunk_size: 分片大小(默认8MB，与上传分片大小一致)

        Returns:
            ETag值（不含引号），失败时返回空字符串
        """
        import os

        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            return ""

        # 与 S3Client.upload_file 的分片阈值一致：<= 8MB 走单次上传，ETag = 整文件 MD5
        if file_size <= chunk_size:
            return HashUtils.calculate_file_md5(file_path)

        # 否则计算分片MD5的MD5
        md5_hashes = []
        parts_count = 0

        try:
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break

                    md5_hash = hashlib.md5()
                    md5_hash.update(chunk)
                    md5_hashes.append(md5_hash.digest())
                    parts_count += 1

            # 合并所有分片的MD5
            combined = b''.join(md5_hashes)
            final_md5 = hashlib.md5()
            final_md5.update(combined)

            # 返回格式: "hash-parts"
            return f"{final_md5.hexdigest()}-{parts_count}"
        except Exception:
            return ""

    @staticmethod
    def verify_file_hash(file_path: str, expected_hash: str, algorithm: str = "md5") -> bool:
        """
        验证文件哈希值

        Args:
            file_path: 文件路径
            expected_hash: 期望的哈希值
            algorithm: 哈希算法 (md5/sha256)

        Returns:
            是否匹配
        """
        if algorithm.lower() == "md5":
            actual_hash = HashUtils.calculate_file_md5(file_path)
        elif algorithm.lower() == "sha256":
            actual_hash = HashUtils.calculate_file_sha256(file_path)
        else:
            return False

        return actual_hash.lower() == expected_hash.lower()
