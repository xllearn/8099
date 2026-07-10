# 8099 项目 P0-P6 分阶段实施计划

> 修订日期：2026-07-10
> 适用项目：`C:\Users\admin\Documents\New project\medical-notice-analyzer-8099-prod`
> 生产地址：`http://192.168.34.88:8099`
> 生产部署目录：`/opt/medical-notice-analyzer`
> 当前正式生成 provider：Dify
> 本计划每次只执行一个明确子阶段；子阶段完成后暂停，得到用户确认后再继续。

## 1. 当前状态与编号映射

| 新编号 | 原编号 | 状态 | 事实依据 |
|---|---|---|---|
| P0.0 | P0.0 | 已完成 | URL 分析软下线，commit `f3867b1` |
| P0.1 | P0.1 | 已完成 | 状态机与质量失败 schema，commit `30804ba` |
| P0.2-a | P0.2 | 已完成 | Generator、QualityGate、RepairPipeline 兼容抽象，commit `e528b3e` |
| P0.2-b1 | 原 P0.2-b0 与 P0.2-b1 | 已完成 | 专项开关、VBP 规则校验、项目公告优先排序，commits `f312714`、`0d62402` |
| P0.2-b2 | 原 P0.2-b2 | 未完成 | 候选 commit `c2ac702` 因线上 Word 命中禁用表达而由 `61969a9` 回退；当前线上无 history API |

当前执行点调整为 **P0.2-b1.4 Word 发布安全熔断**。它为 P0.2-b1.5 的强制正文安全门禁提供 fail-closed 回退点；P0.2-b1.5 通过后才重新实施 P0.2-b2。P0 完成前不进入 native shadow。

当前工作区已有与本计划无关的修改、删除和未跟踪目录。实施时只能提交各执行卡第 5 项列出的文件；不得清理、覆盖、回退或提交其他工作区内容。

## 2. 不可变约束

1. 正式输入只使用数据库材料和 `pack_id`；URL 旧链路保持软下线，直到 P6 达到物理删除门槛。
2. P0 正式 provider 保持 Dify；P1 只做 native shadow；P2 才逐档灰度；P2 稳定并关闭 fallback 后，P6 才能物理删除 Dify。
3. `memory`、`memory_items`、`summary`、`attachment_summary`、`generation_guidance`、模型解释和历史偏好均为 C 级辅助信息，不能独立支撑任何事实。
4. 正式 Markdown、ReportIR 和 Word 正文禁止出现：`原文未披露`、`需人工核验`、`需人工复核`、`证据不足`、`无法确认`、`请核验`、`建议人工确认`、`资料未显示`、`未在原文中找到`、`根据有限信息`、`以上内容需复核`、`待确认`、`待核实`。
5. 上述表达只能出现在 diagnostics、UI、run JSON、测试结果或日志。诊断说明不得写入 Word。
6. 正文事实缺少 A/B 证据时，只能删除完整无支持句、收窄到已有证据语义，或使用 A/B 证据改写；禁止引入补充事实。
7. P0.2-b1.5 上线后，`FormalBodySafetyGate` 是唯一中央正文安全实现，生产环境不可关闭。后续阶段只能调用它，不能复制第二套禁用表达逻辑。
8. 每个子阶段必须 TDD、本地自动化、固定 3-case 本地真实数据、Word 契约检查、Git 范围检查、commit、dulwich push、服务器备份、8099 部署、健康检查、同一固定 3-case 线上验证、失败回退、优化记录更新和暂停。
9. 每个大阶段完成时执行固定 10-case；未通过大阶段门禁不得进入下一大阶段。
10. 部署制品只能来自已经推送且远端 SHA 一致的 clean `git archive`；不得直接上传脏工作区。

## 3. Word 状态真值表

| 正文与门禁状态 | draft_word_export_available | final_word_export_available | deliverable | needs_manual_review |
|---|---:|---:|---:|---:|
| 无报告正文 | false | false | false | 由状态机决定，且必须有 `primary_failure_code` |
| 有正文且禁用表达命中数大于 0 | false | false | false | true |
| 正文清洁但存在其他质量阻断 | true | false | false | true |
| 正文清洁且全部门禁通过 | true | true | true | false |

不变量：

- `final_word_export_available => draft_word_export_available`。
- `word_export_available = draft_word_export_available OR final_word_export_available`。
- Word 先写入不可下载的临时文件，扫描通过后原子发布；扫描失败时删除临时文件，且不生成下载 URL。
- P0.2-b1.5 之后任何阶段回退都必须保留中央正文安全门禁；不得回退到门禁前的可发布状态。

## 4. 证据等级与 source_ref

### 4.1 证据等级

- **A级直接证据**：数据库公告正文、数据库原始字段、原始 PDF/DOC/DOCX 文本、表格真实单元格，以及带页码、表格、行列定位的原始内容。
- **B级确定性派生证据**：日期、地区、机构、金额、产品、数量、规则等标准化结果。每项必须回指 A 级证据，且不得扩张原始语义。
- **C级辅助信息**：摘要、附件摘要、生成指引、memory、memory_items、模型解释和历史偏好。C 级只能用于检索、结构、风格和提示。

禁止把模型摘要中的错误事实再次当成门禁证据。EvidenceIndex 和质量门禁只能接受 A/B。

### 4.2 source_ref 类型

| 字段 | 规则 |
|---|---|
| menu_code、articleid、attachment_id、filename | 字符串；没有附件时 attachment_id、filename 为 null |
| page_no | 1 基正整数；非分页来源为 null |
| sheet_name | 原始工作表名称；非工作簿来源为 null |
| table_index | 1 基正整数；非表格来源为 null |
| row、column | 1 基正整数；无法精确定位时为 null，不得猜测 |
| cell_range | 工作簿使用 A1 范围；PDF/图片表格使用 `R1C1:RnCm`；非单元格来源为 null |
| quote | 原文短片段，用于定位，不改变原字符 |
| source_hash | 小写 SHA-256 十六进制字符串 |

正文 hash：将 CRLF/CR 统一为 LF，只移除每行尾部空白，保留其他字符，按 UTF-8 计算 SHA-256。附件优先对原始 bytes 计算 SHA-256；没有原始 bytes 时，对 `TEXT_ONLY\n` 加规范化解析文本计算。附件按 `(attachment_id, filename, source_url)` 升序；材料聚合 hash 对包含正文 hash 和逐附件 hash 的 sorted-key canonical JSON 计算 SHA-256。

## 5. 固定真实数据集

### 5.1 固定 3-case

| # | menu_code | articleid | 准确标题 | 附件特征 | 选择原因 |
|---:|---|---|---|---|---|
| 1 | project_notice | 803492fa-839b-4fff-aa7c-e9c1d9a3028a | 2026年山东省关于切口保护器等3类医用耗材协议期满接续采购文件公告 | 4 个 DOCX/XLSX 附件 | 表格密集、规则复杂、已知重点案例 |
| 2 | project_notice | 4695e7d8-9d04-4c5a-b198-00ed022bf413 | 2025年新疆维吾尔自治区医疗保障局关于组织开展新疆医保影像云（医用耗材）集中带量采购的通告 | 0 附件 | 正文主导基线 |
| 3 | project_notice | 4ca0eae9-bb5f-4301-8d1a-ee4d85cc30b6 | 2026年国家组织高值医用耗材联合采购办公室关于发布《“国家组织冠脉支架集中带量采购”第二轮接续采购文件（采购文件编号：GH-HD2026-1）》的公告 | 4 个 PDF 附件 | 多附件、采购文件、PDF 表格链路 |

每个子阶段本地一次、线上一次，均使用这三个稳定 `menu_code + articleid`。不得用临时 `pack_id` 代替身份，也不得临时换样本。

### 5.2 固定 10-case

固定 10 为固定 3 加以下 7 例：

| menu_code | articleid | 准确标题 | 基线附件数 |
|---|---|---|---:|
| policy_interpretation | 859bbf46-77b3-4664-9606-e79101c084aa | 国家医保局关于医保支付方式改革有关情况介绍（第1期） | 0 |
| yb_drg | 718b4c38-34b3-4be8-9800-c0c4c24bdb08 | 山西省长治市2026年度一季度DRG付费特例单议评审结果公示 | 0 |
| ylsf | b10e3e1f-bea5-4d3b-ada6-124be038b8a9 | 广东省河源市医疗保障局关于公开征求麻醉类等医疗服务项目价格（征求意见稿）意见的公示 | 2 |
| project_information | 81a4fe2a-04ad-4591-9613-c717a9e688b0 | 广西壮族自治区医保局关于做好省际联盟输尿管支架类医用耗材集中带量采购和使用工作的通知 | 1 |
| project_analysis | 4a2e0dbc-1a24-490e-b807-38374f8cd530 | 【贵州】关于开展医用耗材阳光挂网采购申报工作的通知—项目分析 | 1 |
| lxzn | d6fae12f-20e2-4448-a194-c4cc3e8ece54 | 广西壮族自治区南宁市关于发布综合诊查类等16批医疗服务立项指南映射关系表的公告 | 16 |
| ylsf | 5340b5c6-6faf-4a7c-aa25-ac5445c95e8f | 贵州省医疗服务价格项目映射表 | 29 |

P0.2-b2.5 冻结标题、正文 hash、逐附件 hash、附件数、采集时间和 manifest 版本。身份缺失或 hash 改变时本轮失败；只有用户确认后才能创建新 manifest 版本，旧版本永久保留。

## 6. 统一命令与数值口径

以下命令块是每张 1-20 执行卡中引用命令的完整定义。执行卡仍须逐项写出使用哪个命令、阶段变量、相关接口和专项预期。

### 6.1 自动化与真实数据

```powershell
# T-FULL-WIN
py -3 -m unittest discover -s tests -p "test_*.py" -v

# R3-LOCAL；STAGE 在执行卡中给出
py -3 scripts/run_fixed_regression.py --base-url http://127.0.0.1:18099 --manifest tests/fixtures/8099_regression_cases.json --subset fixed3 --stage $env:STAGE --output "data/test-results/$env:STAGE-local.json"

# R3-ONLINE
py -3 scripts/run_fixed_regression.py --base-url http://192.168.34.88:8099 --manifest tests/fixtures/8099_regression_cases.json --subset fixed3 --stage $env:STAGE --output "data/test-results/$env:STAGE-online.json"

# WORD-SCAN；P0.2-b1.5 创建后使用
py -3 scripts/scan_formal_outputs.py --run-results "data/test-results/$env:STAGE-local.json"
py -3 scripts/scan_formal_outputs.py --run-results "data/test-results/$env:STAGE-online.json"
```

固定 3 本地和线上公共门槛：

- `/analysis/prepare`：3/3 HTTP 200。
- `/analysis/run`：3/3 在每例 1200 秒内进入终态。
- 报告正文非空：3/3。
- 正式 provider 为 Dify 时 `workflow_run_id` 非空：3/3。
- Word 真值表一致：3/3。
- Markdown、ReportIR、DOCX 段落和所有表格单元格禁用表达命中总数：0。
- 状态 schema 矛盾数：0。
- 每个 `deliverable=false` 的 run 都有非空 `primary_failure_code`。
- 非性能阶段同案例、同环境、同计时边界的总耗时不超过 `max(冻结基线 × 1.30, 冻结基线 + 30 秒)`。

定向测试和全量测试均要求 0 failure、0 error。P0.2-b1.4 冻结测试名、环境、skip 名单和 skip 数量 `S`；后续 skip 名单必须是该名单的子集，数量不得大于 `S`。

### 6.2 Git、推送、备份、部署、健康和回退

```powershell
# G-STATUS
git status --short
git diff --check
git diff --name-only

# G-PUSH；运行时凭据从进程环境读取，不写入命令、文件或日志
$env:STAGE_SHA = (git rev-parse HEAD).Trim()
@'
from dulwich import porcelain
porcelain.push('.', 'https://github.com/xllearn/8099.git',
               refspecs=[b'refs/heads/main:refs/heads/main'])
'@ | py -3 -
```

```bash
# B-1；在服务器执行，STAGE 为执行卡编号
cd /opt/medical-notice-analyzer
TS=$(date +%Y%m%d_%H%M%S)
tar --exclude='./deploy_backups' --exclude='./data' --exclude='./reports' --exclude='./site-cache' \
  -czf "deploy_backups/code_before_${STAGE}_${TS}.tgz" .
sha256sum "deploy_backups/code_before_${STAGE}_${TS}.tgz"

# D-1；本地先对已推送 SHA 执行 git archive，再上传到服务器 /tmp
git archive --format=tar.gz --output="8099-${STAGE_SHA}.tar.gz" "${STAGE_SHA}"

# D-2；服务器从 clean 制品同步，保留环境和运行数据
rm -rf "/tmp/8099-${STAGE_SHA}"
mkdir -p "/tmp/8099-${STAGE_SHA}"
tar -xzf "/tmp/8099-${STAGE_SHA}.tar.gz" -C "/tmp/8099-${STAGE_SHA}"
rsync -a --delete --exclude='.env' --exclude='data/' --exclude='reports/' \
  --exclude='site-cache/' --exclude='deploy_backups/' \
  "/tmp/8099-${STAGE_SHA}/" /opt/medical-notice-analyzer/
printf '%s\n' "${STAGE_SHA}" > /opt/medical-notice-analyzer/.deployed_git_sha
cd /opt/medical-notice-analyzer
docker compose up -d --build medical-notice-analyzer

# H-1
curl -fsS http://127.0.0.1:8099/health
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8099/records-ui
docker compose ps medical-notice-analyzer

# RB-1；BACKUP 为 B-1 生成的准确文件
cd /opt/medical-notice-analyzer
tar -xzf "deploy_backups/${BACKUP}" -C /opt/medical-notice-analyzer
docker compose up -d --build medical-notice-analyzer
curl -fsS http://127.0.0.1:8099/health
```

`D-1` 生成的本地压缩包和服务器 `/tmp` 解包目录必须在 SHA、上传 hash 和部署 hash 核对后删除，且不得进入 Git。远端 `refs/heads/main` 必须等于本地 `STAGE_SHA`，否则禁止执行 B-1 和 D-2。

## 7. 每阶段优化记录模板

每张执行卡第 20 项都必须更新 `C:\Users\admin\Desktop\8099项目优化记录.md`，写明：阶段编号和名称、开始/完成时间、目的、方法、修改文件、达到程度、验证方式、本地命令和结果；固定 3 的 menu_code、articleid、pack_id、run_id、workflow_run_id、compact_pack_chars、provider、状态、deliverable、needs_manual_review、primary_failure_code、Word 是否生成、draft/final 字段、禁用命中数、本地与线上分段耗时；Git commit、GitHub push、远端 SHA、服务器备份、部署结果、回退及原因。不得只写“完成阶段”。

---

## 8. P0：证据约束与可交付质量基础

### P0.2-b1.4：Word 发布安全熔断

1. **目的：** 建立独立的 fail-closed Word 发布总开关，为正文门禁首次上线提供安全回退点。
2. **问题和证据：** P0.2-b2 候选线上 Word 曾出现禁用表达；当前没有可在门禁代码失效时阻止全部 Word 发布的稳定回退点。
3. **范围：** 在所有 render、export、run Word 和 download 边界加入同一个发布熔断；冻结 unittest skip 基线。
4. **非范围：** 不扫描正文、不清理禁用表达、不做历史、VBP、compact、provider 或 UI 改造。
5. **文件：** 修改 `app/main.py`、`tests/test_report_export.py`、`.env.example`、`docker-compose.yml`、`README.md`；新增 `scripts/run_fixed_regression.py`、`tests/test_fixed_regression_runner.py`、`tests/fixtures/8099_regression_cases.json`、`tests/baselines/unittest-skips.json`。
6. **开关：** `ENABLE_WORD_EXPORT=false`，代码默认 false；只有 P0.2-b1.5 全部通过后生产才设 true。
7. **数据/API/状态：** 熔断时导出和下载返回 HTTP 503、错误码 `WORD_EXPORT_DISABLED`；不创建文件和 URL；draft/final/word 三字段均 false，报告生成状态不受影响。
8. **TDD：** 先增加 `test_word_export_disabled_blocks_every_export_boundary`、`test_disabled_export_creates_no_file_or_url` 和固定 manifest/runner 身份测试；运行 `py -3 -m unittest tests.test_report_export tests.test_fixed_regression_runner -v`，预期因未实现开关和 runner 而失败；最小实现只加统一边界 guard 与固定 3 runner；再运行定向测试和 `T-FULL-WIN`，并把实际 skip 测试全名和数量写入 baseline JSON。
9. **固定 3 本地：** `STAGE=P0.2-b1.4` 执行 R3-LOCAL；3/3 报告正文生成，所有 Word 端点 503，报告目录新增 Word 文件数为 0。
10. **Word 检查：** 本阶段预期没有可下载 Word；验证 draft=false、final=false、word=false 3/3，错误码均为 `WORD_EXPORT_DISABLED`。
11. **commit 前检查：** 执行 G-STATUS；变更文件必须严格等于第 5 项，`git diff --check` 无输出。
12. **commit/push：** commit message `Add fail-closed Word export switch`；执行 G-PUSH，验证远端 main SHA 等于本地 SHA。
13. **服务器备份：** `STAGE=P0.2-b1.4` 执行 B-1，记录备份路径、大小和 SHA-256。
14. **部署：** 对远端一致 SHA 执行 D-1、D-2；生产环境保持 `ENABLE_WORD_EXPORT=false`。
15. **健康检查：** 执行 H-1；另检查 `/analysis/prepare`、`/analysis/run` 正常，任一 Word 导出和 `/download/{filename}` 均 503。
16. **固定 3 线上：** 执行 R3-ONLINE；3/3 正文非空，Word 文件和 URL 均不存在。
17. **通过标准：** 自动化 0 failure/0 error；固定 3 本地、线上报告成功 3/3；Word 阻断 3/3；磁盘新增 Word 0；其他公共门槛全部通过。
18. **回退触发：** 任一 Word 可下载、任一导出文件残留、报告主流程失败、健康检查失败或范围外文件进入提交。
19. **回退方法：** 若该首个安全阶段自身失败，恢复 B-1 备份后执行 `docker compose stop medical-notice-analyzer`，验证 8099 无可访问 Word 端点；不得在无安全熔断时继续发布。记录停服状态并等待用户决定，不能继续 P0.2-b1.5。
20. **记录和暂停：** 按第 7 节更新优化记录，明确这是 fail-closed 维护状态或成功状态，然后暂停。

