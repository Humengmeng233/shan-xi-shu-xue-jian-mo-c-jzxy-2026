<a name="project"></a>

[项目首页](../README.md) · [章节目录](../README.md#chapters)

# 项目目录与开发说明

[文件目录](#project-section-1) · [最终结果入口](#project-section-2) · [源码模块职责](#project-section-3) · [开发与验证](#project-section-4)

<a name="project-section-1"></a>

## 文件目录

以下列出受版本管理的主要文件；`.venv/`、`__pycache__/` 和编辑器缓存属于本地产物，不纳入项目交付。

```text
SXJM/
├─ README.md                 # 项目概览、核心结果与开发环境
├─ docs/                     # 分章模型、数据、检验与开发文档
├─ main.py                   # 唯一运行入口
├─ src/                      # 全部 Python 模块与回归测试，平铺存放
├─ assets/
│  ├─ overview/              # README 总体流程与四问关系图
│  ├─ results/               # README 展示用结果图表
│  ├─ diagrams/              # 可交互架构、流程图及其定义
│  └─ data/                  # 可选数据诊断图
├─ 题目.docx                # 原始题目
├─ data/
│  ├─ raw/                   # 原始附件
│  ├─ templates/             # 只读空白结果模板
│  ├─ interim/               # 工作簿的表格文本镜像
│  ├─ processed/             # 求解输入，保留物理单位
│  └─ feature_store/         # 可选特征与缩放参数
├─ result/                  # 仅保存最终交付，每份均有独立 README
│  ├─ result1/               # 问题一
│  ├─ result2/               # 问题二
│  ├─ result3/               # 问题三
│  ├─ result4-2/             # 问题四日前
│  └─ result4-3/             # 问题四日内
├─ pyproject.toml            # 依赖与代码格式配置
├─ uv.lock                   # 依赖锁文件
├─ .editorconfig             # UTF-8、LF 与缩进约定
├─ .gitattributes            # Git 文件属性
└─ .gitignore                # 本地环境、缓存与开发检查结果的忽略规则
```

原始题目见 [题目.docx](../题目.docx)。数据说明、数学推导和检验结论按章节保存在 `docs/`，README 提供统一入口。

<a name="project-section-2"></a>

## 最终结果入口

| 结果包 | 内容与说明 |
|---|---|
| [问题一](../result/result1/README.md) | 典型日工作簿、储能调度图、费用输出与解读 |
| [问题二](../result/result2/README.md) | 年度日前工作簿、预测执行图、费用和检验摘要 |
| [问题三](../result/result3/README.md) | 日内调整工作簿、费用比较图、调整次数与解读 |
| [问题四日前](../result/result4-2/README.md) | 波动电价日前工作簿、结果图与运行说明 |
| [问题四日内](../result/result4-3/README.md) | 波动电价日内工作簿、门槛比较图与结果说明 |

每份目录只包含工作簿、相关图片、`运行结果.json`、`程序输出.txt` 和 `README.md`。图片从展示资源复制，`assets/` 中的原图保留。计算过程的时段表、日志、模板检查缓存和临时预览不纳入交付目录。

<a name="project-section-3"></a>

## 源码模块职责

| 文件 | 主要职责 |
| --- | --- |
| [main.py](../main.py)、[arguments.py](../src/arguments.py) | 命令分派与命令行参数处理 |
| [paths.py](../src/paths.py)、[model_config.py](../src/model_config.py) | 项目路径与统一模型参数 |
| [preprocessing.py](../src/preprocessing.py)、[convert_data.py](../src/convert_data.py) | 生成标准求解输入、导出工作簿文本镜像 |
| [feature_pipeline.py](../src/feature_pipeline.py) | 可选分析特征、缩放参数与诊断 |
| [causal_scenarios.py](../src/causal_scenarios.py) | 因果预测、校准区间与历史场景构造 |
| [optimization.py](../src/optimization.py)、[stochastic.py](../src/stochastic.py) | 确定性与随机优化、储能执行和费用计算 |
| [dispatch.py](../src/dispatch.py)、[solver.py](../src/solver.py) | 分问调度、年度回放与日内滚动求解编排 |
| [reporting.py](../src/reporting.py)、[visualization.py](../src/visualization.py) | 结果报告与图表生成 |
| [delivery.py](../src/delivery.py)、[validation.py](../src/validation.py) | 最终结果打包、工作簿与输入一致性核验 |
| [test_models.py](../src/test_models.py)、[test_cli.py](../src/test_cli.py) | 模型约束、信息边界与命令行回归测试 |

<a name="project-section-4"></a>

## 开发与验证

修改模型后，先执行回归测试，再将单日求解输出到独立检查目录：

```powershell
uv run python main.py test
uv run python main.py q2 --days 1 --output result/check
```

`result/check*/` 已由 `.gitignore` 忽略。环境配置见[开发环境与运行入口](../README.md#run)，完整交付的复核方式见[结果检验](08-validation.md#validation)。

<details>
<summary><strong>可选数据诊断 · 文本镜像与特征缩放</strong></summary>

可选数据检查：

```powershell
uv run python main.py convert
uv run python main.py features
```

`convert` 导出工作簿文本镜像；`features` 生成分析特征和缩放诊断，不是正式求解的前置条件。诊断图保存在 `assets/data/`，用于检查处理前后的尺度变化：

![数据缩放前后对比](../assets/data/feature_scaling_before_after.png)

全部数据表的处理对照见[前文的数据处理对照图](02-data.md#data-comparison)。

</details>

源码、注释和说明采用简洁中文；稳定的程序标识符与数据字段保留英文。原始附件与模板只读，修改模型后执行 `python main.py test`，并在独立目录执行一日求解检查。


## 文档排版约定

使用相对链接连接章节，使用 `<a name="…"></a>` 设置稳定锚点；标题变化后仍可保留导航。参见 [GitHub 基础排版语法](https://docs.github.com/en/get-started/writing-on-github/getting-started-with-writing-and-formatting-on-github/basic-writing-and-formatting-syntax#custom-anchors)。

公式使用 `math` 围栏和美元符号加反引号的行内分隔方式。函数名称采用直立体宏 `\mathrm`，避免截图中被拒绝的算子名称宏；公式不要放进 HTML 容器。参见 [GitHub 数学表达式文档](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/writing-mathematical-expressions)。


---

[返回本章顶部](#project) · [返回项目首页](../README.md)
