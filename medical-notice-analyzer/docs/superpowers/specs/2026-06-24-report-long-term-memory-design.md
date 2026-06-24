# MedNoticeAI 报告长期记忆设计

日期：2026-06-24

## 1. 目标

在现有医药公告分析报告系统中增加受控的长期记忆能力，使经过人工确认的写作规则、业务口径、附件处理规则、表格规则和质量门禁可以复用于后续报告。

长期记忆只能指导报告的写法、分析角度和质量检查，不能成为当前公告事实的来源，也不能覆盖当前主材料、附件、辅助材料或用户本次要求。

第一版必须满足：

- 用户可以独立查看和维护正式记忆与候选记忆。
- 用户生成报告时可以选择是否使用正式记忆。
- 正式记忆读取失败不会导致报告生成失败。
- 候选记忆默认不参与报告生成。
- 保存前自动备份，部署和容器重建不丢失数据。
- 旧客户端和现有自动化脚本不传新字段时，保持原有生成行为。

## 2. 范围

### 2.1 第一版包含

- `report_memory.md` 正式长期记忆。
- `memory_candidates.md` 候选长期记忆。
- 后端文件读写服务、备份、校验和 API。
- 独立的 `/memory-ui` 管理页面。
- 报告生成页面的“使用长期记忆”开关。
- `/analysis/run` 接入 `use_report_memory`。
- Dify 新增独立输入变量 `report_memory`。
- 生成、质检、修订、二次质检节点接入长期记忆约束。
- run metadata、日志和前端状态展示。
- 开启、关闭、读取失败和兼容性测试。

### 2.2 第一版不包含

- 模型或质检节点自动写入候选记忆。
- 候选记忆自动合并到正式记忆。
- 自动从历史报告提炼规则。
- 按公告类型自动检索部分记忆。
- 数据库化版本管理和复杂审批流。

候选记忆第一版支持完整文本查看、编辑、保存和人工复制。自动追加接口可以预留，但不接入生产生成链路。

## 3. 核心原则

### 3.1 事实优先级

生成和质检必须遵守：

```text
当前主材料正文
> 当前主材料附件
> 当前辅助材料正文和附件
> 用户本次要求
> report_memory
> 历史分析材料
```

`report_memory` 不得提供或改写当前公告的日期、价格、品种、企业、产品、数量、比例、地区、机构和规则事实。

### 3.2 人工审核隔离

- `report_memory.md` 只存放人工确认的规则。
- `memory_candidates.md` 只存放待审核经验。
- 后端生成报告时永远不读取 `memory_candidates.md`。
- 候选内容必须由用户人工整理后才能进入正式记忆。

### 3.3 兼容默认值

- 管理页面中的开关默认开启，并明确发送 `use_report_memory=true`。
- 后端 `AnalysisRunRequest.use_report_memory` 默认值为 `false`。
- 旧脚本只发送 `pack_id` 时不启用长期记忆，保持现有行为。

## 4. 存储设计

### 4.1 路径

新增环境变量：

```text
MEMORY_DIR=/app/data/memory
```

未配置时默认使用项目运行目录下的 `data/memory`。Docker Compose 显式配置 `/app/data/memory`，并复用现有：

```text
./data:/app/data
```

文件：

```text
/app/data/memory/report_memory.md
/app/data/memory/memory_candidates.md
/app/data/memory/backups/report_memory_YYYYMMDD_HHMMSS.bak.md
/app/data/memory/backups/memory_candidates_YYYYMMDD_HHMMSS.bak.md
```

用户维护内容不进入 Git，不写入镜像层。

### 4.2 初始化

读取文件时若文件不存在：

1. 创建目录。
2. 使用需求中给出的默认模板创建 UTF-8 文件。
3. 返回模板内容。

初始化必须是幂等操作。

### 4.3 保存

保存流程：

1. 校验内容类型、长度和空内容。
2. 如果旧文件存在，将旧文件复制到 `backups`。
3. 将新内容写入同目录临时文件。
4. 刷新并使用原子替换覆盖目标文件。
5. 清理超过保留数量的旧备份。

默认限制：

```text
REPORT_MEMORY_MAX_CHARS=20000
MEMORY_CANDIDATES_MAX_CHARS=100000
MEMORY_BACKUP_KEEP_COUNT=50
```

