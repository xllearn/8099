# 暂停后端不受支持事实删除设计

## 目标

停止 8099 报告后处理对“不受支持事实”的自动缩窄和删除，避免后端质检把已经经过两轮 LLM 质检与修订的报告再次大幅删短。

本变更采用用户选择的方案 B：只调整生产运行配置，不修改报告算法、正文结构、质量状态模型、Word 策略或下载接口。

## 根因

生产运行文件 `docker-compose.s4-runtime.yml` 当前将
`ENABLE_UNSUPPORTED_FACT_REPAIR` 固定为 `"true"`。在
`ENABLE_STRICT_DELIVERY_GATE=true` 时，`UnsupportedFactRepairer` 会：

1. 为 Markdown 和 ReportIR 建立 claim 证据索引；
2. 尝试把混合 claim 缩窄到有证据支持的子句；
3. 无法缩窄时删除整句或整行表格；
4. 在最终校验时再次删除残留的不受支持 claim。

因此后端不仅检测问题，还会改变正式正文，直接造成报告缩短。

## 变更范围

只修改：

- `docker-compose.s4-runtime.yml`
- 对应的运行配置契约测试

配置变更：

```yaml
ENABLE_UNSUPPORTED_FACT_REPAIR: "false"
```

以下门禁保持开启：

```yaml
ENABLE_EVIDENCE_INDEX: "true"
ENABLE_VBP_QUALITY_GATE: "true"
ENABLE_STRICT_DELIVERY_GATE: "true"
```

## 预期行为

配置生效后的新报告：

- `UnsupportedFactRepairer` 原样返回 Markdown 和 ReportIR；
- 后端不再缩窄或删除不受支持事实；
- 证据索引、本地质量门禁、VBP 质量门禁继续运行；
- 检测到问题时仍可写入 `quality_gate`、`quality_check`、
  `remaining_issues` 和失败码；
- 报告仍可被标记为 `needs_manual_review`，不会因为关闭删除器而自动变为
  `deliverable`；
- 既有报告和既有运行记录不进行追溯修改。

## 明确接受的限制

本次不修订现有下载契约、UI 或正文内标记。

当前 `needs_manual_review` 报告仍可能作为草稿通过通用下载路径获取。这是用户选择
方案 B 后明确保留的现状，不得在本次变更中顺带扩展为下载接口、Word 水印、文件名或
历史数据结构改造。

本次也不在正文中插入“待核实”“需人工复核”等文字，因为这些文字会与现有正式载体
安全规则冲突。

## 测试设计

按 TDD 增加运行配置契约断言：

1. `docker-compose.s4-runtime.yml` 中
   `ENABLE_UNSUPPORTED_FACT_REPAIR == "false"`；
2. `ENABLE_EVIDENCE_INDEX == "true"`；
3. `ENABLE_VBP_QUALITY_GATE == "true"`；
4. `ENABLE_STRICT_DELIVERY_GATE == "true"`。

先让新增断言在当前 `"true"` 配置下失败，再将配置改为 `"false"` 并确认通过。
同时运行现有受控修复及发布配置相关单元测试，但不运行固定 3、固定 10、真实公告、
Dify 或报告生成测试。

## 部署设计

本次代码镜像不变，继续使用：

```text
medical-notice-analyzer:s4-88309d5
```

生产代码身份仍为：

```text
88309d57dcda056efa250cf37c2d77757536445e
```

配置提交 SHA 只能表示运行配置版本，不得冒充镜像 revision 或部署代码 SHA。

部署步骤：

1. 只读确认 GitHub main、镜像 revision、当前容器代码 SHA 和四个质量开关；
2. 新建独立的部署前配置备份，不修改已有备份；
3. 将受控的 `docker-compose.s4-runtime.yml` 更新到 8099 部署目录；
4. 使用同一镜像和同一 `S4_GIT_SHA` 重新创建 8099 容器；
5. 不构建新镜像；
6. 不运行报告、固定集、Dify 或真实数据测试；
7. 只读验证容器运行状态、restart/OOM、四个开关、镜像 revision、
   `APP_GIT_SHA`、`.deployed_commit`、GET `/health` 和 GET `/records-ui`；
8. 只读确认 8100 未被修改。

## 回退

如容器无法启动、健康检查失败、身份漂移或配置不符合预期：

1. 恢复本次部署前备份的 `docker-compose.s4-runtime.yml`；
2. 使用同一镜像重新创建 8099 容器；
3. 确认 `ENABLE_UNSUPPORTED_FACT_REPAIR=true`；
4. 再次核对健康、容器和代码身份；
5. 保留失败现场和回退证据，不修改 8100。

## 不在范围内

- 不修改 Dify 工作流、提示词或 URL 旧代码；
- 不修改 compact、PDF/OCR、EvidenceItem；
- 不修改报告正文或 FormalBody 安全策略；
- 不修改 Word 生成、下载、draft/final/deliverable 契约；
- 不修改 checkpoint、history、diagnostics 的事实来源边界；
- 不运行真实数据测试；
- 不合并 main，除非用户后续单独授权。