### P0.2-b1.5：FormalBody 与正文安全门禁

1. **目的：** 在重新实施历史功能前，保证正式 Markdown、ReportIR 和 Word 正文禁用表达命中数恒为 0。
2. **问题和证据：** 上次 b2 线上验证因 Word 出现禁用表达而回退；现有 `ForbiddenPhraseGate`、`ExportGate` 和 repairer 仍为空实现。
3. **范围：** 最小 FormalBody schema、统一遍历、Unicode 规范化匹配、保守清理、临时 DOCX 扫描、原子发布和固定回归 runner。
4. **非范围：** 不实现历史、VBP 事实、PDF 表格、compact、native、shadow；不修改 Dify prompt、model 或远端 workflow。
5. **文件：** 新增 `app/formal_body.py`、`app/formal_body_safety.py`、`scripts/scan_formal_outputs.py`、`tests/test_formal_body_safety.py`；修改 `scripts/run_fixed_regression.py`、`tests/test_fixed_regression_runner.py`、`app/quality_gate.py`、`app/repair_pipeline.py`、`app/main.py`、`tests/test_report_export.py`、`tests/test_generation_pipeline.py`、`.env.example`、`docker-compose.yml`、`README.md`。
6. **开关：** 无正文门禁 bypass 开关；`ENABLE_WORD_EXPORT` 只有在本阶段本地全通过后才从 false 改为 true。生产正文门禁始终 fail-closed。
7. **数据/API/状态：** `FormalBodyDocument` 明确遍历 Markdown、当前 ReportIR 的 title/summary/section heading/paragraph/highlight/table header/table cell，以及 DOCX 正文、跨 run 文本、嵌套表格、页眉页脚；新增 `forbidden_phrase_hits`、`body_safety_passed`、`FORBIDDEN_PHRASE_IN_FORMAL_BODY` 和第 3 节真值表。
8. **TDD：** 先写 13 个表达 × Markdown/ReportIR/DOCX 段落/DOCX 表格、NFKC 匹配、跨 DOCX run、嵌套表格、临时文件清理、四种 Word 真值测试；运行 `py -3 -m unittest tests.test_formal_body_safety tests.test_report_export tests.test_generation_pipeline tests.test_fixed_regression_runner -v`，预期缺少模块和安全行为而失败；最小实现仅做 NFKC 匹配、整句删除/证据收窄、重扫和原子发布；再运行定向与 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P0.2-b1.5` 执行 R3-LOCAL；启用 Word 发布后按真值表导出。
10. **Word 检查：** 执行 WORD-SCAN；临时 DOCX 写入隔离目录，扫描通过后 `os.replace` 发布；失败删除临时文件并保持 URL 不可见；三种正式载体命中总数 0。
11. **commit 前检查：** 执行 G-STATUS；只允许第 5 项文件，skip 名单不得超出 P0.2-b1.4 baseline。
12. **commit/push：** commit message `Enforce formal body safety before Word publish`；G-PUSH 后核对远端 SHA。
13. **服务器备份：** `STAGE=P0.2-b1.5` 执行 B-1；上一安全 SHA 必须是 b1.4。
14. **部署：** 执行 D-1、D-2；先保持 Word false 做健康检查，再设 true 重建容器并验证中央门禁。
15. **健康检查：** H-1；检查 report render/export/download、run report 和 Word 下载接口；不存在临时下载 URL。
16. **固定 3 线上：** `STAGE=P0.2-b1.5` 执行 R3-ONLINE 和 WORD-SCAN。
17. **通过标准：** 52 个载体组合命中测试 100%；清洁样本误报 0；固定 3 正式载体命中 0；Word 真值 3/3；临时文件残留 0；公共门槛全通过。
18. **回退触发：** 任一禁用表达进入正式载体、误报导致清洁报告不可导出、临时文件可下载、状态真值矛盾、自动化或固定 3 失败。
19. **回退方法：** 恢复 b1.4 备份和 SHA，强制 `ENABLE_WORD_EXPORT=false`；H-1 验证主服务，固定 3 验证正文仍可生成且全部 Word 端点 503。不得回退到可发布但无门禁的版本。
20. **记录和暂停：** 更新优化记录，写明 52 项扫描、固定 3、Word 原子发布和回退安全点，暂停。

### P0.2-b2：分析历史记录

1. **目的：** 为每次数据库材料分析建立可追溯、可重建、写失败不阻断主流程的轻量历史。
2. **问题和证据：** 回退后线上无 history API；run JSON 分散，不能按材料对比 provider、workflow、耗时、质量和 Word 状态。
3. **范围：** JSONL 事件、原子 index、查询 API、CLI 重建、单 worker 拓扑门禁、轮转规则。
4. **非范围：** 不迁数据库、不改报告正文、Dify prompt、compact、质量策略、UI、native 或 shadow。
5. **文件：** 新增 `app/analysis_history.py`、`tests/test_analysis_history.py`；修改 `app/main.py`、`tests/test_records_api.py`、`.env.example`、`docker-compose.yml`、`README.md`。
6. **开关：** 使用 `ENABLE_ANALYSIS_HISTORY=true`；新增 `ENABLE_ANALYSIS_HISTORY_REBUILD_API=false`；生产必须 `worker_count=1`，否则 history 自动关闭并在 health 返回 `history_topology_compatible=false`。
7. **数据/API/状态：** 事件类型固定为 run_created、run_updated、word_exported；index 至少记录 record_id、notice_id、menu_code、articleid、pack_id、run_id、分析开始/结束/耗时、provider、workflow_run_id、compact_pack_chars、质量状态/门禁/失败码、Word 文件与下载状态、draft/final/deliverable/manual 四字段。`events.jsonl` 追加并 flush/fsync；锁内从 index 和重放事件取最大 revision 后加 1；`event_id=run_id:event_type:revision`；重复 ID 跳过；index 临时写、文件 fsync、`os.replace`、目录 fsync；末尾坏行先归档再截到最后完整换行，内部坏行触发 run JSON 重建。达到 52428800 bytes 或累计 100000 事件时，先生成只读 snapshot 和 `sqlite_evaluation_required=true` 信号，再原子轮转，保留 12 份校验和归档。GET `/analysis/history` 支持 menu_code+articleid、record_id、notice_id 过滤，默认 20、最大 100、cursor 分页；GET `/analysis/history/{run_id}`；重建默认仅 `python -m app.analysis_history rebuild`。
8. **TDD：** 先写创建/更新/导出幂等、锁内 revision、32 线程、event fsync 后崩溃、index 替换前崩溃、坏尾行、内部坏行、分页、写失败非阻断、worker 拓扑测试；运行 `py -3 -m unittest tests.test_analysis_history tests.test_records_api -v`，预期模块/API 不存在而失败；最小实现只加历史模块和主流程 try/except hook；再运行定向与 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P0.2-b2` 执行 R3-LOCAL；每例至少产生 run_created、run_updated、word_exported 或明确未导出终态事件。
10. **Word 检查：** 执行 WORD-SCAN；历史字段和 diagnostics 不得进入 Word；word_exported 只在原子发布成功后写入。
11. **commit 前检查：** G-STATUS；只允许第 5 项文件，历史运行目录、JSONL、index 和测试输出不得提交。
12. **commit/push：** commit message `Add resilient analysis history index`；G-PUSH 并核对远端 SHA。
13. **服务器备份：** `STAGE=P0.2-b2` 执行 B-1；另记录现有 history 目录不存在或其准确归档 hash。
14. **部署：** D-1、D-2；验证容器只有 1 worker 后启用 history。
15. **健康检查：** H-1；GET history、GET run、limit=101 拒绝、默认关闭的 rebuild POST 拒绝；主分析接口正常。
16. **固定 3 线上：** 执行 R3-ONLINE、WORD-SCAN；查询每例 identity、时间、provider、workflow、compact、质量、Word 四字段。
17. **通过标准：** 固定 3 字段完整率 100%；event_id 重复 0；32 线程丢事件 0；注入历史写失败时报告 3/3 完成；index 可从 run JSON 重建；公共门槛全通过。
18. **回退触发：** 主流程受历史异常影响、事件丢失/重复、index 原子性失败、worker 不兼容仍写入、查询泄露越权字段、Word 回归。
19. **回退方法：** 关闭 history，校验并归档 history 目录，恢复 b1.5 备份/SHA；H-1、固定 3、WORD-SCAN；不得把 index 内容回灌 run JSON。
20. **记录和暂停：** 记录事件数、revision、重建、写失败注入、固定 3 历史字段和回退决定，暂停。

### P0.2-b2.5：回归基线冻结

1. **目的：** 冻结后续所有“改善、下降、不低于基线”所引用的可重放质量与性能基线。
2. **问题和证据：** 旧审计样本与新固定 10 不同，旧 unsupported 总数不能直接作为门槛；单次耗时不能计算 p50/p95。
3. **范围：** manifest、独立离线 evaluator、可重放正文/证据快照、质量指标、固定 3 重复性能基线。
4. **非范围：** 不改变在线生成、报告、compact、provider、质量门禁或 Word 策略。
5. **文件：** 新增 `app/offline_quality_evaluator.py`、`scripts/freeze_regression_baseline.py`、`scripts/evaluate_regression_baseline.py`、`tests/test_regression_manifest.py`、`tests/test_offline_quality_evaluator.py`、`docs/quality-baselines/p0.2-b2.5.json`；修改 `tests/fixtures/8099_regression_cases.json`、`scripts/run_fixed_regression.py`、`.gitignore`。
6. **开关：** 无新增运行开关；evaluator 固定 `unsupported_eval_version=1` 和 rules SHA-256。
7. **数据/API/状态：** 可重放规范化报告与 A/B 证据快照写入受控运行目录 `data/quality_baselines/p0.2-b2.5/`，不进入 Git；提交文件只含 identity、原材料/附件特征、evidence_pack_hash、compact_pack_chars、input_strategy、workflow_run_id、provider、报告状态、deliverable、needs_manual_review、failure codes、unsupported_eval_v1、Word 三可用字段、禁用命中、报告字符数/章节数、prepare/generation/quality/export/total 分环境耗时、hash、聚合指标和版本。每次候选与基线都用同一 evaluator 对快照离线重算。
8. **TDD：** 先写 manifest 缺 identity/title/hash/time、hash 排序不稳定、evaluator 版本/rules hash 缺失、快照不能重放、p95 nearest-rank 测试；运行 `py -3 -m unittest tests.test_regression_manifest tests.test_offline_quality_evaluator -v`，预期文件和 evaluator 不存在而失败；最小实现只建立稳定 schema、hash 和只读 evaluator；再运行定向与 T-FULL-WIN。
9. **固定数据本地：** 固定 10 各运行 1 次；固定 3 每例再运行 2 次，使本地固定 3 每例共 3 次、共 9 次性能样本。
10. **Word 检查：** 固定 10 全部执行 WORD-SCAN；不提交 DOCX，只存规范化正文/表格文本快照、hash 和 Word 状态。
11. **commit 前检查：** G-STATUS；只允许第 5 项文件；真实快照、Word、run JSON、凭据和测试输出不得提交。
12. **commit/push：** commit message `Freeze replayable 8099 regression baseline`；G-PUSH 并核对远端 SHA。
13. **服务器备份：** `STAGE=P0.2-b2.5` 执行 B-1；基线运行目录另做带 SHA-256 的数据归档。
14. **部署：** D-1、D-2；本阶段代码只增加 evaluator/runner，不改变正式生成路径。
15. **健康检查：** H-1；运行 evaluator `--verify-only`，验证 manifest、rules hash、快照索引和环境计时边界。
16. **固定数据线上：** 固定 10 各运行 1 次；固定 3 每例再运行 2 次，线上也为 9 次；本阶段性能总样本为本地 9 + 线上 9 = 18。
17. **通过标准：** 固定 10 identity/hash/指标完整 10/10；快照可重放 10/10；禁用 0；非 deliverable 均有主失败码；每环境固定 3 性能样本 9/9；离线 evaluator 两次重算结果完全相同。新固定 10 unsupported 总数定义为 `B`，旧数值不参与。
18. **回退触发：** source hash 漂移、快照不可重放、evaluator 非确定、字段缺失、18 个性能样本不完整或 Word 命中。
19. **回退方法：** 恢复 b2 代码和 manifest；将失败快照目录校验归档后移出活动基线；H-1、固定 3、WORD-SCAN；不得覆盖上一有效 baseline 版本。
20. **记录和暂停：** 记录 B、evaluator/rules 版本、18 次性能值、固定 10 指标和快照归档位置，暂停。

### P0.2-b3：山东案例分层诊断与 Dify 归因