正式记忆超过 12,000 字符时前端提示精简，超过 20,000 字符时后端拒绝保存。服务端不静默截断。

空白内容默认拒绝保存。第一版不提供强制清空接口。

## 5. 后端组件

新增聚焦的记忆服务模块，避免继续扩大 `app/main.py`：

```text
app/report_memory.py
```

职责：

- 解析安全的固定文件路径。
- 初始化默认模板。
- 读取、校验、备份和原子保存。
- 计算字符数、更新时间和 SHA-256。
- 清理过期备份。

该模块不得接受客户端传入文件路径或文件名。

## 6. API 设计

### 6.1 GET /memory/report

返回：

```json
{
  "success": true,
  "kind": "report",
  "content": "# Report Memory...",
  "chars": 8260,
  "max_chars": 20000,
  "updated_at": "2026-06-24 10:00:00",
  "sha256": "..."
}
```

文件不存在时自动创建模板。

### 6.2 PUT /memory/report

请求：

```json
{
  "content": "# Report Memory..."
}
```

保存前自动备份。响应包含新摘要和备份文件名，不返回服务器绝对路径。

### 6.3 GET /memory/candidates

返回候选记忆完整内容和摘要。候选内容不影响生成。

### 6.4 PUT /memory/candidates

保存候选记忆并自动备份。

### 6.5 POST /memory/candidates/append

第一版可以实现为受保护的结构化追加接口，但不由 Dify 自动调用。若实施复杂或增加风险，可只保留设计，不开放路由。

### 6.6 写入保护

新增环境变量：

```text
MEMORY_WRITE_TOKEN=
```

生产环境配置令牌后，所有 PUT/POST 写接口必须携带：

```text
X-Memory-Write-Token: <token>
```

GET 接口保持只读访问。令牌不得出现在代码、Git、URL、响应和日志中。管理页面只在当前浏览器会话中保留令牌。

若生产环境未配置令牌，页面必须显示安全警告。部署验收要求服务器配置令牌。

## 7. 管理页面

新增：

```text
GET /memory-ui
app/static/memory.html
```

页面使用两个 Tab：

- 正式长期记忆
- 候选记忆

每个 Tab 包含：

- Markdown 文本编辑区。
- 当前字符数和最大字符数。
- 更新时间和内容摘要。
- 保存按钮。
- 保存确认对话框。
- 保存成功或失败提示。

正式记忆额外显示：

- “该内容将影响后续报告写法，但不能覆盖公告事实”的提示。
- 超过建议长度时的警告。

候选记忆额外显示：

- “候选内容不会自动参与报告生成”的提示。
- 人工复制和整理说明。

材料选择页面只增加一个“长期记忆管理”入口，不嵌入记忆编辑器。

## 8. 报告生成接入

### 8.1 请求模型

```python
class AnalysisRunRequest(BaseModel):
    pack_id: str = Field(min_length=1)
    use_report_memory: bool = False
```

前端生成请求：

```json
{
  "pack_id": "pack_xxx",
  "use_report_memory": true
}
```

### 8.2 运行快照

用户点击生成时，后端读取一次正式记忆并形成该 run 的内存快照。后台线程使用该快照，避免报告执行期间文件被修改而导致同一次 run 前后规则不一致。

run 记录只保存：

```json
{
  "report_memory_requested": true,
  "report_memory_applied": true,
  "report_memory_chars": 8260,
  "report_memory_sha256": "...",
  "memory_read_failed": false
}
```

run JSON 不保存完整正式记忆正文。

### 8.3 读取失败

若读取失败：

- 继续生成报告。
- 将 `report_memory` 设为空字符串。
- `report_memory_applied=false`。
- `memory_read_failed=true`。
- warnings 增加轻量提示。
- 日志记录失败类型，不记录完整文件内容。

读取失败不能触发报告兜底，也不能将最终状态单独改为 `needs_manual_review`。

## 9. Dify 设计

### 9.1 输入变量

Dify Start 节点新增：

```text
report_memory
```

后端请求：

```json
{
  "inputs": {
    "pack_id": "pack_xxx",
    "report_memory": "..."
  }
}
```

关闭或读取失败时传空字符串。

`report_memory` 是独立输入变量，不写入 `evidence_pack`，不改变现有 evidence pack 自适应压缩和诊断统计。

### 9.2 节点接入

