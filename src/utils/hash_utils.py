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

        Args:
            file_path: 文件路径
            chunk_size: 分片大小(默认8MB)

        Returns:
            ETag值
        """
        import os

        file_size = os.path.getsize(file_path)

        # 如果文件小于分片大小，直接计算MD5
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
