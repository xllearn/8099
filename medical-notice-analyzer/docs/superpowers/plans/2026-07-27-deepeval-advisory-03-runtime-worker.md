# DeepEval Advisory Runtime Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用独立、可暂停、可恢复的 Worker 异步登记每个当前仍持久化的正式报告版本，使用完整输入缓存和 single-flight 生成只读 DeepEval Advisory，并在不触碰主服务的前提下达到最终 100% enrollment。

**Architecture:** Worker 只读扫描 `analysis_runs` 与 `evidence_packs`，先持久化 per-report job，再按运行时开关决定是否调用 Judge。任务、Projection、租约、缓存、结果、coverage 和 Markdown/JSON 展示全部写入 Worker-only volume；主服务不安装 DeepEval、不持有 Secret、不读取结果、不等待 Worker。

**Tech Stack:** Python 3.11、DeepEval 4.1.3 Worker、Pydantic 2、`unittest`、portalocker、Docker Compose、JSON/Markdown。

---

## 0. 覆盖率口径与 P0 停止条件

已批准产品目标是“每份正式报告一次异步 Advisory，最终登记 100%”。
当前实现事实是每个 `run_*.json` 只可靠暴露当前正式正文；修订历史还
有 5 个 `report_versions`、10 个 `revisions` 的保留上限。因此在
`app/main.py` 保持不变的本计划中，精确定义为：

```text
eligible identity = (run_id, current version, report_sha256)
coverage denominator = 当前仍持久化的每个 run 的最新符合资格正式版本
```

本计划不能承诺已经被覆盖且不再持久化的历史版本。若用户要求这些
历史版本也永久逐份评估：

1. 立即停止；
2. 提交不可变 report-version 事件源设计；
3. 如该设计需要修改 `app/main.py`，先重新评审；
4. 未获批准前不得把“最新版本覆盖”表述为“所有历史版本覆盖”。

本计划的 100% 指当前覆盖率分母。该范围必须写入每份 coverage
artifact。

## 1. 永久隔离约束

不得修改：

```text
app/main.py
requirements.txt
Dockerfile
docker-compose.yml
Dify Workflow
报告正文、状态、deliverable 或 Word 代码
.88
```

允许新增独立 Worker 文件，并对 `.dockerignore` 做最小安全修正。
一旦实现确实需要主服务 hook、主服务 Secret、主服务读取 Advisory
或主服务重启，停止并请用户重新评审。

Advisory 结果固定：

```text
purpose = advisory_observability
blocking = false
affects_deliverable = false
affects_report_status = false
affects_release = false
affects_user_response = false
```

## 2. 目标文件

```text
app/deepeval_advisory/
  worker.py
  reporting.py
  settings.py
  models.py
  store.py
  cache.py

scripts/
  run_deepeval_advisory.py

tests/
  test_deepeval_advisory_worker.py
  test_deepeval_advisory_isolation.py

Dockerfile.deepeval
docker-compose.deepeval-advisory.yml
.dockerignore
docs/runbooks/deepeval-advisory.md
```

### Task 1: 实现只读 run 发现与资格判断

**Files:**

- Create: `app/deepeval_advisory/worker.py`
- Create: `tests/test_deepeval_advisory_worker.py`
- Modify: `app/deepeval_advisory/models.py`

- [ ] **Step 1: 写失败的 discovery 测试**

在 `TemporaryDirectory` 建立只读 source tree，覆盖：

- 只扫描排序后的 `run_*.json`；
- 忽略 `.run_*.tmp`；
- symlink、非普通文件、超限、invalid UTF-8/JSON 被分类拒绝；
- filename/content `run_id` 不一致被拒绝；
- unknown future run Schema 被拒绝；
- `status` 与 `run_status` 不一致被拒绝；
- `finished` 和 `needs_manual_review` 且有正式正文时符合资格；
- `created` 到 `export_checking` 的非终态被分类为 `not_ready`，不是
  corrupt/error；
- `failed`、`interrupted`、空正文和 technical-only 正文不符合资格；
- persisted `formal_body_present` 不能覆盖本地判定；
- linked pack 缺失、malformed、future Schema 时进入可重试 discovery
  error，不写 source；
