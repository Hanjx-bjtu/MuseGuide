"""运行配置。

**硬规则（ADR-0004）：** 密钥只从环境变量 / ``.env``（已被 git 忽略）读入；
**Key 缺失时不得抛异常** —— 只把 LLM 标记为不可用，由上层走降级路径。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """极简 .env 读取（不引入额外依赖；不覆盖已存在的环境变量）。"""
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


class Settings(BaseModel):
    """全部运行期配置。缺失项一律有安全默认值。"""

    # --- LLM ---
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_timeout_s: float = 60.0
    llm_temperature: float = 0.7

    # --- 嵌入后端：ollama → sentence-transformers → hash（ADR-0008）---
    embed_backend: str = "hash"
    embed_model: str = "BAAI/bge-small-zh-v1.5"
    ollama_base_url: str = "http://127.0.0.1:11434"

    # --- 检索 ---
    top_k: int = 5
    candidate_k: int = 20

    # --- 路径 ---
    kb_dir: Path = Field(default=PROJECT_ROOT / "knowledge")
    experiments_dir: Path = Field(default=PROJECT_ROOT / "experiments")

    @property
    def llm_available(self) -> bool:
        """未配置 Key 时返回 False —— 上层据此走降级，而不是抛异常。"""
        return bool(self.llm_api_key.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv()
    return Settings(
        llm_api_key=os.environ.get("DEEPSEEK_API_KEY", os.environ.get("LLM_API_KEY", "")),
        llm_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        llm_model=os.environ.get("LLM_MODEL", "deepseek-chat"),
        llm_timeout_s=float(os.environ.get("LLM_TIMEOUT_S", "60")),
        llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.7")),
        embed_backend=os.environ.get("EMBED_BACKEND", "hash"),
        embed_model=os.environ.get("EMBED_MODEL", "BAAI/bge-small-zh-v1.5"),
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        top_k=int(os.environ.get("TOP_K", "5")),
        candidate_k=int(os.environ.get("CANDIDATE_K", "20")),
    )
