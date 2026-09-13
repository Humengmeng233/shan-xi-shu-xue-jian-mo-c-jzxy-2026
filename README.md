<a name="top"></a>

<div align="center">
  <h1>光伏、储能与购电计划优化</h1>
  <p>园区能源调度 · 日前计划 · 日内滚动优化 · 波动电价分析</p>
  <p><strong>Python 3.12+</strong> &nbsp; · &nbsp; SciPy / HiGHS &nbsp; · &nbsp; 10 分钟调度</p>
  <p>
    <a href="#results">主要结果</a> &nbsp; / &nbsp;
    <a href="#run">快速开始</a> &nbsp; / &nbsp;
    <a href="#project">目录与开发</a> &nbsp; / &nbsp;
    <a href="docs/03-model.md#model">数学模型</a>
  </p>
</div>

---

本项目研究园区如何联合安排光伏、储能和外网购电，在满足负载需求的同时降低购电费用。本页集中展示核心结论、结果入口与开发环境；详细数据处理、数学模型、求解过程和检验依据见分章文档。

<a name="results"></a>

## 主要结果速览

问题一评价一个典型日；问题二至四评价 2025 年 2 月 1 日至 12 月 31 日，共 334 天。表中费用为各自评价期的总购电费用，日内方案包含调整费用。

| 问题与方案 | 总费用（元） | 主要结论 | 详细求解与结果 |
| --- | ---: | --- | --- |
| 问题一：典型日计划 | 35,126.95 | 相比无储能基线，费用降低 26.90% | [求解过程](docs/04-question-1.md) · [结果包](result/result1/README.md) |
| 问题二：固定电价日前计划 | 16,699,663.60 | 紧急购电费用为 993,419.64 元，作为日内调整的比较基准 | [求解过程](docs/05-question-2.md) · [结果包](result/result2/README.md) |
| 问题三：固定电价日内调整 | 16,382,836.18 | 相比问题二降低 1.90%，全年调整 665 次 | [求解过程](docs/06-question-3.md) · [结果包](result/result3/README.md) |
| 问题四：波动电价日前计划 | 17,224,180.08 | 按波动电价重新优化，紧急购电费用为 1,055,616.85 元 | [求解过程](docs/07-question-4.md) · [结果包](result/result4-2/README.md) |
| 问题四：波动电价日内调整 | 16,969,696.31 | 相比同电价日前方案降低 1.48%，全年调整 675 次 | [求解过程](docs/07-question-4.md) · [结果包](result/result4-3/README.md) |

这些结果来自历史数据回放。问题一的单日费用不与年度费用直接比较；问题三与问题二的信息条件不同，在相同零点信息的对照下，日内更新在固定电价和波动电价下分别节省 0.75% 和 0.79%。各问题的完整推导、运行命令和结果解读见下方章节目录。

> [!NOTE]
> 主结果来自附件数据的历史回放。日内调整降低了总费用和大额缺口，但未同时降低日费用波动与紧急购电发生频率；完整比较边界见[检验与适用范围](docs/08-validation.md)。

<a name="overview"></a>

## 总体思路与核心图表

程序将原始数据对齐到 10 分钟时段，按决策时刻可获得的信息构造预测与场景，联合优化购电和储能，再核验能量平衡与费用。四问依次覆盖典型日、年度日前计划、日内滚动调整和波动电价。

![数据到求解结果的总体流程](assets/overview/sxjm-pipeline-overview.png)

储能通过转移用电时段降低成本：典型日购电量减少约 **3.73%**，购电费下降 **26.90%**。年度日内更新在相同零点信息下，固定电价与波动电价分别节省 **0.75%** 和 **0.79%**。

![门控与基线对比](assets/results/05_gate_and_baseline.png)

*同一零点信息下的比较同时包含日内新预报、储能重优化与购电门控的作用。*

<a name="chapters"></a>

## 章节目录

| 章节 | 内容 |
| --- | --- |
| [01 · 总体思路](docs/01-overview.md) | 四问关系、模型处理流程与项目架构图 |
| [02 · 数据与预处理](docs/02-data.md) | 问题拆解、数据范围、字段、质量检查与输出口径 |
| [03 · 统一模型](docs/03-model.md) | 符号、参数、储能约束与信息边界 |
| [04 · 问题一](docs/04-question-1.md) | 典型日线性规划、求解过程与结果分析 |
| [05 · 问题二](docs/05-question-2.md) | 因果预测、分布鲁棒日前计划与年度回放 |
| [06 · 问题三](docs/06-question-3.md) | 日内滚动更新、价值门控与调整结算 |
| [07 · 问题四](docs/07-question-4.md) | 波动电价下的日前与日内策略 |
| [08 · 结果检验](docs/08-validation.md) | 数值核验、统计比较、敏感性与适用范围 |
| [09 · 开发说明](docs/09-development.md) | 完整目录、源码职责、开发验证与排版约定 |