- run/pack 在扫描前后的 SHA-256 完全相同。

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_worker.DiscoveryTests -v
```

Expected: FAIL because `worker.py` does not exist.

- [ ] **Step 2: 实现精确 discovery API**

公开入口：

- `discover_run_paths(run_dir: Path) -> tuple[Path, ...]`
- `inspect_run(path: Path, *, evidence_pack_dir: Path, hmac_key: bytes) -> DiscoveryRecord`
- `reconcile_once(context: WorkerContext) -> ReconcileSummary`

`DiscoveryRecord` 只保存：

```text
run_ref
report_version
report_sha256
projection_sha256
advisory_input_sha256
source_updated_at
eligibility
error_category
```

raw `run_id` 和 `pack_id` 只在一次扫描的内存中用于定位文件，不进入
Projection、job、result、log 或 artifact。

- [ ] **Step 3: 明确 latest-per-run 行为**

同一 `run_*.json` 变化时：

- 正文或 `version` 改变：创建新 identity/job；
- 只有 `updated_at` 或 timing 变化：不创建新 job；
- 老 job 保留为历史；
- coverage 当前分母只引用最新 identity；
- 不尝试从被截断的 revision 列表恢复不可变事件。

- [ ] **Step 4: 运行并提交**

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_worker.DiscoveryTests -v
git add app/deepeval_advisory/worker.py app/deepeval_advisory/models.py tests/test_deepeval_advisory_worker.py
git commit -m "feat: discover persisted formal reports read only"
```

### Task 2: 实现先登记、独立 coverage 与 per-report 索引

**Files:**

- Modify: `app/deepeval_advisory/worker.py`
- Modify: `app/deepeval_advisory/store.py`
- Modify: `app/deepeval_advisory/reporting.py`
- Modify: `tests/test_deepeval_advisory_worker.py`

- [ ] **Step 1: 写 enrollment 失败测试**

覆盖：

- 每个 eligible identity 在任何 Judge 操作前持久化一个 job；
- Judge paused/unavailable 时 job 仍登记；
- 两个报告完整输入相同，各自有 job/per-run index；
- cache hit 仍给当前报告写 `source_evaluation_id` 和 result reference；
- invalid/ineligible source 不进入 eligible 分母，并在 discovery summary
  中有分类；
- coverage index 删除后可由 job/current-run truth 重建；
- 100 个 eligible report 即使 Judge 全暂停也有
  `enrollment_coverage=100%`、`valid_score_coverage=0%`；
- `needs_manual_review` 不被排除，也不改变其原状态。

- [ ] **Step 2: 实现 job identity 与原子登记**

job ID 从以下 canonical 值派生：

```text
run_ref
report_version
report_sha256
projection_sha256
metric_set_sha256
prompt_version
judge_adapter_version
judge_profile_version
```

使用 exclusive create 登记，不使用“先 exists 再 write”。每个报告写
自己的 `run-index/<run_ref>/<job_id>.json`；共享 cache 不能取代
per-report 登记。

- [ ] **Step 3: 实现两个覆盖率**

```text
enrollment_coverage =
  enrolled_current_identities / eligible_current_identities * 100

valid_score_coverage =
  (scored_current_identities + cached_current_identities)
  / eligible_current_identities * 100
```

分母为 0 时输出 `not_applicable`，不得伪造 100%。`paused`、
`unavailable`、`partial`、`over_budget` 和 `indeterminate` 仍留在
分母，分别报告数量。

- [ ] **Step 4: 原子写 coverage artifact**

写入：

```text
coverage/current.json
coverage/current.md
```

两份都声明：

```text
coverage_scope = latest_persisted_formal_version_per_run
historical_superseded_versions_guaranteed = false
```

只展示 HMAC identity、计数、分数、状态、版本、成本和错误分类，不展示
完整正文、Evidence、Prompt 或 raw ID。

