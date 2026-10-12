# 独立 AI 来源诊断（不是作者核验）

本脚本只检查公开语料中 human / AI 标签的可分性。不接入作者核验分数、不从混作者 human 语料建立“个人画像”，也不提供作弊概率、模仿识别率或现实泛化能力声明。

所有结果带 `diagnostic_not_task_disjoint`。**包装捷径、题目泄漏与作者泄漏都可能影响分数。** raw 是聊天包装敏感性诊断；code-only 也不是经编译验证的纯 C++。

## 数据与可证明的边界

- Profiler 官方仓库：<https://github.com/MarkGHX/Profiler>，GCJ 6 份 JSON，每份 274 条，共 1644 条。来源 human、GPT-3.5 Turbo、GPT-4 Turbo Preview、Claude 3 Sonnet、Claude 3 Opus、Gemini 1.0 Pro；使用已取得的 CC-BY-4.0 许可副本。公开报告须保留出处、许可及修改说明。
- 已静态检查官方 `profiler_utils.py` 的 `load_dataset`：GCJ 是直接返回的字符串列表。它没有给出作者、题目或时间，也没有证明不同模型文件相同索引的同题/同作者语义。相同数组长度和代码相似性不能代替出处证据。不伪造配对、不把行号当题目。
- SemEval 2026 的既有 164 条 C++ 本地抽样（77 human、87 AI）只作外部冒烟。当前输入的 author/problem/time 全缺；抽样不是总体代表样本。未追加下载或发布原数据；其许可与抽样边界应以数据采集报告为准。
- 无真实作者 cluster，拒绝作者聚类置信区间；无 problem_id，拒绝严格题隔离；无时间，拒绝时间外推；没有模仿样本，拒绝模仿检测指标。

## 冻结协议

默认 seed 20261012，超参数在代码 CONFIG 中固定，不按测试结果调参。两种预先指定基线全部报告，不选“最好”模型：

1. 字符 TF-IDF：3–5 gram，最多 20000 特征，min_df=2，保留大小写，sublinear_tf。
2. 长度/词法统计：12 项长度、空白、注释、预处理、标点及标识符长度特征；StandardScaler 仅 train 拟合。

二者均 logistic regression：C=1、balanced 权重、liblinear、max_iter=2000。它们输出未校准分类器分数。

- 对六个源做全局 exact SHA-256 / 简单 token SHA-256 克隆检查，**任何重复组全部成员隔离**，不保留一份。token 忽略空白及注释，保留标识符、文字字面量和标点；不是完整 C++ lexer，尤其不保证 raw-string/preprocessor 的精确语义；无法排除近克隆、改名或同算法复写。
- 剩余 human 固定 60/20/20 train/dev/test；各轮 human 测试集复用。AI 五轮留一生成器：该生成器全部仅 test，其他生成器各 80/20 train/dev；已见生成器不进入该轮 test。
- 固定 seed 对源和代码 hash 排序分配，不读取作者/题目标签猜分组。
- 特征词表、IDF、Scaler、分类器只 fit train。dev 在经验 FPR≤5% 的候选阈值中最大化 TPR，并列先低 FPR 再高阈值。测试不参与选择。5% 只是 dev 经验约束，不保证真实 FPR。
- 测试报告 FPR/TPR/ROC-AUC、样本数、FP/TP，以及阈值两侧固定 0.1 分数拒判带的 overall/class coverage 与保留部分 FPR/TPR。coverage 是拒判后的保留比例，不是准确率；拒判不是经过验证的安全机制。
- Wilson 95% 区间只作粗略逐行独立假设下描述，作者/题目依赖未满足，不能当有效聚类区间。五轮共享数据，不汇总成独立试验的置信区间。

## 包装与质量对照

在查看任何实测分数前冻结以下 label-independent 规则，对 human/每个 AI 源完全相同：

- raw 保留全部原文，只作包装敏感性诊断。
- code-only：行首 Markdown 围栏恰好一对、语言标记为空或 c/cpp/c++/cc/cxx 时，仅保留其中代码；无围栏原样保留并标记；多代码块、不闭合围栏、未知语言或空代码拒绝并单独计数。
- 不按标签清洗。记录各来源总数、保留/拒绝数，single-fence/unfenced、少于 80 个简易 token、非代码启发式、未闭合注释/字符串启发式 flags。
- 无围栏自然语言仍可能混入，heuristic flags 不是语法验证。各标记报告独立测试分层，分层可重叠；某层缺任一类别时 FPR/TPR/AUC 对应不可计算项为 null。
- raw 与 code-only 分别做克隆隔离和拟合；清洗改变文本/hash，因而部分 AI 划分或删失也可改变。它是敏感性对照，不是仅去包装的严格配对因果实验；不以其差值声称纯包装效应。
- 外部冒烟同样清洗，内部重复与对任一 Profiler 原文/处理后样本的 exact/token 重合全隔离。所有模型复用同一冒烟集，不再调阈值。

## 运行及产物

依赖见 `requirements.txt`；不运行 C++，不调用训练 API，不下载或执行数据集代码。

```sh
python -m unittest discover -s tests -p test_ai_diagnostic.py -v
python experiments/ai_diagnostic.py --mode raw --data "$PROFILER_GCJ" --out "$OUT/raw" --smoke "$SEMEVAL_CPP"
python experiments/ai_diagnostic.py --mode code_only --data "$PROFILER_GCJ" --out "$OUT/code_only" --smoke "$SEMEVAL_CPP"
```

OUT 必须在仓库外且为空。输出包含 config/script/dataset/split/model SHA-256、版本、完整聚合指标、质量与删失统计。每个模型 pickle 仅供本地受信环境复现；不要加载第三方不可信 pickle。模型参数可能暴露词表片段，**模型文件也禁止提交公开仓库**。

私有 split manifest 和 quarantine manifest 含逐行标识/hash，不含代码但仍不发布；原文、原始样本 ID、逐行 hash、词表、模型均留在仓库外。公开仓库只放方法、原创单测及经审阅无逐行标识的聚合统计。当前单测覆盖 token/字面量、克隆删除、留一与重现、dev 阈值、拒判、Wilson、train-only fit、输出位置与错误输入、围栏/词法质量，不能代替真实准确率验证。

### 可选 CST 质量分层

已有固定版本的 tree-sitter 0.25.2、tree-sitter-cpp 0.23.4 时，可加 `--cst-quality`。复用 `structural_features.extract` 的 accepted/rejection_reasons，报告 `cst_error_free`（同时满足模块的最低结构量要求）与 `cst_rejected` 测试分层及来源删失统计。这里只审计，不过滤主要训练/测试集，也不改变阈值。未安装/版本不符会明确报错，不偷偷降级。真实首轮两种 mode 的CST增强复跑，与无CST版本主要测试指标逐项完全相等，脚本hash核验一致。