1. **目的：** 确定山东案例问题属于解析、compact、Dify、清理还是门禁层，并让 Dify 异常均有唯一主失败码。
2. **问题和证据：** 当前 diagnostics 能展示部分指标，但不能稳定区分输入损失、workflow 异常、输出空/截断/schema 错误和清理损失。
3. **范围：** 只读诊断工件、有限失败枚举、生成前后 hash 和层级归因。
4. **非范围：** 不改变报告正文、Dify prompt/model/workflow、compact、VBP 事实或质量策略。
5. **文件：** 新增 `scripts/diagnose_regression_case.py`、`tests/test_diagnostics.py`、`tests/test_dify_attribution.py`；修改 `app/diagnostics.py`、`app/generation/dify_generator.py`、`app/main.py`、`tests/test_generation_pipeline.py`、`.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_VBP_DIAGNOSTICS=true`、`ENABLE_DIFY_FAILURE_ATTRIBUTION=true`；关闭只隐藏新增诊断，不改变生成。
7. **数据/API/状态：** primary_layer 只能是 attachment_parse、compact、provider、cleanup、quality_gate；Dify 主码只能是 HTTP_ERROR、WORKFLOW_ID_MISSING、WORKFLOW_FAILED、OUTPUT_EMPTY、OUTPUT_TRUNCATED、OUTPUT_SCHEMA_INVALID、TIMEOUT；诊断只写 run JSON/API/日志。
8. **TDD：** 对每层和每个 Dify 码注入确定故障；运行 `py -3 -m unittest tests.test_diagnostics tests.test_dify_attribution tests.test_generation_pipeline -v`，预期当前无法唯一分类而失败；最小实现只采集元数据和枚举，不修报告；再运行定向与 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P0.2-b3` 执行 R3-LOCAL；山东 case 额外执行 diagnose 脚本并导出解析、compact、provider、cleanup、gate 工件。
10. **Word 检查：** WORD-SCAN；诊断码、层名和原始 provider 错误不得进入 Word。
11. **commit 前检查：** G-STATUS；只允许第 5 项，诊断运行工件不得提交。
12. **commit/push：** commit message `Add layered VBP and Dify diagnostics`；G-PUSH 后核对 SHA。
13. **服务器备份：** `STAGE=P0.2-b3` 执行 B-1。
14. **部署：** D-1、D-2；保持正式 Dify 配置不变。
15. **健康检查：** H-1；检查 run diagnostics API 和故障码 schema，确认日志不含密钥或完整请求正文。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；山东 case 导出同结构诊断工件。
17. **通过标准：** 注入分类准确率 100%；每个失败只有 1 个 primary；山东工件字段完整率 100%；固定 3 Word 诊断泄漏 0；公共门槛全通过。
18. **回退触发：** 生成行为改变、失败误分类、日志敏感信息、诊断进入 Word、性能越过公共门槛。
19. **回退方法：** 关闭两个诊断开关，归档工件，恢复 b2.5 备份/SHA，执行 H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 写明山东唯一主层、Dify 归因结果、工件 hash 和验证数据，暂停。

### P0.2-b4：Canonical EvidenceItem 与 source_ref

1. **目的：** 在 PDF 表格和 VBP 派生事实之前建立唯一 A/B/C schema 与可回溯引用。
2. **问题和证据：** 当前 key_facts、table_summaries 和摘要引用结构不统一，无法可靠阻止 C 级自证。
3. **范围：** EvidenceItem v2、source_ref 校验、v1 只读兼容和 pack 版本。
4. **非范围：** 不新增 PDF/OCR 能力、不抽取 VBP 事实、不改报告或 provider。
5. **文件：** 新增 `app/evidence_schema.py`、`tests/test_evidence_schema.py`；修改 `app/main.py`、`app/attachment_parser.py`、`tests/test_records_api.py`、`.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_EVIDENCE_SOURCE_REFS=false`，代码默认 false；本地通过后生产设 true。
7. **数据/API/状态：** `evidence_schema_version=2`；A/B/C 和第 4 节 source_ref；旧 v1 pack 可读但不就地覆盖。
8. **TDD：** 写 A/B 缺 ref、B 回指 C、伪造页码、非法 0 基索引、cell_range、v1 兼容测试；运行 `py -3 -m unittest tests.test_evidence_schema tests.test_records_api -v`，预期 schema 不存在而失败；最小实现只加模型、验证和 adapter；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P0.2-b4` 执行 R3-LOCAL；导出每例 A/B/C 数量和 ref 校验结果。
10. **Word 检查：** WORD-SCAN；source_ref 只作内部溯源，不把诊断 JSON 写入正文。
11. **commit 前检查：** G-STATUS；只允许第 5 项，v2 pack 运行文件不提交。
12. **commit/push：** commit message `Add canonical evidence source references`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P0.2-b4` 执行 B-1。
14. **部署：** D-1、D-2；先 false 验证兼容，再 true 生成新 v2 pack。
15. **健康检查：** H-1；检查 prepare、pack detail、run 对 v1/v2 均可用。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；验证 A/B 引用。
17. **通过标准：** 固定 3 A/B ref 合法率 100%；B 回指 A 100%；C 独立支持率 0；v1 fixture 读取 100%；公共门槛通过。
18. **回退触发：** v1 不兼容、ref 伪造/丢失、C 可支持事实、pack 或报告回归。
19. **回退方法：** 关开关、归档 v2 pack、恢复 b3 备份/SHA；H-1、R3-ONLINE、WORD-SCAN；不改旧 pack。
20. **记录和暂停：** 记录 schema 版本、各级数量、ref 合法率、兼容和回退结果，暂停。

### P0.2-b5：PDF 与图片表格 A 级提取

1. **目的：** 为 PDF/扫描图表格提供页、表、行列和单元格级 A 级证据。
2. **问题和证据：** 当前 PDF 主要输出连续文本，第三个固定 case 的 4 个 PDF 缺稳定表格定位。
3. **范围：** 结构化 PDF 表格、低文本页 OCR 候选、确定性 fixture 和定位 ref。
4. **非范围：** 不抽取 VBP B 级事实、不改 compact、报告、Dify 或质量门禁。
5. **文件：** 新增 `app/pdf_table_parser.py`、`tests/test_pdf_table_parser.py`、`tests/fixtures/pdf_tables/`；修改 `app/attachment_parser.py`、`requirements.txt`、`Dockerfile`、`.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_STRUCTURED_PDF_TABLES=false`、`ENABLE_IMAGE_TABLE_OCR=false`；现有 PDF OCR 默认值不变。
7. **数据/API/状态：** 表格单元格输出 page_no、table_index、row、column、cell_range、quote、source_hash；页可提取文本少于 200 字符时才进入图片 OCR 候选。
8. **TDD：** 先写合并单元格、跨页、扫描图、空页、数字精度和 ref 测试；运行 `py -3 -m unittest tests.test_pdf_table_parser -v`，预期 parser 不存在而失败；最小实现先支持文本 PDF，OCR 受独立开关控制；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P0.2-b5` 执行 R3-LOCAL；第三例 4 个 PDF 全部记录逐附件解析状态。
10. **Word 检查：** WORD-SCAN；解析警告只在 diagnostics，不能进入 Word。
11. **commit 前检查：** G-STATUS；只允许第 5 项；fixture 必须是确定性小文件且无真实凭据。
12. **commit/push：** commit message `Extract traceable PDF and image tables`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P0.2-b5` 执行 B-1，并记录旧镜像 ID。
14. **部署：** D-1、D-2；先启用 structured PDF，OCR 开关在 fixture 和真实 case 均通过后启用。
15. **健康检查：** H-1；检查 attachment parse、prepare、pack detail 和容器依赖导入。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；记录 4 个 PDF 的表格和 ref 数量。
17. **通过标准：** fixture 文本单元格召回率至少 95%，数字单元格 100%，定位字段 100%；第三例 4/4 有可追踪解析状态；其他两例无回归；公共门槛通过。
18. **回退触发：** 数字变形、定位错误、OCR 污染文本、容器依赖失败、耗时或 Word 回归。
19. **回退方法：** 双开关 false，恢复旧镜像和 b4 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录 fixture 指标、4 个 PDF 状态、OCR 触发页数、耗时和回退决定，暂停。

### P0.2-b6：VBP 确定性事实抽取

1. **目的：** 从 A 级证据生成可回溯、语义不扩张的 VBP B 级事实。
2. **问题和证据：** 当前规则文件可校验，但尚无统一 VBP 事实模型和 A→B 引用。
3. **范围：** 日期、地区、机构、产品、价格、数量和采购规则的确定性抽取与冲突诊断。
4. **非范围：** 不生成缺失事实、不使用 C 级来源、不改报告结构、compact 或 provider。
5. **文件：** 新增 `app/vbp_facts.py`、`tests/test_vbp_facts.py`；修改 `app/report_rules/vbp_topic_rules.yml`、`app/report_rules/schema.py`、`app/main.py`、`.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_VBP_FACT_EXTRACTION=false`，代码默认 false；验收部署设 true。
7. **数据/API/状态：** 每个 B 级事实包含 value、normalized_value、fact_type、source_ref、extractor_version；冲突进入 diagnostics，不能进入正式事实集。
8. **TDD：** 写有效 A→B、缺 ref、B 回指 C、冲突值、重复值和边界数字测试；运行 `py -3 -m unittest tests.test_vbp_facts tests.test_vbp_topic_rules -v`，预期模块/字段不存在而失败；最小实现只做规则确定的事实类型；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P0.2-b6` 执行 R3-LOCAL；导出事实数、冲突数和 ref 支持率。
10. **Word 检查：** WORD-SCAN；本阶段不强制报告使用新事实，但任何已使用事实必须有 A/B 支持。
11. **commit 前检查：** G-STATUS；只允许第 5 项，真实 pack 不提交。
12. **commit/push：** commit message `Extract evidence-linked VBP facts`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P0.2-b6` 执行 B-1。
14. **部署：** D-1、D-2；先 false 做兼容检查，再 true。
15. **健康检查：** H-1；检查 prepare pack 中 vbp_facts schema 和 diagnostics 冲突码。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；记录每例事实/ref/冲突。
17. **通过标准：** 输出事实 A/B 支持率 100%；C 来源事实 0；冲突事实进入正式集 0；固定 3 和公共门槛通过。
18. **回退触发：** 任一无 ref 事实、C 自证、冲突事实泄漏、报告或耗时回归。
19. **回退方法：** 关开关，恢复 b5 备份/SHA；H-1、R3-ONLINE、WORD-SCAN，旧 pack 字段继续可读。
20. **记录和暂停：** 记录事实类型、支持率、冲突、固定 3 和回退结果，暂停。

### P0.2-b7：VBP compact 保真

1. **目的：** 在 Dify 80000 字符限制内最大化保留证据，并保持未超限包完全不变。
2. **问题和证据：** 大包曾从 252822 压到 46867，证据损失过大；84597 压到约 78326 的效果合理。
3. **范围：** 二次压缩分级、mandatory 字段保留、确定性输出和显式失败。
4. **非范围：** 不改第一阶段策略、不改 Dify prompt/model/workflow、不改报告和门禁。
5. **文件：** 新增 `app/compact_pack.py`、`tests/test_compact_pack.py`；修改 `app/main.py`、`app/diagnostics.py`、`.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_VBP_COMPACT_PRESERVATION=false`，代码默认 false。
7. **数据/API/状态：** 第一阶段结果不超过 80000 时内容 hash 完全相同且 `secondary_compression=false`；80000-90000 目标 78000-79500；90000-150000 目标 70000-79000；大于 150000 目标 75000-79000。所有 key_facts、表头和 source_ref 保留 100%；每附件保留至少 160 字核心摘要和 ref；mandatory 自身大于 79000 时失败码 `COMPACT_MANDATORY_FIELDS_OVER_LIMIT`。
8. **TDD：** 写 79999、80000、80001、84597、90000、90001、150000、150001、252822、mandatory 超限和重复运行 hash 测试；运行 `py -3 -m unittest tests.test_compact_pack tests.test_records_api -v`，预期新模块/分级不存在而失败；最小实现只在大于 80000 时分级裁剪；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P0.2-b7` 执行 R3-LOCAL；记录 full、first compact、final chars、策略和 retention。
10. **Word 检查：** WORD-SCAN；逐例 unsupported 不得高于 b2.5 同 evaluator 基线。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Preserve VBP evidence under Dify input limit`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P0.2-b7` 执行 B-1。
14. **部署：** D-1、D-2；先 false 记录原结果，再 true 生成配对结果。
15. **健康检查：** H-1；检查 prepare diagnostics 的分级、字符数和 mandatory 指标。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；记录 case 4/5 对应历史测试包的字符目标回归。
17. **通过标准：** 所有边界 fixture 目标区间 100%；不超限内容 hash 不变 100%；mandatory retention 100%；最终字符不超过 80000；固定 3 公共门槛通过。
18. **回退触发：** 不超限包改变、目标区间失败、mandatory 丢失、Dify 拒绝、unsupported 或耗时越限。
19. **回退方法：** 关开关，恢复 b6 备份/SHA；H-1、R3-ONLINE、WORD-SCAN；保留失败配对指标归档。
20. **记录和暂停：** 记录各分级输入输出、retention、fixed 3、case 4/5 和回退决定，暂停。

### P0.2-b8：VBP 报告结构规则

1. **目的：** 让 VBP 报告覆盖规则文件确定的适用主题，同时保持事实只来自 A/B。
2. **问题和证据：** 当前规则文件主要用于校验，尚未形成版本化、provider 兼容的结构指引。
3. **范围：** 结构 guidance、规则适用性、prompt_version 记录和 provider-neutral 主题约束。
4. **非范围：** 不改远端 Dify DSL、model、native、compact 或事实抽取算法。
5. **文件：** 修改 `app/report_rules/vbp_topic_rules.yml`、`app/report_rules/schema.py`、`app/generation/dify_generator.py`、`docs/dify_prompt_stage4.md`、`tests/test_vbp_topic_rules.py`、`tests/test_generation_pipeline.py`、`.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_VBP_REPORT_RULES=false`，代码默认 false。
7. **数据/API/状态：** run 记录 rule_version、rule_hash、prompt_version、applicable_topics；guidance 只引用 A/B facts 和结构名称。
8. **TDD：** 写适用主题缺章节、非适用主题误要求、C 来源 guidance、版本缺失测试；运行 `py -3 -m unittest tests.test_vbp_topic_rules tests.test_generation_pipeline -v`，预期当前不生成版本化 guidance 而失败；最小实现只组装结构要求；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P0.2-b8` 执行 R3-LOCAL；记录 applicable 和 covered topics。
10. **Word 检查：** WORD-SCAN；新增 unsupported_eval_v1 逐例增量必须为 0。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Apply evidence-bound VBP report structure`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P0.2-b8` 执行 B-1。
14. **部署：** D-1、D-2；先 false 生成基准，再 true 生成配对。
15. **健康检查：** H-1；检查 run version 字段和 Dify workflow ID。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；离线 evaluator 重算配对快照。
17. **通过标准：** 固定 3 适用主题覆盖率 100%；新增 unsupported 0；禁用 0；版本字段完整 100%；公共门槛通过。
18. **回退触发：** 主题误判、C 事实进入 guidance、unsupported 增加、Dify/Word/耗时回归。
19. **回退方法：** 关开关，恢复 b7 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录主题覆盖、版本、unsupported 配对和回退决定，暂停。

### P0.2-b9：VBP 专项质量门禁

1. **目的：** 对 VBP 主题完整性、事实 ref 和 C 级自证实施明确阻断。
2. **问题和证据：** 通用 LocalEvidenceGate 不能表达 VBP 专项规则。
3. **范围：** VBP gate、有限失败码、规则适用性和 Word 状态联动。
4. **非范围：** 不复制 FormalBodySafetyGate、不改生成、compact、provider 或 repair。
5. **文件：** 新增 `app/vbp_quality_gate.py`、`tests/test_vbp_quality_gate.py`；修改 `app/quality_gate.py`、`app/main.py`、`app/report_rules/vbp_topic_rules.yml`、`.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_VBP_QUALITY_GATE=false`，代码默认 false。
7. **数据/API/状态：** 失败码限定为 REQUIRED_TOPIC_MISSING、FACT_SOURCE_REF_INVALID、C_LEVEL_FACT_USED、VBP_RULE_CONFLICT；结果进入 diagnostics/run JSON，不进入 Word。
8. **TDD：** 写四类失败、完整报告、非 VBP bypass 和 FormalBody 顺序测试；运行 `py -3 -m unittest tests.test_vbp_quality_gate tests.test_generation_pipeline -v`，预期 gate 不存在而失败；最小实现只判定，不修改正文；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P0.2-b9` 执行 R3-LOCAL；记录每例适用规则和 gate 结果。
10. **Word 检查：** WORD-SCAN；按第 3 节验证 draft/final/deliverable/manual 四字段。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Gate VBP reports on evidence and topic rules`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P0.2-b9` 执行 B-1。
14. **部署：** D-1、D-2；先 false、后 true 做配对。
15. **健康检查：** H-1；检查 run report/diagnostics 和 Word 端点状态。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN。
17. **通过标准：** 四类注入分类 100%；完整 fixture 通过 100%；固定 3 规则判定 3/3、Word 真值 3/3、公共门槛通过。
18. **回退触发：** 正确报告误阻断、失败报告漏过、状态矛盾、诊断泄漏、Word 或耗时回归。
19. **回退方法：** 关 VBP gate，保持 FormalBodySafetyGate，恢复 b8 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录 gate 码、适用规则、Word 状态和回退结果，暂停。

### P0.3：EvidenceIndex 与 A/B 事实匹配

1. **目的：** 为每个报告 claim 提供只基于 A/B 的确定性支持判定和 source_ref。
2. **问题和证据：** 当前 unsupported 检查分散，尚无统一索引和版本化匹配结果。
3. **范围：** EvidenceIndex、claim 切分、数字/日期/机构/单元格匹配和算法版本。
4. **非范围：** 不用 embeddings、不索引 C 级、不自动 repair、不改 provider。
5. **文件：** 新增 `app/evidence_index.py`、`tests/test_evidence_index.py`；修改 `app/quality_gate.py`、`app/main.py`、`.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_EVIDENCE_INDEX=false`，代码默认 false。
7. **数据/API/状态：** index 只含 A/B；match 记录 claim_hash、supported、source_refs、matcher_version；C 命中不计支持。
8. **TDD：** 写数字、日期、机构、表格、近似但扩张、冲突和 C 级测试；运行 `py -3 -m unittest tests.test_evidence_index -v`，预期模块不存在而失败；最小实现使用规范化精确/上下文匹配；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P0.3` 执行 R3-LOCAL；导出 claim/match/ref。
10. **Word 检查：** WORD-SCAN；match diagnostics 不进入 Word；用 b2.5 evaluator 同时重算以保持基线可比。
11. **commit 前检查：** G-STATUS；只允许第 5 项，index 运行文件不提交。
12. **commit/push：** commit message `Index A and B evidence for claim matching`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P0.3` 执行 B-1。
14. **部署：** D-1、D-2；先 shadow 计算不阻断，再启用 gate 消费前等待下一阶段。
15. **健康检查：** H-1；检查 diagnostics match schema 和 matcher version。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；抽查每个 supported claim 可回溯。
17. **通过标准：** fixture 支持/不支持分类 100%；C 支持率 0；固定 3 supported ref 合法率 100%；公共门槛通过。
18. **回退触发：** C 被支持、数字误配、ref 丢失、性能/报告/Word 回归。
19. **回退方法：** 关开关，恢复 b9 备份/SHA；H-1、R3-ONLINE、WORD-SCAN，归档 index 工件。
20. **记录和暂停：** 记录 matcher 版本、claim 数、支持率、误配测试和回退结果，暂停。

