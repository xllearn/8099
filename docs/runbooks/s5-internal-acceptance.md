# 8099 S5 内部验收与持续使用

> S5-PREP/阶段收口日期：2026-07-17
> 文档续作/最终审阅日期：2026-07-20
> 生产代码基线：`88309d57dcda056efa250cf37c2d77757536445e`
> 生产镜像：`medical-notice-analyzer:s4-88309d5`
> S5 分支：`codex/s5-internal-acceptance`
> 本文性质：内部运行手册、验收证据索引和发布/回退检查清单

## 1. 收口结论与边界

### 1.1 结论

本轮项目结构、Git 对象、关键文件和前端衔接均已完成只读完整性检查；文中的当前部署标识、8099 运行状态、历史接口、备份和回退资产证据来自 2026-07-17 S5-PREP 的只读核对。用户取消后续数据测试后的收口只做 Git、文件和文档完整性检查。检查结果允许提交本 S5 文档收口。

S5 没有生产代码或受控运行配置变化，生产部署继续复用
`88309d57dcda056efa250cf37c2d77757536445e`，不重复构建或部署。

### 1.2 用户指令导致的数据门禁豁免

用户于 2026-07-17 明确要求“不再进行数据测试，仅检测项目是否完整，然后提交”。该指令到达后没有新增运行：

- 固定 3 或固定 10；
- 真实数据生成、报告生成或 Dify 调用；
- 全量、容器内全量或定向测试；
- Markdown、ReportIR、FormalBody 或 DOCX 新扫描；
- checkpoint 恢复、部署或回退的执行演练；
- 容器、日志或备份目录检查。

取消指令后的文档收口只复用既有 S0-S4 证据，并执行 Git、文件结构和文档完整性检查。2026-07-20 续作只执行本地 Git/文件漂移检查、远端引用只读核对，以及 `GET /health` 和 `GET /records-ui` 的可用性确认；不访问运行记录内容，不向线上写入。这两个 GET 只确认服务和 UI 入口可用，不称为数据测试。该豁免不等于 M2/M3 数据门禁通过，本轮严格 M2/M3 均未完成，也不补记 S0、S2 的历史缺口。后续功能发布仍须按第 9 节重新判断验证等级。

### 1.3 不变量

- 不物理删除 Dify 或 URL 旧代码。
- 不实施 Native、Shadow 或灰度。
- 不改变 compact、PDF/OCR、EvidenceItem、报告正文或 Word 安全策略。
- memory、history、checkpoint 和 diagnostics 只用于运行、风格、追溯和诊断，不能作为报告事实来源。
- 不修改 8100、备份目录或运行数据；2026-07-17 S5-PREP 对备份只做只读定位和校验。
- 不提交密钥、`.env`、缓存、备份、临时文件或运行产物。

## 2. 基线与衔接

| 项目 | 值 | 结论 |
|---|---|---|
| S4 最终 SHA | `b94c4b914aa0d99ea198de80b54b7d98c12e4b2e` | 可解析 |
| 前端唯一基线 | `88309d57dcda056efa250cf37c2d77757536445e` | 用户明确确认 |
| GitHub `main` | `88309d57dcda056efa250cf37c2d77757536445e` | 只读远端核对一致 |
| GitHub 前端功能分支 | `88309d57dcda056efa250cf37c2d77757536445e` | 只读远端核对一致 |
| 当前部署 SHA | `88309d57dcda056efa250cf37c2d77757536445e` | OCI revision、`APP_GIT_SHA`、`.deployed_commit` 一致 |
| S5 worktree | `C:\Users\admin\.config\superpowers\worktrees\medical-notice-analyzer-8099-prod\s5-internal-acceptance` | 2026-07-17 基线检查时 clean；当前仅新增本手册，提交后须复核 clean |
| S5 分支 | `codex/s5-internal-acceptance` | 从 S4 创建后仅 fast-forward 到用户确认的前端 SHA |

`88309d57...` 是 `b94c4b...` 的线性后代。两者之间仅涉及：

