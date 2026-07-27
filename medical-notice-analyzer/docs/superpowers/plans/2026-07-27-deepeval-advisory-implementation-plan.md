# DeepEval Advisory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变 8099 用户链路、报告状态、deliverable、Word 或发布行为的前提下，建立每份正式报告的异步 DeepEval Advisory、固定数据集 Judge 观测、精确缓存和可暂停 Worker。

**Architecture:** 保持 `app/main.py` 和主服务镜像不引入 DeepEval；独立 Worker 只读扫描 analysis run 与 Evidence Pack，并把任务、投影、缓存、结果和覆盖率写入 Worker 专属存储。实施拆为基础合约、固定数据集、Runtime Worker 三份可独立验收的 TDD 子计划，真实 Judge 与任何 `.87` 操作均设置显式审批停点。

**Tech Stack:** Python 3.11、Pydantic 2、DeepEval 4.1.3、`unittest`、HTTPX、JSON/JSONL、Docker Compose。

---

## 1. 依据与优先级

本计划以以下文件为准：

1. 已批准设计：
   `docs/superpowers/specs/2026-07-27-deepeval-advisory-observability-design.md`
2. 当前事实分支：
   `codex/new-server-87-medical-notice-analyzer-20260722`
3. 设计提交：
   `3cf61872f8c5e8f31b684e908ee61fd8280c1d78`
4. 当前代码事实：
   - `app/main.py:1028-1071`：Evidence Pack 文件目录与读取；
   - `app/main.py:3946-3950`：analysis run 文件目录；
   - `app/main.py:4335-4366`：run ID 与终态；
   - `app/main.py:4744-4998`：正式正文资格、派生字段和原子 run 发布；
   - `app/evidence_schema.py:240-373,540-548`：Evidence A/B/C 规则；
   - `app/evidence_index.py:286-310,326-389`：现有 Claim 与 A/B 匹配；
   - `app/formal_body.py:18-80`：正式正文判定。

若旧路线图与已批准设计冲突，以本次设计为准。特别是旧路线图中的 DeepEval 发布阈值、PR/Release 阻断和未来晋级语义已经被以下永久约束替代：

```text
purpose = advisory_observability
blocking = false
affects_deliverable = false
affects_report_status = false
affects_release = false
affects_user_response = false
```

任何子计划不得重新引入 DeepEval `pass/fail`、required check、发布阈值或状态覆盖。

## 2. 实施前冻结的口径

### 2.1 覆盖率粒度

在不修改 `app/main.py` 的范围内，Runtime 覆盖率分母定义为：

```text
每个 run 当前最新持久化的正式报告版本
identity = (run_id, version, report_sha256)
eligible = status in {"finished", "needs_manual_review"} AND locally_derived_formal_body_present
```

规则：

- 同一 run 只有当前最新版本进入当前覆盖率分母；
- 正文或 `version` 改变时产生新 job，旧 job 只作为历史保留；
- 只有 timing、历史索引等非正文变化时不得重复评估；
- Worker 暂停后必须补齐暂停期间仍可从 run 文件发现的最新正式版本；
- 当前版本只保留最近 5 个 `report_versions` 和 10 个 `revisions`，见 `app/main.py:8677-8727`。

如果产品要求“每一个已被后续修订覆盖的历史版本也必须永久逐份评估”，立即停止实施并重新评审 `app/main.py` 的不可变版本事件写入；不得假称扫描器能恢复已经不再持久化的历史版本。

批准执行本计划时必须同时确认上述
`latest persisted formal version per run` 口径；若不确认，只能停在计划
阶段，不能开始 Foundation 实施。

### 2.2 展示边界

本阶段通过 Worker 专属 JSON/Markdown Artifact 展示：

- Runtime coverage；
- 各指标分数和分布；
- Nightly/Weekly 趋势；
- Judge 与人工 Golden 的一致率和混淆矩阵；
- cache hit、Token、成本、延迟和错误分类。

本阶段不修改 8099 API、records UI 或 Word。若要求在现有 8099 页面内展示 Advisory，必须单独设计只读授权 API；不得把 `app/main.py` 修改偷偷并入本计划。

### 2.3 依赖隔离