### P0.4：Unsupported facts 自动修复

1. **目的：** 对 unsupported claim 只执行删除、收窄或 A/B 证据改写，并保留审计轨迹。
2. **问题和证据：** 当前 `UnsupportedFactRepairer` 为空实现，unsupported 只能阻断，不能安全收敛。
3. **范围：** 单次 repair、操作审计、前后 hash 和 unresolved manual review。
4. **非范围：** 不让模型自由补事实、不二次循环 repair、不使用 C 级或 memory。
5. **文件：** 修改 `app/repair_pipeline.py`、`app/evidence_index.py`、`app/main.py`、`tests/test_generation_pipeline.py`；新增 `tests/test_unsupported_fact_repair.py`；修改 `.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_UNSUPPORTED_FACT_REPAIR=false`，每 run 最大尝试次数固定为 1。
7. **数据/API/状态：** repair action 只能 DELETE_SENTENCE、NARROW_TO_EVIDENCE、REWRITE_FROM_EVIDENCE；记录 before/after hash、source_refs、success；未解决时 manual=true、final=false。
8. **TDD：** 写三种允许操作、模型新增事实、C 来源、第二次尝试和无安全改写测试；运行 `py -3 -m unittest tests.test_unsupported_fact_repair tests.test_generation_pipeline -v`，预期 repairer 无行为而失败；最小实现先确定性删除/收窄；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P0.4` 执行 R3-LOCAL；保存 repair 前后快照并用 evaluator v1 重算。
10. **Word 检查：** WORD-SCAN；修复后所有保留 claim A/B 支持率 100%，诊断不入 Word。
11. **commit 前检查：** G-STATUS；只允许第 5 项，快照不提交。
12. **commit/push：** commit message `Repair unsupported claims from evidence only`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P0.4` 执行 B-1。
14. **部署：** D-1、D-2；false 生成基准，true 生成配对。
15. **健康检查：** H-1；检查 repair_attempted/success/action/hash 字段。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN、evaluator 重算。
17. **通过标准：** 修后保留 claim 支持率 100%；新增事实 0；每例 unsupported_eval_v1 不高于 b2.5；单 run repair 次数不超过 1；公共门槛通过。
18. **回退触发：** 新事实、C 自证、循环 repair、正文破坏、Word 或耗时回归。
19. **回退方法：** 关开关，恢复 P0.3 备份/SHA；H-1、R3-ONLINE、WORD-SCAN；保留审计快照归档。
20. **记录和暂停：** 记录每种 action、前后 unsupported、Word 状态和回退决定，暂停。

### P0.5：通用质量与导出门禁收口

1. **目的：** 将 EvidenceIndex、VBP gate、唯一正文安全门禁和 ExportGate 组合成明确顺序。
2. **问题和证据：** 抽象已存在但部分组件为空，状态字段可能被不同路径独立计算。
3. **范围：** 单一 orchestrator、状态真值、失败码优先级和所有导出路径统一调用。
4. **非范围：** 不重写 FormalBodySafetyGate、不改生成/provider/compact、不增加 repair 次数。
5. **文件：** 修改 `app/quality_gate.py`、`app/formal_body_safety.py`、`app/main.py`、`app/diagnostics.py`、`tests/test_generation_pipeline.py`、`tests/test_report_export.py`；新增 `tests/test_quality_gate_orchestration.py`；修改 `.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_STRICT_DELIVERY_GATE=false`；正文安全门禁无开关并始终执行。
7. **数据/API/状态：** 顺序固定为 EvidenceIndex → VBP gate → repair 一次 → FormalBodySafetyGate → ExportGate；主失败码按第一个阻断组件，次码保留其余；第 3 节真值集中计算。
8. **TDD：** 写顺序、短路、四真值、矛盾状态、每个导出路径和中央门禁只调用一次测试；运行 `py -3 -m unittest tests.test_quality_gate_orchestration tests.test_generation_pipeline tests.test_report_export -v`，预期 orchestrator 不完整而失败；最小实现只做组合和状态集中；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P0.5` 执行 R3-LOCAL，保存各 gate 结果。
10. **Word 检查：** WORD-SCAN；Word 真值 3/3，禁用 0，diagnostics 泄漏 0。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Unify quality and export gate orchestration`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P0.5` 执行 B-1。
14. **部署：** D-1、D-2；false 做兼容，true 做正式配对；正文安全始终开启。
15. **健康检查：** H-1；检查 run、report、revise、render、export、download 全路径。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN、evaluator v1 重算。
17. **通过标准：** 组合测试 100%；状态矛盾 0；中央门禁重复调用 0；固定 3 Word 契约 3/3；公共门槛通过。
18. **回退触发：** 状态矛盾、路径绕过、正文门禁未执行、误阻断、Word/性能回归。
19. **回退方法：** 关 strict gate 但保留 FormalBodySafetyGate，恢复 P0.4 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录顺序、主次失败码、真值、固定 3 和回退结果，暂停。

### P0 大阶段门禁

- 固定 10 本地和线上各 1 轮；prepare、终态、正文、Dify workflow ID、Word 真值均 10/10。
- 正式载体禁用表达命中 0；状态矛盾 0；每个非 deliverable 有主失败码。
- `deliverable_count >= max(6, P0.2-b2.5 deliverable_count)`。
- 使用 b2.5 同一 evaluator、rules hash 和可重放快照，`unsupported_eval_v1_total <= min(19, floor(B × 0.40))`；B=0 时阈值为 0。
- 固定 3 VBP 适用主题覆盖率 100%，A/B source_ref 合法率 100%，C 支持率 0。
- 每环境耗时满足冻结基线门槛。
- 门禁失败则 P0 状态保持未完成，不进入 P1；修复或回退最后一个失败子阶段后重新跑完整门禁。

---

## 9. P1：Native 基础与 Shadow 对比

### P1.0：可比较的 ReportIR 与 provider 测量契约

1. **目的：** 建立 Dify/native 可使用的统一 ReportIR 和完整版本、成本、耗时测量契约。
2. **问题和证据：** 当前 ReportIR 定义位于 `app/main.py`，provider 结果缺少 paired id、版本、token、成本和双路径可比字段。
3. **范围：** ReportIR v1 模块、Dify adapter、telemetry schema 和成本公式。
4. **非范围：** 不调用 native、不做 shadow/灰度、不改变正式报告内容或 Dify workflow。
5. **文件：** 新增 `app/report_ir.py`、`app/generation/telemetry.py`、`tests/test_report_ir.py`、`tests/test_provider_telemetry.py`；修改 `app/generation/base.py`、`app/generation/dify_generator.py`、`app/main.py`、`tests/test_generation_pipeline.py`、`.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_PROVIDER_TELEMETRY=true`；无 ReportIR bypass 开关。
7. **数据/API/状态：** 记录 paired_run_id、evidence_hash、provider、model_version、prompt_version、rule_version、compact_version、input/output tokens、cost_source、cost_cny、prepare/generation/quality/export/total_ms；ReportIR 遍历契约兼容 FormalBody。
8. **TDD：** 写 ReportIR 往返、旧 Dify 输出 adapter、版本缺失、成本公式、timing 求和测试；运行 `py -3 -m unittest tests.test_report_ir tests.test_provider_telemetry tests.test_generation_pipeline -v`，预期新模块/字段不存在而失败；最小实现只迁移模型和采集 Dify 数据；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P1.0` 执行 R3-LOCAL；与 P0 baseline 比较规范化正文和表格文本。
10. **Word 检查：** WORD-SCAN；迁移前后规范化 Word 正文/表格 hash 必须相同。
11. **commit 前检查：** G-STATUS；只允许第 5 项，不提交 token/cost 原始响应。
12. **commit/push：** commit message `Define comparable ReportIR and provider telemetry`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P1.0` 执行 B-1。
14. **部署：** D-1、D-2；正式 provider 保持 Dify。
15. **健康检查：** H-1；检查 run/report schema、版本和 timing 字段。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；记录 telemetry 完整率。
17. **通过标准：** ReportIR 往返 100%；固定 3 规范化正文/表格 hash 与 P0 相同 3/3；telemetry 字段完整 100%；分段和 total 绝对误差不超过 100ms；公共门槛通过。
18. **回退触发：** ReportIR 文本变化、字段缺失、成本错误、timing 误差、正式报告或 Word 回归。
19. **回退方法：** 恢复 P0.5 备份/SHA，保留 FormalBodySafetyGate；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录 ReportIR 版本、hash 对比、telemetry 完整率和回退决定，暂停。

### P1.1：Native client 与 NativeReportGenerator

1. **目的：** 实现可独立测试、默认不参与正式结果的 native 候选生成器。
2. **问题和证据：** 当前只有 DifyReportGenerator，尚无 native client、prompt 版本和 ReportIR adapter。
3. **范围：** native HTTP client、超时/重试、脱敏、prompt v1、ReportIR 输出和候选固定 3。
4. **非范围：** 不路由正式流量、不 shadow、不 fallback、不删除或修改 Dify。
5. **文件：** 新增 `app/generation/native_client.py`、`app/generation/native_generator.py`、`app/generation/prompts/native_report_v1.md`、`tests/test_native_client.py`、`tests/test_native_generator.py`；修改 `app/generation/__init__.py`、`app/generation/base.py`、`.env.example`、`docker-compose.yml`、`README.md`。
6. **开关：** `ENABLE_NATIVE_CLIENT=false`、`ENABLE_NATIVE_GENERATOR=false`；模型地址、模型名和密钥只从环境读取，日志永不输出密钥。
7. **数据/API/状态：** native 候选返回 ReportIR、provider metadata、usage、cost、prompt/model version；候选文件与 official run 隔离。
8. **TDD：** 写成功、429/5xx、timeout、重试上限、脱敏、非法 JSON、禁用表达和 memory 事实泄漏测试；运行 `py -3 -m unittest tests.test_native_client tests.test_native_generator -v`，预期模块不存在而失败；最小实现只支持单模型和固定重试；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P1.1` 执行 R3-LOCAL 保持 official Dify；另用 native candidate 命令对固定 3 各运行一次，不能替代 official 验证。
10. **Word 检查：** official 执行 WORD-SCAN；native candidate 只渲染隔离临时 Word 并扫描，不发布 URL。
11. **commit 前检查：** G-STATUS；只允许第 5 项；配置值、响应和临时 Word 不提交。
12. **commit/push：** commit message `Add disabled native report generator`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P1.1` 执行 B-1。
14. **部署：** D-1、D-2；两个 native 开关保持 false，只执行受控 candidate 命令。
15. **健康检查：** H-1；确认 official provider=dify，native 配置缺失不会影响主流程。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；受控 candidate 固定 3 各 1 次并归档，不公开。
17. **通过标准：** 注入错误分类 100%；重试不超过配置次数；敏感字段日志命中 0；candidate ReportIR 可解析 3/3、禁用 0；official 公共门槛通过。
18. **回退触发：** official 路径受影响、密钥泄漏、candidate 不可解析、禁用命中或成本/超时失控。
19. **回退方法：** 双开关 false，恢复 P1.0 备份/SHA；H-1、R3-ONLINE、WORD-SCAN；删除临时 candidate 制品。
20. **记录和暂停：** 记录 candidate provider/model/prompt/耗时/成本/质量及 official 不变证据，暂停。

### P1.2：Shadow 前持久任务、幂等与恢复

1. **目的：** 在开始 Shadow 观察前消除配对丢失、重复提交和重复计费造成的幸存者偏差。
2. **问题和证据：** 当前长任务主要依赖进程内执行；重启可能丢失 provider 结果或重复提交。
3. **范围：** provider job 原子落盘、幂等键、heartbeat、interrupted、启动恢复和结果提交。
4. **非范围：** 不开始 shadow、不路由 native 正式流量、不实现 P4 的跨版本迁移和权限治理。
5. **文件：** 新增 `app/provider_jobs.py`、`app/analysis_runs.py`、`tests/test_provider_jobs.py`、`tests/test_analysis_run_recovery.py`；修改 `app/main.py`、`app/generation/base.py`、`.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_DURABLE_PROVIDER_JOBS=false`，代码默认 false；heartbeat=30 秒，interrupted 阈值=120 秒。
7. **数据/API/状态：** `idempotency_key=run_id:provider:attempt_no`；提交前原子写 job；结果临时写+fsync+replace；同键最多一次计费提交；启动扫描 running 且心跳过期任务并恢复或标 interrupted。
8. **TDD：** 写提交前/后崩溃、结果落盘前/后崩溃、20 次重复请求、心跳边界、启动恢复测试；运行 `py -3 -m unittest tests.test_provider_jobs tests.test_analysis_run_recovery -v`，预期模块不存在而失败；最小实现只支持单 worker durable queue；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P1.2` 执行 R3-LOCAL；每例在生成阶段模拟一次进程中断并恢复。
10. **Word 检查：** WORD-SCAN；恢复后只允许一个发布 Word 和一个 URL。
11. **commit 前检查：** G-STATUS；只允许第 5 项，job/run 运行文件不提交。
12. **commit/push：** commit message `Persist and recover provider jobs idempotently`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P1.2` 执行 B-1；运行目录另做校验归档。
14. **部署：** D-1、D-2；单 worker 下启用 durable jobs。
15. **健康检查：** H-1；检查 job queue 深度、heartbeat age 和 interrupted 指标。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；受控重启一次并验证 3/3 恢复。
17. **通过标准：** 20 次重复提交的 provider 调用重复数 0；结果丢失 0；固定 3 恢复 3/3；Word 重复 0；公共门槛通过。
18. **回退触发：** 重复计费调用、结果丢失、无法恢复、run 状态矛盾、Word 重复或主流程回归。
19. **回退方法：** 停止新任务，归档 job 目录，恢复 P1.1 备份/SHA；H-1、R3-ONLINE、WORD-SCAN；未决任务标 interrupted，不自动重提。
20. **记录和暂停：** 记录故障注入、重复调用、恢复率、未决任务和回退决定，暂停。

### P1.3：Provider router 与隔离 Shadow

1. **目的：** 在 official Dify 不变的前提下，生成可配对的 native shadow 工件。
2. **问题和证据：** 已有两个 generator，但没有确定路由、paired_run_id 和隔离存储。
3. **范围：** router、shadow store、配对身份、official/shadow 隔离和资源上限。
4. **非范围：** 不用 native 作为正式结果、不灰度、不 fallback、不把 shadow Word 暴露给用户。
5. **文件：** 新增 `app/provider_router.py`、`app/shadow_store.py`、`tests/test_provider_router.py`、`tests/test_shadow_store.py`；修改 `app/main.py`、`app/generation/__init__.py`、`.env.example`、`docker-compose.yml`、`README.md`。
6. **开关：** `REPORT_GENERATOR_PROVIDER=dify`、`ENABLE_NATIVE_SHADOW=false`、`NATIVE_GRAY_PERCENT=0`、`ENABLE_DIFY_FALLBACK=true`。
7. **数据/API/状态：** official run 和 shadow result 使用同 paired_run_id/evidence_hash/schema/rule/prompt 版本；shadow 存储目录与 report/download 隔离；shadow 失败不改变 official 状态。
8. **TDD：** 写 official 不变、shadow 成功/失败/timeout、隔离下载、版本不一致拒配对和资源上限测试；运行 `py -3 -m unittest tests.test_provider_router tests.test_shadow_store tests.test_generation_pipeline -v`，预期 router/store 不存在而失败；最小实现只支持 dify official + native shadow；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P1.3` 执行 R3-LOCAL；打开 shadow 后固定 3 各生成一对。
10. **Word 检查：** official WORD-SCAN；shadow Word 只在隔离目录扫描，下载 URL 数为 0。
11. **commit 前检查：** G-STATUS；只允许第 5 项，shadow 工件不提交。
12. **commit/push：** commit message `Route isolated native shadow runs`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P1.3` 执行 B-1。
14. **部署：** D-1、D-2；先 shadow=false 做 health，再 true。
15. **健康检查：** H-1；检查 official provider、shadow queue/store 和无 shadow download route。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；固定 3 paired 字段完整。
17. **通过标准：** official=Dify 3/3；配对完整 3/3；shadow 失败对 official 影响 0；shadow 下载 URL 0；公共门槛通过。
18. **回退触发：** official 被 native 替换、shadow 影响状态/耗时门槛、工件可下载、版本错配或 durable job 回归。
19. **回退方法：** shadow=false，归档 shadow store，恢复 P1.2 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录 paired 字段、隔离、official/shadow 结果和回退决定，暂停。

### P1.4：Shadow 观察与进入灰度门禁

1. **目的：** 用连续观察窗口证明 native 在质量、耗时、成本和可靠性上达到灰度条件。
2. **问题和证据：** 固定 3 只能验证功能，不能代表长期 provider 稳定性。
3. **范围：** shadow evaluator、纳入/排除规则、50 对以上观察和固定 10 两轮。
4. **非范围：** 不改变 official provider、不灰度、不关闭或删除 Dify。
5. **文件：** 新增 `app/shadow_evaluator.py`、`tests/test_shadow_evaluator.py`、`docs/quality-baselines/p1-shadow.json`；修改 `app/shadow_store.py`、`scripts/run_fixed_regression.py`。
6. **开关：** `ENABLE_NATIVE_SHADOW=true`；其他 P1.3 路由值不变。
7. **数据/API/状态：** 只有同 evidence/schema/prompt/rule 版本、双 provider 终态、非人工重跑的 pair 纳入；p95 使用 nearest-rank；成本为运行时输入/输出 token 单价加明确外部费用。
8. **TDD：** 写纳入/排除、缺成本、p95、deliverable 差值、时间窗口和故障 pair 测试；运行 `py -3 -m unittest tests.test_shadow_evaluator -v`，预期 evaluator 不存在而失败；最小实现只读 shadow store 计算指标；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P1.4` 执行 R3-LOCAL；本地生成配对用于 evaluator smoke，不计入线上 50 对。
10. **Word 检查：** official 和 shadow 均 WORD-SCAN；shadow 不发布。
11. **commit 前检查：** G-STATUS；只允许第 5 项，原始 shadow 工件不提交。
12. **commit/push：** commit message `Evaluate native shadow quality and cost`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P1.4` 执行 B-1，备份 shadow index 元数据。
14. **部署：** D-1、D-2；观察窗口从该 SHA 部署并启用 durable jobs 后重新起算。
15. **健康检查：** H-1；每日检查 shadow backlog、pair completeness、provider errors 和成本完整率。
16. **固定数据线上：** R3-ONLINE；固定 10 在两个不同自然日各 1 轮；累计至少 50 个完整 pair，首个与最后一个相隔至少 168 小时。
17. **通过标准：** cost 完整 pair 至少 50；native deliverable_count 不低于 Dify count 减 `floor(0.05×n)`；ReportIR parse 至少 `ceil(0.98×n)`；unsupported 中位数不高于 Dify；p95 不超过 Dify 1.5 倍；平均成本不超过 1.2 倍；禁用 0；official 事故 0。
18. **回退触发：** 任一禁用命中、official 事故、durable job 重复、上述任一门槛失败或观察数据不可重放。
19. **回退方法：** shadow=false，恢复 P1.3 备份/SHA，归档观察数据；H-1、R3-ONLINE、WORD-SCAN；不得进入 P2。
20. **记录和暂停：** 记录 n、时间窗、全部公式输入、固定 10 两轮、通过/失败和回退决定，暂停。

### P1 大阶段门禁

- P0 大门仍全部通过。
- P1.4 的完整 pair、168 小时、固定 10 两轮和全部质量/成本/耗时门槛通过。
- durable jobs 重复 provider 提交 0、丢结果 0。
- official 仍为 Dify，native 只存在 shadow 工件。
- 未通过时禁止进入 P2。

---

## 10. P2：Native 灰度、切换与 Dify fallback

### 10.1 灰度统一计数规则

路由输入固定为 canonical JSON 数组 `[menu_code, articleid]`，使用 UTF-8、`ensure_ascii=false`、无多余空格；取 SHA-256 前 8 个十六进制字符转整数后 `% 100`，bucket 小于 percent 时命中 native。固定 3 放入版本化 validation allowlist，始终走 native 并标记 `route_reason=validation_allowlist`，不计入普通流量样本。

每档只统计该档 SHA 部署后的新 run，不跨档累计。相同 `menu_code + articleid` 在 Asia/Shanghai 同一自然日只纳入第一个被路由的 run；重跑仍记录但不扩大样本。分母包含所有被选 native 的首次 run，包括成功、失败、超时和 fallback。各档共同门槛：

- 禁用表达命中 0；Word 发布失败 0。
- `native_deliverable_count >= max(0, ceil(n × (P0_deliverable_rate - 0.05)))`。
- unsupported_eval_v1 均值不超过 P0 均值 + 0.5/报告。
- n 小于 20 时出现第 2 个 provider error 立即回退；n 至少 20 时 provider error rate 大于 2% 立即回退。
- 固定 3 validation allowlist 全部通过。

### P2.0：10% Native 灰度

1. **目的：** 用最小正式流量验证 deterministic 路由、native 交付和 Dify fallback。
2. **问题和证据：** Shadow 不影响用户结果，尚未验证 native official 状态和 fallback。
3. **范围：** 10% 路由、validation allowlist、正式状态、样本计数和回退。
4. **非范围：** 不提高到 30%、不关闭 fallback、不删除 Dify。
5. **文件：** 修改 `app/provider_router.py`、`app/main.py`、`tests/test_provider_router.py`、`.env.example`、`docker-compose.yml`；新增 `docs/quality-baselines/p2.0-10-percent.json`。
6. **开关：** `ENABLE_NATIVE_GRAY=true`、`NATIVE_GRAY_PERCENT=10`、`ENABLE_DIFY_FALLBACK=true`。
7. **数据/API/状态：** 记录 canonical route key hash、bucket、route_reason、selected/actual provider、fallback 和分母资格。
8. **TDD：** 写 UTF-8 canonical key、bucket 边界、allowlist、同日去重、失败/timeout/fallback 分母测试；运行 `py -3 -m unittest tests.test_provider_router -v`，预期 10% official 和计数规则不存在而失败；最小实现只开放 10%；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P2.0` 执行 R3-LOCAL；3/3 由 allowlist 走 native official。
10. **Word 检查：** WORD-SCAN；native official Word 真值 3/3。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Release native generator to 10 percent`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P2.0` 执行 B-1。
14. **部署：** D-1、D-2；先 0% health，再设 10% 重建。
15. **健康检查：** H-1；检查 router metrics、fallback、provider error 和 Word 状态。
16. **固定 3 线上与观察：** R3-ONLINE、WORD-SCAN；累计至少 30 个合格普通 run、至少 10 个唯一材料、首末相隔至少 72 小时。
17. **通过标准：** 满足 10.1 共同门槛、n≥30、唯一材料≥10、时间窗≥72 小时、固定 3 通过。
18. **回退触发：** 10.1 任一触发、路由不确定、样本去重错误、固定 3 或健康失败。
19. **回退方法：** percent=0、gray=false、fallback=true，恢复 P1.4 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录 n、唯一材料、窗口、分母、错误、fallback、质量和回退决定，暂停。