- `app/static/records.html`；
- `docs/superpowers/specs/2026-07-17-records-ui-material-layout-design.md`；
- `tests/test_records_api.py`；
- `tests/test_records_ui_layout.py`。

前端变化与 S5 新增运行手册没有路径重叠；未改后端、Dify/URL provider、compact、PDF/OCR、EvidenceItem、报告正文、Word 安全策略或 8100 内容。

用户提供的前端验收证据为：布局测试 2/2、部署镜像相关测试 5/5、`/records-ui` HTTP 200、4787 条记录、1918×926 预览和选择交互通过，运行容器无 OOM 或重启。S5 没有重复运行这些测试。

## 3. 项目静态完整性检查

2026-07-17 在 S5 worktree 对 `88309d57...` 执行只读检查：

| 检查项 | 结果 |
|---|---:|
| 关键项目路径 | 18/18 存在且受 Git 追踪 |
| Git 跟踪文件 | 145 |
| 工作区状态项 | 0 |
| 跟踪文件删除 | 0 |
| 未合并索引项 | 0 |
| 密钥/凭据命名的跟踪文件 | 0 |
| 缓存、备份、发布包、日志等运行产物命名的跟踪文件 | 0 |
| 冲突标记 | 0 |
| `git diff --check` | 通过 |
| `git fsck --full --no-dangling` | 通过 |

18 个关键路径覆盖：

- 根约束和构建：`AGENTS.md`、`README.md`、`requirements.txt`、`Dockerfile`、两个 compose 文件；
- 主服务和 UI：`app/main.py`、`app/static/records.html`、`app/static/analysis_history.html`；
- 历史、恢复和安全：`app/analysis_history.py`、`app/run_checkpoints.py`、`app/layered_diagnostics.py`、`app/formal_body_safety.py`；
- 发布工具：`scripts/deploy_8099.py`、`scripts/rollback_8099.py`、`scripts/release_support.py`；
- 发布手册和权威计划同路径文件。

当前工作区内同路径计划是较早的已提交版本。用户指定的当前权威计划仍位于原脏工作区，SHA-256 为
`BFDB9E67B6138ABA50864C8A8BD56CA37AF6C0C7DA0571FCD1CE8795258D49BA`；
S5 worktree 中已提交旧版本的 SHA-256 为
`2F2EE57FA5408DCD927DA0B0DC5D614AA0D3F1D0E2ED8CEF4591C579A594D84B`。
本 S5 分支没有从原脏工作区复制或覆盖该文件；本手册按当前权威计划的 S5、M1/M2/M3、影响指纹和发布规则执行并记录差异。

强制停止条件：在与 `BFDB9E67B6138ABA50864C8A8BD56CA37AF6C0C7DA0571FCD1CE8795258D49BA` 精确匹配的权威计划进入受控版本库或等效受控归档并由负责人确认前，禁止下一次功能发布。

## 4. S0-S4 证据索引

下表区分“当时实际完成的证据”与“当前计划要求”。后续阶段通过不能倒推补齐早期未执行门禁。