DeepEval 4.1.3 的 wheel 元数据要求：

```text
Python >=3.9,<4.0
pydantic >=2.11.7,<3.0.0
```

主服务当前锁定 `pydantic==2.10.4`，见 `requirements.txt:7`。因此：

- `requirements.txt` 保持不变；
- Worker 使用独立 `requirements-deepeval.in` 和带哈希 lock；
- 主服务 Dockerfile 保持不安装 DeepEval；
- DeepEval 合约测试只在 Worker 专用 Python 3.11 容器执行。

### 2.4 fixed10

现有 `tests/fixtures/8099_regression_cases.json` 声明 10 个 fixed10 案例，但 `fixed-4-guizhou-project-analysis` 带 `SOURCE_ATTACHMENT_UNAVAILABLE`，`select_cases()` 会过滤它，实际只能运行 9 个。

新 Nightly 数据真源必须是独立的 `tests/fixtures/deepeval_advisory/fixed10/v1/`：

- 恰好 10 个可重放案例；
- `skip` 和 exclusions 均为 0；
- 每个案例绑定内容哈希；
- 旧 manifest 保留为历史基线，不就地伪装成“已修好”。

### 2.5 Judge Provider

已批准的是“独立、经过批准的 Judge API”，但当前没有获批的：

- Provider；
- HTTPS origin；
- wire protocol；
- 模型/profile 版本；
- 凭据注入方式；
- 留存和数据地域证明。

因此基础阶段只实现并验证 `JudgeTransport` 契约、fake transport 和 DeepEval Adapter。真实网络 Adapter 必须在上述资料齐全后写入一份 provider-specific 追加计划并经用户批准；不得发明 endpoint、复用 Dify 凭据或把任意 base URL 做成运行时输入。

## 3. 文件结构

最终目标结构：

```text
app/deepeval_advisory/
  __init__.py
  models.py
  settings.py
  hashing.py
  projection.py
  store.py
  cache.py
  judge.py
  evaluator.py
  datasets.py
  reporting.py
  worker.py

scripts/
  run_deepeval_advisory.py
  freeze_deepeval_dataset.py
  validate_deepeval_labels.py

tests/
  test_deepeval_advisory_models.py
  test_deepeval_advisory_projection.py
  test_deepeval_advisory_store.py
  test_deepeval_advisory_judge.py
  test_deepeval_advisory_evaluator.py
  test_deepeval_advisory_dataset.py
  test_deepeval_advisory_calibration.py
  test_deepeval_advisory_scheduled.py
  test_deepeval_advisory_worker.py
  test_deepeval_advisory_isolation.py
  test_deepeval_advisory_deepeval_contract.py

tests/fixtures/deepeval_advisory/
  unit/
  fixed10/v1/
  calibration100/v1/

requirements-deepeval.in
requirements-deepeval.lock
Dockerfile.deepeval
docker-compose.deepeval-advisory.yml
docs/runbooks/deepeval-advisory.md
```

默认不修改：

```text
app/main.py
requirements.txt
Dockerfile
docker-compose.yml
Dify Workflow
.88
现有报告状态、deliverable 和 Word 代码
```

`.dockerignore` 允许进行最小安全修正，以防构建上下文包含 `data/`、报告或 Advisory 运行制品。

## 4. 子计划与顺序

| 顺序 | 子计划 | 独立产物 | 退出条件 |
|---:|---|---|---|
| 1 | `2026-07-27-deepeval-advisory-01-foundation.md` | 严格 Schema、投影、哈希、Store、Cache、fake Judge、DeepEval metric runner | 全部 fake/contract 测试通过；没有真实 Judge；没有 Runtime 扫描 |
| 2 | `2026-07-27-deepeval-advisory-02-datasets.md` | 真正 10 个 fixed10、100 样本标注契约、Nightly/Weekly runner 与报告 | 10/10 可重放；双智能体预标；全部分歧/高风险人工审核；至少 40 个验证样本全部人工确认 |
| 3 | `2026-07-27-deepeval-advisory-03-runtime-worker.md` | 独立 Worker、只读输入挂载、Worker-only 状态、暂停/恢复、backfill、运行手册 | disabled discovery 通过；真实 Judge 获批后才允许 Runtime Advisory；最终 enrollment coverage 100% |

