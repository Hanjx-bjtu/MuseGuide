"""业务服务层。

依赖方向：``services`` 可以依赖 ``core`` 与 ``providers``，**不得依赖 api/ 或 main.py**。
详见 ``docs/DECISIONS.md`` ADR-0003。
"""

from app.services.intent import RULE_MAP, map_by_rules, resolve_intent
from app.services.layman import TERM_MAP, style_guide, to_layman

__all__ = [
    "RULE_MAP",
    "TERM_MAP",
    "map_by_rules",
    "resolve_intent",
    "style_guide",
    "to_layman",
]
