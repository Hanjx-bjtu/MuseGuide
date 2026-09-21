# 知识条目撰写规范（kb_authoring）

> **阶段：** P3.1 交付物
> **依据：** `MVP计划.md` §3.6.1（目录树）、§3.6.2（条目结构）、§3.6.3（20~30 篇）、§8（知识库质量风险）
> **强制校验：** `python knowledge/build_kb.py --check` —— 不合规的条目会让校验失败，也会让 `pytest` 变红。

---

## 1. 为什么规范必须被机器强制

`MVP计划.md` §8 把「知识库质量不足」列为首要风险。但「质量」如果只写在文档里，就会在第一周之后失效。

因此本项目把质量拆成**可校验的字段约束**：

| 质量诉求 | 机器校验 |
|---|---|
| 内容可追溯、不编造 | `source.title` + `source.url` + `source.license` 三项必填 |
| 对零基础用户可理解 | `layman_title` + `layman_content` 必填 |
| 分类体系不塌陷 | `category` 必须是六个合法值之一；六个目录均非空 |
| 知识不是孤岛 | `related` 引用的 id 必须真实存在 |
| id 可稳定引用 | `id` 全局唯一，且以 `category.` 为前缀 |
| 可被检索过滤 | `tags` 必须为非空列表 |

**校验器不是形式主义**：本次撰写过程中，它抓出了 3 处悬空的 `related` 引用和 7 处缺失的通俗层。

---

## 2. 文件组织

```text
knowledge/entries/<category>/<NN>_<slug>.yaml
```

- `category` ∈ `harmony` / `melody` / `emotion` / `starter` / `examples` / `layman`
- `NN` 为两位序号，从 `01` 开始（仅用于排序，无业务含义）
- **一个文件可承载多个条目**（以 `- id:` 起头的 YAML 列表），用于同一主题下的相关条目

> `MVP计划.md` §3.6.3 说的「文档数量 20~30 篇」指**文件数**；条目数可以更多。

---

## 3. 条目字段

### 3.1 必填

| 字段 | 说明 | 约束 |
|---|---|---|
| `id` | 全局唯一标识 | `category.subcategory.NN`，如 `harmony.modal_interchange.01` |
| `title` | 中文标题 | 面向用户的说法，不含缩写 |
| `category` | 六大分类之一 | 必须与所在目录一致 |
| `content` | 知识点正文 | 供 LLM 消费；**结构化、可直接支撑建议** |
| `layman_title` | 通俗标题 | 零基础模式下**优先渲染这个** |
| `layman_content` | 通俗解释 | 见 §4 的写作要求 |
| `source` | 来源与许可 | `title` / `url` / `license` 三者必填 |

### 3.2 推荐

| 字段 | 说明 |
|---|---|
| `title_en` | 英文标题，便于跨语言检索 |
| `subcategory` | 二级分类 |
| `tags` | 检索与 metadata filter 的依据 |
| `emotions` | 该条目对应的情绪，**受控词表内**（见 `app/core/options.py`） |
| `examples` | 和弦进行 / 旋律片段示例 |
| `key_context` | 示例所在的调性 |
| `roman_numerals` | 罗马数字，供进阶用户与精确检索 |
| `difficulty` | `beginner` / `intermediate` / `advanced` |
| `confidence_level` | `high` / `medium` / `low`——**乐理有争议处必须标 medium/low** |
| `applicable_genres` | 供 Genre 维度的 metadata filter |
| `related` | 关联条目的 id |
| `priority` | 1~5，检索排序的加权依据 |

### 3.3 正文格式

front-matter 之后是 Markdown 正文，供人类阅读与原文展示（P5 的「查看理论依据」直接渲染它）。

建议结构：

```markdown
## 什么时候会用到
（用户会怎么问）

## 怎么做
（可操作的手法）

## 要注意
（常见错误与边界）
```

---

## 4. `layman_content` 的写作要求

这是本项目区别于普通乐理知识库的核心，写法有硬性要求（对齐 `MVP计划.md` §3.8.3）：

1. **先用日常类比**，再谈音乐
2. **先描述「听起来会怎样」**，再解释「为什么」
3. **不出现** `BANNED_TERMS` 中的词（三度叠置、音程、转位、声部进行……）
4. 长度 2~4 句，能独立看懂，**不依赖上下文**
5. 落点应是「你需要做的选择」，而不是「你需要理解的概念」

**正例：**

> 就像画画时，本来用暖色调，突然加一点冷色，画面会更有层次。
> 音乐里也可以从别的调「借」一个和弦过来，让听感多一层色彩。

**反例：**

> 从平行自然小调借用 iv 级和弦，其 b3 音与主调大三度形成半音倾向关系。

---

## 5. 来源与许可（不可协商）

### 5.1 允许的来源

| 来源 | 许可 | 用途 |
|---|---|---|
| [Open Music Theory](https://openmusictheory.github.io/) | CC BY-SA 4.0 | 乐理主体内容 |
| 公开和弦进行数据 | 事实性数据 | 案例库——**只收录「某进行高频出现」这类事实** |
| MuseGuide 项目自建 | CC BY-SA 4.0 | 情绪映射表、通俗解释层、起步引导 |

### 5.2 明确禁止

- ❌ 复制受版权保护的乐谱、歌词、大段教材原文
- ❌ 收录具体歌曲的完整和弦谱（案例库只收「进行模式」这一事实）
- ❌ 无来源的「我觉得」「一般来说」——**每句话都要能被追溯**

### 5.3 引用格式

```yaml
source:
  title: Open Music Theory — Modal Mixture
  url: https://openmusictheory.github.io/modalMixture.html
  license: CC BY-SA 4.0
```

自建内容同样要写清来源：`MuseGuide 情绪映射表（综合 Open Music Theory 条目整理）`。

---

## 6. 与代码的耦合点

**修改知识库时，以下位置必须同步检查：**

| 位置 | 耦合关系 |
|---|---|
| `app/services/intent.py::RULE_MAP` | §3.2.2 的九条规则；新增情绪词需与条目 `emotions` 一致 |
| `app/core/options.py` | 情绪受控词表；条目的 `emotions` 必须在其范围内 |
| `tests/test_knowledge_base.py` | 分类完整性、来源完整性、通俗层、related 可解析的断言 |
| `app/services/retrieval/*`（P3.3+） | `tags` / `category` 是 metadata filter 的依据 |

---

## 7. 当前覆盖情况

| 分类 | 目标篇数 | 说明 |
|---|---|---|
| `harmony/` | 6 | 进行基础、七和弦、调式借用、副属和弦、功能与终止式、和弦色彩 |
| `melody/` | 4 | 张力释放、动机发展、旋律轮廓、音域与情绪 |
| `emotion/` | 5 | 温暖和声、忧郁色彩、伤感→释然、张力释放、告别场景 |
| `starter/` | 5 | 如何开始、调性与速度、曲式结构、按风格选进行、第一首歌落地 |
| `examples/` | 4 | 流行、民谣、日系、低音线案例 |
| `layman/` | 5 | 和弦、调性、进行、终止式、情绪与和声 |
| **合计** | **29** | 落在 §3.6.3 的 20~30 区间 |
