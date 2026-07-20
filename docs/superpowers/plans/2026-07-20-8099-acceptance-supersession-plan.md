# 8099 S0/S2 追补与 S5 最终验收计划局部替代方案（受控批准版）

## 0. 文档控制

- 文档状态：`CONTROLLED_APPROVED`
- 执行状态：`LIVE_NOT_STARTED`
- 受控版本：`controlled-2026-07-20.1`
- 草拟时间：`2026-07-20T11:16:56+08:00`
- V2 草稿来源：`C:\tmp\8099-handoffs\8099-plan-supersession-draft-v2.md`
- V2 草稿 SHA-256：`1B96E6F11696A4A0DBC97DC7C23406A3CBA9EAA8FF0C9E835F96ECB4FF55283E`
- 唯一候选生产代码基线：`88309d57dcda056efa250cf37c2d77757536445e`
- 受控文件路径：`docs/superpowers/plans/2026-07-20-8099-acceptance-supersession-plan.md`
- 批准记录：`docs/superpowers/approvals/2026-07-20-8099-acceptance-supersession-approval.md`
- 建议的持久证据根目录：`/opt/medical-notice-analyzer/data/test-results/acceptance-supersession-<approval-id>/`

用户已通过 `PLAN-SUPERSESSION-APPROVAL-AND-CONTROLLED-COMMIT（单人负责版）` 明确批准本方案进入受控 Git，并授权本次文档 commit 及 feature branch push。本项目为单人负责，全部职责由同一项目负责人承担；不要求真实姓名，不建立多角色、职责分离、角色兼任或实名登记门槛。

该批准只覆盖本文件、权威计划受控副本、批准记录和 S5 运行手册的文档修改、commit 及 `codex/s5-internal-acceptance` 推送。本文不授权 S5-FINAL、真实数据测试、Dify 调用、Word 生成或扫描、8099/8100 访问、部署、恢复、回退、生产写操作或 main 合并，不得被当作 `LIVE_DATA_TESTS_AUTHORIZED=YES` 或任一阶段的 8099 独占批准。

## 1. 固定依据与 V2 修订目的

| 输入 | 路径或值 | 固定身份 |
|---|---|---|
| 权威计划 | `C:\Users\admin\Documents\New project\medical-notice-analyzer-8099-prod\docs\superpowers\plans\2026-07-09-8099-optimization-implementation-plan.md` | SHA-256 `BFDB9E67B6138ABA50864C8A8BD56CA37AF6C0C7DA0571FCD1CE8795258D49BA` |
| S0 PREP | `C:\tmp\8099-handoffs\S0-fixed10-prep-handoff.md` | SHA-256 `064AEA6E8225801925CCB269B747012F71106222CCF9AD847E0B7522B6075517` |
| S2 PREP | `C:\tmp\8099-handoffs\S2-m3-prep-handoff.md` | SHA-256 `35410BA015C4B85A3C4B78298909A41061B0F5F99D5BBF226E49879F40A24DFE` |
| V2 草稿 | `C:\tmp\8099-handoffs\8099-plan-supersession-draft-v2.md` | SHA-256 `1B96E6F11696A4A0DBC97DC7C23406A3CBA9EAA8FF0C9E835F96ECB4FF55283E`，本轮已精确复核 |
| 最终候选代码 | 当前最终生产代码基线 | `88309d57dcda056efa250cf37c2d77757536445e` |

本受控版保留 V2 的历史事实、串行追补和最终证明边界，并按用户批准改为单人负责模式；同时保留 R1 验证并复用当前 883 部署、证据目录、A/B/C 技术阻塞分类、42 次运行成本，以及 commit `88309d57...` 的工具能力静态审计结论。

权威计划与本补充方案发生歧义时，不得由执行窗口自行解释为放宽授权或门禁。无法可信判断时状态为 `WAIT/STOP`。

## 2. 方案效力、历史事实与证明边界

### 2.1 受限局部替代路径

本方案只提出以下执行顺序：

1. 不部署、不运行历史 S0/S2 SHA。
2. 只在最终候选 `88309d57dcda056efa250cf37c2d77757536445e` 上执行三次相互独立、严格串行的验收：
   - R1：S0 追补运行；
   - R2：S2 追补运行；
   - R3：S5 最终运行。
3. R1、R2、R3 分别取得用户明确批准，分别独占 8099，分别产生新调用、新报告、新 artifact、新 manifest 副本、新 evaluation 和新 hash。
4. 后一阶段只可只读引用前一阶段已经封存的 baseline/hash；前一阶段的案例结果、Word、snapshot、扫描或 evaluation 不得计入后一阶段。
5. 8100 始终为禁止修改对象。

本方案不替代权威计划的硬门禁，不授权 native、shadow 或灰度，也不改变 compact、PDF/OCR、EvidenceItem、报告正文或 Word 安全策略。

### 2.2 历史事实冻结，不倒写历史

S0 历史事实保持不变：

- 历史 S0 代码 SHA 为 `b0dcddd8c905ce8be47d454a1f333bf6f9efd2d9`，只作历史标识，不得部署。
- 当时受控记录为 `fixed3_only`，`fixed10` 被明确豁免。
- 本次 R1 的 fixed10 和 baseline B 是在 `88309d57...` 上形成的当代追补证据，不得覆盖、改名或升格历史 S0 记录。

S2 历史事实保持不变：

- 历史 S2 代码 SHA 为 `99a4df42da9266b22f3fa8b0d7972320887d5a49`，只作历史标识，不得部署。
- 当时只完成条件验收；第二次完整 fixed10、完整 Word 扫描和完整 baseline compare 没有关闭。
- 本次 R2 不能改写为“历史 S2 SHA 当时已经完成 M3”。

三次新运行若全部通过，只能证明：

> 最终候选 `88309d57dcda056efa250cf37c2d77757536445e` 及各次运行记录的当时配置、数据和 Dify 状态满足相应门禁。

它不能证明历史 S0/S2 SHA 当时已经满足后来补齐的门禁，也不能证明历史 SHA 与当前 schema、checkpoint、history 或运行数据兼容。

## 3. 当前授权、租约和仓库能力的事实核对

### 3.1 本轮核对范围

本轮只对本地 Git 对象 `88309d57dcda056efa250cf37c2d77757536445e` 做静态只读核对；没有运行测试，没有访问服务器，没有访问或操作 8099/8100。因此：

- 仓库能力可以按本节证据判定；
- 服务器是否另有授权、租约或归档设施为 `UNKNOWN_BY_SCOPE`；
- 不得因为服务器未核对，就要求一个尚未证明存在的系统作为方案生效或运行前置条件。

