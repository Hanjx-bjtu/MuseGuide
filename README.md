# MuseGuide

> **面向零基础音乐创作爱好者的知识增强型 AI 创作陪伴助手**
> 帮你迈出写歌的第一步——不需要懂乐理，用你自己的话描述就行。

<p align="center">
  <em>MVP 目标：跑通「从零起步引导」与「已有雏形迭代辅导」两条核心链路</em>
</p>

---

## 这是什么

有一群人：完全不懂乐理，不会乐器，没用过 DAW，但心里有一个愿望——**"我想自己写一首歌。"**

MuseGuide 不替用户生成一整首歌，而是：

```text
理解用户的创作意图（日常语言）
        ↓
映射到音乐概念（Intent Mapping）
        ↓
从音乐理论知识库检索依据
        ↓
生成有理论依据的起步方案 / 修改建议
        ↓
用通俗语言解释每一个选择（Layman Adaptation）
```

**两条链路：**

| 链路 | 用户 | 输入 | 输出 |
|---|---|---|---|
| **Creative Starter** | 零基础 | 「我想写一首关于毕业的歌，有点伤感但最后释然」+ 情绪/风格/速度选择 | 调性 + 主歌/副歌和弦框架 + 每项的通俗解释 |
| **Creative Tutor** | 已有雏形 | 创作目标 + `C \| G \| Am \| F` | 结构化分析 + 2~3 个修改方向 + 理论依据 |

---

## 当前状态

> 🚧 **P0 工程地基 + P1 意图映射已完成**：契约层、降级协议、知识库校验器、测试基建、
> Intent Mapping 四层瀑布、Layman 三档适配、受控词表。业务链路 P2~P6 按 `实现阶段计划.md` 推进。

| 阶段 | 内容 | 状态 | 里程碑 |
|---|---|---|---|
| **P0** | 工程地基（契约 / 测试 / 配置 / 降级） | ✅ 已完成 | — |
| **P1** | 意图映射 + 选择式输入 + Layman 术语表 | ✅ 已完成（**G1 通过**） | — |
| P2 | 解析 + 分析 + 起步方案 | ⬜ 待开始 | **M2** |
| P3 | 知识库 29 篇 + Hybrid 检索 + Query 分解 | 🔨 **P3.1 知识库完成（29 篇）**；检索待开始 | **M1** |
| P4 | 进阶分析 + 多方案 + Grounding 校验 | ⬜ 待开始 | **M4** |
| P5 | 双入口 Streamlit 界面 | ⬜ 待开始 | **M3** |
| P6 | 评估 + 报告 + Demo | ⬜ 待开始 | **M5 / M6** |

**当前测试：** `169 passed, 1 skipped`（无需 API Key 与网络）

**文档：**

| 文档 | 作用 |
|---|---|
| [`实现阶段计划.md`](实现阶段计划.md) | **P0~P6 实现路线（本文档的执行依据）** |
| [`MVP计划.md`](MVP计划.md) | MVP 范围与功能的唯一依据 |
| [`设计草案.md`](设计草案.md) | 长期愿景（含 v2 设想），**不是本 MVP 的范围依据** |
| [`docs/DATA_CONTRACTS.md`](docs/DATA_CONTRACTS.md) | 契约字段表 |
| [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md) | 验收口径总表 |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | 架构决策记录（ADR） |
| [`docs/kb_authoring.md`](docs/kb_authoring.md) | 知识条目撰写规范 + 来源与许可证规则 |

---

## 知识库

29 篇条目，覆盖 `MVP计划.md` §3.6.1 目录树的全部 6 个分类：

| 分类 | 篇数 | 内容 |
|---|---|---|
| `harmony/` | 6 | 进行基础、七和弦、调式借用、终止式、副属和弦、和弦色彩 |
| `melody/` | 4 | 张力释放、旋律轮廓、音域与情绪、动机发展 |
| `emotion/` | 5 | 温暖和声、忧郁色彩、伤感→释然、张力释放、告别场景 |
| `starter/` | 5 | 起步四决定、调性与速度、曲式结构、按风格选进行、落地三步 |
| `examples/` | 4 | 流行、民谣、日系、低音线案例 |
| `layman/` | 5 | 和弦、调性、进行、终止式、情绪与和声（概念科普） |

**每条目都带通俗解释层**（`layman_title` + `layman_content`）—— 这是 Layman-aware
Generation 的物理载体，在零基础模式下优先渲染，**不是可选的装饰**。