- [ ] **Step 5: 运行并提交**

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_worker.EnrollmentCoverageTests -v
git add app/deepeval_advisory/worker.py app/deepeval_advisory/store.py app/deepeval_advisory/reporting.py tests/test_deepeval_advisory_worker.py
git commit -m "feat: enroll advisory jobs before judging"
```

### Task 3: 接通成功缓存、single-flight 与可恢复执行

**Files:**

- Modify: `app/deepeval_advisory/worker.py`
- Modify: `app/deepeval_advisory/cache.py`
- Modify: `app/deepeval_advisory/store.py`
- Modify: `tests/test_deepeval_advisory_worker.py`

- [ ] **Step 1: 写 fake Judge 并发/恢复失败测试**

覆盖：

- 两个不同报告有相同 `advisory_input_sha256` 时只发生一次 Judge call；
- 两个报告各有独立 job 和 result reference；
- 只有 validated `completed` 结果进入 cache；
- `partial`、`unavailable`、`over_budget`、`indeterminate` 和 error
  不缓存；
- Worker 在 pre-request lease 阶段终止，TTL 后可重领；
- Worker 在 `issued_at` 后终止，恢复为 `indeterminate`，不自动重发；
- provider 明确 429/503 且确认未执行时最多重试 2 次；
- timeout/outcome unknown 不自动重试；
- cache/lease 损坏 fail closed；
- source run/pack hash 始终不变。

- [ ] **Step 2: 实现执行顺序**

每个 job 的固定顺序：

1. 校验 job 和 Projection；
2. 若 runtime Judge 关闭，保持 `paused`；
3. 查询完整输入成功 cache；
4. cache hit：写当前 job 的 cached result reference，不调用 Judge；
5. cache miss：按 `advisory_input_sha256` 获取跨进程 lease；
6. 再查一次 cache；
7. 写 attempt `phase=pre_request`；
8. 在真正发出请求前原子写 `issued_at`；
9. 调用 Foundation evaluator；
10. 原子写 result；
11. 仅完整 `completed` 写 immutable cache；
12. 更新当前 job 和 coverage。

- [ ] **Step 3: 固定租约恢复语义**

lease 字段：

```text
advisory_input_sha256
owner_ref
acquired_at
expires_at
heartbeat_at
attempt_ref
request_phase = pre_request | issued | response_received
```

只有 stale `pre_request` 可自动回收。stale `issued` 必须转
`indeterminate`，由人工/Provider 幂等证据决定是否重试。

- [ ] **Step 4: 运行并提交**

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_worker.ExecutionRecoveryTests -v
git add app/deepeval_advisory/worker.py app/deepeval_advisory/cache.py app/deepeval_advisory/store.py tests/test_deepeval_advisory_worker.py
git commit -m "feat: execute advisory jobs with exact single flight"
```

### Task 4: 实现 runtime pause/resume 与 backlog

**Files:**

- Modify: `app/deepeval_advisory/settings.py`
- Modify: `app/deepeval_advisory/worker.py`
- Modify: `tests/test_deepeval_advisory_worker.py`

- [ ] **Step 1: 写开关隔离失败测试**

严格配置：

```text
DEEPEVAL_RUNTIME_ADVISORY_ENABLED=false
DEEPEVAL_SCHEDULED_EVALUATION_ENABLED=false
```

测试：

- runtime false 时继续 discovery/enrollment，不调用 Judge；
- 新 job 状态为 `paused` 并进入 backlog；
- runtime 从 false 变 true 后按创建时间恢复 backlog；
- runtime true 变 false 时，不启动新 Judge call；
- 已发出的单次 call 允许记录响应，不开始下一 job；
- scheduled true/runtime false 时，Nightly/Weekly 可运行；
- runtime true/scheduled false 时，固定集不运行；
- invalid bool 值 fail closed，不静默使用默认值；
- 两个开关都不影响任何确定性门禁或用户服务。

- [ ] **Step 2: 实现动态控制文件**

环境变量提供启动上限，Worker-only volume 中的
`control/runtime.json` 提供动态暂停：

```text
schema_version = 8099.deepeval-runtime-control/v1
runtime_paused
updated_at
updated_by_ref
control_sha256
```

有效运行条件：

```text
DEEPEVAL_RUNTIME_ADVISORY_ENABLED == true
AND runtime_paused == false
```

控制文件用同目录原子 replace、0600 和完整性 hash。缺失时默认
`runtime_paused=false`；损坏时 fail closed 为 paused。Scheduled
runner 不读取该文件。

- [ ] **Step 3: 增加本地控制命令**

在同一个脚本提供：

```powershell
python scripts/run_deepeval_advisory.py --control pause-runtime
python scripts/run_deepeval_advisory.py --control resume-runtime
python scripts/run_deepeval_advisory.py --control show-runtime
```

命令只写 Worker-only control 文件，不修改 Compose、主服务或 source
数据。`resume-runtime` 只解除暂停；仍受环境变量的启动上限约束。