仓库授权/租约搜索采用：

```powershell
git -C "C:\Users\admin\Documents\New project\medical-notice-analyzer-8099-prod" grep -n -I -E "LIVE_DATA_TESTS_AUTHORIZED|PRODUCTION_8099_LEASE|fencing[_ -]?token|authorization[_ -]?record|PLAN_SUPERSESSION_REFERENCE" 88309d57 -- .
```

本轮结果为零命中，`git grep` 退出码为 `1`。本次有限关键词静态检索未发现与上述名称对应的验收授权记录系统、生产 8099 lease 服务或 fencing token 服务；它不能排除采用其他名称的实现。服务器未获准核对，因此服务器侧能力仍为 `UNKNOWN_BY_SCOPE`。本方案只据此认定“没有已核实可用的对应设施”，不作绝对不存在性声明。

仓库存在的 `threading.RLock`、checkpoint recovery ID、analysis history 的单 worker 检查和 release journal 不是跨窗口生产租约，也不能被描述成 authorization/fencing 基础设施。

### 3.2 已存在但用途不同的发布能力

下列真实文件存在于 `88309d57...`：

- `scripts/release_support.py`
  - `create_git_artifact()`：生成并校验带 `.release-source-sha` 的代码制品；
  - `install_code_artifact()`：校验制品后安装，并写入 `.deployed_commit`；
  - `build_and_deploy_image()`：为镜像写入 `org.opencontainers.image.revision=<source_sha>`。
- `docker-compose.s4-runtime.yml`
  - 将 `S4_GIT_SHA` 注入容器为 `APP_GIT_SHA`；
  - 固定 `WEB_CONCURRENCY=1`、`UVICORN_WORKERS=1`。
- `scripts/deploy_8099.py`
  - 是发布工具；默认 dry-run，只有显式执行模式才会产生部署写操作。

本轮静态核对命令为：

```powershell
git -C "C:\Users\admin\Documents\New project\medical-notice-analyzer-8099-prod" show 88309d57:scripts/release_support.py
git -C "C:\Users\admin\Documents\New project\medical-notice-analyzer-8099-prod" show 88309d57:docker-compose.s4-runtime.yml
git -C "C:\Users\admin\Documents\New project\medical-notice-analyzer-8099-prod" show 88309d57:scripts/deploy_8099.py
```

这些能力可为部署身份核对提供证据，但它们不是授权、租约或 fencing 系统，也不证明服务器上的当前制品实体仍可定位。

## 4. 方案批准、逐阶段人工租约与单人负责模式

### 4.1 方案批准

用户已明确批准以 SHA-256 `1B96E6F11696A4A0DBC97DC7C23406A3CBA9EAA8FF0C9E835F96ECB4FF55283E` 标识的 V2 内容及本次单人负责修订进入指定受控 Git 路径。本方案只有在 A 类文档门禁全部关闭、文档提交成功、仅推送指定 feature branch 且远端同名分支与本地提交完全一致后，才进入受控生效状态。受控引用必须记录：

`PLAN_SUPERSESSION_REFERENCE=<受控commit>:<受控路径>:<文件SHA-256>`

批准草稿、把文件纳入 Git、批准 R1/R2/R3 LIVE 是三个不同动作。批准本方案不自动授权任何一次真实数据运行。

### 4.2 不依赖未核实存在系统的人工租约

在没有已核实的服务端租约基础设施时，本方案对“8099 独占租约”的唯一有效定义是：

> 用户在对应 R1、R2 或 R3 的 Codex 任务中发出的明确批准消息。

批准不能由执行窗口自行生成、自行补写或从模糊语句推导。每次开始前的阶段批准消息及其持久记录至少包含：

- 对话/任务/thread 标识和可定位的消息标识或完整引用；
- 消息时间及时区；
- 唯一 `stage-approval-id`；
- 阶段：R1/S0、R2/S2 或 R3/S5；
- 精确 `PLAN_SUPERSESSION_REFERENCE`；
- `candidate_code_ref=88309d57dcda056efa250cf37c2d77757536445e`；
- `LIVE_DATA_TESTS_AUTHORIZED=YES`；
- 对应的 `PRODUCTION_8099_LEASE=S0|S2|S5`；
- 获准操作范围、写入范围、时间窗或结束规则；
- 明确 `8099_EXCLUSIVE=YES` 和 `8100_MODIFICATION=PROHIBITED`；
- 是否包含 Dify、Word、真实数据写入；部署、recover、回退必须分别明确，不能默认为包含；
- 确认本项目继续采用单人负责模式；同一项目负责人承担当次批准范围核对、执行、证据封存和停止处置职责，但不得自行把本次文档批准扩展为 LIVE 或生产写授权。

运行期间或结束后发生的变更、撤销和释放消息不可能预先存在，不属于开始前批准字段。它们必须在发生后追加到同一阶段的 control 记录，并记录消息引用、时间、原因和当时状态；阶段封存及下一阶段的 B14 再核对释放记录。

这些字段只是人类可审计的批准记录，不是环境变量赋值后即可自授权的技术开关。没有明确消息、开始前字段不完整、消息已撤销、存在并发窗口或阶段不匹配时，状态一律为 `NOT_LEASED/STOP`。

本方案不要求数字签名、不可自签 authorization 服务、lease ID 服务、TTL/heartbeat 服务、fencing token 或双人签发，因为本轮没有核实到这些设施可用，服务器也未获准核对。若以后发现真实设施，只有在给出精确文件、命令、运行证据并经用户批准修订本方案后，才可采用；不得反向把它变成本方案的隐含前置条件。

### 4.3 单人负责模式

本项目为单人负责，全部职责由同一项目负责人承担。不要求填写或记录真实姓名，不划分独立人员角色，不设置职责分离或角色兼任门槛。

同一项目负责人负责候选代码和部署身份复核、阶段门禁复核、8099 独占协调、备份和数据保护、Dify 配置证据、业务验收、运行观察及记录维护。

单人负责只取消人员登记和职责分离门槛，不合并或豁免技术门禁、逐阶段明确授权、8099 独占、备份、工具能力、前序阶段、恢复、回退及 8100 隔离要求。

## 5. A/B/C 阻塞分类

### 5.1 A 类：方案进入受控 Git 前必须关闭