### P2.1：30% Native 灰度

1. **目的：** 在更大正式流量验证 native 质量和 provider 容量。
2. **问题和证据：** 10% 样本不足以覆盖高峰和更多材料分布。
3. **范围：** 仅把普通流量从 10% 提到 30%，重新开始独立观察。
4. **非范围：** 不累计 10% 样本、不提高到 50%、不关闭 fallback。
5. **文件：** 修改 `tests/test_provider_router.py`、`.env.example`、`docker-compose.yml`；新增 `docs/quality-baselines/p2.1-30-percent.json`。
6. **开关：** `NATIVE_GRAY_PERCENT=30`，gray=true、fallback=true。
7. **数据/API/状态：** 延用 10.1 schema，stage_id 更新为 P2.1，样本从该 SHA 起算。
8. **TDD：** 写 bucket 0/9/10/29/30/99、阶段样本清零和上一档不累计测试；运行 `py -3 -m unittest tests.test_provider_router -v`，预期当前配置/阶段计数仍为 10% 而失败；最小实现只改配置和 stage marker；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P2.1` 执行 R3-LOCAL，3/3 native official。
10. **Word 检查：** WORD-SCAN，真值 3/3。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Expand native gray release to 30 percent`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P2.1` 执行 B-1。
14. **部署：** D-1、D-2；先确认 10% 备份，再设 30%。
15. **健康检查：** H-1；检查 stage_id、bucket 分布、错误/fallback。
16. **固定 3 线上与观察：** R3-ONLINE、WORD-SCAN；至少 60 个新合格 run、20 个唯一材料、首末至少 120 小时。
17. **通过标准：** 10.1 共同门槛、n≥60、唯一材料≥20、窗口≥120 小时。
18. **回退触发：** 任一共同门槛、容量/路由/Word/固定 3 失败。
19. **回退方法：** 配置回 10%，恢复 P2.0 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录本档独立 n、分布、窗口、质量和回退决定，暂停。

### P2.2：50% Native 灰度

1. **目的：** 验证 native 在半量正式流量下的稳定性和材料覆盖。
2. **问题和证据：** 30% 尚不能代表多数请求路径。
3. **范围：** 只把普通流量改为 50%，独立观察。
4. **非范围：** 不累计前档、不全量、不关闭 fallback。
5. **文件：** 修改 `tests/test_provider_router.py`、`.env.example`、`docker-compose.yml`；新增 `docs/quality-baselines/p2.2-50-percent.json`。
6. **开关：** `NATIVE_GRAY_PERCENT=50`，fallback=true。
7. **数据/API/状态：** stage_id=P2.2，按 10.1 去重和分母。
8. **TDD：** 写 49/50 边界、阶段重置、分母和 rollback config 测试；运行 `py -3 -m unittest tests.test_provider_router -v`，预期 50% 配置断言失败；最小实现只改配置/marker；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P2.2` 执行 R3-LOCAL。
10. **Word 检查：** WORD-SCAN。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Expand native gray release to 50 percent`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P2.2` 执行 B-1。
14. **部署：** D-1、D-2；配置 50%。
15. **健康检查：** H-1；检查 bucket、队列、provider errors、fallback。
16. **固定 3 线上与观察：** R3-ONLINE、WORD-SCAN；至少 100 个新合格 run、30 个唯一材料、首末至少 168 小时。
17. **通过标准：** 10.1 共同门槛、n≥100、唯一材料≥30、窗口≥168 小时。
18. **回退触发：** 共同门槛、容量、固定 3、Word 或健康失败。
19. **回退方法：** 配置回 30%，恢复 P2.1 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录独立样本、唯一材料、窗口、质量和回退决定，暂停。

### P2.3：100% Native + Dify fallback

1. **目的：** 让全部正式流量优先 native，同时保留 Dify 安全 fallback。
2. **问题和证据：** 50% 未验证所有 bucket 和全量队列容量。
3. **范围：** 100% native 优先、Dify fallback、全量观察。
4. **非范围：** 不关闭或删除 Dify，不物理移除任何配置。
5. **文件：** 修改 `tests/test_provider_router.py`、`.env.example`、`docker-compose.yml`；新增 `docs/quality-baselines/p2.3-100-percent-fallback.json`。
6. **开关：** `NATIVE_GRAY_PERCENT=100`、`ENABLE_DIFY_FALLBACK=true`。
7. **数据/API/状态：** 所有普通 bucket 选择 native；fallback_reason/provider/workflow ID 完整记录。
8. **TDD：** 写 bucket 99、native 失败 fallback、Dify 失败、双失败主码和分母测试；运行 `py -3 -m unittest tests.test_provider_router tests.test_generation_pipeline -v`，预期 100%/fallback 状态失败；最小实现只改配置和双失败映射；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P2.3` 执行 R3-LOCAL。
10. **Word 检查：** WORD-SCAN；native 和受控 fallback 两路径均扫描。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Route all reports to native with Dify fallback`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P2.3` 执行 B-1。
14. **部署：** D-1、D-2；配置 100%+fallback。
15. **健康检查：** H-1；检查 native、fallback 和 Dify workflow 指标。
16. **固定 3 线上与观察：** R3-ONLINE、WORD-SCAN；至少 200 个新合格 run、50 个唯一材料、首末至少 336 小时。
17. **通过标准：** 10.1 共同门槛；n≥200、唯一材料≥50、窗口≥336 小时；最近 200 个合格 run 中 fallback 次数不超过 2。
18. **回退触发：** 共同门槛、fallback 大于 2/200、队列容量、固定 3 或 Word 失败。
19. **回退方法：** 配置回 50%+fallback，恢复 P2.2 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录 200 run、50 identity、fallback 明细、质量和回退决定，暂停。

### P2.4：关闭 Dify fallback

1. **目的：** 验证 native 可独立承担正式生成，同时保留可快速恢复的 Dify 代码。
2. **问题和证据：** P2.3 仍可能依赖 Dify fallback，尚不能进入后续物理删除观察窗。
3. **范围：** 关闭 fallback、观察 native-only、保留 Dify client/config/code。
4. **非范围：** 不物理删除 Dify，不删除 Dify 测试或部署变量。
5. **文件：** 修改 `tests/test_provider_router.py`、`.env.example`、`docker-compose.yml`；新增 `docs/quality-baselines/p2.4-native-only.json`。
6. **开关：** `NATIVE_GRAY_PERCENT=100`、`ENABLE_DIFY_FALLBACK=false`。
7. **数据/API/状态：** native 失败直接进入明确失败状态；Dify call count 必须为 0。
8. **TDD：** 写 native 失败不调用 Dify、Dify call counter=0、恢复 fallback 配置测试；运行 `py -3 -m unittest tests.test_provider_router tests.test_generation_pipeline -v`，预期当前 fallback=true 而失败；最小实现只改配置和断言；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P2.4` 执行 R3-LOCAL。
10. **Word 检查：** WORD-SCAN。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Disable Dify fallback after native stabilization`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P2.4` 执行 B-1。
14. **部署：** D-1、D-2；配置 fallback=false，Dify 代码保持。
15. **健康检查：** H-1；Dify call counter、fallback counter 均为 0。
16. **固定 3 线上与观察：** R3-ONLINE、WORD-SCAN；至少 100 个新合格 run、30 个唯一材料、首末至少 168 小时。
17. **通过标准：** 10.1 共同门槛；n≥100、唯一材料≥30、窗口≥168 小时；Dify 调用 0；固定 3 通过。
18. **回退触发：** 任一共同门槛、native provider error、固定 3/Word/健康失败。
19. **回退方法：** 立即设置 fallback=true 并恢复 P2.3 备份/SHA；H-1、R3-ONLINE、WORD-SCAN；不得删除 Dify。
20. **记录和暂停：** 记录 native-only 样本、Dify 0 调用证据、质量和回退决定，暂停。

### P2 大阶段门禁

- P2.4 全部样本、时间、唯一材料和质量门槛通过。
- 固定 10 在两个不同自然日各运行 1 轮，全部满足 Word 和正式载体规则。
- Dify fallback 代码仍存在但开关关闭；Dify 正式调用为 0。
- 未通过禁止进入 P3；本阶段绝不物理删除 Dify。

---

## 11. P3：性能、可观测性与用户体验

性能子阶段使用同一代码 SHA、同一环境、同一材料和同一计时边界做开关关闭/开启配对。每种配置在本地固定 3 × 3 次=9 次，线上固定 3 × 3 次=9 次；基准与候选合计每环境 18 次、两个环境 36 次。p50/p95 均按环境分开使用 nearest-rank，不混合本地与线上。

### P3.0：全链路 timing 与观测 API