以下节点接收 `report_memory`：

- Generate Report JSON
- Quality Check JSON
- Revise Report JSON
- Second Quality Check JSON

生成节点使用它指导写作结构、分析角度、附件处理和错误规避。

质检节点检查：

- 是否违反人工确认的质量规则。
- 是否把长期记忆当成当前公告事实。
- 是否因长期记忆引入不受证据支持的内容。

修订节点只能在 evidence pack 支持的范围内按长期记忆修复。

### 9.3 输入额度

正式记忆独立限制为 20,000 字符。它不从 evidence pack 的 75,000 字符目标中扣除，但总请求规模必须在回归测试中观察。

如果线上 Dify 或模型的总上下文不足，应优先要求人工精简长期记忆，不得进一步压缩主材料事实来给长期记忆让位。

## 10. 诊断和界面状态

报告详情页显示：

- 是否请求使用长期记忆。
- 是否实际应用。
- 记忆字符数。
- 记忆读取是否失败。
- 记忆内容摘要哈希的短版本。

不显示完整正式记忆，不把记忆内容插入报告正文。

日志记录：

```text
run_id
pack_id
report_memory_requested
report_memory_applied
report_memory_chars
memory_read_failed
```

## 11. 错误处理

- 文件不存在：创建模板。
- 目录不可写：GET/PUT 返回明确错误；生成链路降级为不使用记忆。
- 内容为空：拒绝保存。
- 内容超限：返回 422 和当前/最大字符数。
- 备份失败：拒绝覆盖旧正式记忆。
- 原子替换失败：旧文件保持不变。
- 写入令牌错误：返回 401 或 403。
- Dify 不接受新增变量：本地测试阻断部署，不静默退回旧工作流。

## 12. 测试设计

### 12.1 单元和 API 测试

- 文件缺失时创建默认模板。
- 正式记忆和候选记忆读取。
- 保存前创建备份。
- 原子写入失败不破坏旧文件。
- 空内容和超长内容被拒绝。
- 备份数量受限。
- 路径不可由请求操控。
- 写入令牌正确、缺失和错误场景。
- 旧 `/analysis/run` 请求不传字段时保持关闭。
- 开启、关闭和读取失败的 run metadata。
- Dify payload 中 `report_memory` 的正确值。
- 候选记忆从不进入 Dify payload。

### 12.2 前端测试

- `/memory-ui` 可加载两个 Tab。
- 可以读取、编辑、确认和保存。
- 字符限制和错误提示正确。
- 生成页面默认开启并显式发送布尔值。
- 报告详情显示实际应用状态。

### 12.3 工作流和回归测试

选择 5 组代表性样本进行开启/关闭对照：

- 短正文无附件。
- 短正文、附件主导。
- 普通 `1+0`。
- `1+n` 主辅材料。
- 复杂 `2+n`。

随后运行现有 16 组回归。比较：

- 失败数和超时数。
- unsupported fact 数。
- source fidelity。
- analysis depth。
- 报告字数和附件覆盖。
- 主辅材料边界。

## 13. 验收标准

- `/memory-ui` 可查看和维护两类记忆。
- 正式记忆保存前自动备份，部署后数据仍存在。
- 候选记忆不参与报告生成。
- 页面开启时实际传入正式记忆。
- 页面关闭时传入空字符串。
- 旧客户端不传字段时保持当前行为。
- 读取失败不导致报告失败。
- run metadata 和报告详情准确显示使用状态。
- 记忆不会出现在报告正文或公开日志中。
- 开启长期记忆后 unsupported fact 数不能增加。
- 原文遵循评分不能明显下降。
- 现有主辅材料、附件解析、Dify 调用、报告修订和 Word 下载测试保持通过。

## 14. 发布顺序

1. 在本地实现文件服务、API、页面和测试。
2. 修改本地 Dify DSL 并完成结构测试。
3. 使用本地或测试 Dify 验证新增变量。
4. 运行完整自动化测试和 5 组开启/关闭对照。
5. 备份服务器当前代码和持久化数据。
6. 更新并发布生产 Dify 工作流。
7. 部署后端。
8. 验证健康检查、管理页面和读写权限。
9. 运行 5 组线上定向测试。
10. 通过后运行 16 组完整回归。

生产部署前必须保留服务器原版本，并确保 `data/memory` 不被部署包覆盖。