| ID | 阻塞项 | 当前状态 | 关闭证据 |
|---|---|---|---|
| A1 | 用户批准 V2 基础内容、本次单人负责修订、指定受控路径和“只在 883 上重新举证”的替代边界 | `CLOSED_BY_APPROVAL_RECORD` | `docs/superpowers/approvals/2026-07-20-8099-acceptance-supersession-approval.md` |
| A2 | 受控文件内容、SHA-256、doc-only diff、文档 commit 和远端同名 feature branch 一致；文档 commit 不得冒充部署 SHA | `CLOSE_ON_VERIFIED_COMMIT` | 本轮提交前检查、提交、Python dulwich 推送及推送后远端 SHA 核对 |

A 类关闭后，才可把精确获批文件加入建议路径。Git commit 产生后还必须重新计算受控文件 SHA-256，并形成 `PLAN_SUPERSESSION_REFERENCE`；该动作不等于 R1 授权。

### 5.2 B 类：每次 R1/R2/R3 开始前必须关闭

| ID | 阻塞项 | 当前状态 | 关闭要求 |
|---|---|---|---|
| B1 | 当次用户批准消息和人工 8099 独占租约 | `NOT_GRANTED` | 满足第 4.2 节“开始前”全部记录字段；每阶段单独批准，运行后释放不作为开始前字段 |
| B3 | 无其他部署、固定数据、Dify、恢复或回退任务，前一窗口已退出服务器操作 | `UNKNOWN_BY_SCOPE` | 当次只读现场证据及用户窗口协调记录 |
| B4 | `VERIFY_AND_REUSE_EXISTING_883` 的全部身份和配置字段一致 | `UNKNOWN_BY_SCOPE` | 第 6 节完整 identity manifest；任一漂移即停 |
| B5 | 当前 source release artifact 实体、marker 和 SHA 可定位并与 883 一致 | `UNKNOWN_BY_SCOPE` | 制品绝对路径、SHA-256、`.release-source-sha` 和安装关联证据 |
| B6 | 真实数据写入前的数据/history/checkpoint/release state 备份与恢复映射 | `NOT_PERFORMED` | 当次备份清单、hash、时间、恢复路径；不含实际 recover |
| B7 | manifest 的 declared/selected/excluded、材料 hash 和本次来源可用性观察已冻结 | `PENDING_RUNTIME_VERIFICATION` | 当次 manifest contract/hash 和观察记录 |
| B8 | 持久证据根、create-new 目录、实际权限、保留期和归档处置获批 | `PENDING_METHOD_APPROVAL` | 第 7 节记录；不存在专用 ACL/归档系统时记录并批准实际方法，不得假称已有 |
| B9 | runner 首错即停和所有失败路径证据保全方法 | `BLOCKED_METHOD_DESIGN` | 独立证据侧方法、精确文件/hash、命令和验证结果获批 |
| B10 | 从叶证据独立重算全部 S2/M3 指标的方法 | `BLOCKED_METHOD_DESIGN` | 不信任 producer summary 的独立 evaluator 设计及验证证据 |
| B11 | 不改写 stage 的 R2/R3 baseline compare 方法 | `BLOCKED_METHOD_DESIGN` | 独立 comparator 的文件/hash、命令、输入输出契约及验证证据 |
| B12 | 完整 carrier、递归残留和下载/history 一致性方法 | `BLOCKED_METHOD_DESIGN` | 第 8 节缺口全部关闭或证明当次制品不存在未覆盖 carrier |
| B13 | Dify workflow/prompt/model/config 影响指纹采集 | `PENDING_CAPTURE` | 当次逐字段记录 value 或显式 `unknown`、来源和可信度即可关闭“采集”项；unknown 不得改写成相同，也不阻止强制完整验收，但必须令 `reuse_eligible=false`，R3 保持 M3 |
| B14 | 上一阶段独立 PASS、证据封存和人工租约释放 | R1 为 `N/A`，R2/R3 为 `PENDING_PREDECESSOR` | 前一阶段 handoff、顶层 hash 和用户释放消息 |

B 类不是要求先建设新的授权或 lease 服务。凡现有能力缺失，只允许采用经批准、可复核的独立证据方法；在方法关闭前不得开始 LIVE。

### 5.3 C 类：只需在 S5 最终结论前关闭

| ID | 阻塞项 | 当前状态 | 关闭要求 |
|---|---|---|---|
| C1 | 与 `BFDB9E67...` 精确一致的权威计划进入受控版本 | `CLOSE_ON_VERIFIED_COMMIT` | 同路径受控副本 SHA-256、文档提交和远端一致性 |
| C2 | 当前前端发布包实体 | `PENDING_NOT_LOCATED` | 定位可验证、持久保存的 `88309d57...` 前端制品，或按另行批准流程建立该制品；只有状态文件 hash 不算实体可用 |
| C3 | R1、R2、R3 三套独立证据、handoff、hash 和人工租约释放记录 | `NOT_STARTED` | 三阶段各自 PASS，不能互相冒充 |

前端发布包实体明确属于 C 类，不阻止方案进入受控 Git；若它同时被声称为 R1 身份链中的 source release artifact，则还必须满足 B5，不能用 C 类标签绕过运行身份门禁。单人负责模式已由批准记录固定，不构成 A、B 或 C 类姓名与人员分工阻塞；各项技术和逐阶段授权门禁仍按其原分类关闭。

## 6. R1 部署锚点：验证并复用当前 883

### 6.1 推荐模式

推荐且默认的 R1 模式为：

`R1_DEPLOYMENT_MODE=VERIFY_AND_REUSE_EXISTING_883`

如果以下身份全部可定位、可哈希、相互一致并精确指向 `88309d57dcda056efa250cf37c2d77757536445e`，则复用当前已部署 883，不为了 R1/R2/R3 阶段标签重复构建或部署：

1. 本地批准的生产源 HEAD；
2. GitHub `main` 的远端 SHA；
3. source release artifact 实体、完整 SHA-256 和 `.release-source-sha`；
4. 镜像 `org.opencontainers.image.revision`、image ID/digest；
5. 运行容器使用的 image ID/digest；
6. 容器 `APP_GIT_SHA`；
7. 安装根 `.deployed_commit`；
8. 批准范围内的源配置文件 hash 和不泄密的有效配置 hash；
9. 容器名、容器 ID、启动时间、运行状态、单 worker、restart count、OOM 状态和端口绑定；
10. 当前镜像预期引用 `medical-notice-analyzer:s4-88309d5` 与实际镜像身份。

建议在未来获批的只读核对中采用下列命令形态；这些命令本轮没有执行，远端名、GitHub URL、安装根和容器名必须先按现场确认：