```powershell
python knowledge/build_kb.py --check     # 校验：来源、通俗层、id 唯一、related 可解析
python knowledge/build_kb.py --build     # 校验并产出（带版本 hash，供实验溯源）
```

校验与以下约束**由测试强制**，不合规会让 `pytest` 变红：
- 六大分类配比与计划一致
- 100% 条目含 `source.title` / `url` / `license`
- 通俗层不含零基础禁区术语
- 情绪标签落在受控词表内
- 项目自建的经验性内容必须标 `medium`，不得标 `high`

## 5 分钟跑通

### 1. 环境

```powershell
# 需要 Python 3.11+（本机已验证：conda 环境 RAG）
pip install -r requirements-dev.txt
```

### 2. 配置（可选）

```powershell
Copy-Item .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
```

> **没有 Key 也能跑。** 系统不会报错，而是走降级路径：
> 意图映射回落到规则层，生成回落到 Mock / 知识库直出。
> 这是刻意设计（ADR-0004），它让测试与 CI 不依赖网络。

### 3. 跑测试（P0 阶段门）

```powershell
pytest
```

> ⚠️ **本机已知问题（R13）：`pip` 无法联网安装依赖** —— 子进程链路拿不到 TLS 凭证
> （`schannel: SEC_E_NO_CREDENTIALS`），`pip install pytest` 会挂起无输出。
> 网络本身可达（TCP 443 通），是进程级 TLS 问题。
> 候选解法：`conda install -c conda-forge pytest streamlit rank_bm25`，
> 或在本机其他已装 pytest 的 conda 环境（`DPL` / `MCP` / `VLM1`）中运行。
> **契约层与知识库校验器只依赖 `pydantic`（已装）+ 标准库，不受此问题影响。**

### 4. 校验知识库

```powershell
python knowledge/build_kb.py --check     # 只校验
python knowledge/build_kb.py --build     # 校验并产出
```

### 5. 启服务（P5 完成后）

```powershell
# 终端 1：后端
uvicorn app.api.server:app --reload --port 8000

# 终端 2：界面
streamlit run app/main.py
```

---

## 架构

```text
┌──────────────────────────────────────────────────┐
│  app/main.py          Streamlit 双入口            │  ← P5
│    零基础入口 / 进阶入口                           │
└───────────────────────┬──────────────────────────┘
                        │ HTTP（ADR-0005：界面只调 API）
┌───────────────────────▼──────────────────────────┐
│  app/api/             FastAPI 路由层              │  ← P2 / P4
└───────────────────────┬──────────────────────────┘
┌───────────────────────▼──────────────────────────┐
│  app/services/        业务编排                    │
│  intent · layman · starter · parser · analyzer   │
│  query · retrieval · generation · advisor        │
└───────────────────────┬──────────────────────────┘
┌───────────────────────▼──────────────────────────┐
│  app/providers/       LLM / Embedding 适配        │  ← P0 ✅
│  deepseek · mock · (ollama)                      │
└───────────────────────┬──────────────────────────┘
┌───────────────────────▼──────────────────────────┐
│  app/core/            契约层（零业务依赖）         │  ← P0 ✅
│  MusicArtifact · CreativeIntent · Evidence       │
│  StarterPlan · Advice · Trace · Degradation      │
└──────────────────────────────────────────────────┘
```

**分层铁律（由 `tests/test_architecture.py` 强制）：**

```text
core/  ←── 谁都可以依赖，它不依赖任何业务模块
  ↑
providers/ ←── services/ ←── api/ ←── main.py
```

### 为什么有 `Trace`

`MVP计划.md` §3.9.3 要求「固定输出格式，保证可解释性与可评估性」。
`Trace` 是这句话的工程实现——**评测、报告、界面依据展示消费同一个对象**：

- 评测层直接消费 `Trace` 列表，不用手写解析
- 报告数字**由 Trace 渲染**，不可能与数据漂移
- 界面可以直接展示「这条建议由哪条 query 召回、在第几级排名」
- `Trace` 含 config + prompt + 原始输出，**逐位可回放**

---

## 目录结构

