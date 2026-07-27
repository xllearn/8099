# DeepEval Advisory Dataset and Scheduled Evaluation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立真实可重放的 fixed10、100 个 Claim/章节校准单元、两个独立子智能体预标与人工 Golden 流程，并提供只读 Nightly/Weekly 一次性评测和 Judge 准确性报告。

**Architecture:** 项目自己的严格 Dataset Manifest、label 和 review 文件是数据真源；DeepEval `Golden`/`LLMTestCase` 只在执行时生成。固定集全部使用基础阶段的 A/B Projection、指标、Judge Adapter 和结果 Schema，Nightly/Weekly 强制 `cache_mode=bypass`，输出只进入 Advisory Artifact。

**Tech Stack:** Python 3.11、Pydantic 2、DeepEval 4.1.3 Worker、`unittest`、JSON/JSONL、SHA-256。

---

## 0. 前置条件与停止点

开始本计划前必须满足：

- `2026-07-27-deepeval-advisory-01-foundation.md` 已完成；
- Foundation 的 fake Judge 和 DeepEval 4.1.3 合约测试通过；
- `app/main.py`、`requirements.txt`、主 `Dockerfile` 未改变；
- 尚未调用真实 Judge。

永久约束：

```text
purpose = advisory_observability
blocking = false
affects_deliverable = false
affects_report_status = false
affects_release = false
affects_user_response = false
```

以下动作是显式停止点，不得自动越过：

1. 从 `.87` 或任何服务器读取/生成第 10 个真实案例；
2. 向两个预标子智能体发送真实 100 样本；
3. 调用任何真实 Judge；
4. 写入 `.87/.88`、部署或配置定时任务。

缺少用户对相应阶段的明确批准时，只实现 Schema、校验器、fake
集成测试和合成 fixture。不得把 9 个案例报告成 10 个，不得用复制、
删 `skip` 或动态选样伪造 fixed10。

## 1. 目标文件

```text
app/deepeval_advisory/
  datasets.py
  reporting.py

scripts/
  freeze_deepeval_dataset.py
  validate_deepeval_labels.py
  run_deepeval_advisory.py

tests/
  test_deepeval_advisory_dataset.py
  test_deepeval_advisory_calibration.py
  test_deepeval_advisory_scheduled.py

tests/fixtures/deepeval_advisory/
  fixed10/v1/
    manifest.json
    cases/
  calibration100/v1/
    manifest.json
    cases/
    labeling/
      rubric-v1.json
      prelabel-prompt-v1.md
      agent-a/
        run-manifest.json
        labels.jsonl
      agent-b/
        run-manifest.json
        labels.jsonl
      human/
        reviews.jsonl
        adjudications.jsonl
        golden.jsonl
```

运行产生的结果写入 Worker 专属 artifact 目录，不提交到
`tests/fixtures/**`。

### Task 1: 定义严格的数据集与标注 Schema

**Files:**

- Create: `app/deepeval_advisory/datasets.py`
- Create: `tests/test_deepeval_advisory_dataset.py`

- [ ] **Step 1: 写失败的 Schema 测试**

在 `tests/test_deepeval_advisory_dataset.py` 使用 `unittest` 覆盖：

- unknown field 被拒绝；
- `dataset_version` 只接受不可变版本 `fixed10/v1` 或
  `calibration100/v1`；
- `case_ref`、`source_group_ref`、Projection hash 和文件 hash 格式；
- case 只包含 `AdvisoryProjection` 的安全字段；
- raw `run_id`、`pack_id`、`articleid`、`menu_code`、文件名、路径、
  URL、Evidence C、完整报告和完整 Evidence Pack 被拒绝；
- manifest 的 `manifest_sha256` 从移除自身字段后的 canonical JSON
  计算；
- JSONL 每行独立通过严格 Schema；
- duplicate `case_ref`、路径逃逸和 symlink 被拒绝。