- [ ] **Step 4: 运行并提交**

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_worker.RuntimeControlTests -v
git add app/deepeval_advisory/settings.py app/deepeval_advisory/worker.py scripts/run_deepeval_advisory.py tests/test_deepeval_advisory_worker.py
git commit -m "feat: pause runtime judge without affecting reports"
```

### Task 5: 实现 `runtime-worker` 与 `backfill` 模式

**Files:**

- Modify: `scripts/run_deepeval_advisory.py`
- Modify: `app/deepeval_advisory/worker.py`
- Modify: `tests/test_deepeval_advisory_worker.py`

- [ ] **Step 1: 写 CLI 失败测试**

覆盖：

- `--mode runtime-worker --once` 只做一次 scan/reconcile；
- 未加 `--once` 时按严格 interval 循环；
- `--mode backfill` 对现有全部 current eligible run 做有界扫描；
- 两种模式使用 runtime cache/single-flight；
- 默认 runtime false 时只登记，不调用 Judge；
- `--max-jobs`、`--max-judge-calls` 和 `--max-cost` 是硬预算；
- 预算耗尽后保留 backlog 并正常输出 summary；
- Ctrl+C 在当前原子写完成后退出；
- source 目录不存在/不可读时错误退出，但不影响主服务。

- [ ] **Step 2: 实现四 mode 单一入口**

最终支持：

```powershell
python scripts/run_deepeval_advisory.py --mode runtime-worker
python scripts/run_deepeval_advisory.py --mode backfill
python scripts/run_deepeval_advisory.py --mode fixed10
python scripts/run_deepeval_advisory.py --mode calibration100
```

Runtime/backfill 使用完整输入 cache；fixed10/calibration100 的
`cache_mode=bypass` 由 Dataset 子计划永久固定。

- [ ] **Step 3: 固定低并发**

默认：

```text
DEEPEVAL_WORKER_CONCURRENCY=1
DEEPEVAL_SCAN_INTERVAL_SECONDS=30
DEEPEVAL_LEASE_TTL_SECONDS=300
DEEPEVAL_LEASE_HEARTBEAT_SECONDS=30
```

`concurrency` 只允许 1–2；TTL 必须至少是 heartbeat 的 3 倍。所有
非法值 fail closed。

- [ ] **Step 4: 运行并提交**

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_worker.WorkerCliTests -v
git add scripts/run_deepeval_advisory.py app/deepeval_advisory/worker.py tests/test_deepeval_advisory_worker.py
git commit -m "feat: add advisory runtime and backfill modes"
```

### Task 6: 建立 Worker-only 容器与构建边界

**Files:**

- Modify: `.dockerignore`
- Modify: `Dockerfile.deepeval`
- Create: `docker-compose.deepeval-advisory.yml`
- Create: `tests/test_deepeval_advisory_isolation.py`

- [ ] **Step 1: 先写静态隔离测试**

测试必须证明：