```bash
git -C /opt/medical-notice-analyzer rev-parse HEAD
git -C /opt/medical-notice-analyzer status --porcelain
git -C /opt/medical-notice-analyzer remote -v
git ls-remote <approved-github-url> refs/heads/main

sha256sum <source-release-artifact>
tar -xOf <source-release-artifact> .release-source-sha
cat /opt/medical-notice-analyzer/.deployed_commit

docker image inspect --format '{{.Id}} {{index .RepoDigests 0}} {{index .Config.Labels "org.opencontainers.image.revision"}}' medical-notice-analyzer:s4-88309d5
docker inspect --format '{{.Name}} {{.Id}} {{.Image}} {{.State.Status}} {{.State.StartedAt}} {{.RestartCount}} {{.State.OOMKilled}}' medical-notice-analyzer
docker exec medical-notice-analyzer printenv APP_GIT_SHA
docker exec medical-notice-analyzer printenv WEB_CONCURRENCY
docker exec medical-notice-analyzer printenv UVICORN_WORKERS
```

`docker image inspect` 对空 RepoDigests 的格式需在执行卡中先做无副作用兼容处理，不能因模板报错跳过 digest 核对。

仓库当前没有经核实的 canonical config-hash 工具。未来方法必须：

- 明确列出实际参与 8099 的 compose/runtime/config 文件；
- 对 `.env` 等敏感文件只输出 hash，不输出内容；
- 对有效容器环境采用不泄露变量值的规范化 hash；
- 保存 expected/actual hash 和方法脚本自身 hash；
- 不把配置 hash 当作配置内容可以忽略的替代品。

该方法当前为 `B-PENDING/BLOCKED_METHOD_DESIGN`，本方案不声称已经实现。

### 6.2 漂移和重新部署

- 任一 local/remote/source artifact/image revision/`APP_GIT_SHA`/`.deployed_commit`/配置 hash/容器身份缺失、不一致或不可可信比较，立即 `STOP`。
- 不得为关闭漂移而擅自重新构建、重新部署或修改配置。
- 如果确需重新部署同一 883，必须另行取得用户明确批准，批准记录至少写明 `DEPLOY_8099_AUTHORIZED=REDEPLOY_883`、精确制品、命令、备份、影响范围和回退点。
- 本 V2 的批准、R1 数据测试批准或 8099 人工租约均不包含 `REDEPLOY_883`。
- 如候选部署 SHA 不再是 `88309d57...`，本序列停止并重新制定发布/验收方案。

### 6.3 数据写入前备份

即使复用现有 883，在每次会产生真实数据写入的阶段开始前，仍须备份受影响的：

- `data`；
- analysis `history`；
- analysis `checkpoint`；
- release state，包括 `.deployed_commit`、release journal/manifest 和相关配置 hash。

每份备份必须有绝对路径、创建时间、源范围、完整 SHA-256 清单、恢复映射和只读验证结果。备份不授权 recover 或回退；实际恢复/回退仍需单独批准。

## 7. 持久证据根与禁止覆盖规则

### 7.1 建议目录

建议精确根目录为：

`/opt/medical-notice-analyzer/data/test-results/acceptance-supersession-<approval-id>/`

根目录中的 `<approval-id>` 固定为本方案批准记录所引用的用户批准消息标识，或由该不可变消息标识生成的无歧义、可回查标签，本文称为 `supersession-approval-id`。它不是授权服务生成的 token。R1/R2/R3 各自另有第 4.2 节的唯一 `stage-approval-id`。每个阶段和每个 runner 调用均使用全新的、此前不存在的子目录：

```text
acceptance-supersession-<approval-id>/
  R1-S0-<stage-approval-id>-<timestamp>/
    control/
    fixed3/
    fixed10/
    evaluations/
    baseline-B/
    carriers/
    state-and-interfaces/
    backup-references/
    manifest-and-hashes/
  R2-S2-<stage-approval-id>-<timestamp>/
    control/
    fixed3/
    fixed10/
    evaluations/
    compare-to-R1/
    carriers/
    state-and-interfaces/
    backup-references/
    manifest-and-hashes/
  R3-S5-<stage-approval-id>-<timestamp>/
    control/
    fixed3/
    fixed10/
    evaluations/
    compare-to-R1/
    carriers/
    state-and-interfaces/
    backup-references/
    manifest-and-hashes/
```

一个 `supersession-approval-id` 只对应获批方案版本；三个 `stage-approval-id` 分别对应三次 LIVE 批准。阶段目录和 `control/` 必须同时记录两者及消息引用。任何情况下都不得共享 runner output 目录。

### 7.2 create-new 和封存

目标 commit 的 runner 对 `--output-dir` 使用 `mkdir(parents=True, exist_ok=False)`，因此现有目录会报错，支持 runner 根目录的 create-new 语义；但它没有名为 `--create-new` 的参数，也不会自动建立 R1/R2/R3 编排。

`scripts/evaluate_regression_baseline.py` 和 `scripts/freeze_regression_baseline.py` 的原子写入最终使用 `os.replace`，若指定的 output 文件已存在会覆盖。现有 evaluator/freeze 因此不具备文件级 create-new 保证；B8 的执行方法必须在调用前断言目标文件不存在，并在已存在时 fail closed。

执行卡必须保证：

1. 父级阶段目录和 fixed3/fixed10 output 均不存在后再创建；
2. 目录已存在即停止，不删除、不清空、不改名后复用；
3. fixed3 与 fixed10 使用不同 `--output-dir`；
4. 失败目录原样封存；修复后重跑使用新的 `stage-approval-id` 和新 timestamp 目录；
5. 每阶段复制并哈希自己的 manifest、artifact、evaluation、carrier、状态和接口证据；
6. 每阶段生成覆盖全部叶文件的 `SHA256SUMS`，再记录该清单自身的 SHA-256；
7. R2/R3 对 R1 baseline B 只能保存只读引用、路径和 hash，不得将其改称本阶段产物。

### 7.3 Git、ACL、保留期和归档

证据根位于项目 `data/` 下；目标 commit 的 `.gitignore` 忽略 `data/`，但不能只依赖 ignore 规则。运行制品、真实数据、报告、DOCX、日志、备份、密钥和 `.env` 禁止进入 Git，且禁止使用 `git add -f`。

未来执行前应只读确认：

```bash
git -C /opt/medical-notice-analyzer check-ignore -v data/test-results/acceptance-supersession-<approval-id>/
```

当前没有核实服务器上的专用 ACL、保留期策略或归档系统，因此状态必须保持：

```text
EVIDENCE_ROOT_EXISTENCE=PENDING_SERVER_VERIFICATION
EVIDENCE_ROOT_ACL=PENDING_USER_APPROVAL
EVIDENCE_RETENTION=PENDING_USER_APPROVAL
EVIDENCE_ARCHIVE=PENDING_USER_APPROVAL
```