1. **目的：** 让 prepare、parse、compact、generation、quality、export 和 total 可测且可对账。
2. **问题和证据：** 现有 diagnostics 有部分耗时，不能完整支撑性能回归门禁。
3. **范围：** monotonic timing、run 字段、diagnostics API 和 UI 基础展示。
4. **非范围：** 不并发解析、不缓存、不分段、不异步 QA。
5. **文件：** 新增 `app/timing.py`、`tests/test_timing.py`；修改 `app/main.py`、`app/diagnostics.py`、`app/static/analysis_run.html`、`tests/test_records_api.py`、`.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_DETAILED_TIMING=true`。
7. **数据/API/状态：** 所有 timing 为整数毫秒；total 起止边界固定为 prepare 请求进入至 Word 状态落盘；字段缺失不得填 0 伪装成功。
8. **TDD：** 写 monotonic、异常路径、求和、缺段和 API schema 测试；运行 `py -3 -m unittest tests.test_timing tests.test_records_api -v`，预期字段不完整而失败；最小实现只加计时 context；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P3.0` 执行 R3-LOCAL；每例重复 3 次，共 9 次。
10. **Word 检查：** WORD-SCAN；timing 不进入 Word。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Measure end-to-end report timings`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P3.0` 执行 B-1。
14. **部署：** D-1、D-2。
15. **健康检查：** H-1；检查 timing API/UI 字段。
16. **固定 3 线上：** 每例重复 3 次，共 9 次；WORD-SCAN。
17. **通过标准：** 本地 9/9、线上 9/9 字段完整；各 run 分段和 total 误差不超过 100ms；错误 0/18；公共质量门槛通过。
18. **回退触发：** 计时边界错误、字段伪 0、API/UI 回归、开销超过 P0 基线门槛。
19. **回退方法：** 关开关，恢复 P2.4 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录 18 次分段值、误差和回退决定，暂停。

### P3.1：有界附件并发解析

1. **目的：** 在输出完全一致的前提下降低附件解析耗时。
2. **问题和证据：** 多附件 case 解析耗时高，串行或无界并发都不满足稳定性要求。
3. **范围：** 并发数 3、顺序稳定、异常隔离和 paired benchmark。
4. **非范围：** 不改解析算法、PDF 事实、compact、generation 或 QA。
5. **文件：** 修改 `app/attachment_fetcher.py`、`app/attachment_parser.py`、`app/main.py`、`tests/test_records_api.py`；新增 `tests/test_attachment_concurrency.py`；修改 `.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_CONCURRENT_ATTACHMENT_PARSE=false`、`ATTACHMENT_PARSE_CONCURRENCY=3`。
7. **数据/API/状态：** 结果按原 attachment 顺序提交；单附件失败不取消其他任务；记录 queue/parse_ms。
8. **TDD：** 写最大并发、顺序、单失败、timeout、取消和 hash 等价测试；运行 `py -3 -m unittest tests.test_attachment_concurrency tests.test_records_api -v`，预期并发行为不存在而失败；最小实现使用有界 semaphore；定向后 T-FULL-WIN。
9. **固定 3 本地：** 开关 false/true 各每例 3 次，共 18 次。
10. **Word 检查：** 两组均 WORD-SCAN；规范化 evidence/report/Word hash 相同。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Parse attachments with bounded concurrency`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P3.1` 执行 B-1。
14. **部署：** D-1、D-2；线上 false/true 配对。
15. **健康检查：** H-1；检查并发、队列、错误和内存指标。
16. **固定 3 线上：** false/true 各每例 3 次，共 18 次；WORD-SCAN。
17. **通过标准：** 输出 hash 一致 100%；错误 0/36；attachment parse p50 改善至少 20%；total p95 恶化不超过 10%；公共质量门槛通过。
18. **回退触发：** hash 不同、并发大于 3、错误、p50/p95 门槛或 Word 失败。
19. **回退方法：** 开关 false，恢复 P3.0 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录 36 次配对、并发峰值、p50/p95 和回退决定，暂停。

### P3.2：Compact 缓存

1. **目的：** 对未改变的 evidence/version 复用 compact，避免重复计算且不产生陈旧证据。
2. **问题和证据：** 同一 pack 重跑会重复 compact。
3. **范围：** 版本化 cache key、原子缓存、命中校验和失效。
4. **非范围：** 不改变 compact 内容或压缩策略、不缓存 provider 输出。
5. **文件：** 新增 `app/compact_cache.py`、`tests/test_compact_cache.py`；修改 `app/main.py`、`app/diagnostics.py`、`.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_COMPACT_CACHE=false`。
7. **数据/API/状态：** key=evidence_hash+schema/rule/compact version；value 包含 content hash；原子写；任一版本变化强制 miss。
8. **TDD：** 写 hit/miss、版本变化、损坏、并发、原子写和 hash 测试；运行 `py -3 -m unittest tests.test_compact_cache -v`，预期模块不存在而失败；最小实现只缓存 compact JSON；定向后 T-FULL-WIN。
9. **固定 3 本地：** false/true 各每例 3 次，共 18 次；true 组每例首 miss 后两次 hit。
10. **Word 检查：** 两组 WORD-SCAN；compact/report/Word hash 一致。
11. **commit 前检查：** G-STATUS；只允许第 5 项，cache 文件不提交。
12. **commit/push：** commit message `Cache versioned compact evidence packs`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P3.2` 执行 B-1。
14. **部署：** D-1、D-2；false/true 配对。
15. **健康检查：** H-1；检查 hit/miss/corruption 指标。
16. **固定 3 线上：** false/true 各每例 3 次，共 18 次；WORD-SCAN。
17. **通过标准：** 预期 hit 12/12；内容 hash 一致 100%；本地 hit 读取不超过 50ms、线上不超过 100ms；错误 0/36；公共质量门槛通过。
18. **回退触发：** 陈旧 hit、hash 不同、损坏未隔离、读取超时、Word/质量回归。
19. **回退方法：** 关开关，归档并清空活动 cache，恢复 P3.1 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录 hit/miss、hash、时延和回退决定，暂停。

### P3.3：大包分段生成

1. **目的：** 对第一阶段仍大于 80000 的大包分段生成，保留 source_ref 并稳定合并。
2. **问题和证据：** 超大多附件材料可能无法在单次 provider 输入内保留全部证据。
3. **范围：** 确定分段、分段 ReportIR、去重合并和大包专项回归。
4. **非范围：** 不对不超过 80000 的包分段、不改变 compact 分级、不提高 provider 并发。
5. **文件：** 新增 `app/generation/segmented_generator.py`、`tests/test_segmented_generator.py`；修改 `app/generation/native_generator.py`、`app/provider_router.py`、`app/main.py`、`.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_SEGMENTED_GENERATION=false`；触发条件严格为 first_stage_chars>80000。
7. **数据/API/状态：** segment_id、source_refs、input hash、output hash、merge_version；不超限包 segment_count=1 且内容不变。
8. **TDD：** 写 80000/80001、ref 覆盖、重复 claim、分段失败、合并顺序和未超限不变测试；运行 `py -3 -m unittest tests.test_segmented_generator -v`，预期模块不存在而失败；最小实现按附件/主题边界切分并顺序合并；定向后 T-FULL-WIN。
9. **固定数据本地：** 固定 3 false/true 各每例 3 次；另加 29 附件固定 10 case false/true 各 3 次。
10. **Word 检查：** 全部 WORD-SCAN；重复 claim=0，ref 覆盖 100%。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Generate oversized evidence packs in traceable segments`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P3.3` 执行 B-1。
14. **部署：** D-1、D-2；false/true 配对。
15. **健康检查：** H-1；检查 segment count、失败和 merge 指标。
16. **固定数据线上：** 与本地相同配对和 29 附件 case；WORD-SCAN。
17. **通过标准：** 未超限内容不变 100%；大包 source_ref 覆盖 100%；重复 claim 0；错误 0；候选 p95 不高于关闭分段的 1.10 倍；禁用 0。
18. **回退触发：** 未超限包改变、ref 丢失、重复 claim、分段失败、p95/Word/质量门槛失败。
19. **回退方法：** 关开关，恢复 P3.2 备份/SHA；H-1、R3-ONLINE、WORD-SCAN；归档分段工件。
20. **记录和暂停：** 记录 segment、ref、重复、配对耗时和回退决定，暂停。

### P3.4：异步 QA 状态机

1. **目的：** 将耗时 QA 从生成请求拆出，同时保持 draft/final 和交付状态准确。
2. **问题和证据：** 同步 QA 增加用户等待，直接异步化又可能提前暴露 final Word。
3. **范围：** quality_pending、durable QA job、幂等、状态迁移和 UI 轮询。
4. **非范围：** 不改变 QA 规则、不跳过 FormalBodySafetyGate、不允许 pending final。
5. **文件：** 新增 `app/async_quality.py`、`tests/test_async_quality.py`；修改 `app/analysis_runs.py`、`app/main.py`、`app/static/analysis_run.html`、`.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_ASYNC_QUALITY=false`。
7. **数据/API/状态：** 生成后状态 quality_pending；清洁正文可 draft=true；final=false、deliverable=false；QA 终态后统一门禁决定 final；QA job 使用 idempotency key。
8. **TDD：** 写 pending 真值、成功/失败、20 次重复、重启、timeout 和 UI 轮询测试；运行 `py -3 -m unittest tests.test_async_quality tests.test_analysis_run_recovery -v`，预期异步状态不存在而失败；最小实现复用 durable job；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P3.4` 执行 R3-LOCAL；验证每例 pending→终态。
10. **Word 检查：** pending 只按真值提供 clean draft，final URL 不存在；终态执行 WORD-SCAN。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Run report quality checks asynchronously`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P3.4` 执行 B-1。
14. **部署：** D-1、D-2；false/true 配对。
15. **健康检查：** H-1；检查 QA queue、pending age、终态和 UI 轮询。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；记录状态时间线。
17. **通过标准：** 20 次任务丢失 0、重复 QA 0；固定 3 状态迁移 3/3；pending final 暴露 0；公共门槛通过。
18. **回退触发：** 任务丢失/重复、pending 暴露 final、状态卡死、Word/质量回归。
19. **回退方法：** 关异步开关，等待/归档 pending job，恢复 P3.3 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录队列、pending 时长、迁移、重复/丢失和回退决定，暂停。

### P3.5：历史对比与交付状态 UI

1. **目的：** 让用户按材料查看历史 provider、版本、耗时、质量和 draft/final 状态。
2. **问题和证据：** 后端历史存在，但 records-ui 尚无低风险对比入口。
3. **范围：** 材料历史入口、两次 run 对比、状态展示、分页和可访问性。
4. **非范围：** 不在 UI 编辑事实、不显示敏感 diagnostics 原文、不改生成或门禁。
5. **文件：** 修改 `app/static/records.html`、`app/static/analysis_run.html`、`app/main.py`、`tests/test_records_api.py`；新增 `tests/test_analysis_history_ui.py`；修改 `.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_ANALYSIS_HISTORY_UI=false`。
7. **数据/API/状态：** 展示 provider/version/total_ms/primary failure/四 Word 字段；对比 API 默认 20、最大 100；不展示凭据、原始 provider 请求或 C 级事实。
8. **TDD：** 写入口、分页、空历史、两 run 对比、HTML 转义、字段权限和响应布局测试；运行 `py -3 -m unittest tests.test_analysis_history_ui tests.test_records_api -v`，预期 UI/API 行为不存在而失败；最小实现只读展示；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P3.5` 执行 R3-LOCAL；每例历史入口和至少两 run 对比可用。
10. **Word 检查：** WORD-SCAN；UI diagnostics 不影响 Word。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Show analysis history and delivery states`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P3.5` 执行 B-1。
14. **部署：** D-1、D-2；先 false、后 true。
15. **健康检查：** H-1；检查 records-ui、analysis-run、history API；1440×900 和 390×844 截图无重叠。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；3/3 历史和对比可查。
17. **通过标准：** 固定 3 可查 3/3；30 次 history API 请求 p95≤500ms；两视口重叠/溢出 0；字段泄漏 0；公共门槛通过。
18. **回退触发：** UI 阻塞主流程、字段泄漏、布局重叠、API p95 超限、Word/质量回归。
19. **回退方法：** UI 开关 false，恢复 P3.4 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录截图、API p95、固定 3 查询和回退决定，暂停。

### P3 大阶段门禁

- 固定 10 本地、线上各 1 轮，质量和 Word 门槛全部通过。
- 线上 total p95 相对 P0 冻结基线恶化不超过 10%。
- timing 完整率 100%，异步任务丢失/重复 0，历史 UI 字段泄漏 0。
- 未通过禁止进入 P4。

---

## 12. P4：可靠性、任务恢复与数据治理

### P4.0：跨步骤任务恢复与 reconciliation

1. **目的：** 让容器在 prepare、generation、quality、export 任一步重启后恢复到唯一终态。
2. **问题和证据：** P1.2 只保证 provider job 的最小持久化，尚未覆盖全链路 reconciliation。
3. **范围：** 启动扫描、步骤 checkpoint、恢复策略、终态幂等和人工可见 interrupted。
4. **非范围：** 不改 provider、报告内容、质量规则或 history 存储介质。
5. **文件：** 新增 `app/run_recovery.py`、`tests/test_run_recovery.py`；修改 `app/analysis_runs.py`、`app/provider_jobs.py`、`app/main.py`、`.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_RUN_RECOVERY=false`；单 run 自动恢复次数上限为 1。
7. **数据/API/状态：** checkpoint 包含 step、input/output hash、attempt、heartbeat；恢复只重做未原子提交步骤；无法证明幂等时标 interrupted，不自动重提 provider。
8. **TDD：** 在 prepare/generation/quality/export 各注入 5 次中断，共 20 次；写 hash 不一致、恢复上限和终态重复测试；运行 `py -3 -m unittest tests.test_run_recovery tests.test_analysis_run_recovery -v`，预期全链路恢复不存在而失败；最小实现复用原子 run 文件；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P4.0` 执行 R3-LOCAL；每例在不同步骤中断一次并恢复。
10. **Word 检查：** WORD-SCAN；每 run 最多一个发布文件/URL，恢复 diagnostics 不入 Word。
11. **commit 前检查：** G-STATUS；只允许第 5 项，checkpoint 运行文件不提交。
12. **commit/push：** commit message `Recover interrupted report runs safely`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P4.0` 执行 B-1；run/job 目录另做校验归档。
14. **部署：** D-1、D-2；先 false、后 true 做受控重启。
15. **健康检查：** H-1；检查 recovery queue、interrupted、attempt 和 checkpoint age。
16. **固定 3 线上：** R3-ONLINE；受控重启容器一次，3/3 恢复；WORD-SCAN。
17. **通过标准：** 20 个故障 fixture 终态 20/20；重复 provider 0；重复 Word 0；固定 3 恢复 3/3；公共门槛通过。
18. **回退触发：** 重复提交、丢结果、恢复循环、错误终态、Word/质量/健康回归。
19. **回退方法：** 关恢复开关，停止新任务，归档 checkpoint，恢复 P3.5 备份/SHA；H-1、R3-ONLINE、WORD-SCAN，未决 run 标 interrupted。
20. **记录和暂停：** 记录 20 次故障、恢复率、重复数、固定 3 和回退决定，暂停。

### P4.1：Run、history 与 ReportIR schema 版本化

1. **目的：** 让持久工件可迁移、可拒绝未知高版本且不静默损坏。
2. **问题和证据：** 多阶段新增字段后，旧 run/history/ReportIR 缺少统一迁移入口。
3. **范围：** 版本号、纯函数迁移、兼容读取、未知高版本保护和 fixture。
4. **非范围：** 不改业务事实、不迁数据库、不删除旧 schema 文件。
5. **文件：** 新增 `app/schema_migrations.py`、`tests/test_schema_migrations.py`、`tests/fixtures/schema_versions/`；修改 `app/analysis_runs.py`、`app/analysis_history.py`、`app/report_ir.py`、`app/main.py`。
6. **开关：** 无新增开关；`RUN_SCHEMA_VERSION`、`HISTORY_SCHEMA_VERSION`、`REPORT_IR_SCHEMA_VERSION` 为显式整数常量。
7. **数据/API/状态：** 读取时逐版本迁移到内存；写入只使用当前版本；未知高版本拒写并保留原文件；迁移前后 hash 和版本记录 diagnostics。
8. **TDD：** 写当前版、前 3 版、重复迁移、未知高版、损坏字段和原文件不变测试；运行 `py -3 -m unittest tests.test_schema_migrations -v`，预期迁移模块不存在而失败；最小实现只支持计划中真实旧版本；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P4.1` 执行 R3-LOCAL；复制其 run/history/ReportIR 降版后重读。
10. **Word 检查：** WORD-SCAN；迁移前后规范化正文/表格 hash 相同。
11. **commit 前检查：** G-STATUS；只允许第 5 项，迁移运行副本不提交。
12. **commit/push：** commit message `Version persistent report schemas`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P4.1` 执行 B-1；持久数据目录做只读 hash 清单。
14. **部署：** D-1、D-2；先只读扫描全部现有 run，0 错误后开放写入。
15. **健康检查：** H-1；检查 schema scan counts、migration errors 和 unknown versions。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；读取旧 fixture 和新 run。
17. **通过标准：** 前 3 版 fixture 读取 100%；迁移幂等 100%；未知高版拒写 100%；原文件意外修改 0；公共门槛通过。
18. **回退触发：** 旧数据不可读、迁移不幂等、未知高版被覆盖、Word hash 改变。
19. **回退方法：** 停写、恢复 P4.0 备份/SHA 和数据 hash 清单对应文件；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录版本矩阵、扫描数量、迁移结果和回退决定，暂停。

### P4.2：History 轮转、备份与损坏恢复