测试入口：

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_dataset.DatasetSchemaTests -v
```

Expected: FAIL because `datasets.py` does not exist.

- [ ] **Step 2: 实现精确 v1 合约**

在 `datasets.py` 定义：

```text
DatasetCase
  schema_version = 8099.deepeval-case/v1
  case_ref
  source_group_ref
  split = fixed | calibration | validation
  risk_tier = high | standard
  risk_tags[]
  material_identity_sha256
  source_content_sha256
  projection
  case_sha256

DatasetEntry
  case_ref
  relative_path
  file_sha256
  source_group_ref
  split
  risk_tier

DatasetManifest
  schema_version = 8099.deepeval-dataset-manifest/v1
  dataset_id
  dataset_version
  projection_version = claim-ab-v1
  rubric_version
  entries[]
  subsets
  declared_count
  runnable_count
  exclusion_count
  manifest_sha256

Prelabel
  schema_version = 8099.deepeval-prelabel/v1
  case_ref
  agent_id = agent-a | agent-b
  input_sha256
  rubric_sha256
  prompt_sha256
  metric_labels
  bounded_reason

HumanReview
  schema_version = 8099.deepeval-human-review/v1
  review_ref
  case_ref
  reviewer_ref
  review_reason[]
  metric_labels
  reviewed_at

GoldenLabel
  schema_version = 8099.deepeval-golden/v1
  case_ref
  human_review_refs[]
  metric_labels
  golden_sha256
```

`risk_tags` 只允许：

```text
date
amount
entity
procurement_scope
attachment_state
evidence_c_boundary
memory_boundary
unsupported_key_conclusion
```

任一 `risk_tags` 非空时 `risk_tier` 必须为 `high`。理由最多 500
字符，只保存结论性理由，不保存 chain-of-thought。

- [ ] **Step 3: 实现 canonical 读取与验证**

提供以下精确公共入口：

- `load_dataset_manifest(path: Path) -> DatasetManifest`
- `load_dataset_case(root: Path, entry: DatasetEntry) -> DatasetCase`
- `iter_jsonl(path: Path, model_type: type[StrictModel]) -> Iterator[StrictModel]`
- `validate_dataset_tree(root: Path) -> DatasetValidation`

校验必须先拒绝 symlink、非普通文件、绝对路径、`..`、UTF-8/JSON
错误和超限文件，再校验文件 hash、case 自身 hash、manifest hash。
`DatasetValidation` 只报告 `valid`/错误分类的确定性完整性，不代表
报告质量或发布通过。

- [ ] **Step 4: 运行并提交**

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_dataset.DatasetSchemaTests -v
git add app/deepeval_advisory/datasets.py tests/test_deepeval_advisory_dataset.py
git commit -m "feat: define advisory dataset contracts"
```

Expected: Schema tests PASS; no fixture is claimed ready yet.

### Task 2: 实现 immutable fixed10 freeze 与验证

**Files:**

- Modify: `app/deepeval_advisory/datasets.py`
- Create: `scripts/freeze_deepeval_dataset.py`
- Modify: `tests/test_deepeval_advisory_dataset.py`

- [ ] **Step 1: 写 fixed10 失败测试**

覆盖以下精确不变量：

```text
declared_count = 10
runnable_count = 10
exclusion_count = 0
len(entries) = 10
len(unique case_ref) = 10
len(unique material_identity_sha256) = 10
fixed3 contains exactly 3 entries from fixed10
every referenced case and hash exists
no case contains skip or exclusion
```

增加一个 legacy contract test，证明
`tests/fixtures/8099_regression_cases.json` 仍是“声明 10、实际 9”，
从而防止实现误把旧 manifest 当 Nightly 真源。

- [ ] **Step 2: 实现离线 freeze 命令**

命令：

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  scripts/freeze_deepeval_dataset.py fixed10 `
  --source-dir artifacts/deepeval-advisory/fixed10-approved-source `
  --output-dir tests/fixtures/deepeval_advisory/fixed10/v1