关闭这些 B 类项不要求凭空建设新系统。可以由用户批准现有 OS 权限、明确保留天数和实际持久存储/归档方法；如果没有归档系统，必须如实记录“无专用归档系统”和获批处置，不能写成“已实现归档”。

## 8. runner、evaluator、freeze、compare 和 carrier scanner 静态审计

### 8.1 审计方法和限制

本节结论来自对 commit `88309d57...` 的 `git show`、`git grep` 和源代码阅读，不是测试结果或服务器运行结果。以后若工具文件变化，必须按新 blob/hash 重新审计。

主要核对入口：

```powershell
git -C "C:\Users\admin\Documents\New project\medical-notice-analyzer-8099-prod" show 88309d57:scripts/run_fixed_regression.py
git -C "C:\Users\admin\Documents\New project\medical-notice-analyzer-8099-prod" show 88309d57:app/regression_manifest.py
git -C "C:\Users\admin\Documents\New project\medical-notice-analyzer-8099-prod" show 88309d57:app/offline_quality_evaluator.py
git -C "C:\Users\admin\Documents\New project\medical-notice-analyzer-8099-prod" show 88309d57:scripts/evaluate_regression_baseline.py
git -C "C:\Users\admin\Documents\New project\medical-notice-analyzer-8099-prod" show 88309d57:scripts/freeze_regression_baseline.py
git -C "C:\Users\admin\Documents\New project\medical-notice-analyzer-8099-prod" show 88309d57:app/formal_body.py
git -C "C:\Users\admin\Documents\New project\medical-notice-analyzer-8099-prod" show 88309d57:app/formal_body_safety.py
```

### 8.2 能力矩阵

| 能力 | 真实文件与证据 | 判定 | 对本方案的影响 |
|---|---|---|---|
| fixed3/fixed10 runner | `scripts/run_fixed_regression.py:1102-1123` 提供 `--subset`、`--repeat`、`--stage`、`--output-dir`；`validate_run_matrix()` 对 S0 只接受 fixed3×3 或 fixed10×1 | `SUPPORTED` | R1 可用 `stage=S0`；R2 用 `stage=S2`；R3 必须用 `stage=M3` 以触发 M3/S2 观察 |
| manifest declared/selected/excluded | `app/regression_manifest.py` 的 `select_cases()`/`excluded_cases()`；当前 fixture 声明 10、静态预期 selected 9/excluded 1 | `SUPPORTED_WITH_RUNTIME_RECHECK` | 当前 fixed10 预计 9 个新报告，不等于执行 10 个；每阶段前必须重验来源 |
| create-new output | runner 使用 `exist_ok=False` | `SUPPORTED` | 每次调用的新根可防覆盖；不存在显式 `--create-new` 或三阶段编排 |
| evaluator/freeze 文件级防覆盖 | 两个 CLI 的原子 writer 最终使用 `os.replace` | `PARTIAL/BLOCKED_FOR_NO_OVERWRITE` | 必须由 B8 的独立执行检查保证 output 不存在；不得覆盖旧 evaluation/baseline |
| case 级首错即停 | runner 捕获 case 异常后写入 `failures` 并继续后续 case/attempt；没有 `--fail-fast` | `BLOCKED` | 在独立 fail-fast 方法获批前，R1/R2/R3 均不得开始 |
| 所有失败路径完整写 summary | health/Word 前置检查和最终 snapshot replay 校验都发生在 `summary.json` 写入前 | `PARTIAL` | 方法必须保存前置失败、部分 case 和未运行项，不能假称 runner 自带完整失败证据 |
| snapshot/hash/manifest evaluator | `evaluate_artifact_from_snapshots()` 验证快照 hash，重算 diagnostics、unsupported、FormalBody/DOCX、history/Word/compact 和 material fingerprint | `PARTIAL` | 可作基础，但不能满足全部独立质量重算 |
| S2/M3 质量指标独立重算 | evaluator 以 `derived={**dict(sample), ...}` 保留 summary 中的 claim/support/C-support/repair/new-fact 等字段，后续直接聚合 | `BLOCKED` | 必须另有从叶 snapshot 重算这些字段的方法，不能相信 producer summary |
| 同一 evaluator 重复两次 | CLI 可对同一 artifact 分别写两个 output，且实现具确定性 | `SUPPORTED_BUT_NOT_INDEPENDENT_IMPLEMENTATION` | 两次调用可检查确定性，但不能替代独立算法或叶重算缺口 |
| R1 full baseline freeze | `scripts/freeze_regression_baseline.py` 支持多个 `--evaluation` 和 `--coverage-mode full`；freeze 强制 `server_test`、`stage=S0`、fixed3×3 + fixed10×1 | `SUPPORTED_AFTER_EVALUATOR_GAPS_CLOSE` | R1 必须保留 `stage=S0`，可形成新 baseline B；仓库已有 s0.json 仍是旧 evaluator 的 fixed3_only，不能复用 |
| R2/R3 compare | `compare_evaluation_to_baseline()` 首先调用 `_validate_evaluation_for_freeze()`，该校验强制 `stage==S0` | `BLOCKED` | 当前 compare 会拒绝 S2/M3；禁止把 R2/R3 改标成 S0，必须设计非改标 comparator |
| `NO_COMPATIBLE_PREDECESSOR` | 目标 commit 全仓无此符号；现有 compare 只给 generic mismatch/无 matching subset | `BLOCKED` | R1 的“无兼容前序”只能由获批的独立结构化证据记录生成，不能声称现有工具支持 |
| Markdown/ReportIR/FormalBody | `app/formal_body.py` 枚举 ReportIR 文本；`app/formal_body_safety.py` 扫描 FormalBody 全文本段 | `SUPPORTED` | 可纳入每阶段 carrier 证据 |
| DOCX 常规载体 | `scan_docx()` 扫描正文段落、普通/嵌套表格、默认/首页/偶数页页眉页脚，并做 NFKC 和空白规范化 | `SUPPORTED_FOR_COVERED_PARTS` | 13 条禁用表达可在这些承载面扫描 |
| 完整 OOXML carrier | 现有 scanner 不覆盖 textbox/shape、footnote、endnote、comment、custom XML、altChunk、属性或嵌入对象 | `PARTIAL/BLOCKED_FOR_COMPLETE_CLAIM` | 必须证明本次自产 DOCX 不含未覆盖部件，或采用经批准的独立 OOXML scanner |
| 残留扫描 | runner 只在指定目录顶层 `glob(".word-staging-*")`；未递归扫描 `*.tmp`、`*.part` | `PARTIAL/BLOCKED` | R2/R3 的全树残留为 0 需要独立递归方法 |
| history/download 一致性 | runner/evaluator 检查 detail/list 和部分 Word 状态；`word_downloaded` 参数未被实际用于完整事件/URL/filename 对账 | `PARTIAL/BLOCKED` | 四状态、failure code、history 和下载字段的完整一致性需补充证据方法 |