```text
MuseGuide/
├── app/
│   ├── core/           # 契约层 ✅
│   ├── providers/      # 模型适配 ✅
│   ├── services/       # 业务逻辑（P1~P4）
│   ├── api/            # FastAPI 路由（P2/P4）
│   └── main.py         # Streamlit（P5）
├── knowledge/
│   ├── entries/        # 6 分类知识条目（P3 扩至 29 篇）
│   ├── build_kb.py     # 校验 + 构建（零第三方依赖）✅
│   └── build/          # 构建产物
├── datasets/           # 评测集（P3/P4 建立）
├── experiments/        # Trace 存档
├── reports/            # 由 Trace 自动渲染的报告
├── docs/               # 契约 / 验收口径 / ADR ✅
└── tests/              # pytest ✅
```

---

## 测试策略

```powershell
pytest                          # 全部（不需要 Key / 网络）
pytest tests/test_contracts.py  # 契约往返 + 业务约束
pytest -m slow                  # 需要真实 LLM 的测试（默认跳过）
```

| 测试文件 | 断言什么 |
|---|---|
| `test_contracts.py` | 每个契约可序列化往返；`Advice` 只能是 2~3 个方案；起步方案每个解释非空 |
| `test_architecture.py` | `app/core/` 零业务依赖；无向上层依赖（ADR-0003） |
| `test_degradation.py` | 无 Key 不抛异常；Mock 确定性且产出能通过契约校验 |
| `test_knowledge_base.py` | 六分类非空、来源 100% 可追溯、通俗层必备、related 可解析 |
| `test_intent.py` | §3.2.2 九条规则逐条命中；LLM 失败/解析失败回落规则层；选择优先于推断 |
| `test_layman.py` | §3.8.2 七条术语映射逐条断言；三档输出策略；术语守卫；受控词表完整性 |

### Intent Mapping 的四层瀑布

```text
① 规则层  RULE_MAP 关键词扫描（零成本、确定性、无 Key 也能工作）
     ↓ 规则层留下空白（style / tempo_feel / emotion 任一为空）
② LLM 层  DeepSeek 结构化输出（§3.2.3）
     ↓ 不可用 / 超时 / JSON 解析失败
③ 选择层  用户的下拉选择（覆盖系统推断）
     ↓ 什么都没有
④ 默认层  温和流行 + 中等偏慢 + C 大调，并提示用户
```

每层都在 `Trace.degradation` 留痕，因此「系统为什么给了这个方向」永远可解释。
实测演示（§3.1.2 的毕业歌输入）：

```text
无 LLM：emotion=['伤感','释然']  style=None      tempo=None      source=rule
有 LLM：emotion=['伤感','释然']  style='流行'    tempo='中等偏慢' source=llm
```

> 两条路径都给出可用的**调性倾向「大调（释然感）」** —— 这正是「规则兜底」的价值。

---

## MVP 边界（明确不做）

依据 `MVP计划.md` §1.3：

- ❌ 哼唱 / 录音输入与识别；音频文件上传
- ❌ 自动生成完整歌曲、自动编曲 / 混音 / 母带
- ❌ 复杂乐谱渲染与播放；试听音频（MVP 用文字描述替代）
- ❌ 用户历史记忆与个性化推荐
- ❌ 多轮创作辅导循环（先做单轮起步 + 单轮建议）
- ❌ Reranker 与实验对比矩阵
- ❌ LangChain / LlamaIndex（ADR-0007）

---

## 局限（诚实清单）

> 这一节不是免责声明，而是项目的一部分。**知道系统哪里不行，并量化它**，比声称"什么都好"更可信。

- 知识库当前为骨架规模（每分类 1~2 篇示例，共 8 篇），尚未达到 §3.6.3 要求的 20~30 篇
- 嵌入后端默认 `hash`（占位实现），**其检索指标不代表真实水平**（ADR-0008）
- 未接入真实 LLM 的端到端验证（Mock 后端保证了结构与降级路径，但不验证生成质量）
- 无专业音乐人复核
- 尚未建立评测集，所有「更好」的结论都还不能声称
- **`pytest` 尚未在本机安装成功**（R13 环境问题）；测试逻辑已用等价 runner 验证 33/33 通过，
  但尚未在标准 `pytest` 下运行过

---

## 技术选型

| 模块 | 方案 |
|---|---|
| 后端 | FastAPI |
| 前端 | Streamlit |
| 音乐解析 | music21 |
| 向量检索 | Chroma |
| 关键词检索 | rank_bm25 |
| LLM | DeepSeek（OpenAI 兼容，模型无关设计） |
| 嵌入 | Ollama / BGE（可替换，见 ADR-0008） |
| 知识库 | Markdown + YAML front-matter，含通俗解释层 |
