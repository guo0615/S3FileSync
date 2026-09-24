"""
图标工具模块
负责管理文件类型图标和勾选图标，提供图标路径解析

图标方案：
1. 优先使用 qtawesome（Font Awesome 5）渲染彩色矢量图标，样式美观、清晰
2. 若未安装 qtawesome，则回退到内置 PNG 图标（src/resources/icons/）
"""
import os
import sys
from typing import Optional


def _get_resource_root() -> str:
    """
    获取图标资源根目录。

    - 直接运行 `python main.py` 时：使用源码目录下的 `src/resources/icons`
      （即本文件所在目录的上一级 + resources/icons）
    - 使用 PyInstaller 打包运行时：build.spec 中将 `src/resources` 映射到
      顶层 `resources` 目录，运行时 PyInstaller 会把该目录解压到
      `sys._MEIPASS/resources`，因此需要基于 `sys._MEIPASS` 查找。

    若两者均不可用，则回退到源码目录（最后一道防线）。
    """
    # 1) PyInstaller 打包后运行环境
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            # build.spec: ('src/resources', 'resources')
            packed = os.path.join(meipass, 'resources', 'icons')
            if os.path.isdir(packed):
                return packed

    # 2) 源码运行环境：src/utils/icon_utils.py -> ../../resources/icons
    src_resource_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'resources', 'icons'
    )
    return src_resource_dir


# 图标资源目录
_RESOURCE_DIR = _get_resource_root()

# 尝试导入 qtawesome（第三方图标库）
try:
    import qtawesome as _qta
    _QTAVAILABLE = True
except Exception:
    _qta = None
    _QTAVAILABLE = False


def _icon_path(name: str) -> str:
    """
    获取图标绝对路径

    Args:
        name: 图标文件名

    Returns:
        图标绝对路径（Linux/Windows 统一为正斜杠，便于 QSS 使用）
    """
    path = os.path.join(_RESOURCE_DIR, name)
    return path.replace('\\', '/')


# 文件扩展名 -> 图标文件名（PNG 回退方案）
_FILE_TYPE_ICONS = {
    # 文件夹
    'folder': 'folder.png',
    # 文本文件
    'text': 'text_file.png',
    # 图片
    'image': 'image_file.png',
    # 视频
    'video': 'video_file.png',
    # 音频
    'audio': 'audio_file.png',
    # 代码
    'code': 'code_file.png',
    # 压缩包
    'archive': 'archive_file.png',
    # 文档
    'pdf': 'pdf_file.png',
    'word': 'word_file.png',
    'excel': 'excel_file.png',
    'ppt': 'ppt_file.png',
    # 可执行
    'executable': 'exe_file.png',
    # 数据库
    'database': 'db_file.png',
}

# 文件类型 -> Font Awesome 图标名 + 颜色（qtawesome 方案）
_FA_ICONS = {
    'folder':      ('fa5s.folder-open',      '#FFB300'),
    'text':        ('fa5s.file-alt',         '#90A4AE'),
    'image':       ('fa5s.file-image',       '#8E24AA'),
    'video':       ('fa5s.file-video',       '#E53935'),
    'audio':       ('fa5s.file-audio',       '#FB8C00'),
    'code':        ('fa5s.file-code',        '#3949AB'),
    'archive':     ('fa5s.file-archive',     '#F57C00'),
    'pdf':         ('fa5s.file-pdf',         '#E53935'),
    'word':        ('fa5s.file-word',        '#1E88E5'),
    'excel':       ('fa5s.file-excel',       '#43A047'),
    'ppt':         ('fa5s.file-powerpoint',  '#F4511E'),
    'executable':  ('fa5s.cogs',             '#546E7A'),
    'database':    ('fa5s.database',         '#00897B'),
}

# 常见扩展名分组
_EXT_MAP = {
    # 文本
    '.txt': 'text', '.log': 'text', '.md': 'text', '.readme': 'text',
    '.ini': 'text', '.cfg': 'text', '.conf': 'text', '.csv': 'text',
    '.json': 'text', '.xml': 'text', '.html': 'text', '.htm': 'text',
    '.yaml': 'text', '.yml': 'text', '.properties': 'text',
    # 图片
    '.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.gif': 'image',
    '.bmp': 'image', '.svg': 'image', '.webp': 'image', '.ico': 'image',
    '.tiff': 'image', '.tif': 'image',
    # 视频
    '.mp4': 'video', '.avi': 'video', '.mkv': 'video', '.mov': 'video',
    '.wmv': 'video', '.flv': 'video', '.webm': 'video', '.mpg': 'video',
    '.mpeg': 'video',
    # 音频
    '.mp3': 'audio', '.wav': 'audio', '.flac': 'audio', '.aac': 'audio',
    '.ogg': 'audio', '.wma': 'audio', '.m4a': 'audio',
    # 代码
    '.py': 'code', '.js': 'code', '.ts': 'code', '.java': 'code',
    '.c': 'code', '.cpp': 'code', '.h': 'code', '.hpp': 'code',
    '.cs': 'code', '.php': 'code', '.rb': 'code', '.go': 'code',
    '.rs': 'code', '.swift': 'code', '.kt': 'code', '.sql': 'code',
    '.sh': 'code', '.bat': 'code', '.ps1': 'code', '.css': 'code',
    '.scss': 'code', '.less': 'code',
    # 压缩包
    '.zip': 'archive', '.rar': 'archive', '.7z': 'archive',
    '.tar': 'archive', '.gz': 'archive', '.bz2': 'archive',
    '.xz': 'archive', '.tgz': 'archive',
    # 文档
    '.pdf': 'pdf', '.doc': 'word', '.docx': 'word', '.dot': 'word',
    '.xls': 'excel', '.xlsx': 'excel', '.xlsm': 'excel', '.csv2': 'excel',
    '.ppt': 'ppt', '.pptx': 'ppt', '.pps': 'ppt',
    # 可执行
    '.exe': 'executable', '.msi': 'executable', '.app': 'executable',
    '.dll': 'executable', '.so': 'executable',
    # 数据库
    '.db': 'database', '.sqlite': 'database', '.sqlite3': 'database',
    '.mdb': 'database',
}

