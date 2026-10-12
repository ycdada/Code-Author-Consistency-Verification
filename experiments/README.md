# 可复现作者声明核验实验

此工具核验“候选程序是否与所声明作者的历史代码相容”。**不检测 AI、不输出 AI 概率、不以题目标签冒充作者标签。** 不编译、不运行任何输入 C++，只读取文本。

## 运行

Python 3.12；依赖版本见 `requirements.txt`。已有依赖时不需要安装任何东西。可在隔离虚拟环境从官方 Python 包索引安装这些常用依赖：

```sh
python -m pip install -r experiments/requirements.txt
python -m unittest discover -s tests -p 'test_benchmark.py' -v

# DATA 与 OUT 是用户指定的外部路径；OUT 必须为空且在本源码树之外。
python experiments/benchmark.py --data "$DATA" --out "$OUT" --seed 20261012 \
  --provenance "$PROVENANCE"

# 可选：使用 Node.js 调用现有 dist/engine.js 的 12 项词法习惯。
python experiments/benchmark.py --data "$DATA" --out "$OTHER_OUT" --seed 20261012 \
  --provenance "$PROVENANCE" --lexical
```

`--synthetic` 明确标记工程自测数据；所有单元测试都使用原创合成程序，不能把其分数当成真实效果。默认三个 TF-IDF 基线无需 Node。`--engine` 可指定兼容的 engine.js；默认按脚本相对路径定位，不依赖本机私有目录。

## 输入协议

每行一个 JSON 对象：

```json
{"id":"sample-001","author_id":"anonymous-author-01","problem_id":"contest/problem-01","timestamp":"2020-01-01T12:00:00Z","code":"int main(){return 0;}","source":"dataset-name/version","label":"normal"}
```

- 必填非空字符串：`id`、`author_id`、`problem_id`、`code`、`source`。`id` 唯一。
- `author_id` 必须代表真实提交者，而非题目、语言、类别。使用稳定匿名 ID；账户归属也不等于独立证明代码由账户本人原创。
- `problem_id` 跨作者保持同一题目的统一标识，跨比赛/年份应加命名空间。
- `timestamp` 可省略/为 null；若有则为带时区的 ISO8601 或 Unix 秒。不猜测无时区时间，不把比赛年份伪装成提交时间。
- `label` 可省略，或为 `normal`/`human`/`original`/`clean`。此输入必须是已知作者原始代码；混入 AI、未知作者或变换样本会被拒绝，需另建实验。
- 不支持运行输入程序。单个样本限制 500,000 字符。
- provenance JSON 可含 `dataset_url`、`version`、`license_url`、`license_review`、`author_identity_evidence`、`problem_id_derivation`、`collection_procedure`、`limitations`。不提供则结果明确警告身份、许可与构造未核验。请勿在该文件写秘密或公开个人账号。

## 预注册式固定协议

1. 先按规范换行后的 SHA-256 与去注释、忽略空白的原始 token SHA-256 建克隆族。所有重复族成员都隔离，包含同作者重复，不挑有利的一个保留。规范化改名、近重复和语义克隆尚未全面识别，报告保留此限制。
2. 固定种子划分题目互斥的 train / dev / test（约 60% / 20% / 20%）。最少 4 位作者、6 道不同题。数据不够即拒绝，不换 seed 寻找好结果。
3. 默认 `--split auto`：完整时间戳存在时，用去重时间序列的 60%/80% 切点做严格全局时间分割，跨切点的整道题隔离。保证所有 train 早于 dev，dev 早于 test。某分区为空时拒绝，绝不偷偷回退。
4. 时间缺失时明确用 `group_problem`；任何此模式结果都不能声称验证了历史→未来推广。显式 `--split time` 在时间缺失时直接拒绝。
5. train 为 enrollment；每位作者每题只取最早提交（无时间按 ID），减少同题反复提交的权重。默认至少 3 道参考题，可 `--min-enrollment` 调整但不得低于 2。dev/test 至少覆盖 4 位具足够 enrollment 的作者。未覆盖的候选统计并排除，不以默认分数代替。
6. 每个候选有 1 条真实作者声明（正常）及默认 3 条无放回、固定 seed 抽样的不同作者声明（不一致）。三个基线共享这些 claims；任务是同一批已入库作者的 verification，而非未知作者识别。
7. 字符 TF-IDF：区分大小写，字符 3–5 gram。原始 token TF-IDF：去注释的 token 1–3 gram。标识符规范化 token TF-IDF：按首次出现编号非 C++ 关键字，其余同前；数字、字符串仍保留。词法器是近似扫描器，不解析 C++ 作用域/宏/全部预处理规则。字符基线保留注释与空白。
8. TF-IDF 词表和 IDF **仅在 train 拟合**，上限 100,000 项。作者 profile 为训练程序单位 TF-IDF 向量均值后再单位化。分数是 `1 - cosine(candidate, claimed_author_profile)`，越大越不相容。零向量拒判。无模型/特征选择、无测试集调参。
9. 可选 12 项习惯沿用现有 engine：每作者每题等权 rate 平均。至少 5 道参考题；候选主体 ≥80 token；至少 4 项具有候选 ≥3 次机会、参考 ≥3 道可观察题。分数为共同覆盖项的 rate 绝对差均值。未满足就拒判，不能与 TF-IDF 不同覆盖率的样本简单比较。
10. 阈值只看 dev 正常声明，用固定目标经验 FPR（默认 5%）的保守顺序统计量选择；严格 `score > threshold` 才报警，并冻结到 test。相同分数平局不报警。此规则**不保证总体或未来 FPR 为 5%**；不足 100 条正常 dev 的校准很不稳定。