### 8.3 已存在 CLI 的合法使用边界

R1 runner 的真实命令形态为：

```bash
python scripts/run_fixed_regression.py \
  --base-url http://127.0.0.1:8099 \
  --manifest tests/fixtures/8099_regression_cases.json \
  --subset fixed3 --repeat 3 --stage S0 --environment server_test \
  --output-dir <NEW_R1_FIXED3_ROOT> --report-dir <REPORT_DIR> --poll-seconds 2

python scripts/run_fixed_regression.py \
  --base-url http://127.0.0.1:8099 \
  --manifest tests/fixtures/8099_regression_cases.json \
  --subset fixed10 --repeat 1 --stage S0 --environment server_test \
  --output-dir <NEW_R1_FIXED10_ROOT> --report-dir <REPORT_DIR> --poll-seconds 2
```

R2 应使用 `--stage S2`，R3 应使用 `--stage M3`；不得为了通过 freeze/compare 把 R2/R3 改成 S0。

现有评价入口为：

```bash
python scripts/evaluate_regression_baseline.py \
  --artifact <summary.json> --artifact-root <artifact-root> --output <evaluation.json>
```

现有 R1 baseline freeze 入口为：

```bash
python scripts/freeze_regression_baseline.py \
  --baseline-id B-S0-CATCHUP-88309D57-<timestamp> \
  --manifest <frozen-manifest.json> \
  --evaluation <R1-fixed3-evaluation.json> \
  --evaluation <R1-fixed10-evaluation.json> \
  --coverage-mode full \
  --output <NEW-baseline-B.json>
```

在 B9-B12 关闭前，上述命令只说明真实 CLI 形态，不构成运行授权，也不证明整个链路已满足本方案。

### 8.4 独立方法设计的最小要求

缺失能力只能通过独立证据侧方法关闭，不能修改或部署 883 应用来临时绕过。每个方法必须：

1. 有受控文件路径、Git/blob SHA、完整 SHA-256 和依赖锁定；
2. 有精确输入/输出契约、退出码和 fail-closed 规则；
3. 有离线验证用例，覆盖正例、缺失、篡改、首错、stage 不改写及 carrier 边界；
4. 运行时保存精确命令、stdout/stderr、退出码和输出 hash；
5. 与 `candidate_code_ref=88309d57...` 分栏记录，证据工具 commit 不得冒充生产部署 SHA；
6. 未实现或未验证时标记 `BLOCKED`，不得把本计划中的伪代码或描述视为已实现能力。

## 9. 串行状态与三次运行独立性

本方案的状态标签只是审批和证据记录，不是仓库当前存在的 lease 服务：

`DRAFT → A_CLOSED → CONTROLLED_PLAN → R1_APPROVAL_PENDING → R1_USER_LEASED → R1_RUNNING → R1_SEALED → R1_USER_RELEASED → R2_APPROVAL_PENDING → R2_USER_LEASED → R2_RUNNING → R2_SEALED → R2_USER_RELEASED → R3_APPROVAL_PENDING → R3_USER_LEASED → R3_RUNNING → R3_SEALED → R3_USER_RELEASED → FINAL_PENDING → CLOSED`

约束如下：

1. R1、R2、R3 必须串行；任一时刻只允许对应 Codex 任务执行 8099 操作。
2. 每阶段使用自己的 approval message、run ID、workflow run ID、调用集合、artifact 根、manifest 副本、evaluation 和顶层 hash。
3. R2 只有在 R1 PASS、封存和用户释放后开始；R3 只有在 R2 PASS、封存和用户释放后开始。
4. 前一阶段的报告、Word、snapshot 或评价不计入后一阶段 completed/passed。
5. 人工批准范围撤销、到期或发生竞争窗口时，当前阶段立即停止；没有自动续租。
6. 失败阶段不得通过补跑少数失败样本变成 PASS；重新开始须使用新批准消息、新目录和本阶段完整矩阵。

## 10. 新报告运行数量与时间估计

### 10.1 当前矩阵

| 阶段 | fixed3 | fixed10 当前预计 | 阶段合计 |
|---|---:|---:|---:|
| R1 / S0 追补 | 3 例各 3 次 = 9 | selected 预计 9 例各 1 次 = 9 | 18 |
| R2 / S2 追补 | 3 例各 1 次 = 3 | selected 预计 9 例各 1 次 = 9 | 12 |
| R3 / S5 M3 | 3 例各 1 次 = 3 | selected 预计 9 例各 1 次 = 9 | 12 |
| 总计 | 15 | 27 | **42 次新报告运行** |

当前 fixed10 contract 是 declared=10、静态预期 selected=9、excluded=1；excluded 不产生新报告，但必须保留客观原因、材料身份和本次观察时间。

若 fixed10 的 selected 数为 `s`，总数公式为：

`15 + 3s`

selected/excluded 在 R1 前或阶段边界发生变化时，必须停止、重新核对材料、版本化 manifest、重新计算三阶段数量和时间，并重新取得受影响阶段批准。运行中不得静默缩样、把失败改成 excluded，或以成本/时间为由减少样本。

### 10.2 时间估计

以下只是排期估计，不是性能门禁或超时保证。仓库已有 fixed3-only 基线记录 mean 约 165.38 秒、p95/max 约 278.463 秒，但它没有覆盖当前 fixed10，不能直接当作 42 次的可靠预测。runner 默认单次 analysis timeout 为 1200 秒，准备、附件、下载、扫描、备份、评价和审批不包含在该单项上限中。

| 情形 | 估计 | 假设与说明 |
|---|---|---|
| 顺利 | 约 4–6 小时 | 42 次调用接近历史 fixed3 表现，身份/备份/扫描/封存无返工；纯调用粗估约 2–4 小时，另加控制与证据时间 |
| 常规 | 约 8–12 小时，建议跨 1–2 个工作日串行安排 | fixed10 重附件样本更慢，三阶段均需独立备份核对、评价、compare、carrier 扫描、handoff 和用户释放/批准 |
| 异常 | 至少 2–3 个工作日，且无可信硬上界 | 任一门禁失败即停；需要调查、独立方法修订、重新批准和从该阶段完整重跑。若系统性接近 20 分钟单项 timeout，单纯 42 次轮询理论量级已可达 14 小时，尚不含处置 |

