"""让 ``streamlit run app/main.py`` 在任何工作目录下都能 import ``app.*``。

**为什么需要这个模块（实测的启动即崩 bug）：**

``streamlit run app/main.py`` 会把**脚本所在目录**（即 ``app/``）放进
``sys.path[0]``，而**不是**当前工作目录。于是 ``app/`` 自己成了顶层路径，
包名 ``app`` 反而不可见：

```text
ModuleNotFoundError: No module named 'app'
    app/main.py:58  from app.ui.client import MuseGuideClient
```

这个问题在测试里发现不了 —— ``AppTest.from_file`` 与 ``pytest`` 都从仓库根目录
运行，``sys.path`` 里恰好有仓库根，``import app`` 自然成功。
**只有真正 ``streamlit run`` 时才暴露。**

修法：把仓库根目录（本文件的上一级）插入 ``sys.path``。
这是 Streamlit 多页应用的常见做法，比在每处 import 前加 hack 更干净。
"""

from __future__ import annotations

import sys
from pathlib import Path

#: 仓库根目录 = app/ 的上一级
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def ensure_project_root_on_path() -> Path:
    """确保仓库根在 ``sys.path`` 中，使 ``import app.*`` 可用。

    :return: 仓库根目录
    """
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        # 插到最前面：优先于 streamlit 放进去的 app/ 目录
        sys.path.insert(0, root)
    return PROJECT_ROOT


# 导入本模块即生效 —— 页面文件只需 ``import app.bootstrap`` 即可
ensure_project_root_on_path()