```

该命令只读已批准的 10 份安全 Projection export；不访问网络、不调用
报告生成器和 Judge。它必须：

1. 要求恰好 10 份不同材料身份；
2. 重新验证 A/B boundary；
3. 对 entry 排序；
4. 原子写 case、manifest 和 hash；
5. 若 output 已存在且内容不同则失败；
6. 不生成 skip/exclusion；
7. 对相同输入生成 byte-identical 输出。

- [ ] **Step 3: 用 10 份合成 Projection 验证工具**

测试在 `TemporaryDirectory` 生成 10 份最小安全 Projection，运行
freeze 两次并比较目录 hash。再用 9 份、重复材料、Evidence C 标记、
错误 hash 各验证一次 fail closed。

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_dataset.Fixed10FreezeTests -v
```

Expected: PASS with synthetic data; committed real fixed10 still absent until
Gate B.

- [ ] **Step 4: 提交工具**

```powershell
git add app/deepeval_advisory/datasets.py scripts/freeze_deepeval_dataset.py tests/test_deepeval_advisory_dataset.py
git commit -m "feat: freeze immutable advisory fixed10 datasets"
```

### Task 3: Gate B 下获取并冻结真实第 10 个案例

**Files:**

- Create after approval:
  `tests/fixtures/deepeval_advisory/fixed10/v1/manifest.json`
- Create after approval:
  `tests/fixtures/deepeval_advisory/fixed10/v1/cases/*.json`

- [ ] **Step 1: 停止并取得明确批准**

向用户报告：

- 旧 fixed10 的第 4 个
  `fixed-4-guizhou-project-analysis` 因
  `SOURCE_ATTACHMENT_UNAVAILABLE` 不可运行；
- 第一候选是静态 16-case 中
  `project_information / 28296323-9aa5-4fa0-81c0-36f6c3e18adc`；
- 需要在获批隔离环境只运行一次现有报告链路，取得 run 和 pack 的
  只读副本；
- 本步骤会触发真实报告生成成本，但不调用 DeepEval Judge。

没有明确批准：标记此 Task blocked，继续完成不依赖真实 fixed10 的
Schema/测试，不得创建假 case。

- [ ] **Step 2: 在批准的隔离环境生成候选**

先用 `apply_patch` 创建不提交 Git 的
`artifacts/deepeval-advisory/candidate10-source-manifest.json`：

```json
{
  "cases": [
    {
      "id": "case10_candidate_guangdong_mzgl",
      "combo": "1+0",
      "name": "广东麻醉管路三类耗材带量联动采购文件原始公告",
      "primary": [
        {
          "menu_code": "project_information",
          "articleid": "28296323-9aa5-4fa0-81c0-36f6c3e18adc"
        }
      ],
      "auxiliary": [],
      "manual_keywords": ["广东", "麻醉管路"],
      "reason": "DeepEval fixed10 第十案例候选，只允许本次显式记录。"
    }
  ]
}
```

然后使用现有 16-case runner 的单 case source-manifest 入口：

```powershell
docker run --rm --network bridge --entrypoint python `
  -v "${PWD}:/work" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  scripts/run_16case_report_regression.py `
  --base-url http://192.168.34.87:8099 `
  --case-set 16 `
  --source-manifest artifacts/deepeval-advisory/candidate10-source-manifest.json `
  --output-dir artifacts/deepeval-advisory/candidate10-run