1. **目的：** 控制历史文件增长，并验证归档、校验和和损坏恢复闭环。
2. **问题和证据：** b2 只有基础轮转规则，尚未覆盖归档缺失、截断和恢复演练。
3. **范围：** 轮转、12 份保留、SHA 清单、备份、坏尾行/坏 index/缺归档恢复。
4. **非范围：** 不多 worker 写入、不迁 SQLite、不改变 run JSON 真相来源。
5. **文件：** 修改 `app/analysis_history.py`、`tests/test_analysis_history.py`、`README.md`；新增 `tests/test_history_recovery.py`、`scripts/verify_history_archive.py`。
6. **开关：** `ANALYSIS_HISTORY_ROTATE_BYTES=52428800`、`ANALYSIS_HISTORY_ROTATE_EVENTS=100000`、`ANALYSIS_HISTORY_ARCHIVE_KEEP=12`。
7. **数据/API/状态：** 达到任一阈值先完成当前 event/index，再原子轮转；删除第 13 份前必须验证外部备份 hash；重建只读 run JSON。
8. **TDD：** 注入末尾截断、内部坏行、坏 index、缺 archive、错 checksum 各 6 次，共 30 次；运行 `py -3 -m unittest tests.test_analysis_history tests.test_history_recovery -v`，预期恢复/轮转不完整而失败；最小实现只加归档清单和恢复工具；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P4.2` 执行 R3-LOCAL；用缩小测试阈值触发一次轮转并重建。
10. **Word 检查：** WORD-SCAN；历史恢复信息不进入 Word。
11. **commit 前检查：** G-STATUS；只允许第 5 项，归档和 run 数据不提交。
12. **commit/push：** commit message `Rotate and recover analysis history safely`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P4.2` 执行 B-1；history 另做独立 tar+SHA 备份。
14. **部署：** D-1、D-2；生产阈值使用第 6 项数值。
15. **健康检查：** H-1；运行 verify 脚本，检查 active/archive/checksum/rebuild 指标。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；验证事件写入和查询未中断。
17. **通过标准：** 30 次损坏恢复 30/30；run JSON 修改 0；第 13 份未备份删除 0；固定 3 历史完整；公共门槛通过。
18. **回退触发：** 事件丢失、错误删除归档、checksum 不一致未阻断、run JSON 被改、主流程/Word 回归。
19. **回退方法：** history 开关 false，恢复独立 history 备份和 P4.1 SHA；verify、H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录轮转、12 份保留、30 次恢复、hash 和回退决定，暂停。

### P4.3：管理接口权限与审计

1. **目的：** 限制 history rebuild、详细诊断和治理接口，并留下不含秘密的审计记录。
2. **问题和证据：** 重建和详细 run 信息具有资源消耗与元数据暴露风险。
3. **范围：** 管理认证 hook、授权矩阵、actor/action/result 审计和速率限制。
4. **非范围：** 不修改普通 records-ui 查询、不把认证数据写入报告或 Git。
5. **文件：** 新增 `app/auth.py`、`app/audit.py`、`tests/test_admin_auth.py`、`tests/test_audit_log.py`；修改 `app/main.py`、`.env.example`、`docker-compose.yml`、`README.md`。
6. **开关：** `ENABLE_ADMIN_API=false`；管理凭据只从环境读取；rebuild 默认关闭。
7. **数据/API/状态：** 审计字段只含 timestamp、actor_id、action、target_id、result、request_id；禁止 token、Cookie、正文和附件内容。
8. **TDD：** 写未认证/无权限/有权限、过期、速率限制、审计脱敏和失败审计测试；运行 `py -3 -m unittest tests.test_admin_auth tests.test_audit_log -v`，预期模块不存在而失败；最小实现只保护管理入口；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P4.3` 执行 R3-LOCAL；普通分析不要求管理凭据。
10. **Word 检查：** WORD-SCAN；actor/audit/diagnostics 泄漏 0。
11. **commit 前检查：** G-STATUS；只允许第 5 项；凭据和审计运行日志不提交。
12. **commit/push：** commit message `Protect and audit analysis administration APIs`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P4.3` 执行 B-1。
14. **部署：** D-1、D-2；admin=false 验证普通路径，再受控启用。
15. **健康检查：** H-1；授权矩阵接口分别得到 401/403/2xx，普通接口 2xx。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；审计普通 run 只记录允许字段。
17. **通过标准：** 授权矩阵正确率 100%；未授权成功数 0；秘密日志命中 0；固定 3 和公共门槛通过。
18. **回退触发：** 越权、普通路径被阻断、秘密写日志、审计缺失或 Word 回归。
19. **回退方法：** admin=false，恢复 P4.2 备份/SHA；H-1、R3-ONLINE、WORD-SCAN；归档审计日志。
20. **记录和暂停：** 记录矩阵、脱敏扫描、速率限制和回退决定，暂停。

### P4.4：SQLite 离线评估门

1. **目的：** 只在负载或拓扑达到明确条件后，离线比较文件方案与 SQLite，不自动迁移生产数据。
2. **问题和证据：** 当前日量适合文件方案；多 worker、10 万事件或 50MB 单文件会改变并发和恢复要求。
3. **范围：** threshold metrics、只读快照 benchmark、报告和后续决策门。
4. **非范围：** 不在本阶段启用多 worker、不让 history 消失、不迁生产 SQLite。
5. **文件：** 新增 `app/history_metrics.py`、`scripts/benchmark_history_storage.py`、`tests/test_history_storage_benchmark.py`、`docs/quality-baselines/p4.4-history-storage.json`；修改 `app/analysis_history.py`、`app/diagnostics.py`。
6. **开关：** `ENABLE_HISTORY_STORAGE_BENCHMARK=false`；触发条件为计划部署 worker≥2、累计事件≥100000、或活动单文件≥52428800 bytes 任一成立。
7. **数据/API/状态：** 生产继续单 worker 文件写；benchmark 使用校验过的只读副本，分别测 append、query、rebuild、corruption recovery。
8. **TDD：** 写三触发、未触发、只读快照、结果公式和生产路径不切换测试；运行 `py -3 -m unittest tests.test_history_storage_benchmark -v`，预期工具不存在而失败；最小实现只生成对比 JSON；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P4.4` 执行 R3-LOCAL；在复制数据上运行 benchmark，主 history 保持可写。
10. **Word 检查：** WORD-SCAN；benchmark 不影响正文。
11. **commit 前检查：** G-STATUS；只允许第 5 项；数据库/快照文件不提交。
12. **commit/push：** commit message `Benchmark history storage at explicit thresholds`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P4.4` 执行 B-1；history 快照单独校验。
14. **部署：** D-1、D-2；benchmark 默认 false，受控离线执行。
15. **健康检查：** H-1；确认 worker 仍为 1、history 正常、benchmark 不持有生产写锁。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；benchmark 前后 history 事件完整。
17. **通过标准：** 未触发时不建议迁移；触发时 SQLite 正确性 100%，append p95≤文件方案 80%，query 和 rebuild p95 均≤文件方案 80% 才形成“建议独立迁移计划”；生产切换次数 0。
18. **回退触发：** benchmark 修改生产数据、持有写锁、history 中断、指标不可重放、固定 3 回归。
19. **回退方法：** 关 benchmark，删除只读副本，恢复 P4.3 备份/SHA；verify history、H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录触发条件、对比数据和“保持文件/另立迁移计划”的结论，暂停；未经用户确认不迁移。

### P4 大阶段门禁

- 全链路 20 次故障恢复 20/20，重复 provider/Word 0。
- schema 前 3 版读取、幂等迁移和未知高版保护全部通过。
- history 30 次损坏恢复 30/30，授权越权 0。
- 固定 10 本地、线上各 1 轮通过；未通过禁止进入 P5。

---

## 13. P5：代码拆分、测试体系与自动化交付

P5 只做行为等价的渐进拆分。每个子阶段只迁移一个责任集合，不在一次提交中重写全部 `app/main.py`。

### P5.0：Settings 与 schemas 拆分

1. **目的：** 把环境解析和 Pydantic schema 从 main 移到明确模块，保持行为不变。
2. **问题和证据：** main 过大，配置和 schema 与路由实现耦合。
3. **范围：** settings、公共请求/响应 schema 和兼容 re-export。
4. **非范围：** 不迁 routes/services，不改默认值、API 或业务行为。
5. **文件：** 新增 `app/settings.py`、`app/schemas.py`、`tests/test_settings.py`、`tests/test_schemas.py`；修改 `app/main.py`。
6. **开关：** 无新增开关；所有现有默认值逐项快照比较。
7. **数据/API/状态：** OpenAPI schema 和环境变量解析结果保持等价。
8. **TDD：** 写默认值、bool/int 解析、OpenAPI schema snapshot、旧 import 测试；运行 `py -3 -m unittest tests.test_settings tests.test_schemas tests.test_records_api -v`，预期模块不存在而失败；最小实现迁移定义并 re-export；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P5.0` 执行 R3-LOCAL。
10. **Word 检查：** WORD-SCAN；规范化报告/Word hash 与 P4 baseline 相同。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Extract application settings and schemas`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P5.0` 执行 B-1。
14. **部署：** D-1、D-2。
15. **健康检查：** H-1；比较 OpenAPI route/schema 列表。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN。
17. **通过标准：** 设置默认值差异 0；OpenAPI 语义差异 0；固定 3 和公共门槛通过。
18. **回退触发：** 配置默认改变、schema/route 差异、固定 3/Word 回归。
19. **回退方法：** 恢复 P4.4 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录迁移定义数、snapshot 差异和回退决定，暂停。

### P5.1：Records 与 evidence 服务拆分

1. **目的：** 把数据库列表、详情、选材和 evidence pack 责任移出 main。
2. **问题和证据：** records SQL、附件和 pack 逻辑集中在 main，改动风险高。
3. **范围：** records router/service、evidence service 和兼容入口。
4. **非范围：** 不改 SQL 语义、排序、筛选、分页、附件解析或 pack schema。
5. **文件：** 新增 `app/routers/records.py`、`app/services/records.py`、`app/services/evidence.py`、`tests/test_records_service.py`、`tests/test_evidence_service.py`；修改 `app/main.py`、`tests/test_records_api.py`。
6. **开关：** 无新增开关。
7. **数据/API/状态：** 路由路径、响应 schema、默认 project_notice_first 和 sort=latest 保持不变。
8. **TDD：** 写 SQL/参数、排序、筛选、分页、pack hash、旧 route snapshot；运行 `py -3 -m unittest tests.test_records_service tests.test_evidence_service tests.test_records_api -v`，预期模块不存在而失败；最小实现委托原函数；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P5.1` 执行 R3-LOCAL。
10. **Word 检查：** WORD-SCAN；evidence/report hash 与迁移前一致。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Extract records and evidence services`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P5.1` 执行 B-1。
14. **部署：** D-1、D-2。
15. **健康检查：** H-1；检查 records-ui、records、detail、prepare、pack。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN。
17. **通过标准：** SQL 参数/结果集差异 0；排序/分页差异 0；pack hash 差异 0；公共门槛通过。
18. **回退触发：** 数据集/排序/pack 改变、API/Word/性能回归。
19. **回退方法：** 恢复 P5.0 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录迁移函数、SQL snapshot、固定 3 和回退决定，暂停。

### P5.2：Analysis runs 与 history 服务拆分

1. **目的：** 统一 run 生命周期、状态和历史 hook 的模块边界。
2. **问题和证据：** main 仍编排 run 文件、history、recovery 和 API。
3. **范围：** run service、history router 和原路由兼容。
4. **非范围：** 不改状态机、history 格式、recovery、权限或 UI。
5. **文件：** 新增 `app/routers/analysis_runs.py`、`app/services/analysis_runs.py`、`app/routers/history.py`、`tests/test_analysis_run_service.py`；修改 `app/main.py`、`app/analysis_runs.py`、`tests/test_records_api.py`。
6. **开关：** 无新增开关。
7. **数据/API/状态：** run/history schema、事件顺序、revision 和路由保持等价。
8. **TDD：** 写创建/更新/恢复/history hook/route snapshot；运行 `py -3 -m unittest tests.test_analysis_run_service tests.test_analysis_history tests.test_records_api -v`，预期服务模块不存在而失败；最小实现只搬迁编排；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P5.2` 执行 R3-LOCAL。
10. **Word 检查：** WORD-SCAN；Word 状态时间线与迁移前一致。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Extract analysis run and history services`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P5.2` 执行 B-1，history/run 数据另做 hash 清单。
14. **部署：** D-1、D-2。
15. **健康检查：** H-1；检查 run、report、revise、history、rebuild 权限。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；事件顺序比较。
17. **通过标准：** route/schema 差异 0；事件/状态差异 0；固定 3 和公共门槛通过。
18. **回退触发：** 状态/事件改变、history 丢失、recovery/Word/API 回归。
19. **回退方法：** 恢复 P5.1 备份/SHA 和数据 hash；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录迁移边界、事件比较和回退决定，暂停。

### P5.3：Generation、quality 与 export 服务拆分

1. **目的：** 把 provider 编排、质量组合和 Word 发布从 main 移到独立 service/router。
2. **问题和证据：** 这些高风险责任仍通过 main 内部函数耦合。
3. **范围：** generation service、quality service、export service/router 和兼容调用。
4. **非范围：** 不改 provider 路由、prompt、门禁顺序、repair、Word 样式或状态真值。
5. **文件：** 新增 `app/services/generation.py`、`app/services/quality.py`、`app/services/export.py`、`app/routers/report_export.py`、`tests/test_generation_service.py`、`tests/test_quality_service.py`、`tests/test_export_service.py`；修改 `app/main.py`、`tests/test_generation_pipeline.py`、`tests/test_report_export.py`。
6. **开关：** 无新增开关。
7. **数据/API/状态：** provider metadata、gate 顺序、repair 次数、ReportIR 和 Word URL 保持等价。
8. **TDD：** 写 service contract、调用顺序、异常映射、route snapshot 和 Word hash；运行 `py -3 -m unittest tests.test_generation_service tests.test_quality_service tests.test_export_service tests.test_generation_pipeline tests.test_report_export -v`，预期服务不存在而失败；最小实现逐一委托原逻辑；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P5.3` 执行 R3-LOCAL。
10. **Word 检查：** WORD-SCAN；规范化 Word hash 与迁移前一致。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Extract generation quality and export services`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P5.3` 执行 B-1。
14. **部署：** D-1、D-2。
15. **健康检查：** H-1；检查 official provider、quality、render/export/download。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN。
17. **通过标准：** 调用顺序/异常/schema 差异 0；Word hash 差异 0；公共门槛通过。
18. **回退触发：** provider/gate/export 行为变化、路径绕过、Word/性能回归。
19. **回退方法：** 恢复 P5.2 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录 service contract、hash、固定 3 和回退决定，暂停。

### P5.4：Memory 与 diagnostics API 拆分

1. **目的：** 隔离 C 级偏好信息和 diagnostics，防止其跨入事实或正式正文。
2. **问题和证据：** memory/diagnostics 路由仍在 main，边界不够显式。
3. **范围：** memory router/service、diagnostics router/service 和权限复用。
4. **非范围：** 不改变 memory 内容、候选规则、事实边界或 diagnostics 算法。
5. **文件：** 新增 `app/routers/memory.py`、`app/services/memory.py`、`app/routers/diagnostics.py`、`app/services/diagnostics.py`、`tests/test_memory_service.py`、`tests/test_diagnostics_service.py`；修改 `app/main.py`、`tests/test_memory_items.py`、`tests/test_report_memory.py`。
6. **开关：** 无新增开关。
7. **数据/API/状态：** memory 明确标 level=C；diagnostics 响应不传入 FormalBody/Word renderer。
8. **TDD：** 写 route snapshot、C 级支持率 0、diagnostics 正文泄漏 0、权限测试；运行 `py -3 -m unittest tests.test_memory_service tests.test_diagnostics_service tests.test_memory_items tests.test_report_memory -v`，预期服务不存在而失败；最小实现只迁移；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P5.4` 执行 R3-LOCAL。
10. **Word 检查：** WORD-SCAN；memory/diagnostics 独有短语正文命中 0。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Separate memory and diagnostics APIs`；G-PUSH 核对 SHA。
13. **服务器备份：** `STAGE=P5.4` 执行 B-1。
14. **部署：** D-1、D-2。
15. **健康检查：** H-1；检查 memory、diagnostics、权限和 records-ui。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN。
17. **通过标准：** route/schema 差异 0；C 级事实支持率 0；diagnostics Word 泄漏 0；公共门槛通过。
18. **回退触发：** memory 事实化、diagnostics 泄漏、API/Word/性能回归。
19. **回退方法：** 恢复 P5.3 备份/SHA；H-1、R3-ONLINE、WORD-SCAN。
20. **记录和暂停：** 记录 C 级边界测试、route 对比和回退决定，暂停。

### P5.5：测试分层与自动化部署回退