<a name="q1"></a>
<a name="q2"></a>
<a name="q3"></a>
<a name="q4"></a>

### 结果文件

| 方案 | Excel 工作簿 | 结果包说明 |
| --- | --- | --- |
| 问题一 | [result1.xlsx](result/result1/result1.xlsx) | [图表与运行摘要](result/result1/README.md) |
| 问题二 | [result2.xlsx](result/result2/result2.xlsx) | [图表与运行摘要](result/result2/README.md) |
| 问题三 | [result3.xlsx](result/result3/result3.xlsx) | [图表与运行摘要](result/result3/README.md) |
| 问题四日前 | [result4-2.xlsx](result/result4-2/result4-2.xlsx) | [图表与运行摘要](result/result4-2/README.md) |
| 问题四日内 | [result4-3.xlsx](result/result4-3/result4-3.xlsx) | [图表与运行摘要](result/result4-3/README.md) |

<a name="run"></a>

## 开发环境与运行

### 开发环境简介

| 项目 | 环境与用途 |
| --- | --- |
| Python | `pyproject.toml` 要求 **3.12 或更高版本**；本地 `.venv` 使用 CPython 3.12 |
| 本地开发平台 | Windows + PowerShell；下文命令均在项目根目录执行 |
| 环境管理 | uv 创建 `.venv`，按 `uv.lock` 同步依赖；项目设置 `package = false`，无需安装项目包 |
| 数值计算与求解 | NumPy 处理数组，SciPy 构造稀疏约束并调用 HiGHS 求解器 |
| 数据与工作簿 | pandas 处理时序表，openpyxl 读写 Excel，无需启动 Microsoft Excel |
| 图表 | Matplotlib 输出调度、费用和数据诊断图 |
| 类型与格式 | pandas-stubs、scipy-stubs 提供类型提示；Ruff 配置为 Python 3.12、100 字符行宽，Ruff 本身未列入项目依赖 |
| 回归测试 | Python 标准库 unittest，通过 `main.py test` 发现 `src/test_*.py` |

依赖版本要求以 [pyproject.toml](pyproject.toml) 为准，完整解析版本见 [uv.lock](uv.lock)。先准备 Python 与 uv，并用以下命令确认工具可用：

```powershell
python --version
uv --version
```

### 安装与首次运行

在项目根目录执行；`--locked` 要求沿用现有锁文件，配置与锁文件不一致时会报错，便于发现环境偏差：

```powershell
uv sync --locked
uv run python main.py preprocess
uv run python main.py solve --ablations
uv run python main.py test
```

所有操作均通过根目录 `main.py` 分派。`solve` 运行全部问题，`--ablations` 额外计算相同零点信息下不进行日内更新的对照组。最终文件按问题保存到 `result/result1/`、`result/result2/`、`result/result3/`、`result/result4-2/` 和 `result/result4-3/`。每份包含工作簿、图片、运行摘要、程序输出与独立 README；中间计算使用系统临时目录，完成后自动清理。

分问命令只更新对应的最终结果包。完整年度总运行同时更新 `assets/results/` 中的展示图，结果包中的图片为独立副本。问题二至四的一日检查必须显式指定独立交付目录，例如：

```powershell
uv run python main.py q2 --days 1 --output result/check
```

`result/check/` 仅供开发验证，不能与完整年度费用直接比较。

<a name="project"></a>

## 项目目录

```text
SXJM/
├─ README.md          # 项目概览、核心结果与开发环境
├─ docs/              # 01—09 分章说明：数据、模型、四问求解、检验与开发
├─ main.py            # 统一命令行入口
├─ src/               # 数据处理、优化、报告、核验与回归测试
├─ data/              # raw 原始附件、templates 模板、processed 求解输入
│                     # interim 文本镜像、feature_store 可选分析特征
├─ assets/            # 总体流程、结果图、交互图与数据诊断图
├─ result/            # result1、result2、result3、result4-2、result4-3
├─ 题目.docx         # 原始题目
├─ pyproject.toml     # Python 要求、依赖与格式配置
└─ uv.lock            # 依赖锁文件
```

完整文件目录与源码职责见[开发说明](docs/09-development.md)。原始附件和模板只读，开发验证输出使用 `result/check*/`。

<p align="center"><a href="#top">返回顶部 ↑</a></p>