```

此命令只能在用户批准的环境和预算内执行。运行前先用
network-disabled 的一次性容器测试确认 source manifest 被加载为恰好
1 个 case；不得省略
`--source-manifest`，否则脚本会回退到 16-case/dynamic 选样。

- [ ] **Step 3: 只读导出安全 Projection**

从生成后的 run 和 pack 使用 Foundation 的 `build_projection`，
输出到
`artifacts/deepeval-advisory/fixed10-approved-source/case10.json`。
导出前后分别计算 source run 与 pack SHA-256 并要求完全相同。

若候选缺附件、无法形成正式正文、Projection 超限或不能重放：

- 停止；
- 保存错误分类，不保存不完整 case；
- 向用户报告并请求批准另一个明确候选；
- 不修改旧 case 的 `skip`。

- [ ] **Step 4: 准备另外 9 份真实安全 Projection**

逐份从旧 9 个可运行案例的已批准 run/pack 导出，不复制旧 manifest
中的不完整材料。要求每份 `material_identity_sha256` 与
`source_content_sha256` 可追溯且不同。

- [ ] **Step 5: freeze、复核并提交**

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  scripts/freeze_deepeval_dataset.py fixed10 `
  --source-dir artifacts/deepeval-advisory/fixed10-approved-source `
  --output-dir tests/fixtures/deepeval_advisory/fixed10/v1
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_dataset -v
git add tests/fixtures/deepeval_advisory/fixed10/v1
git commit -m "test: freeze ten replayable advisory cases"
```

Expected: 10 declared, 10 runnable, 0 excluded; no network path, raw ID,
complete report, complete pack, Evidence C or secret in committed fixture.

### Task 4: 建立 calibration100 manifest 与无泄漏分组

**Files:**

- Modify: `app/deepeval_advisory/datasets.py`
- Modify: `scripts/freeze_deepeval_dataset.py`
- Create: `tests/test_deepeval_advisory_calibration.py`
- Create after source approval:
  `tests/fixtures/deepeval_advisory/calibration100/v1/manifest.json`
- Create after source approval:
  `tests/fixtures/deepeval_advisory/calibration100/v1/cases/*.json`

- [ ] **Step 1: 写失败的 100 样本测试**

固定以下规则：

```text
total units = 100
validation units >= 40
calibration units <= 60
each source_group_ref appears in exactly one split
all validation units have risk_tier and risk_tags
all files and projections are hash-bound
```

测试还要构造“同一公告两单元跨 split”并确认失败。

- [ ] **Step 2: 实现确定性 split**

`freeze_deepeval_dataset.py calibration100` 输入经批准的安全 Projection
单元和显式 source group，不自行抓取记录。split 算法：

1. 按 `source_group_ref` 排序；
2. 以 source group 为不可拆分单元；
3. 使用 manifest 中明确提交的 `split_assignment`；
4. 校验 validation 至少 40、calibration 至多 60；
5. 禁止运行时随机抽样；
6. 同一输入产生相同 manifest hash。

不允许脚本为了凑 60/40 拆分来源组；数量不满足时直接失败并要求
人工调整显式 assignment。

- [ ] **Step 3: 建立风险覆盖验证**

100 样本至少覆盖设计规定的八个 `risk_tags`。风险标签由两个预标
Agent 各自提出、人工 reviewer 最终确认；在人工确认前数据集状态为
`labeling_incomplete`。

