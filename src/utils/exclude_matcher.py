"""
排除规则匹配器
实现类似 .gitignore 的排除规则语法，用于按任务排除不参与同步的文件/目录。

支持的语法（与 .gitignore 基本一致）：
- 空行忽略；以 `#` 开头的行为注释（`\\#` 转义为字面 #）；
- `!` 前缀表示取反（重新包含之前被排除的路径），最后一条匹配的规则生效；
- 结尾 `/` 表示只匹配目录（及其下所有内容）；
- 含 `/`（不含结尾）的模式相对于任务根目录锚定，否则在任意层级匹配；
- 通配符：`*` 匹配单层内任意字符，`?` 匹配单个字符，`[abc]` 匹配字符集合；
- `**` 匹配任意层级（`a/**/b` 匹配 a 与 b 之间任意层）。

匹配对象为「相对任务根目录的路径」，统一使用正斜杠。
"""
import fnmatch
import os
import re
from typing import List, Optional


class _Pattern:
    """单条排除规则（编译后）"""

    __slots__ = ("raw", "segments", "dir_only", "negated")

    def __init__(self, raw: str, segments: List[str], dir_only: bool, negated: bool):
        self.raw = raw
        self.segments = segments
        self.dir_only = dir_only
        self.negated = negated


class ExcludeMatcher:
    """排除规则匹配器

    用法::

        m = ExcludeMatcher(["*.log", "build/", "!keep.log"])
        m.is_excluded("a/b.log")      # True
        m.is_excluded("build/x.txt")  # True
        m.is_excluded("keep.log")     # False（被 !keep.log 重新包含）
        m.is_excluded("src/main.py")  # False
    """

    def __init__(self, patterns: Optional[List[str]] = None):
        self._patterns: List[_Pattern] = []
        self.load(patterns or [])

    # ---- 构建 ----

    def load(self, patterns: List[str]):
        """加载规则列表（覆盖式）"""
        self._patterns = []
        for raw in patterns or []:
            if raw is None:
                continue
            line = str(raw)
            # 统一换行/分隔符：允许从 .gitignore 文件整段粘贴
            for part in re.split(r"[\r\n]+", line):
                self._add_line(part)

    def _add_line(self, line: str):
        """解析并添加一行规则"""
        # 去掉首尾空白；忽略空行
        s = line.strip()
        if not s:
            return

        # 注释：以 # 开头（\# 转义为字面 #）
        if s.startswith("\\#"):
            s = s[1:]
        elif s.startswith("#"):
            return

        # 取反
        negated = False
        if s.startswith("!"):
            negated = True
            s = s[1:].strip()
            if not s:
                return
        elif s.startswith("\\!"):
            s = s[1:]

        # 统一为 POSIX 分隔符（兼容从 Windows 粘贴的反斜杠）
        s = s.replace("\\", "/")

        # 目录规则：结尾 /
        dir_only = s.endswith("/")
        if dir_only:
            s = s.rstrip("/")
        if not s:
            return

        # 锚定：含 /（且不在结尾）时相对根目录锚定
        anchored = s.startswith("/") or "/" in s
        s = s.lstrip("/")
        if not s:
            return

        segments = [seg for seg in s.split("/") if seg not in ("", ".")]
        if not segments:
            return

        # 非锚定：等价于在最前面加 **（任意层级匹配）
        if not anchored:
            segments = ["**"] + segments

        self._patterns.append(_Pattern(line, segments, dir_only, negated))

    # ---- 匹配 ----

    def is_excluded(self, rel_path: str, is_dir: bool = False) -> bool:
        """判断相对路径是否被排除（最后一条匹配的规则生效）

        Args:
            rel_path: 相对任务根目录的路径（正/反斜杠均可）
            is_dir: 该路径是否为目录

        Returns:
            是否应排除
        """
        if not self._patterns or not rel_path:
            return False

        norm = rel_path.replace("\\", "/")
        segs = [seg for seg in norm.split("/") if seg not in ("", ".")]
        if not segs:
            return False

        excluded = False
        for pat in self._patterns:
            if self._pattern_matches(pat, segs, is_dir):
                excluded = not pat.negated
        return excluded

    @staticmethod
    def _pattern_matches(pat: _Pattern, segs: List[str], is_dir: bool) -> bool:
        """单条规则是否命中该路径（命中其本身或其任一祖先目录）"""
        # 命中祖先目录（pattern 匹配 path 的某个真前缀）：
        # 例如 "build/" 命中 "build/x.txt"，"node_modules" 命中 "node_modules/a/b"
        for k in range(1, len(segs)):
            if _segments_match(segs[:k], pat.segments):
                return True
        # 命中路径本身
        if _segments_match(segs, pat.segments):
            # 目录规则要求命中项本身是目录
            return is_dir if pat.dir_only else True
        return False

    def __bool__(self) -> bool:
        return bool(self._patterns)

    def __len__(self) -> int:
        return len(self._patterns)


def _segments_match(path_segs: List[str], pat_segs: List[str]) -> bool:
    """按段匹配路径与模式，支持 `**` 跨层级通配"""
    if not pat_segs:
        return not path_segs
    head = pat_segs[0]
    if head == "**":
        # ** 匹配零个或多个段
        if _segments_match(path_segs, pat_segs[1:]):
            return True
        for i in range(len(path_segs)):
            if _segments_match(path_segs[i + 1:], pat_segs[1:]):
                return True
        return False
    if not path_segs:
        return False
    if not fnmatch.fnmatchcase(path_segs[0], head):
        return False
    return _segments_match(path_segs[1:], pat_segs[1:])


def build_matcher(task) -> Optional[ExcludeMatcher]:
    """从任务对象构造匹配器（无规则时返回 None，便于调用方短路）"""
    patterns = getattr(task, "exclude_patterns", None) or []
    matcher = ExcludeMatcher(patterns)
    return matcher if matcher else None


def is_excluded_path(task, rel_path: str, is_dir: bool = False) -> bool:
    """便捷函数：判断任务下某相对路径是否被排除"""
    matcher = build_matcher(task)
    return bool(matcher and matcher.is_excluded(rel_path, is_dir))


def path_is_dir(abs_path: str) -> bool:
    """安全判断绝对路径是否为目录"""
    try:
        return os.path.isdir(abs_path)
    except OSError:
        return False