## 输出与解读

- `config.json`：种子、参数、依赖版本、输入数据与 runner 哈希。
- `split_manifest.json`：分区的样本/匿名作者/题目 ID、时间、源码/token 哈希、排除理由。失败时为明确 incomplete manifest，绝不伪造有效分区。
- `provenance.json`：来源与外部 provenance（若有）。
- `results.json`：每基线冻结阈值、dev/test FPR、TPR、ROC-AUC、覆盖和 95% 作者 cluster bootstrap 区间。
- `predictions.jsonl`：每条 claim 的分数、真实标签与冻结阈值判断；覆盖不足为 null。
- `artifact_hashes.json`：上述产物 SHA-256；不递归哈希自身。

这里 FPR = 真实作者声明中误报不一致的比例；TPR = 错误作者声明中检出的比例。ROC-AUC 必须同时有两类有效分数；无覆盖则不输出率。无覆盖的 12 项基线直接标为 `insufficient_coverage`。覆盖率必须与效果一起看，低覆盖不等于高可靠性。

作者数 <10 或 bootstrap 次数 <100 时区间明确为不足，不出精确区间。否则以候选的真实作者为 cluster 重采样、保留其全部 claims，阈值固定。区间是条件性近似：**未涵盖共享声明作者 enrollment 依赖、阈值校准不确定性、数据选择偏差**；退化的 [0,0] 不证明零风险。作者数/题数少时结果仅作探索性观察。适用于本划分的结果不可推广到别的竞赛、年份、作者规模或实际学生群体。

## 数据与公开仓库

不要提交真实源码、账号、原始 JSONL、下载包或实验输出。输出目录强制位于源码树外；其中 ID 和来源仍可能涉及隐私，应视为本地审计数据。可公开经过审阅的聚合数字及方法限制；许可不明确时不重新分发原始代码。测试不联网、不依赖真实数据或私有绝对路径。

## 已覆盖的工程回归

三个基线实际运行/固定 seed 重现、输出哈希重现、题目/样本/哈希互斥、严格时间顺序、跨时题隔离、缺时显式降级、克隆隔离、小数据拒绝、测试集独有 token 不进入词表且不改变阈值、标签约束、规范化词法器、配对、阈值平局、作者 cluster 区间、可选习惯覆盖不足和 CLI。

## 看真实分数前登记的第三种划分：`time_grouped`

```sh
python experiments/benchmark.py --data "$DATA" --out "$TEMPORAL_OUT" \
  --seed 20261012 --split time_grouped --provenance "$PROVENANCE" --lexical
```

这一独立对照在接触任何真实效果分数前加入。默认 `auto` / `time` 完全不变。

- 先用同一固定 seed 将全部题目按 60/20/20 分入互斥 train/dev/test 题组；题目归组不看特征、作者结果或分类分数。
- 全部去重后的真实时间值预定 60%/80% 切点为 T1/T2，与严格 time 的选点算法一致。
- train 题只保留 `t < T1`，dev 题只保留 `T1 <= t < T2`，test 题只保留 `t >= T2`。其他记录丢弃并逐条记入 `outside_assigned_problem_time_window`，不搬到其他分区。
- 因此题目严格互斥，所有 enrollment 早于 dev，dev 早于 test；长期开放的题不必整题丢弃，但数据利用率和作者覆盖可能很低。
- 全部时间戳必须存在；分区、作者或参考题不足即拒绝。不得换种子寻找能跑或好看的结果，不把另一个划分的好结果说成此模式效果。
- manifest 记录时间切点、每道题预定归组和排除 ID。`results.data_efficiency` 记录原始/去克隆/分割/作者过滤各阶段计数、保留比例和排除原因。即使严格模式拒绝，也保留可用的中间分区与完整跨窗排除清单供审计。