1. **目的：** 固化 unit、API、真实数据门禁，并把 clean SHA 备份/部署/健康/回退做成可演练工具。
2. **问题和证据：** 当前真实数据脚本和部署步骤依赖人工编排，容易漏范围或 hash 检查。
3. **范围：** 测试 suite 分类、dulwich push helper、部署/回退工具、dry-run 和 runbook。
4. **非范围：** 不自动跳过用户确认、不把凭据写入仓库、不自动执行生产部署。
5. **文件：** 新增 `scripts/push_with_dulwich.py`、`scripts/deploy_8099.py`、`scripts/rollback_8099.py`、`tests/test_deploy_8099.py`、`tests/test_test_suites.py`、`docs/runbooks/deploy-8099.md`；修改 `scripts/run_fixed_regression.py`、`README.md`。
6. **开关：** `DEPLOY_DRY_RUN=true` 为工具默认；生产执行必须显式 `--execute --sha <远端存在SHA>`。
7. **数据/API/状态：** 状态机 verify_remote_sha→backup→upload_hash→deploy→health→fixed3→complete；任一步失败进入 rollback_required，不自动越过。
8. **TDD：** 写 SHA 不存在、脏范围、备份失败、上传 hash 错、health 失败、fixed3 失败和 rollback 失败测试；运行 `py -3 -m unittest tests.test_deploy_8099 tests.test_test_suites -v`，预期工具不存在而失败；最小实现先 dry-run；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P5.5` 执行 R3-LOCAL；工具 dry-run 读取结果但不部署。
10. **Word 检查：** WORD-SCAN；自动门必须解析四 Word 字段和禁用命中。
11. **commit 前检查：** G-STATUS；只允许第 5 项；部署凭据、压缩包、日志不提交。
12. **commit/push：** commit message `Automate verified 8099 deployment gates`；先用新 dulwich helper push，并核对远端 SHA。
13. **服务器备份：** 用工具执行 B-1 等价步骤并核对生成路径/hash。
14. **部署：** 使用 `py -3 scripts/deploy_8099.py --execute --sha <远端SHA> --stage P5.5`；工具必须从 git archive 部署。
15. **健康检查：** 工具执行 H-1 和相关 API；主线程再次手工 H-1 交叉验证。
16. **固定 3 线上：** 工具执行 R3-ONLINE 和 WORD-SCAN；主线程核对结果 JSON。
17. **通过标准：** dry-run 10/10；5 类故障注入正确停止/回退 5/5；真实部署所有状态完成；固定 3 公共门槛通过。
18. **回退触发：** 工具跳步、SHA/hash 不一致、秘密日志、health/fixed3/Word 失败。
19. **回退方法：** 执行 `scripts/rollback_8099.py --execute --backup <准确备份> --expected-sha <上一SHA>`；H-1、R3-ONLINE、WORD-SCAN；失败则停止自动化并人工恢复 B-1。
20. **记录和暂停：** 记录 dry-run、5 类故障、真实状态机、SHA 和回退决定，暂停。

### P5 大阶段门禁

- `app/main.py` 只保留 app 装配和路由注册；settings、schemas、records、evidence、runs、history、generation、quality、export、memory、diagnostics 均有明确模块。
- OpenAPI、固定 10 evidence/report/Word 规范化结果与 P4 baseline 语义差异 0。
- unit/API 全量 0 failure/0 error，固定 3 自动门和部署 dry-run 全部通过。
- 未通过禁止进入 P6。

---

## 14. P6：遗留链路删除与持续质量治理

### P6.0：遗留调用观察、版本与漂移监控

1. **目的：** 在物理删除前获得 URL/Dify 真实调用的可量化观察窗，并建立版本/质量漂移告警。
2. **问题和证据：** 软下线和 fallback=0 不等于没有隐藏依赖。
3. **范围：** legacy counters、probe 排除、run 版本、rolling 质量/性能告警。
4. **非范围：** 不删除 URL/Dify，不改变 provider 或报告。
5. **文件：** 新增 `app/legacy_usage.py`、`app/drift_monitor.py`、`tests/test_legacy_usage.py`、`tests/test_drift_monitor.py`；修改 `app/main.py`、`app/diagnostics.py`、`.env.example`、`docker-compose.yml`。
6. **开关：** `ENABLE_LEGACY_USAGE_MONITOR=true`、`ENABLE_DRIFT_MONITOR=true`。
7. **数据/API/状态：** 带 `X-8099-Probe: true` 的自动探针不计真实调用；run 记录 model/prompt/rule/compact/schema 版本；rolling window=最近 50 个正式 run。
8. **TDD：** 写 probe/真实区分、URL/Dify counter、版本缺失、rolling50 边界和告警恢复测试；运行 `py -3 -m unittest tests.test_legacy_usage tests.test_drift_monitor -v`，预期模块不存在而失败；最小实现只采集和告警；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P6.0` 执行 R3-LOCAL，标记 probe，不增加真实计数。
10. **Word 检查：** WORD-SCAN；监控和版本 diagnostics 不入 Word。
11. **commit 前检查：** G-STATUS；只允许第 5 项。
12. **commit/push：** commit message `Monitor legacy usage and quality drift`；使用 P5.5 helper push并核对 SHA。
13. **服务器备份：** 工具以 `STAGE=P6.0` 执行 B-1。
14. **部署：** 使用 P5.5 clean SHA 工具部署。
15. **健康检查：** H-1；检查 counter、版本完整率和 rolling 指标。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；probe 计数 3，真实计数增量 0。
17. **通过标准：** probe/真实分类 100%；版本完整率 100%；禁用命中>0 立即告警；rolling50 deliverable 下降>5 个百分点、unsupported 均值增加>0.5、p95 增加>20% 均准确告警。
18. **回退触发：** 真实调用误排除、probe 计入真实、版本丢失、监控影响主流程或 Word。
19. **回退方法：** 关监控开关，恢复 P5.5 备份/SHA；H-1、R3-ONLINE、WORD-SCAN；保留只读 counter 归档。
20. **记录和暂停：** 记录 counter 初值、分类测试、rolling 告警和回退决定，暂停。

### P6.1：物理删除 URL 遗留链路

1. **目的：** 在确认无真实依赖后删除 URL 分析、抓取和专用配置，保留数据库附件链。
2. **问题和证据：** `/analyze`、`/analyze_v2` 已软下线，但旧代码和依赖仍在。
3. **范围：** 删除 URL routes、URL crawler/known-site 代码、专用配置和测试；更新文档。
4. **非范围：** 不删除数据库附件 fetch/parse/cache、records-ui、prepare/run、Word 或 Dify/native。
5. **文件：** 修改 `app/main.py`、`tests/test_records_api.py`、`.env.example`、`docker-compose.yml`、`README.md`；本阶段不删除其他模块文件，URL-only 函数只从 `app/main.py` 移除。
6. **开关：** 删除 `ENABLE_URL_ANALYZE`；不新增替代开关。
7. **数据/API/状态：** `/analyze`、`/analyze_v2` 变为 404；legacy counter 归档为只读删除证据。
8. **TDD：** 先写两路由 404、URL 专用符号/配置/依赖扫描 0、数据库附件链仍工作测试；运行 `py -3 -m unittest tests.test_records_api -v`，预期当前路由仍返回软下线响应而失败；最小实现删除 URL 注册和仅有依赖；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P6.1` 执行 R3-LOCAL，数据库附件链 3/3。
10. **Word 检查：** WORD-SCAN。
11. **commit 前检查：** G-STATUS；只允许第 5 项及扫描证明 URL-only 的删除文件；依赖报告入 commit。
12. **commit/push：** commit message `Remove unused URL analysis pipeline`；dulwich helper push并核对 SHA。
13. **服务器备份：** `STAGE=P6.1` 执行 B-1，URL 观察 counter 另存 hash。
14. **部署：** clean SHA 工具部署。
15. **健康检查：** H-1；两 URL route 404；records-ui/prepare/run/pack/download 正常。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；固定 10 再运行 1 轮。
17. **通过标准：** 前置连续 720 小时非 probe URL 真实调用 0，期间数据库正式报告≥300；依赖扫描 0；固定 10 在两个不同自然日各通过 1 轮；删除后固定 3/10 全通过。
18. **回退触发：** 观察窗/样本不足、隐藏调用、数据库附件受损、固定 3/10、Word 或健康失败。
19. **回退方法：** 恢复 P6.0 备份/SHA 和软下线路由；H-1、R3-ONLINE、WORD-SCAN；重新开始 720 小时观察窗。
20. **记录和暂停：** 记录 720 小时、≥300 报告、依赖扫描、两轮固定 10 和回退决定，暂停。

### P6.2：物理删除 Dify 遗留链路

1. **目的：** 在 native-only 长期稳定后删除 Dify client、配置和依赖。
2. **问题和证据：** P2.4 只关闭 fallback，Dify 代码仍是可快速恢复的安全垫。
3. **范围：** 删除 Dify generator、路由分支、环境配置、依赖和专用测试；更新文档。
4. **非范围：** 不改变 native prompt/model/质量门禁，不删除历史 run 中的 provider=dify 记录。
5. **文件：** 删除 `app/generation/dify_generator.py`；修改 `app/generation/__init__.py`、`app/provider_router.py`、`app/main.py`、`.env.example`、`docker-compose.yml`、`README.md`、`tests/test_generation_pipeline.py`、`tests/test_provider_router.py`。
6. **开关：** 删除 Dify fallback/provider 配置；正式 provider 固定 native。
7. **数据/API/状态：** 历史 Dify metadata 继续只读；新 run 不再包含 Dify call/fallback 分支。
8. **TDD：** 写 import/配置/路由 Dify 符号扫描 0、旧 run 可读、native 失败状态和固定回归；运行 `py -3 -m unittest tests.test_generation_pipeline tests.test_provider_router -v`，预期当前仍有 Dify 路径而失败；最小实现删除 Dify-only 分支；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P6.2` 执行 R3-LOCAL。
10. **Word 检查：** WORD-SCAN。
11. **commit 前检查：** G-STATUS；只允许第 5 项及 Dify-only 删除文件；依赖扫描报告入 commit。
12. **commit/push：** commit message `Remove retired Dify generation pipeline`；dulwich helper push并核对 SHA。
13. **服务器备份：** `STAGE=P6.2` 执行 B-1，Dify 观察 counter 另存 hash。
14. **部署：** clean SHA 工具部署。
15. **健康检查：** H-1；native run、quality、export、history 正常；Dify 配置引用 0。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；固定 10 再运行 1 轮。
17. **通过标准：** 前置 native official 且 fallback 已关闭；连续 720 小时 Dify 真实调用 0；期间 native 正式 run≥500；固定 10 在三个不同自然日各通过 1 轮；事故 0；删除后固定 3/10 通过。
18. **回退触发：** 观察窗/样本不足、隐藏 Dify 依赖、native/固定 3/10/Word/健康失败。
19. **回退方法：** 恢复 P6.1 备份/SHA 和 Dify fallback 能力，先启用 fallback；H-1、R3-ONLINE、WORD-SCAN；重新开始 720 小时观察窗。
20. **记录和暂停：** 记录 720 小时、≥500 run、三轮固定 10、依赖扫描和回退决定，暂停。

### P6.3：持续质量治理与故障手册

1. **目的：** 把固定回归、版本漂移、告警处置和回退矩阵变成持续运行制度。
2. **问题和证据：** 物理删除完成后仍可能发生模型、prompt、规则、compact 或性能漂移。
3. **范围：** 周期任务、告警、版本矩阵、故障处置和演练记录。
4. **非范围：** 不自动升级模型/prompt/规则，不绕过用户确认或门禁。
5. **文件：** 新增 `docs/runbooks/quality-and-rollback.md`、`scripts/scheduled_regression.py`、`tests/test_scheduled_regression.py`；修改 `app/drift_monitor.py`、`README.md`。
6. **开关：** `ENABLE_SCHEDULED_REGRESSION=false`，确认调度环境后设 true。
7. **数据/API/状态：** 固定 3 每 7×24 小时，固定 10 每 30×24 小时；每次保存 manifest/version/quality/performance 和告警状态。
8. **TDD：** 写时间边界、漏跑、重复、manifest 漂移、告警和回退矩阵测试；运行 `py -3 -m unittest tests.test_scheduled_regression tests.test_drift_monitor -v`，预期调度器不存在而失败；最小实现只生成任务和结果，不自动改配置；定向后 T-FULL-WIN。
9. **固定 3 本地：** `STAGE=P6.3` 执行 R3-LOCAL，并模拟两个周期。
10. **Word 检查：** WORD-SCAN；调度 diagnostics 不入 Word。
11. **commit 前检查：** G-STATUS；只允许第 5 项，周期运行工件不提交。
12. **commit/push：** commit message `Establish continuous report quality governance`；dulwich helper push并核对 SHA。
13. **服务器备份：** `STAGE=P6.3` 执行 B-1。
14. **部署：** clean SHA 工具部署；先 scheduled=false 验证，再 true。
15. **健康检查：** H-1；检查 last_run、next_run、alert 和 version matrix。
16. **固定 3 线上：** R3-ONLINE、WORD-SCAN；手动触发一次周期任务。
17. **通过标准：** 固定 3 调度间隔 7 天、固定 10 间隔 30 天；连续 2 个应执行周期未完成即告警；P6.0 三项漂移阈值准确；固定 3/10 结果可重放。
18. **回退触发：** 重复任务、漏跑不告警、自动改配置、版本错配、Word/质量/健康回归。
19. **回退方法：** scheduled=false，恢复 P6.2 备份/SHA；H-1、R3-ONLINE、WORD-SCAN；保留运行结果归档。
20. **记录和暂停：** 记录周期模拟、告警、版本矩阵、演练和回退决定，暂停。

### P6 大阶段门禁

- URL 与 Dify 物理删除均分别满足各自 720 小时、运行数、固定 10 轮次和 0 事故门槛。
- 固定 3 周期、固定 10 周期、漂移告警和回退手册可执行。
- 正式载体禁用表达持续为 0；memory/C 级事实支持率持续为 0。

---

## 15. 两轮审阅修正闭环

### 第一轮独立审阅已处理

- 新固定 10 不再直接复用旧 unsupported 数值；P0.2-b2.5 重新冻结 B，并用同一离线 evaluator 重算。
- 依赖顺序改为 canonical evidence → PDF/图片 A 级表格 → VBP B 级事实 → compact → 报告规则 →专项门禁。
- Shadow 前补齐 telemetry；灰度前补齐 durable jobs、幂等、heartbeat 和恢复。
- history 限定单 worker；revision、event_id、fsync、atomic index、分页和 CLI rebuild 均明确。
- 部署来源改为已推送 SHA 的 clean archive，并核对本地、远端和服务器 SHA。

### 第二轮独立审阅已处理

- 每个未来子阶段均展开 1-20，不再以一句“遵循公共流程”代替阶段参数。
- 增加 P0.2-b1.4 Word 熔断；P0.2-b1.5 无 bypass，回退只能回到 Word 禁止发布状态。
- 最小 FormalBody schema 和遍历契约提前到 P0.2-b1.5，P1.0 只做模块化和可比较 ReportIR。
- b2.5 增加 evaluator 版本、rules hash、可重放正文/证据快照；P0 门槛使用同一算法。
- 性能基线修正为本地 9 + 线上 9 = 18；性能优化使用关闭/开启配对共 36 次。
- durable jobs 和恢复移到 Shadow 观察之前；168 小时窗口从可靠版本部署后重新起算。
- 路由使用 canonical JSON+UTF-8，定义同日去重、唯一材料数和失败/超时/fallback 分母。
- FormalBody 增加 NFKC、跨 run、嵌套表格、临时 DOCX、原子发布；source hash 和索引基数规则固定。
- SQLite 多 worker 条件只触发离线 benchmark；生产在迁移决策前保持单 worker 文件写。

两轮审阅意见均已接受，没有保留未解决的 Critical 或 Important。未采纳的做法只有“直接关闭正文门禁作为回退”，因为它违反正式报告的不可变安全约束；最终使用 Word fail-closed 熔断替代。

## 16. 写入后与每次实施前自检

1. 重新读取本计划，确认 P0-P6、当前执行点和编号映射一致。
2. 搜索并消除未完成占位标记和不可计算验收语句。
3. 检查计划和提交中没有 API key、密码、Cookie、数据库凭据值或认证 token。
4. 检查固定 3 的三个 `menu_code + articleid` 与标题准确，固定 10 未被替换。
5. 检查每个执行卡恰有 1-20，包含 RED 命令、最小实现、固定 3、Word、Git、push、备份、部署、健康、线上验证、数值门槛、回退和暂停。
6. 检查当前 provider 和开关边界：P0 Dify、P1 shadow、P2 灰度、P6 才删除。
7. 检查 `memory`/`memory_items` 始终为 C 级，不能支持事实。
8. 检查 `FormalBodySafetyGate` 没有生产 bypass，后续回退不跨越 P0.2-b1.5 安全底线。
9. 执行 `git status --short`，确认实施提交只含该阶段第 5 项文件，既存无关改动保持不变。
10. 每个子阶段完成后更新优化记录并暂停；没有用户确认不得进入下一阶段。

## 17. 当前下一步

本计划写入后不执行任何代码、Git、推送或部署。等待用户确认后，从 **P0.2-b1.4：Word 发布安全熔断** 开始，严格按该执行卡实施。