每份子计划独立提交。不得跨过前一计划的退出条件。

## 5. Stage Gates

### Gate A：隔离纯合约

允许：

- 新模块、单元测试、fake Judge；
- 临时容器内安装 DeepEval 4.1.3；
- 生成并提交依赖 lock；
- 合成 fixture。

禁止：

- 真实 Judge；
- `.87/.88` 网络请求；
- 生产数据扫描；
- 部署。

### Gate B：固定数据与真实 Judge 前置

必须同时具备：

- 用户批准一次性隔离测试环境；
- 真正第 10 个案例的材料与附件可重放；
- 独立 Judge Provider 合约获批；
- Worker-only Secret 注入；
- 请求数和成本硬预算。

缺任一条件时，固定集和真实 Judge smoke 保持未执行，不影响基础代码验收。

### Gate C：Runtime disabled

允许部署独立 Worker，但：

```text
DEEPEVAL_RUNTIME_ADVISORY_ENABLED=false
DEEPEVAL_SCHEDULED_EVALUATION_ENABLED=false
```

只验证：

- run 发现；
- 资格判断；
- 任务登记；
- coverage 分母；
- 输入挂载只读；
- 主服务完全不变。

### Gate D：Runtime Advisory

必须由用户再次明确批准后才可：

- 注入真实 Judge Secret；
- 打开 Runtime Judge；
- 处理新报告；
- 分批 backfill。

### Gate E：稳态

验收：

- 每个当前正式 run 版本均登记 Advisory；
- 相同完整输入只付费一次；
- Nightly/Weekly 强制 cache bypass；
- Judge 暂停不影响任何用户功能；
- DeepEval 永远不成为发布或交付门禁。

## 6. 全局验证命令

兼容测试继续使用仓库现有 `unittest`，不引入 pytest；测试在
network-disabled 的一次性 Worker 容器运行，不在 host 直接运行项目：

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest discover -s tests -p "test_*.py" -v
```

DeepEval 依赖测试只在 Worker 镜像内运行：

```powershell
docker build -f Dockerfile.deepeval -t medical-notice-analyzer-deepeval:test .
docker run --rm --network none medical-notice-analyzer-deepeval:test `
  python -m unittest `
  tests.test_deepeval_advisory_deepeval_contract `
  tests.test_deepeval_advisory_judge `
  tests.test_deepeval_advisory_evaluator -v
```

Compose 只做静态合并和隔离验证，除非进入 Gate C：

```powershell
docker compose -f docker-compose.deepeval-advisory.yml config
```

不得把 DeepEval 命令加入 required check。分数、Judge 不可用或低分不能改变命令的发布判定；运行结果通过 Artifact 表达。

## 7. 全局停止条件

出现以下任一情况，停止当前阶段并请求重新评审：

- 需要修改 `app/main.py` 才能发现当前最新正式报告；
- 需要主服务安装 DeepEval 或持有 Judge Secret；
- 需要把 Advisory 结果写回 analysis run；
- 需要向 Judge 发送完整报告、完整 Evidence Pack、Evidence C、Memory、附件或内部路径；
- 真实 Judge Provider 合约仍未批准；
- fixed10 仍只有 9 个可运行案例；
- calibration100 的验证样本不足 40 个或没有全部人工确认；
- 需要操作 `.88`；
- 任一方案会改变 report status、deliverable、Word、用户响应或发布结果。

## 8. 回滚总则

最小回滚：

```text
DEEPEVAL_RUNTIME_ADVISORY_ENABLED=false
```

完全回滚：

- 停止独立 Worker；
- 保留任务、结果与缓存；
- 不删除或修改 analysis run、Evidence Pack、报告和 Word；
- 主服务继续按现有确定性门禁运行；
- 恢复后由扫描器重建 coverage 并补齐最新版本 backlog。

## 9. 计划执行记录

每完成一个子计划：

1. 运行该计划列出的最新验证命令；
2. 检查 staged scope；
3. 单独提交；
4. 记录代码 SHA、依赖 lock SHA、fixture SHA；
5. 停在下一 Gate 前等待用户确认。