- [ ] **Step 4: 运行并提交 Schema/工具**

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_calibration.CalibrationManifestTests -v
git add app/deepeval_advisory/datasets.py scripts/freeze_deepeval_dataset.py tests/test_deepeval_advisory_calibration.py
git commit -m "feat: enforce grouped advisory calibration splits"
```

真实 100 case fixture 只有在来源使用获批后单独提交。

### Task 5: 执行两个独立子智能体预标

**Files:**

- Create:
  `tests/fixtures/deepeval_advisory/calibration100/v1/labeling/rubric-v1.json`
- Create:
  `tests/fixtures/deepeval_advisory/calibration100/v1/labeling/prelabel-prompt-v1.md`
- Create after both runs:
  `tests/fixtures/deepeval_advisory/calibration100/v1/labeling/agent-a/run-manifest.json`
- Create after both runs:
  `tests/fixtures/deepeval_advisory/calibration100/v1/labeling/agent-a/labels.jsonl`
- Create after both runs:
  `tests/fixtures/deepeval_advisory/calibration100/v1/labeling/agent-b/run-manifest.json`
- Create after both runs:
  `tests/fixtures/deepeval_advisory/calibration100/v1/labeling/agent-b/labels.jsonl`
- Modify: `tests/test_deepeval_advisory_calibration.py`

- [ ] **Step 1: 冻结相同输入与 rubric**

生成一个 canonical prelabel bundle，包含 100 个安全单元、四个 metric
的版本化标注说明、风险标签定义和输出 Schema。记录：

```text
dataset_manifest_sha256
bundle_sha256
rubric_sha256
prelabel_prompt_sha256
```

bundle 不包含 DeepEval/Judge 分数、另一 Agent 输出、完整报告、完整
Evidence Pack、Evidence C、memory 或 raw source ID。

- [ ] **Step 2: 启动两个相互不可见的子智能体**

协调 Agent 必须同时创建两个独立任务，使用无历史上下文的 worker：

- Agent A 只接收 bundle、rubric、prompt 和输出 Schema；
- Agent B 接收完全相同的四项输入；
- 两者都不得读写仓库，不得调用 DeepEval/Judge，不得查看另一任务；
- 两者只把结构化 label 返回给协调 Agent；
- 协调 Agent 在两者均结束前不向任何一方披露输出。

若执行环境不能保证任务输出隔离，则停止并改用两个真正隔离的会话；
不得把同一会话的两次顺序回答称为“独立预标”。

- [ ] **Step 3: 验证并持久化两个输出**

协调 Agent 分别验证 100 行严格 Schema，然后写入独立目录。两个
`run-manifest.json` 必须记录：

```text
agent_id
model_version
prompt_version
dataset_manifest_sha256
bundle_sha256
rubric_sha256
prelabel_prompt_sha256
labels_sha256
started_at
completed_at
```

验证两者 `bundle_sha256`、rubric 和 prompt hash 相同，model/output
hash 独立。只保存 bounded reason，不保存 chain-of-thought。

- [ ] **Step 4: 写并运行独立性测试**

测试必须发现：

- 少于 100 行；
- 两 Agent 使用不同 input/rubric/prompt hash；
- output 互相复制且 run identity 相同；
- label 中出现 Judge/DeepEval 字段；
- 任一 Agent 输出先被嵌入另一 Agent 输入。

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_calibration.IndependentPrelabelTests -v
```

- [ ] **Step 5: 提交预标证据**

```powershell
git add tests/fixtures/deepeval_advisory/calibration100/v1/labeling tests/test_deepeval_advisory_calibration.py
git commit -m "data: record independent advisory prelabels"
```

### Task 6: 完成人工审核、裁决和 Golden 验证

**Files:**

- Create: `scripts/validate_deepeval_labels.py`
- Create after human work:
  `tests/fixtures/deepeval_advisory/calibration100/v1/labeling/human/reviews.jsonl`
- Create after human work:
  `tests/fixtures/deepeval_advisory/calibration100/v1/labeling/human/adjudications.jsonl`
- Create after human work:
  `tests/fixtures/deepeval_advisory/calibration100/v1/labeling/human/golden.jsonl`
- Modify: `tests/test_deepeval_advisory_calibration.py`

- [ ] **Step 1: 实现 review requirement 计算**

脚本对每个 case 计算原因集合：

```text
agent_disagreement
high_risk
validation_split
```

要求人工审核：

- 两 Agent 全部分歧；
- 全部 `risk_tier=high`，即使两者一致；
- validation split 全部单元。

三类可以重叠，但每个 case 的所有触发原因必须写入 review。

- [ ] **Step 2: 写失败测试**

构造并拒绝：

- 漏掉一个 disagreement；
- 漏掉一个 high-risk agreement；
- 40 个 validation 中有一个未人工确认；
- review 引用不存在的 case/Agent label；
- Golden 没有人工 review provenance；
- 一个来源跨 calibration/validation；
- review 内容含 chain-of-thought 或超限 reason。

