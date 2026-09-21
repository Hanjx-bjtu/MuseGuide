"""生成层：Prompt 组装 + LLM 调用 + 结构化输出解析。

P2 阶段实现起步方案（§3.3）所需的最小集合；
P4 会在此基础上加入 Grounding 校验（``generation/grounding.py``）。
"""

from app.services.generation import prompts
from app.services.generation.parser import (
    OutputParseError,
    extract_json,
    parse_advice,
    parse_starter_plan,
)

__all__ = [
    "OutputParseError",
    "extract_json",
    "parse_advice",
    "parse_starter_plan",
    "prompts",
]