| 阶段 | 最终提交 | 测试和固定数据/Word | 备份、部署和回退 | 结论 |
|---|---|---|---|---|
| S0 | `b0dcddd8c905ce8be47d454a1f333bf6f9efd2d9` | 307 pass、9 个既有 skip；固定 3 每例 3 次，共 9/9；制品校验 108/108；禁用表达 0；完整固定 10 当时被豁免 | `20260715-103330-s0-predeploy`、`20260715-112953-s0-fixed3-mode-predeploy` | baseline 可定位，但缺少当前计划要求的 S0 第一次完整固定 10 |
| S1 | `afbf576d562a2f7578b4b233531659f370659a47` | 全量 450/450、专项 143/143、9 个既有 skip；固定 3 为 3/3；离线证据链 9 个可用案例，1 个附件不可用排除 | `20260715-105146-s1-predeploy`；候选镜像和制品 hash 有记录 | 与 S1 的 M2 加离线固定 10 范围一致 |
| S2 | `99a4df42da9266b22f3fa8b0d7972320887d5a49` | 505/505、9 个既有 skip；固定 3 首轮 3/3，后续两轮一次完整、一次不完整；第二次完整固定 10 未完成 | 曾回退 S1，后按用户确认条件性重部署；`20260716-094804-s2-conditional-redeploy` | 仅条件验收，不表述为完整 M3 通过 |
| S3 | `d6777fc8233f46f446bfa51840085eba2b7233f5` | 定向 85/85；两种全量均 550/550、9 个既有 skip；最终固定 3 为 3/3、新 9 为 9/9、次日复核 3/3；18 份 DOCX 及相关载体扫描命中 0 | `20260716-152511-s3fix-d677-predeploy`；初版失败和回退链可定位 | 最终修复版通过 |
| S4 | `b94c4b914aa0d99ea198de80b54b7d98c12e4b2e` | 617 pass、0 failure、0 error、9 个既有 skip；固定 3 为 3/3；固定 10 声明 10、选择 9、排除 1；12 份 DOCX 和相关载体的 13 条禁用表达命中 0；checkpoint/history/schema 证据可定位 | `20260717-120828-s4-predeploy`；首次健康等待假失败使用 `20260717-115516-s4-predeploy` 回退，修复后重新部署成功 | 最近完整技术验收基线；影响指纹为 M3 且不可复用 |

S4 影响指纹制品记录：

- `fingerprint_sha256=463a2e4670e114edf072e4afcda1a4511f86b2ccafe528e94ea2c96b90c02597`；
- `reuse_eligible=false`；
- `validation_level=M3`；
- Dify workflow/prompt/model 的若干远端字段为 `unknown`。

因此，在没有更强证据前，任何后续功能发布都不能仅凭该指纹复用而降级为 M2。

## 5. 2026-07-17 S5-PREP 只读线上历史证据

以下详细线上证据由 2026-07-17 S5-PREP 在用户取消后续数据测试之前收集；本次 2026-07-20 续作没有重新读取容器、日志、备份目录或运行数据，也没有运行报告或数据测试：

| 检查 | 结果 |
|---|---|
| `GET /health` | HTTP 200，`status=ok`；Word、历史 UI、checkpoint、恢复能力已启用 |
| `GET /records-ui` | HTTP 200 |
| `GET /records?page=1&page_size=1` | HTTP 200，`total=4787` |
| `GET /analysis-history-ui` | HTTP 200 |
| `GET /analysis/history` | HTTP 200，`total=72` |
| 已存在 run 的详情、compare-self、diagnostics、report 接口 | HTTP 200；只检查状态码，不输出 run 内容 |
| 容器 | running，restart 0，OOM false |
| 进程 | 单个 uvicorn worker |
| 日志 | 当前容器 566 行；ERROR/Traceback/Exception 命中 0，OOM 命中 0 |
| 端口隔离 | 8099 正常监听；8100 无监听 |
| 挂载 | reports、site-cache、data 为预期读写挂载 |

当前发布链：

- 镜像 ID：`sha256:21f46c8990698f0cd864b9e6f8112cb9fbe8fe50d89291e569de8b71f2ea9dba`；
- 发布状态：
  `/opt/medical-notice-analyzer/deploy_backups/20260717-162305-records-ui-readability-predeploy-release-state.json`；
- 发布状态为 `verified`；
- 发布状态文件 SHA-256：
  `f71c9b36258200f87d19929a416374efd10114ae142e4814caa312844cea93f9`；
- 记录的发布包 SHA-256：
  `57db899601019d1b63f7173c24217bb3aca6aae896b29e8146e0746fc81ec89e`。

已知制品缺口：上述发布包原文件当前不可定位，只能在 `verified` 状态文件中定位其 hash，无法独立重算。这不影响当前容器和备份的只读核对，但下一次发布必须把制品归档到受控持久目录，不能只保留 `/tmp` 文件名或状态记录。

当前前端发布前备份：

`/opt/medical-notice-analyzer/deploy_backups/20260717-162305-records-ui-readability-predeploy`

