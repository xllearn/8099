# 8099 验收补充方案受控批准记录

- 批准时间：`2026-07-20T13:27:43+08:00`
- 时区：`Asia/Shanghai`
- 批准来源：用户在 Codex 任务 `PLAN-SUPERSESSION-APPROVAL-AND-CONTROLLED-COMMIT（单人负责版）` 中发出的明确消息
- 批准标识：`PLAN-SUPERSESSION-APPROVAL-AND-CONTROLLED-COMMIT-2026-07-20`
- 基础分支：`codex/s5-internal-acceptance`
- 基础 HEAD：`3b439146bf021b5cb399190669bba373f980e062`
- 候选生产代码：`88309d57dcda056efa250cf37c2d77757536445e`
- 受控方案路径：`docs/superpowers/plans/2026-07-20-8099-acceptance-supersession-plan.md`

## 1. 批准内容

```text
PLAN_SUPERSESSION_APPROVED=YES
CONTROLLED_DOC_COMMIT_AUTHORIZED=YES
CONTROLLED_DOC_PUSH_AUTHORIZED=YES
SINGLE_OPERATOR_MODEL=YES
REAL_NAME_REQUIRED=NO
```

用户批准以 SHA-256
`1B96E6F11696A4A0DBC97DC7C23406A3CBA9EAA8FF0C9E835F96ECB4FF55283E`
标识的 V2 草稿作为基础，并按本次指令改为单人负责模式后纳入受控 Git。

本项目为单人负责，全部职责由同一项目负责人承担。不要求或记录真实姓名，不划分独立人员角色，不设置职责分离或角色兼任门槛。单人负责不降低任何技术、授权、备份、工具能力、前序阶段、恢复、回退或 8099/8100 隔离门禁。

## 2. 本次允许范围

仅允许：

1. 将 SHA-256 为
   `BFDB9E67B6138ABA50864C8A8BD56CA37AF6C0C7DA0571FCD1CE8795258D49BA`
   的权威计划精确字节复制到受控同路径；
2. 创建
   `docs/superpowers/plans/2026-07-20-8099-acceptance-supersession-plan.md`；
3. 创建本批准记录；
4. 对 `docs/runbooks/s5-internal-acceptance.md` 作单人负责及本次受控计划纳管所必需的事实同步；
5. 创建一个文档提交，并仅推送
   `codex/s5-internal-acceptance`。

## 3. 未授权范围

```text
LIVE_DATA_TESTS_AUTHORIZED=NO
PRODUCTION_8099_LEASE=NOT_GRANTED
DEPLOY_AUTHORIZED=NO
RECOVER_AUTHORIZED=NO
ROLLBACK_AUTHORIZED=NO
MAIN_MODIFICATION_AUTHORIZED=NO
S5_FINAL_STARTED=NO
```

本批准不授权 R1/S0、R2/S2、R3/S5、真实数据、固定 3/10、任何测试、Dify、Word、8099/8100 访问或操作、部署、恢复、回退、main 合并、cherry-pick 或 PR 创建。

批准本方案不构成任何阶段人工 8099 租约。R1、R2、R3 仍须分别取得新的明确用户批准消息，并关闭受控方案中全部适用 B 类门禁。

## 4. 受控生效条件

只有以下条件全部成立，方案才进入受控生效状态：

- S5 worktree 预检通过；
- 权威计划受控副本 SHA-256 精确匹配；
- diff 只包含批准的四份文档；
- 不含姓名占位符、人员分工派生阻塞、密钥、环境文件、缓存、备份或运行产物；
- `git diff --check` 通过；
- 文档提交成功；
- `codex/s5-internal-acceptance` 推送成功；
- 远端同名分支 SHA 与本地提交完全一致。

包含本文件的提交 SHA、各文件 SHA-256 和最终受控引用由本轮最终输出记录，避免在提交内容中形成自引用。