- [ ] **Step 3: 生成人工审核清单**

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  scripts/validate_deepeval_labels.py prepare-review `
  --dataset tests/fixtures/deepeval_advisory/calibration100/v1 `
  --output artifacts/deepeval-advisory/human-review-queue.jsonl
```

该命令只生成清单，不代替人工决定。人工 reviewer 填写结构化 label
和 bounded reason；全部分歧需要 adjudication 引用。

- [ ] **Step 4: 验证最终 Golden**

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  scripts/validate_deepeval_labels.py verify `
  --dataset tests/fixtures/deepeval_advisory/calibration100/v1
```

Expected:

```text
units=100
agent_a_labels=100
agent_b_labels=100
disagreements_reviewed=all
high_risk_reviewed=all
validation_units>=40
validation_human_confirmed=all
source_group_leakage=0
```

未经人工确认的低风险一致样本可以保留为 calibration evidence，但不写
入 `golden.jsonl`，也不计入独立验证准确率。

- [ ] **Step 5: 提交人工审计链**

```powershell
git add scripts/validate_deepeval_labels.py tests/test_deepeval_advisory_calibration.py tests/fixtures/deepeval_advisory/calibration100/v1/labeling/human
git commit -m "data: validate human advisory golden labels"
```

### Task 7: 实现 Nightly/Weekly 一次性 runner

**Files:**

- Create: `app/deepeval_advisory/reporting.py`
- Create: `scripts/run_deepeval_advisory.py`
- Create: `tests/test_deepeval_advisory_scheduled.py`

- [ ] **Step 1: 写 fake Judge 失败测试**

覆盖：

- `--mode fixed10` 恰好加载 10 个可运行 case；
- fixed10 每 case 至少评一次，默认 `repeat=1`；
- `--mode calibration100` 恰好加载 100 个 case；
- calibration100 默认并固定 `repeat=2`；
- 两种模式都把 `cache_mode=bypass` 传入 evaluator；
- 即使存在 runtime cache，也发生新的 fake Judge call；
- 单个 metric 错误记录为 `partial/unavailable`，不记分为 0；
- Judge 全部失败时命令仍生成 artifact，不产生发布失败；
- 输出没有 `passed`、release decision、deliverable/status override；
- dataset/Projection/metric/prompt/adapter/Judge profile/code 版本齐全。

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_scheduled.ScheduledRunnerTests -v
```

Expected: FAIL because runner and reporting modules do not exist.

- [ ] **Step 2: 实现固定模式**

CLI 只支持：

```text
runtime-worker
backfill
fixed10
calibration100
```

本子计划先实现 `fixed10` 与 `calibration100`；另外两个 mode 明确返回
“由 Runtime 子计划实现”，不得偷偷启动扫描器。

正式命令：

```powershell
python scripts/run_deepeval_advisory.py --mode fixed10
python scripts/run_deepeval_advisory.py --mode calibration100
```

固定模式忽略 runtime cache 设置并强制 `cache_mode=bypass`。若调用方
尝试传 `use`，CLI 直接拒绝。调度频率由外部 scheduler 决定；仓库
不自行创建 cron/Task Scheduler。

- [ ] **Step 3: 实现 Advisory-only 报告**

每次 run 原子写：

```text
scheduled/<mode>/<run_ref>/manifest.json
scheduled/<mode>/<run_ref>/results.jsonl
scheduled/<mode>/<run_ref>/summary.json
scheduled/<mode>/<run_ref>/summary.md
```

`summary.json`/Markdown 展示：

- 各 metric 样本数、scored/unavailable/partial 数；
- median/min/max 和 repeat 波动；
- 与上一个相同版本 dataset/Judge/metric 的趋势；
- token、cost、latency、error category；
- fixed10 的每日 drift；
- calibration100 与 human Golden 的一致率和各 risk tag 混淆矩阵。