六项要求文件存在，`SHA256SUMS` SHA-256 为
`f3522d0b5366cb388709a698b3fb4d860f78f1b280e65b98de70f14b8a61f870`。
本轮只读重算并匹配：

- manifest：`d2336d90f832106e33093021cc379b782203e63595bc71db1ba09f0d76f290f5`；
- code：`2ec31a8bda1b4338e4fe2d1d8d32a20d490d26a223f556f4be81e91f6f7fac52`；
- config：`bc283a82be149844830839a372878e1fe2da88ea76afbfa44e143a4debf36b7c`。

数据和镜像未在本轮重新哈希，状态文件记录值分别为：

- data：`cd448c20f6fbed9669cd266340b0a256b2dd48c6f619c879ee05e3bf33ee202f`；
- image：`1fb288dadea6557b7a59c68ff6cec622eddfe2116d2219a18cfc730031ca47e1`。

该备份的回退源 SHA 为 `831f05f4de647192e8c03e18038316e38a032e25`；对应回退镜像存在且 revision 匹配。

## 6. 日常运行手册

### 6.1 日常健康检查

使用 `GET`，不要用 `HEAD` 代替 UI/API 验收。

执行频率：每个工作日首个任务前、服务重启后、发布后 15-30 分钟及下一工作日各检查一次。

1. `GET /health` 必须为 HTTP 200 且 `status=ok`。
2. `GET /records-ui`、`GET /analysis-history-ui` 必须为 HTTP 200。
3. `GET /records?page=1&page_size=1` 与 `GET /analysis/history?page=1&page_size=1` 必须能返回结构化列表。
4. 容器必须 running，worker 必须等于 1；8099 正常监听且 8100 未被本项目改变。
5. 记录 restart 数和 `OOMKilled`；`OOMKilled=true` 或 restart 较上次快照增加时停止新任务。
6. 查看并计数当前启动后新增的应用严重错误，包括 ERROR、Traceback、Exception、OOM 和重复恢复日志。
7. `/health` 非 HTTP 200 或非 ok、任一 UI 非 HTTP 200，或出现新增应用严重错误时，停止新任务并升级发布负责人和验收负责人。
8. 运行值守将检查写入仓库外受控运维日志，记录时间、部署 SHA、health/UI、worker、restart/OOM、错误计数、结论和处置；异常时保留日志和状态，不清理运行数据。

### 6.2 报告生成和状态判断

1. 从 `/records-ui` 选择主材料和辅助材料。
2. 先预览选择，再准备 pack；确认材料身份和角色无误。
3. 启动分析后，使用 run ID 查询 `/analysis/runs/{run_id}`。
4. `created`、`running` 是非终态；`finished`、`needs_manual_review`、`failed`、`interrupted` 是终态或需处理状态。
5. 不用“有 Word 文件”或“HTTP 200”代替交付判断；同时检查质量门禁、失败码、下载字段和正式正文安全状态。
6. 非 deliverable 结果必须保留唯一主失败码，不得手工改历史数据掩盖矛盾。

### 6.3 四个关键字段

| 字段 | 含义 | 使用规则 |
|---|---|---|
| `draft_word_export_available` | 正文安全、repair 未失败、run 为 `finished` 或 `needs_manual_review`，且 Word 导出开关开启 | 只表示可导出草稿，不表示正式可交付 |
| `final_word_export_available` | draft 可导出且 `deliverable=true`；正式交付不变量同时要求 `needs_manual_review=false` | 只有该字段为 true 且 `needs_manual_review=false` 时才可走正式 Word 下载 |
| `deliverable` | run 已 finished、有正式正文、正文安全，且显式质量门禁为 deliverable | 是正式交付真值，不能由 UI 猜测 |
| `needs_manual_review` | run/质量门禁要求人工复核、repair 失败，或终态有正文但不满足 deliverable | 保留诊断和草稿能力，但不得冒充正式交付 |

四字段任何矛盾均 fail-closed：停止正式下载和发布，先查 run、quality gate、正式正文安全扫描和失败码。

### 6.4 历史追溯