当前仓库证据不能给出可审计的货币成本：runner artifact 未完整记录 input/output token 和有效价格。不得虚构金额；如用户要求预算门禁，应在每阶段批准前另行给出模型版本、价格生效时间、token 口径和批准上限。

## 11. R1：S0 追补运行

R1 只能在 A 类已关闭、受控方案引用存在、当次 B 类全部关闭后开始。

1. 按第 6 节执行 `VERIFY_AND_REUSE_EXISTING_883`；全部身份一致时不构建、不部署。
2. 在任何真实数据写入前完成 R1 备份和恢复映射。
3. 容器内全量测试要求 `0 failure、0 error`；skip 不得无说明增加。
4. 使用全新 R1 fixed3 artifact 执行 3 例各 3 次，共 9 个新报告。
5. 使用全新 R1 fixed10 artifact 执行第一次完整 contract：declared=10、selected+excluded=10、每个 selected 例 1 次；当前预计 9 个新报告。
6. 每个 selected case 保存自己的 run/workflow ID、snapshot、Markdown、ReportIR、FormalBody、全部预期 DOCX、history/detail/list/download、状态和 hash。
7. 13 条禁用表达命中为 0；四状态、`primary_failure_code`、history 和下载字段一致。
8. 由关闭 B10 的独立方法从叶证据重算全部指标；同一只读输入可再运行一次检查确定性，但重复同一实现不能冒充独立算法。
9. 在 evaluator 缺口关闭后，以 R1 fixed3 和 fixed10 的 `stage=S0` evaluation 冻结全覆盖追补 baseline B：

   `B-S0-CATCHUP-88309D57-<timestamp>`

10. baseline B 只由 R1 新证据组成，不含历史 fixed3_only、S2/S4 或任何后续结果。
11. 当前没有兼容的前序 full baseline，且现有工具不支持 `NO_COMPATIBLE_PREDECESSOR`。必须用获批的独立结构化记录列明候选集合、rules/manifest/evaluator 不兼容原因和 hash；该状态表示首次建立 baseline，不表示 compare PASS。

R1 任一项失败，序列停止。只有 R1 handoff 明确 PASS、证据封存且用户明确释放 S0 人工租约后，R2 才可申请批准。

## 12. R2：S2 追补运行

R2 必须使用新的批准消息、目录、调用和证据，不能复用 R1 样本。

1. 重新完成 R2 的 B 类门禁、身份核对和写入前备份。
2. 执行本阶段要求的容器内全量测试，要求 `0 failure、0 error`。
3. fixed3 3 例各 1 次，共 3 个新报告，`stage=S2`。
4. 使用与 R1 可比且重新冻结核对的 manifest contract 执行第二次完整 fixed10；当前预计 9 个新报告，`stage=S2`。
5. 对本次全部 selected case 的 Markdown、ReportIR、FormalBody、所有预期 DOCX 做扫描。
6. 对 DOCX 正文、普通/嵌套表格、页眉/页脚和 NFKC/空白变体扫描；对未覆盖 OOXML part 必须先证明不存在，或使用关闭 B12 的独立 scanner。
7. 递归核对 `.word-staging-*`、`*.tmp`、`*.part` 为 0。
8. 13 条禁用表达命中为 0；draft/final/deliverable/manual-review、`primary_failure_code`、run/history/list/detail、下载 URL/文件名/路径矛盾为 0。
9. 由独立叶证据 evaluator 生成 R2 evaluation。
10. 使用关闭 B11 的非改标 comparator 与 R1 baseline B 比较；不得将 `stage=S2` 改写为 S0。
11. 比较必须同时执行绝对门禁；“优于 baseline”不能豁免任何硬失败。
12. Dify 字段 unknown 时保留 unknown，并记录 `reuse_eligible=false`；compare 只能说明观测差异，不能证明 Dify 配置相同。

R2 任一项失败，序列停止。只有 R2 handoff 明确 PASS、证据封存且用户明确释放 S2 人工租约后，R3 才可申请批准。

## 13. R3：S5 最终运行

### 13.1 强制 M3

R3 开始时重新计算完整影响指纹。只要 Dify workflow、prompt、model 任一字段 unknown、缺失或不可可信比较，必须：

`reuse_eligible=false`

并以 `stage=M3` 执行完整 M3。R1、R2、S4 或任何旧证据不能替代 R3。部署 SHA 仍为 883 只表示可以不重复构建/部署，不表示可以复用验收结果。

### 13.2 R3 最低范围

1. 重新完成 R3 的 B 类门禁、身份核对和写入前备份。
2. 容器内全量测试 `0 failure、0 error`。
3. fixed3 3 例各 1 次，共 3 个新报告。
4. 第三次独立完整 fixed10，当前预计 9 个新报告。
5. 独立叶证据 evaluation，以及用获批 comparator 对 R1 baseline B 的比较；R2 只作相邻运行观察，不得充当 R3。
6. Markdown、ReportIR、FormalBody、所有预期 DOCX 的完整 carrier 扫描，13 条禁用表达命中 0。
7. 四状态、`primary_failure_code`、history/list/detail 和下载字段一致。
8. 使用 GET 验证 `/health`、`/records-ui`、历史 UI 及相关接口；不得用 HEAD 代替 GET。
9. 验证 8099/8100 隔离、单 worker、restart、OOM 和本次新增严重日志。
10. 完成权威计划要求的主动观察并保存证据。

### 13.3 checkpoint、恢复和回退

- 可只读引用 S4 已有 checkpoint 演练证据，或使用获批安全测试对象；引用不计入 R3 自己的报告、fixed3、fixed10、载体或 evaluation。
- 未经单独批准，不得对生产 run 发起 recover。
- provider 已开始但结果 unknown 时禁止自动重发。
- 回退优先核对 dry-run 和已有演练证据；如必须产生新生产写操作，暂停并申请单独授权。
- 历史 S0/S2 SHA 不是合法回退目标。
- 安全恢复点只能是本序列前已封存、与当前 schema/data 匹配的 883 精确安全状态。

### 13.4 S5 最终结论前的 C 类收口

R3 技术运行通过后仍不能直接宣布 S5 完成。C1-C3 必须全部关闭，尤其：

- SHA-256 为 `BFDB9E67B6138ABA50864C8A8BD56CA37AF6C0C7DA0571FCD1CE8795258D49BA` 的权威计划精确受控副本；
- 可定位、可验证、持久保存的 883 前端发布包实体；
- R1、R2、R3 的独立 PASS handoff、证据 hash 和用户租约释放记录。