# 默认图标
_DEFAULT_ICON = 'text_file.png'


def _get_file_type(file_name: str, is_dir: bool = False) -> str:
    """
    根据文件名获取文件类型分组

    Args:
        file_name: 文件名或路径
        is_dir: 是否为文件夹

    Returns:
        文件类型标识
    """
    if is_dir:
        return 'folder'

    ext = os.path.splitext(str(file_name))[1].lower()
    return _EXT_MAP.get(ext, 'text')


# 文件类型分组在"按类型排序"时的先后顺序（越小越靠前）
_TYPE_ORDER = {
    'folder': 0,
    'text': 1,
    'code': 2,
    'image': 3,
    'audio': 4,
    'video': 5,
    'pdf': 6,
    'word': 7,
    'excel': 8,
    'ppt': 9,
    'archive': 10,
    'database': 11,
    'executable': 12,
}


def get_file_type_sort_key(file_name: str, is_dir: bool = False):
    """
    按文件类型排序用的复合键：先按类型分组，组内再按名称（不区分大小写）。

    与 get_file_icon 使用同一套类型分组（_get_file_type），保证"图标种类"
    与"排序分组"一致。类型未知时排在已知类型之后，但仍按名称有序。

    Args:
        file_name: 文件名或路径
        is_dir: 是否为文件夹

    Returns:
        (类型序号, 名称小写) 元组，可直接作为 sort 的 key
    """
    file_type = _get_file_type(file_name, is_dir)
    order = _TYPE_ORDER.get(file_type, len(_TYPE_ORDER))
    # 用 basename 排序，避免路径中的目录名干扰
    name = os.path.basename(str(file_name)).lower()
    return (order, name)


def is_qtawesome_available() -> bool:
    """检查 qtawesome 是否可用"""
    return _QTAVAILABLE


def get_file_icon_path(file_name: str, is_dir: bool = False) -> Optional[str]:
    """
    根据文件名获取图标路径（PNG 回退方案）

    Args:
        file_name: 文件名或路径
        is_dir: 是否为文件夹

    Returns:
        图标绝对路径，如果是文件夹返回文件夹图标
    """
    file_type = _get_file_type(file_name, is_dir)
    icon_name = _FILE_TYPE_ICONS.get(file_type, _DEFAULT_ICON)
    return _icon_path(icon_name)


def get_file_icon(file_name: str, is_dir: bool = False, size: int = 24) -> Optional['QIcon']:
    """
    获取文件类型图标 QIcon

    优先使用 qtawesome（Font Awesome 矢量图标，美观清晰），
    未安装时回退到内置 PNG 图标。

    Args:
        file_name: 文件名或路径
        is_dir: 是否为文件夹
        size: 图标尺寸（像素）

    Returns:
        QIcon 对象，失败时返回 None
    """
    file_type = _get_file_type(file_name, is_dir)

    # 优先 qtawesome
    if _QTAVAILABLE and _qta is not None:
        try:
            fa_icon, color = _FA_ICONS.get(
                file_type, _FA_ICONS['text'])
            return _qta.icon(fa_icon, color=color)
        except Exception:
            pass

    # 回退 PNG
    from PyQt6.QtGui import QIcon
    path = get_file_icon_path(file_name, is_dir)
    if path:
        return QIcon(path)
    return None


def get_checkbox_checked_icon() -> str:
    """获取勾选图标路径"""
    return _icon_path('checkbox_checked.png')


def get_checkbox_unchecked_icon() -> str:
    """获取未勾选图标路径"""
    return _icon_path('checkbox_unchecked.png')


def get_icons_dir() -> str:
    """获取图标目录"""
    return _RESOURCE_DIR.replace('\\', '/')


def qss_url(path: str) -> str:
    """
    将路径转换为QSS可用的url()格式

    Args:
        path: 图标路径

    Returns:
        url(...) 格式的字符串
    """
    return f"url({path})"