入口和接口：

- `/analysis-history-ui`；
- `GET /analysis/history`；
- `GET /analysis/history/{run_id}`；
- `GET /analysis/history/compare`；
- `GET /analysis/runs/{run_id}`、`/diagnostics`、`/report`。

按 run ID、创建时间、材料身份和版本定位，不用标题猜测。比较时核对代码 SHA、影响指纹、manifest、Dify 配置、材料 hash 和状态差异。历史与 diagnostics 仅用于追溯，不能作为新报告的事实来源；下载字段为空时不得绕过 UI 直接读取服务器路径。

### 6.5 checkpoint 恢复

接口：`POST /analysis/runs/{run_id}/recover`。这是线上写操作，只能由发布负责人（或其指定的恢复批准人）批准，并由运行值守在维护窗口内执行。

- 执行前保存 run/checkpoint 状态摘要及相关 run、recovery、provider ID，不记录正文或密钥。
- 先确认 run ID、当前状态、checkpoint 和 recovery ID；同一 recovery ID 最多发起 1 次。
- 已 finished 或 `needs_manual_review` 的终态应返回 `already_terminal=true`，不得重复生成。
- 同一 run 已有恢复任务时应返回 409，禁止并发恢复；遇到 409 立即停止并升级，不循环重试。
- checkpoint 损坏、状态阻断或版本不安全时应 fail-closed 并返回 409。
- 恢复能力关闭时应返回 404。
- provider 调用已开始但结果 unknown 时，禁止自动重发 Dify；立即停止并升级转人工调查，避免重复生成或双写。
- 任一恢复失败均立即停止并升级，不循环重试。
- 恢复后仍须经过正常质量、Word 和下载门禁。
- checkpoint 是执行状态，不是事实证据。
- 事后由运行值守在仓库外受控运维日志记录批准人、执行人、相关 ID、结果、结论和处置。

### 6.6 备份、部署和回退

与当前权威 hash 精确匹配的权威计划和 `docs/runbooks/deploy-8099.md` 是发布、回退及影响指纹的规范来源；本手册只做摘要，生产性操作须按上述规范来源执行。

发布：

1. 只使用用户确认、clean、已推送的完整 SHA。
2. 生成并记录发布包 SHA-256；上传后重算一致。
3. 先审阅 deploy dry-run，目标只能是 8099。
4. 部署前原子备份 code、config、image 和受影响 data，并核对 `SHA256SUMS`。
5. 执行后核对 `/health`、镜像 revision、`APP_GIT_SHA` 和 `.deployed_commit`。
6. M2/M3 测试只在部署后的 Ubuntu 8099 执行，不在 Windows 执行。
7. S5 无生产代码变化且部署 SHA 未变时，不重复构建或部署。

回退：

1. 明确失败门禁、失败 SHA、上一安全 SHA 和回退理由。
2. 选择该次发布对应的精确 backup 目录和 state 文件，不按日期猜测。
3. 核对 manifest 与 `SHA256SUMS`，先审阅 rollback dry-run。
4. 确认命令、路径、容器和端口范围不含 8100。
5. 执行后核对 revision、`APP_GIT_SHA`、`.deployed_commit`、健康和必要 UI/API。
6. 数据使用合并恢复，不删除备份后产生的新用户文件。
7. state 必须由工具闭合为 `rolled_back`，不得手工改状态或删除备份证据。

### 6.7 常见故障

