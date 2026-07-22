# `.87` 最小容量与证据摘要改造

日期：2026-07-22
状态：用户批准执行
目标分支：`codex/new-server-87-medical-notice-analyzer-20260722`

## 范围

本次只处理两项代码改动，并只部署到新服务器 `192.168.34.87:8099`：

1. 去掉新环境被旧 `80000` 字符常量再次钳制的问题。新环境按配置的 `240000` 字符执行，必保字段上限随请求变为 `239000`；旧环境继续保持 `80000/79000`，UTF-8 硬上限继续为 `870400` 字节。
2. 复用现有附件解析结果。表头清楚时由代码直接映射；表头含糊时，公司 DeepSeek 仅输出中文摘要和列索引映射。企业、产品、规格、采购量、价格等行值仍由程序从解析行或现有 `table_cell` 证据中读取并投影，模型不能返回或覆盖原始值。

公司模型 Key 只允许由 analyzer 的 `EVIDENCE_SUMMARY_LLM_API_KEY` 环境变量注入，并且只在报告生成前的含糊表格摘要步骤读取。它不进入 Git、Dify、前端、日志、最终报告、QA、修订流程或旧服务器。

## 最小实现

- `app/main.py`：删除 VBP 的旧 8 万兼容钳制；调用证据裁剪时按 `target_max_chars - 1000` 传入必保字段上限；在证据包完成后执行一次含糊表格摘要增强；把摘要、列映射和最多 40 行程序投影值传入 compact evidence。
- `app/compact_pack.py`：保留旧默认 `79000`，新增请求级必保字段参数。
- `app/attachment_parser.py`：保留当前已解析的行值，补充规则列索引和清晰/含糊状态；不重建多格式解析体系。
- `app/attachment_cache.py`：只提升解析缓存版本，使 `.87` 不继续复用缺少行数据传递的旧缓存；不删除现有缓存文件。
- `app/evidence_summary_llm.py`：只负责含糊表格摘要与列索引映射；一次请求最多处理 20 张含糊表，每张最多提供 40 行，输入总量最多 60000 字符。
- `docker-compose.yml`、`.env.example`：增加空 Key 和非秘密模型配置；默认关闭。
- `prompts/report_*_prompt.md`：删除“详见附件清单”等冲突话术，明确正文自包含、明细过多只在正文展示部分，且不输出附录或页码/表号等内部溯源信息。
- `dify_workflow_pack_id_human_style.yml`、`app/formal_body_safety.py`：同步同一条自包含约束，并阻止“详见附件/附录/内部溯源”等措辞流入正式正文。
- 提升 `COMPACT_RULE_VERSION`，防止 `.87` 复用修复前约 8 万字符的 compact cache。

不实施 Dify Contract V2、完整表格规范层、聚合平台、新质量门禁、附录体系或本地测试体系。

## 部署与验证

按用户要求不做本地测试。提交并推送新服务器分支后：

1. 只读记录 `.87` 当前镜像、提交标记、运行 Compose 文件和健康状态。
2. 在 `/opt/medical-notice-analyzer-releases/` 创建带时间戳的备份，保留当前源码包、运行 Compose、环境配置和当前 Docker 镜像引用。
3. 构建新镜像，只重建 `medical-notice-analyzer-development-87`；不连接、不修改 `.88`。
4. 在 `.87` 验证 `/health`、模型接口可用性、一个清晰表头样本不调用 DeepSeek、浙江样本含糊表头调用 DeepSeek，以及最终 compact 字符数不再固定在约 7.9 万。
5. 任一关键检查失败即使用备份恢复旧镜像和 Compose 配置。

## 验收口径

- `.87` 的 `hard_limit_chars` 仍为 `240000`，80K 到 240K 之间的结果不再被旧兼容钳制压回约 79K；超过 240K 时沿现有压缩路径收敛到接近 240K。
- `.88` 未部署、未重启、未修改。
- 清晰表头使用 `semantic_summary_source=rules`；含糊表头成功时使用 `semantic_summary_source=deepseek`。
- `row_values` 中的企业、产品和数值均来自原解析行，DeepSeek 只提供摘要和列索引。
- 仓库、提交和服务器日志均不出现真实 API Key。