回归测试用每题均跨三个时期的明确合成数据，验证新协议可以保留合法的时间窗口，而原严格模式仍拒绝；验证所有题目与时间互斥，以及恰好 1/3 的数据保留率。以上是工程验证，不能作为真实作者检测效果。

## 参考规模对照：H=3 / 5 / 10 / all

本对照也在查看真实分数前登记，针对每周只有少量历史作业的使用约束。分别传 `--max-enrollment 3`、`5`、`10`，不传则为 all。每组必须使用相同原始数据、seed、split、min-enrollment 和其他参数，以及不同的空输出目录。

- 先完成相同的题目/时间划分与每作者每题一个代表样本，再固定同一批合格作者和候选集合。
- 时间完整的作者从历史中按时间最近优先取 H（同时间按 ID）；时间不完整则按 `SHA256(seed + NUL + sample_id)` 排序取 H。各 H 形成嵌套参考集。
- H 必须 ≥ min-enrollment，避免因人为降低参考资格而更换候选集合。各 H 的负声明采样也完全相同。
- 每个 H 独立只用自己的 train 重新拟合词表/IDF/作者中心，并只用该 H 的正常 dev 重新校准冻结阈值。不得沿用 all 的词表或根据 test 挑 H。
- 被截去的历史记录标记为 `enrollment_cap`；结果报告参考上限与实际每作者最少/最多参考数，并继续报告各基线覆盖。
- H=3 时 12 项习惯基线因要求 ≥5 道参考题而明确拒判。这一限制应原样报告，不放宽为可以出分。
- 所有组都报告，包含失败与低覆盖；不只挑最好的一个作为结论。这些是探索性对照，不是已验证部署建议。

## 第五个固定基线：原始 CST TF-IDF

在查看 CodeNet 真实效果分数前登记 `--structural`，使用 `structural_features.py` 中冻结的 `cpp-cst-v1`。可选依赖见 `requirements-structural.txt`，细节见 `STRUCTURAL_FEATURES.md`（若提取模块文档有更新，以其固定接口说明为准）。默认实验仍只要求三个词法基线。

- 原始代码的 CST 节点类型、父子/祖孙类型路径、深度/分支形状桶计数；**包含模板混杂，不是去模板 AST**。
- 固定 DictVectorizer + TfidfTransformer（L2、use_idf=True、smooth_idf=True、sublinear_tf=False），词表和 IDF 仅在当前 H 的合格 train 拟合。
- 每作者至少 min-enrollment 个解析可接受的参考题；不够时该作者 profile 不可用。训练中心再 L2 标准化，用 `1 - cosine` 评分，阈值和其他基线一样只由正常 dev 冻结。
- 解析拒绝、结构不足、零向量或不可用 profile 均为 score=null，不以 0 填充；manifest 的候选不因结构失败而更换，预测与效果报告必须包括覆盖。
- 输出记录解析器版本、模块哈希、解析拒绝 ID/原因/质量、原始与可用参考数量。可选依赖缺失明确报错，不偷偷换解析器或词法器。
- 这些特征和参数不会依据真实 dev/test 分数再修改；结果必须与其余固定基线一并报告。

一次执行完整预登记矩阵（需要 Node 和固定可选结构依赖）：

```sh
python experiments/run_matrix.py --data "$DATA" --out "$MATRIX_OUT" \
  --provenance "$PROVENANCE" --seed 20261012
```

在任何子实验开始前写 `matrix_plan.json`，固定三种 split × H=3/5/10/all 共 12 组、五个基线；顺序完成所有组，不按分数决定下一组。每个子目录遵循同样独立审计规则，拒绝与失败也会保存。`matrix_results.json` 的“完成所有尝试”不代表所有组都可评估，更不代表模型有效。脚本不下载数据、不安装依赖、不执行 C++。

## 个体差异与代码长度的只读评估

不改任何模型或冻结阈值，额外报告 test 正常声明按真实作者的 FPR、正常样本数、有效样本数和覆盖率的最小值、25/50/75/90/95 分位数、最大值；只有聚合分布，不输出作者 ID 名单或作者排名。另显示零覆盖作者数及有效正常样本少于 20 条的作者数，明确个体误报率与“最差值”很不稳定，不能用于学生处置。

代码长短使用预先固定的去注释原始 token 数桶：<100、100–499、≥500，每桶报告 FPR/TPR/AUC、样本数和 coverage。不根据 test 数值挑桶，不为不同桶另调阈值，小桶不支持稳健结论。

目标使用规模约 40 名学生仍需在适当授权和真实教学场景下单独验证；CodeNet 的 40 个竞赛账号便利样本不等于 40 名学生的验证，账号也不独立证明亲写。字符基线保留注释，可能学到源码中的名字/账号签名；即使字符分数更好，也不能直接归因于个人编码行为。本轮不依效果选择性删除身份头或注释。