| 现象 | 首查 | 处理 |
|---|---|---|
| `/health` 非 200 或非 ok | 容器、启动日志、端口、三处 SHA | 暂停新任务；若由新部署引起，按对应备份回退 |
| UI 失败但 API 正常 | GET、静态资源、浏览器控制台、容器日志 | 区分 UI 和 API，不用 HEAD 代替 GET |
| 状态或下载字段矛盾 | run、quality gate、失败码、正式正文安全 | 阻断正式下载，禁止手工改运行记录 |
| 正式载体命中禁用表达 | Markdown、ReportIR、FormalBody、DOCX 全载体 | 阻断发布；只能删除、收窄或基于 A/B evidence 重写 |
| Dify 超时或结果 unknown | provider run ID、checkpoint、失败码 | 不把超时解释为事实缺失；unknown 时禁止自动重发 |
| checkpoint 恢复 409 | 并发恢复、损坏、状态阻断 | 不强制覆盖；解决并发或转人工结论 |
| 历史列表有记录但详情缺失 | run/history 一致性、挂载、rebuild dry-run | 先备份；只在批准维护窗口执行重建 |
| OOM 或 restart 增长 | 当前启动后的 kernel/容器日志、资源峰值 | 暂停高负载任务并保存诊断；不清理运行数据 |
| GitHub、镜像、部署 SHA 不同 | local、remote、artifact、image、deployed 五点 | 停止发布，定位漂移，不覆盖未确认分支 |
| 8100 状态变化 | 监听、容器和近期操作 | 立即停止 8099 变更并上报，不在本项目操作 8100 |

## 7. 发布检查清单

- [ ] 已确认发布负责人、验收负责人和业务验收人。
- [ ] 候选 SHA clean、完整、已推送，local/remote 一致。
- [ ] 变化文件与批准范围一致；无密钥、缓存、备份、临时文件或运行产物。
- [ ] 未删除 Dify/URL 旧路径，未触碰 8100。
- [ ] 已重新计算影响指纹，所有字段可定位。
- [ ] 已按第 9 节选择 M2 或 M3，且没有以 unknown 作为复用依据。
- [ ] 发布包 hash 在本地、上传后和 state 中一致，并归档到持久受控位置。
- [ ] deploy dry-run 已审阅。
- [ ] code/config/image/data 备份及 checksum 已核对。
- [ ] 部署后的全量测试 0 failure、0 error，skip 与安全基线一致。
- [ ] 固定 3 每例 1 次通过；M3 时完整固定 10 和 baseline compare 通过。
- [ ] 适用的 Markdown、ReportIR、FormalBody、DOCX 扫描命中 0。
- [ ] `/health`、`/records-ui`、历史 UI 和受影响接口通过。
- [ ] 8099/8100 隔离保持。
- [ ] 状态、失败码、Word 和下载字段一致。
- [ ] checkpoint、备份和回退路径可用。
- [ ] GitHub、发布包、镜像和部署 SHA 一致。
- [ ] 已记录观察窗口、日志、OOM/restart 和是否回退。
- [ ] 最终通过后再更新桌面优化记录。

## 8. 回退检查清单

- [ ] 已记录触发门禁、失败 SHA、上一安全 SHA 和理由。
- [ ] 已停止继续发布或扩大测试。
- [ ] backup 与 release state 精确对应本次发布。
- [ ] `backup-manifest.json` 和 `SHA256SUMS` 完整且匹配。
- [ ] rollback dry-run 只影响 8099。
- [ ] 8100 不在命令、路径、容器和端口范围内。
- [ ] 旧镜像存在且 revision 匹配。
- [ ] 数据恢复为保护新文件的合并方式。
- [ ] 回退后 revision、`APP_GIT_SHA`、`.deployed_commit` 一致。
- [ ] `/health`、`/records-ui` 和必要历史接口正常。
- [ ] release state 已由工具闭合为 `rolled_back`。
- [ ] 失败、回退、备份、验证和后续处置已写入记录。

## 9. 固定 3、按需固定 10 与影响指纹

### 9.1 固定 3

每次功能发布执行固定 3：manifest 中三个案例在部署后的 8099 各运行 1 次，共 3 次。核对：

- 成功/失败状态和唯一主失败码；
- draft/final/deliverable/manual review；
- Word 和下载字段；
- 受影响 UI/API；
- 适用正式载体的 13 条禁用表达命中 0。

S0 的每例重复 3 次、共 9 次是基线阶段特殊要求，不是普通 M2 默认次数。

### 9.2 按需固定 10

以下任一条件触发 M3 和完整固定 10：