- main `requirements.txt` 不含 DeepEval/portalocker/Judge SDK；
- main `Dockerfile` 不引用 `requirements-deepeval.lock`；
- Worker Dockerfile 精确安装 lock；
- Compose 不修改/extend/depends_on 主服务；
- 只挂载 `analysis_runs` 和 `evidence_packs` 为 `ro`；
- 没有 `./data:/app/data`；
- Advisory state 使用仅 Worker 可见的 named volume；
- base Worker `network_mode: none`；
- base flags 均为 false；
- main service 未收到 Judge/HMAC Secret；
- `.dockerignore` 排除 runtime data、reports、artifacts 和 secrets。

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_isolation.StaticIsolationTests -v
```

Expected: FAIL until Compose and ignore rules exist.

- [ ] **Step 2: 修正 `.dockerignore`**

至少加入：

```text
data
data/**
reports
reports/**
artifacts
artifacts/**
secrets
secrets/**
deepeval_advisory
deepeval_advisory/**
```

保留现有规则。构建前用 `docker build --no-cache --progress=plain` 的
context 输出确认没有运行数据被传给 builder。

- [ ] **Step 3: 固定 Gate C Compose**

`docker-compose.deepeval-advisory.yml` 只定义
`deepeval-advisory-worker`，要求：

```text
network_mode: none
read_only: true
user: 10001:10001
cap_drop: ALL
security_opt: no-new-privileges:true
restart: unless-stopped
DEEPEVAL_RUNTIME_ADVISORY_ENABLED=false
DEEPEVAL_SCHEDULED_EVALUATION_ENABLED=false
DEEPEVAL_TELEMETRY_OPT_OUT=1
DEEPEVAL_DISABLE_DOTENV=1
DEEPEVAL_NO_INSPECT_PROMPT=1
ENABLE_DEEPEVAL_CACHE=0
```

Volumes：

```text
./data/analysis_runs:/source/analysis_runs:ro
./data/evidence_packs:/source/evidence_packs:ro
deepeval_advisory_state:/state
tmpfs:/tmp
```

`Dockerfile.deepeval` 必须保留固定 UID/GID `10001:10001`，并在切换
非 root 用户前创建 mode 0700、属于该 UID/GID 的 `/state`。首次挂载
named volume 后先验证容器用户可原子写 `/state`；失败时停止，不以
root 运行 Worker。

Reference HMAC key 通过 Worker-only Docker secret file 注入；base
Compose 不包含 Judge API key。Secret 文件位于已忽略的 `secrets/`
目录，永不输出值、永不放进命令行。

- [ ] **Step 4: 为真实 Judge 保留 provider-specific overlay Gate**

base Compose 永远 `network_mode:none`。未来真实 Judge 必须由另一个
经批准、固定 HTTPS origin 和受限 egress 的 provider-specific
overlay 打开网络并注入 Judge Secret。该 overlay 不在 Provider
获批前创建。

- [ ] **Step 5: 运行静态测试并提交**

```powershell
docker run --rm --network none --entrypoint python `
  -v "${PWD}:/work:ro" -w /work `
  medical-notice-analyzer-deepeval:foundation-test `
  -m unittest tests.test_deepeval_advisory_isolation.StaticIsolationTests -v
docker compose -f docker-compose.deepeval-advisory.yml config
git add .dockerignore Dockerfile.deepeval docker-compose.deepeval-advisory.yml tests/test_deepeval_advisory_isolation.py
git commit -m "build: isolate deepeval advisory worker"
```

Expected: Compose config 有且只有 Worker 服务；不输出 secret 值。

### Task 7: 完成 fake 端到端与主服务不变量测试

**Files:**

- Modify: `tests/test_deepeval_advisory_worker.py`
- Modify: `tests/test_deepeval_advisory_isolation.py`
- Create: `docs/runbooks/deepeval-advisory.md`

- [ ] **Step 1: 写 fake 端到端测试**

在临时只读 source 和 Worker state 中：

1. 放入 `finished` 与 `needs_manual_review` 两个 run；
2. 放入对应 v2 pack；
3. 启动 `runtime-worker --once`；
4. 发现并登记两个 job；
5. 解除 test control pause；
6. fake Judge 生成分数；
7. 写独立 result/coverage JSON/Markdown；
8. 比较 source tree 全量 hash 完全不变。

再覆盖 identical input single-flight、worker crash/recovery、429/503、
outcome unknown、pause/resume/backlog 和历史 backfill。

- [ ] **Step 2: 写业务不变量测试**

读取实现前保存的 main source hash 清单，断言：

```text
app/main.py unchanged
requirements.txt unchanged
Dockerfile unchanged
docker-compose.yml unchanged
```

测试结果对象的五个非影响字段全为 false。任何结果分数、error 或
unavailable 都不得调用 analysis-run writer。

- [ ] **Step 3: 写运行手册**

`docs/runbooks/deepeval-advisory.md` 必须包含：

- coverage scope 与历史版本限制；
- Gate C/D 权限矩阵；
- build/start/inspect/stop 命令；
- pause/resume/backlog 命令；
- fixed10/calibration100 命令；
- cache/single-flight/indeterminate 解释；
- secret 文件只检查存在/权限，不显示内容；
- Artifact 路径；
- 100% enrollment 核验；
- Worker 停止和 provider rollback；
- 明确“分数不影响发布、deliverable、状态、Word 和用户响应”；
- 明确 base Compose 没有 Judge 网络。

- [ ] **Step 4: 在 Worker 容器运行全套测试**

```powershell
docker build -f Dockerfile.deepeval -t medical-notice-deepeval-advisory:test .
docker run --rm --network none medical-notice-deepeval-advisory:test `
  python -m unittest `
  tests.test_deepeval_advisory_models `
  tests.test_deepeval_advisory_projection `
  tests.test_deepeval_advisory_store `
  tests.test_deepeval_advisory_judge `
  tests.test_deepeval_advisory_evaluator `
  tests.test_deepeval_advisory_dataset `
  tests.test_deepeval_advisory_calibration `
  tests.test_deepeval_advisory_scheduled `
  tests.test_deepeval_advisory_worker `
  tests.test_deepeval_advisory_isolation `
  tests.test_deepeval_advisory_deepeval_contract `
  -v
```

Expected: PASS with fake Judge, no network, no server/model call.

- [ ] **Step 5: 提交**

```powershell
git add tests/test_deepeval_advisory_worker.py tests/test_deepeval_advisory_isolation.py docs/runbooks/deepeval-advisory.md
git commit -m "test: verify advisory worker isolation end to end"
```

### Task 8: Gate C disabled discovery

- [ ] **Step 1: 停止并取得 `.87` 书面批准**

报告将执行的只读/独立动作：

- 构建独立 Worker image；
- 不修改或重启 8099 main；
- source 只读挂载；
- base Worker 无网络；
- runtime/scheduled flags 均 false；
- 只验证 discovery、enrollment 和 coverage 分母。

未批准时不得登录、复制、部署或启动 Worker。

- [ ] **Step 2: 保存部署前证据**

只记录非秘密：

- main container ID/image/revision/start time；
- `app/main.py` 与 Compose source hash；
- analysis run/evidence pack source tree hash；
- main `/health`；
- 当前报告状态/Word contract 的只读样本。

- [ ] **Step 3: 启动独立 disabled Worker**

使用单独项目名和 Worker Compose，不把它并入现有 main release runner。
不创建 provider overlay，不注入 Judge API key。

- [ ] **Step 4: 验证**

要求：

- eligible current reports 全部登记；
- Judge calls = 0；
- base Worker network = none；
- source hashes 未变；
- main container ID/start time/health 未变；
- 用户 API、报告状态、deliverable 和 Word 未变；
- coverage 明确 latest-persisted scope。

- [ ] **Step 5: 回滚演练**

停止并删除独立 Worker container，保留 named volume；确认 main
继续运行。再次启动 Worker 后从 job truth 重建 coverage，无 Judge
调用。

### Task 9: Gate D 真实 Runtime Advisory

- [ ] **Step 1: 先完成 provider-specific 追加计划**

必须冻结并获批：

```text
provider
HTTPS origin
wire protocol
immutable model/profile
prompt/response schema
secret injection
retention/region/training policy
egress restriction
request and cost hard budgets
retry/idempotency semantics
```

缺一项不得创建网络 overlay，不得运行真实 smoke。

- [ ] **Step 2: 最小 smoke**

在明确批准的 1–3 个 fixed case 上验证 boundary、Schema、成本和
resolved model。任何敏感信息、Evidence C、完整报告或内部路径出现，
立即暂停 runtime control 并停止。

- [ ] **Step 3: 低并发启用**

先 `concurrency=1`，设置 `max-judge-calls` 和 `max-cost`，解除
runtime pause。每轮核验：

- 每个 eligible current identity 已登记；
- cache hit 仍有 per-report 记录；
- identical input 不重复付费；
- error 不写 cache；
- 分数和错误不回写 run；
- 用户链路不等待 Worker。

- [ ] **Step 4: 有界 backfill**

```powershell
python scripts/run_deepeval_advisory.py --mode backfill
```

预算耗尽或 pause 时保留 backlog。恢复后继续，不删除任务、不把
unavailable 记为 0。

- [ ] **Step 5: 达到当前口径 enrollment 100%**

验收：

```text
coverage_scope = latest_persisted_formal_version_per_run
enrollment_coverage = 100%
eligible = enrolled
Judge failure does not reduce enrollment
valid_score_coverage is reported separately
```

不得宣称已覆盖不再持久化的 superseded history。

## 3. 整体验证与停止条件

实现阶段最终运行：

```powershell
git diff --exit-code -- app/main.py requirements.txt Dockerfile docker-compose.yml
git diff --check
```

并证明：

- Worker 停止、Judge 失败、低分、暂停、secret 缺失均不影响 8099；
- `needs_manual_review` 仍按原 Word 语义存在；
- main image 无 DeepEval；
- main environment 无 Judge/HMAC Secret；
- main 不建立 Judge 网络连接；
- source mount 是只读；
- Advisory state 不在 `./data`，main 看不到；
- JSON/Markdown 是本阶段唯一展示面；
- Nightly/Weekly 使用独立开关并继续 cache bypass；
- `app/main.py` hash 与计划开始时一致。

任何一项失败：暂停 Worker，保留 state volume 和审计证据，主服务
无需回滚。

## 4. 回滚

### 运行时快速暂停

```powershell
python scripts/run_deepeval_advisory.py --control pause-runtime
```

### 完全停止 Worker

停止独立 Worker Compose 项目；不停止、不重启 main。保留 named
volume 以便审计和恢复。

### 代码回滚

按独立 Worker 提交逆序回退。不要删除已登记 job、cache、result 或
coverage 历史；标记废弃版本并使用新 Schema/adapter/profile hash
自然失效。
