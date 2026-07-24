# Dify 调试直出模式设计

## 目标

只在新服务器 `.87` 增加可回退的 Dify 调试直出模式，使前端和 Word 展示 Dify 实际生成结果，不再由后端用公告材料拼接兜底报告，也不再由后端改写、删除或修复 Dify 正文。旧服务器 `.88` 不修改。

## 当前问题

`.87` 当前即使关闭 `ENABLE_UNSUPPORTED_FACT_REPAIR`，仍存在三层会改变或拦截 Dify 输出的逻辑：

1. `StructureRepairer` 会把空、过短、列表开头或结构不足的正文替换成 `backend_pack_fallback`。
2. `ForbiddenPhraseRepairer` 和 `_clean_model_output` 会删除或清理正文内容。
3. Word 下载依赖正文安全检查；命中禁用表达时，即使 Dify 返回了正文也可能无法下载。

因此，仅设置 `ENABLE_STRICT_DELIVERY_GATE=false` 不能实现调试直出。

## 开关和作用域

新增环境开关：

```text
ENABLE_DIFY_DEBUG_PASSTHROUGH=true
```

- 默认值为 `false`，不改变既有环境行为。
- 只在 `.87` 的运行环境中设置为 `true`。
- `.88` 不新增、不修改此变量。
- 调试结束后将 `.87` 的开关恢复为 `false` 并重建容器，即恢复原有后处理流程。

## 数据流

### Dify 返回非空正文

1. 保留 Dify `report_markdown` 的原始字符串，不调用 `_clean_model_output`。
2. 跳过 `StructureRepairer`、`UnsupportedFactRepairer` 和 `ForbiddenPhraseRepairer`。
3. 质量门禁继续运行，但只写入诊断、问题代码、质量状态和 `needs_manual_review`，不得改变 `report_title`、`report_markdown` 或 `report_ir`。
4. 前端直接展示保存的 Dify 正文。
5. 调试版 Word 可以下载；系统仍可把报告标记为 `needs_manual_review`，且不得标记为最终可交付版本。

### Dify 正文为空或调用失败

1. 不调用 `_fallback_report_from_pack`，不把正文、附件摘要、结构化表格或证据包内容拼成报告。
2. 生成诊断 Markdown，内容仅包括：
   - Dify 工作流运行 ID（如存在）；
   - 错误代码、错误消息和经过长度限制的错误详情；
   - Dify 最终输出摘要或原始响应摘要（如后端已获得）。
3. 诊断结果标记为 `needs_manual_review` 和 `dify_debug_diagnostic`，并允许下载调试版 Word。
4. 诊断 Word 不加入公告材料、证据包内容、页码或溯源信息。

### 超时看门狗

调试模式下，看门狗超时也只能写入 Dify 超时诊断结果，不得提前生成材料兜底稿。若后续 Dify 结果按现有并发保护规则被接受，则仍保存 Dify 实际结果。

## Word 导出

- 调试模式下，只要保存记录中存在非空 Dify 正文或诊断正文，即允许生成调试版 Word。
- 调试模式绕过会删除正文内容的 `_safe_markdown_formal_body` 清理路径，直接把保存的 Markdown 交给现有 Word 渲染器。
- 质量问题仍显示在前端诊断区，不写入正式报告正文。
- `draft_word_export_available=true`；`final_word_export_available=false`，除非将来关闭调试模式并重新通过正式交付门禁。

## 可观测性

分析运行记录增加或明确写入：

```json
{
  "dify_debug_passthrough": true,
  "candidate_source": "dify_raw_output",
  "fallback_used": false
}
```

空输出或调用失败时：

```json
{
  "dify_debug_passthrough": true,
  "candidate_source": "dify_debug_diagnostic",
  "diagnostic_output_used": true,
  "fallback_used": false
}
```

日志只记录运行 ID、错误代码、字符数和状态，不记录完整报告、完整证据包或密钥。

## 最小验证

遵守“不在本地运行项目”的要求。部署到 `.87` 后只做服务器最小验证：

1. `/health` 正常，容器健康。
2. 容器环境中 `ENABLE_DIFY_DEBUG_PASSTHROUGH=true`。
3. 使用合成的非空 Dify 结果验证保存前后正文哈希一致，质量门禁只写诊断。
4. 使用合成的空输出/错误验证结果为诊断正文，且不含公告材料内容。
5. 验证两种结果均可下载调试版 Word。
6. 只读确认 `.88` 容器未重建、镜像和环境未改变。

不在本次变更中运行真实公告批量测试；后续真实材料测试由用户另行确认。

## 回退

发布前保留 `.87` 当前镜像标签和部署文件副本。若出现健康检查失败、正文仍被替换或 Word 无法下载：

1. 恢复原部署文件；
2. 使用原镜像重建 `.87` 容器；
3. 核验 `/health`；
4. 不操作 `.88`。