任一 C 类缺口未关闭，最终结论必须为 `WAIT/未完成`。

## 14. 失败停止、数据保护、隔离与回退

1. B9-B12 未关闭时不开始 LIVE；不得用现有 runner 的 continue-on-error 行为冒充“任一门禁失败立即停止”。
2. 方法关闭后，任一身份、授权、测试、样本、Dify、hash、扫描、compare、状态一致性、隔离或观察失败，立即停止当前阶段和后续序列。
3. 失败现场写入当次新目录并封存，列出首个失败、已运行项、未运行项、原始叶文件和 `SHA256SUMS`；不得修补原目录后改称 PASS。
4. Dify/provider 结果 unknown 时标记 `INDETERMINATE`，不得自动重发。
5. 修复后重跑必须重新获批，使用新 approval ID、新 run/workflow ID、新目录，从该阶段完整矩阵开始；不得只补跑失败样本。
6. 每次真实数据写入前备份受影响 data/history/checkpoint/release state；备份、应用代码和运行证据分开封存。
7. 如果没有部署身份变化且系统仍处于已封存的 883 安全状态，不因质量失败自动回退；保持现场并暂停。
8. 如获批操作改变了数据或状态，只能按预先批准的恢复映射恢复；recover/回退需要单独用户批准。
9. 8099 始终独占；不得修改 8100。发现 8100 变化时停止、保全差异并上报，不得顺手修复。
10. 不删除 Dify 或 URL 旧代码；不改变 compact、PDF/OCR、EvidenceItem、报告正文或 Word 安全策略。
11. memory、history、checkpoint、diagnostics 只可作为运行/诊断证据，不得作为公告事实来源。
12. 恢复成功不能把失败验收改成通过；恢复后仍需 GET 核对 8099，并只读确认 8100 未变。

## 15. 最终结论与 Git 边界

即使三次运行和 C 类收口全部通过，最终表述也必须同时保留：

- 历史 S0 当时豁免 fixed10；
- 历史 S2 当时仅条件验收；
- R1/R2/R3 是在 `88309d57...` 上产生的三套新证据；
- 三次运行相互独立，不能互相冒充；
- Dify unknown 不能改写成相同；
- 本方案文档提交 SHA 不是生产代码 SHA。

生产代码 SHA 始终单独记录为：

`88309d57dcda056efa250cf37c2d77757536445e`

未来受控方案 commit 只表示治理文档版本，不得部署，也不得冒充生产代码。本方案不授权合并 main、cherry-pick、部署文档提交或部署历史 S0/S2 SHA。

## 16. 需求追踪

| V2 要求 | 落实位置 |
|---|---|
| 不建立未核实存在的签名、自签 authorization、lease/fencing 基础设施 | 第 3、4.2 节 |
| 租约改为用户在对应 Codex 任务中的明确批准消息 | 第 4.2、9 节 |
| 单人负责、不要求实名登记、不设置职责分离或角色兼任门槛 | 第 4.3、5 节 |
| R1 推荐 VERIFY_AND_REUSE_EXISTING_883 | 第 6 节 |
| 真实数据写入前备份 data/history/checkpoint/release state | 第 6.3、14 节 |
| 精确持久证据根、create-new、禁入 Git | 第 7 节 |
| A/B/C 阻塞区分；前端制品和三阶段证据归类 | 第 5 节 |
| R1 18、R2 12、R3 12，当前共 42 次及时间估计 | 第 10 节 |
| 核对 runner/evaluator/freeze/compare/carrier scanner 真实能力 | 第 8 节 |
| 工具缺口只能 BLOCKED 或独立方法设计 | 第 5.2、8.4、14 节 |
| 保留 S0 fixed10 豁免和 S2 条件验收历史 | 第 2.2、15 节 |
| 不部署历史 S0/S2 SHA | 第 2.1、13.3、15 节 |
| 在 883 上按 R1→R2→R3 串行运行 | 第 2.1、9、11–13 节 |
| 三次证据、调用和 artifact 独立 | 第 7、9、11–13 节 |
| Dify unknown 时 reuse_eligible=false，R3 保持 M3 | 第 5.2、12、13 节 |
| 任一门禁失败立即停止 | 第 14 节 |
| 8099 独占、8100 禁止修改 | 第 4.2、9、14 节 |
| 只证明最终候选，不证明历史 SHA 当时通过 | 第 2.2、15 节 |

## 17. 当前未解决项与受控生效条件

当前未解决项按门禁保留：

- A：A1 已由本次批准记录关闭；A2 仅在 doc-only diff、文件 hash、提交、指定 feature branch 推送及远端 SHA 一致性全部验证后关闭。
- B：本轮未访问服务器，因此 8099 独占、883 全身份、source artifact、配置 hash、备份、manifest、证据根权限/保留/归档均未验证；runner fail-fast、全部叶指标独立重算、非改标 compare、完整 carrier/残留/下载一致性方法仍为 BLOCKED。
- C：C1 随本轮权威计划精确副本、提交、推送及远端一致性验证关闭；前端发布包实体和三阶段最终证据仍未关闭。

对本受控方案的结论：

```text
PLAN_SUPERSESSION_APPROVED=YES
CONTROLLED_DOC_COMMIT_AUTHORIZED=YES
CONTROLLED_DOC_PUSH_AUTHORIZED=YES
SINGLE_OPERATOR_MODEL=YES
REAL_NAME_REQUIRED=NO
PLAN_EFFECTIVE=YES_ONLY_AFTER_VERIFIED_REMOTE_MATCH
LIVE_READY=NO
LIVE_DATA_TESTS_AUTHORIZED=NO
S5_FINAL_STARTED=NO
DEPLOYED=NO
MAIN_MODIFIED=NO
```

方案受控生效只表示批准的治理文档已经由指定 feature branch 的一致提交固定；不表示 B/C 技术门禁已经关闭，不表示可以开始 R1、R2、R3 或 S5-FINAL。

## 18. 本次受控纳管操作声明

本次 `PLAN-SUPERSESSION-APPROVAL-AND-CONTROLLED-COMMIT（单人负责版）` 只授权权威计划精确副本、本受控方案、批准记录和 S5 运行手册的必要文档修订，以及一个文档提交和 `codex/s5-internal-acceptance` feature branch 推送。

本次未授权且不得执行测试、固定数据、Dify、Word、8099/8100 访问或操作、部署、恢复、回退、main 合并、cherry-pick 或 PR 创建。完成受控文档推送后状态保持：

`CONTROLLED_APPROVED / LIVE_NOT_STARTED / PAUSED_AFTER_DOCUMENT_PUSH`