1. EvidenceItem schema、抽取、compact、FormalBody、Word、报告门禁或下载真值变化；
2. 影响指纹变化；
3. 任一指纹字段 unknown、缺失、不可定位或不可可信比较；
4. `reuse_eligible=false`；
5. 质量指标相对 baseline 有退化风险；
6. 公司正式演示前人工触发完整验证；
7. M2 出现状态、禁用表达、历史或下载不一致。

固定 10 按 manifest 声明案例执行。源附件客观不可用时，记录稳定材料身份、`SOURCE_ATTACHMENT_UNAVAILABLE` 和观察时间，列入 `excluded_cases`；不得伪造 hash，也不得用 Dify 异常缩减样本。

### 9.3 影响指纹

影响指纹须将规范字段序列化为 canonical JSON，并对该 canonical JSON 计算 SHA-256；字段和规则以第 6.6 节指定的规范来源为准。重算至少覆盖：

- manifest 版本及材料、正文、附件 hash；
- EvidenceItem schema/version；
- PDF、表格、OCR、VBP 抽取规则；
- compact schema、规则和实现；
- Dify workflow ID/version/definition、prompt/model/generator 版本和配置 hash；
- QualityGate 与 RepairPipeline；
- FormalBody、正文清理、Word 生成和安全发布。

只有完全一致且所有字段均已知时，才可按 M2 执行固定 3 和前端接口/UI 检查。任一变化或 unknown 均升级 M3。

本轮没有以静态源文件 hash 冒充完整影响指纹，也没有因用户取消数据测试而改写 `reuse_eligible=false`。

## 10. 责任分工

具体姓名待项目负责人指定；未指定前只定义职责，不虚构责任人。

| 角色 | 负责人 | 职责 | 不得越权 |
|---|---|---|---|
| 发布负责人 | 待指定 | clean SHA、备份、dry-run、发布/回退决策；本人或指定恢复批准人审批 checkpoint 恢复 | 不得跳过门禁、覆盖未确认分支或无依据批准恢复 |
| 验收负责人 | 待指定 | M2/M3、固定 3/10、Word、状态和 UI/API 结论 | 不得把 history/diagnostics 当事实证据 |
| 前端负责人 | 待指定 | 提供完整 SHA、变化范围和 UI 验收结论 | 不得直接改写 S5 worktree |
| Dify 配置负责人 | 待指定 | 提供已发布 workflow/prompt/model 版本和非敏感 hash | 不得用本地文件 hash 冒充远端已发布配置 |
| 运行值守 | 待指定 | 健康、日志、容器、历史、OOM/restart 观察和升级；经批准在维护窗口执行 checkpoint 恢复并记录结果 | 不得未经批准恢复、自行部署、回退或清理运行数据 |
| 业务验收 | 待指定 | 判断正式可交付和人工复核结论 | 不得绕过下载或质量门禁 |
| 记录维护 | 待指定 | 门禁通过后更新优化记录和证据索引 | 不得提前写“已通过”或改写历史缺口 |

任何角色发现 SHA、影响指纹、状态、制品或 8100 隔离异常，均可停止发布流程。

## 11. 已知缺口与后续停止点

本次提交不掩盖以下已知缺口：

1. S0 没有执行当前计划定义的第一次完整固定 10。
2. S2 没有完成第二次完整固定 10 和完整 M3。
3. S4 影响指纹含 Dify unknown 字段，`reuse_eligible=false`。
4. 当前权威计划尚未作为同内容文件进入本 S5 Git 分支。
5. 当前发布包原文件不可定位，仅在 verified state 中保留 SHA-256。
6. 责任人姓名尚未指定。
7. 本轮按用户明确指令没有运行任何新增数据、报告或 Word 门禁。

处理结论：

- 当前生产代码、UI、运行服务和回退资产可以保持内部持续使用；
- 本 S5 提交只关闭“项目静态完整性、证据索引和运行手册”工作；
- 不声称关闭严格 M3 数据门禁；
- 下一次功能发布前必须解决适用缺口并重新选择 M2/M3；
- 本文提交后暂停，不进入其他阶段，不触发部署或关机。
