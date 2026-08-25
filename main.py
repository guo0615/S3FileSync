"""
S3文件同步工具 - 主入口
"""
import sys
import os

# 添加src目录到Python路径
src_dir = os.path.dirname(os.path.abspath(__file__))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from src.utils.logger import setup_global_logging
from src.utils.config_manager import ConfigManager
from src.gui.main_window import run_app


def main():
    """主函数"""
    # 设置日志（配置全局日志系统，所有模块日志统一输出到文件）
    config_manager = ConfigManager()
    app_config = config_manager.get_app_config()

    logger = setup_global_logging(
        log_file=app_config.log_file,
        level=app_config.log_level
    )

    logger.info("=" * 60)
    logger.info("S3文件同步工具启动")
    logger.info("=" * 60)

    try:
        # 运行应用
        run_app()
    except Exception as e:
        logger.error(f"应用运行失败: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info("S3文件同步工具退出")


if __name__ == "__main__":
    main()
