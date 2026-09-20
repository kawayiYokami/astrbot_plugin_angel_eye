"""Angel Eye 测试配置：统一接管导入路径。

插件根目录既支持 `from core.formatter import ...` 顶层导入，
也支持 `from astrbot_plugin_angel_eye.core.formatter import ...` 包导入。
"""

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(PLUGIN_ROOT.parent), str(PLUGIN_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