Judge accuracy 的展示分箱固定为
`judge-accuracy-bands-v1`：

```text
score >= 0.80  consistent
0.50 <= score < 0.80  mixed
score < 0.50  inconsistent
metric not applicable  not_applicable
```

该分箱只用于与人工 label 对照，不是业务阈值、质量门禁或发布判定。
不计算跨 metric 综合分，不用平均分覆盖 P0。

- [ ] **Step 4: 运行 fake 集成测试**

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_scheduled -v
```

Expected: PASS using only fake Judge and temporary artifacts.

- [ ] **Step 5: 提交**

```powershell
git add app/deepeval_advisory/reporting.py scripts/run_deepeval_advisory.py tests/test_deepeval_advisory_scheduled.py
git commit -m "feat: add advisory nightly and weekly runners"
```

### Task 8: Gate B 后运行真实固定集

- [ ] **Step 1: 再次核对真实 Judge 前置**

必须全部具备：

- 用户批准 Provider-specific 追加计划；
- HTTPS origin、wire protocol、immutable model/profile 已冻结；
- Worker-only secret 注入已验证；
- Provider 留存、地域、二次训练政策已批准；
- 每次运行请求数和货币成本硬上限；
- fixed10 10/10 和 calibration100/Golden 校验通过。

缺一项：只运行 fake Judge，不把 Dataset 完成误报为 Judge 已验证。

- [ ] **Step 2: 最小 fixed3 smoke**

先在批准环境运行固定 3 个 case；记录 resolved model、prompt、adapter、
metric、token、cost、latency 和 error 分类。任何 boundary violation
立即停止，不自动重试。

- [ ] **Step 3: Nightly fixed10**

```powershell
python scripts/run_deepeval_advisory.py --mode fixed10
```

确认 10/10 enrollment、`cache_mode=bypass`、每 case 至少一次；分数
高低和 Judge 不可用均不影响发布。

- [ ] **Step 4: Weekly calibration100**

```powershell
python scripts/run_deepeval_advisory.py --mode calibration100
```

确认 100/100 enrollment、每 case 两次 Judge、全部 validation Golden
参与 accuracy，对未人工确认 calibration case 不宣称独立准确率。

- [ ] **Step 5: 只提交代码和不可变数据，不提交运行秘密**

运行 artifacts、secret、provider request/response 原文不进入 Git。
需要留存的审核证据只包含 hash、版本、计数、bounded error 和成本汇总。

## 2. 整体验证

在 Worker 容器运行：

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest `
  tests.test_deepeval_advisory_dataset `
  tests.test_deepeval_advisory_calibration `
  tests.test_deepeval_advisory_scheduled `
  -v
```

静态检查：

```powershell
rg -n "passed|affects_deliverable.*true|affects_report_status.*true|affects_release.*true|blocking.*true" `
  app/deepeval_advisory scripts/run_deepeval_advisory.py `
  tests/fixtures/deepeval_advisory
```

Expected:

- 只有测试中的禁止性断言允许出现 `passed`；
- 不出现任何 `true` 的影响/阻断字段；
- fixed10 是 10/10/0；
- calibration100 是 100，validation 至少 40 且全部人工确认；
- 两个 Agent 的输入/rubric/prompt hash 相同，输出和 run identity 独立；
- Nightly/Weekly 永远 cache bypass；
- 结果只有分数、覆盖、准确性、成本、延迟和错误，不产生发布结论。

## 3. 回滚

数据或 runner 有误时：

1. 停止外部 Nightly/Weekly scheduler；
2. 不删除或改写历史 artifacts；
3. 回退 dataset/label/runner 提交；
4. 保留错误版本 hash 和原因；
5. 发布新的 immutable dataset 版本，不就地重写 `v1`；
6. 主服务、报告、Word、状态和用户链路无需回滚，因为本计划不修改它们。
