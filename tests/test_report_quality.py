from __future__ import annotations

import json
import unittest

from app.main import (
    CheckedExportReportRequest,
    ReportIR,
    ReportQAParseRequest,
    ReportSection,
    ReportTable,
    _normalize_report_ir,
    _quality_check_report_against_evidence,
    export_report_checked,
    parse_report_qa,
)


def rich_procurement_evidence() -> str:
    return """
# 网页正文
标题: 关于国家组织冠脉支架集中带量采购第二轮接续采购文件的公告
URL: https://example.test/notice
国家组织高值医用耗材联合采购办公室发布《“国家组织冠脉支架集中带量采购”第二轮接续采购文件》。
采购品种范围包括冠状动脉药物洗脱合金支架和冠状动脉药物洗脱不锈钢支架。
采购周期自中选结果实际执行日起至2029年6月30日。
企业报价要求包括最高有效申报价、询价基准、有效报价、拟中选产品确定和采购执行。
最高有效申报价：冠状动脉药物洗脱合金支架949元/个，冠状动脉药物洗脱不锈钢支架848元/个。
询价基准：冠状动脉药物洗脱合金支架848元/个，冠状动脉药物洗脱不锈钢支架757元/个。
非中选产品管理、价格联动、名词解释、信用评价、失信约束、取消中选资格均在采购文件中列明。

# 附件
文件名: 采购文件.docx
采购品种 | 最高有效申报价 | 询价基准
冠状动脉药物洗脱合金支架 | 949元/个 | 848元/个
冠状动脉药物洗脱不锈钢支架 | 848元/个 | 757元/个
"""


def short_procurement_report() -> ReportIR:
    return ReportIR(
        title="国采冠脉支架第二轮接续采购启动",
        notice_type="集采/接续采购类",
        lead_paragraphs=[
            "国家组织高值医用耗材联合采购办公室发布冠脉支架第二轮接续采购文件，企业需关注报价规则。"
        ],
        sections=[
            ReportSection(
                heading="报价规则",
                paragraphs=["产品申报价格不超过最高有效申报价的，可获得中选资格。"],
            )
        ],
        enterprise_tips=["企业需关注报价上限。"],
    )


class LocalReportQualityTests(unittest.TestCase):
    def test_rich_procurement_evidence_blocks_short_summary_report(self) -> None:
        qa = _quality_check_report_against_evidence(
            short_procurement_report(),
            rich_procurement_evidence(),
            history_text="",
        )

        self.assertEqual(qa.status, "needs_fix")
        self.assertTrue(any(issue.category == "report_depth" for issue in qa.issues))
        self.assertTrue(any("补充" in item for item in qa.fix_instructions))
        self.assertTrue(any("1800" in item for item in qa.fix_instructions))

    def test_short_process_notice_uses_medium_depth_not_rich_procurement_threshold(self) -> None:
        report = ReportIR(
            title="河南调整医用耗材申报挂网操作流程",
            notice_type="挂网流程调整类",
            lead_paragraphs=["河南调整申报入口，企业需按通知要求办理。"],
        )
        evidence = (
            "河南省发布《关于调整医用耗材申报挂网操作流程的通知》，"
            "自2026年6月1日起通过全国联审通办提交申报。"
            "相关医用耗材生产经营企业需按照调整后的入口和操作流程办理。"
        )

        qa = _quality_check_report_against_evidence(report, evidence, history_text="")

        self.assertEqual(qa.status, "needs_fix")
        self.assertTrue(any(issue.category == "process_notice_depth" for issue in qa.issues))
        self.assertFalse(any("1800" in issue.fix_instruction for issue in qa.issues))
        self.assertTrue(any("调整内容" in item for item in qa.fix_instructions))

    def test_short_process_notice_with_required_analysis_passes_medium_depth_check(self) -> None:
        report = ReportIR(
            title="河南调整医用耗材申报挂网操作流程",
            notice_type="挂网流程调整类",
            lead_paragraphs=[
                "河南省调整医用耗材申报挂网操作流程，企业后续申报需关注入口变化和材料提交口径。"
            ],
            sections=[
                ReportSection(
                    heading="调整内容",
                    paragraphs=[
                        "通知明确医用耗材申报挂网操作流程发生调整，企业需通过全国联审通办路径提交申报。"
                    ],
                ),
                ReportSection(
                    heading="影响分析",
                    paragraphs=[
                        "本次调整主要影响企业申报入口、操作路径和内部申报准备节奏，不改变企业仍需按平台要求提交真实材料的基本要求。"
                    ],
                ),
                ReportSection(
                    heading="企业关注点",
                    paragraphs=[
                        "企业应提前核对账号权限、申报资料和产品信息，避免因入口切换或材料准备不足影响挂网进度。"
                    ],
                ),
            ],
            enterprise_tips=["关注申报入口调整。", "提前核对产品申报材料。"],
        )
        evidence = (
            "河南省发布《关于调整医用耗材申报挂网操作流程的通知》，"
            "自2026年6月1日起通过全国联审通办提交申报。"
            "相关医用耗材生产经营企业需按照调整后的入口和操作流程办理。"
        )

        qa = _quality_check_report_against_evidence(report, evidence, history_text="")

        self.assertFalse(any(issue.category == "process_notice_depth" for issue in qa.issues))

    def test_table_value_absent_from_evidence_is_unsupported(self) -> None:
        report = ReportIR(
            title="国采冠脉支架第二轮接续采购启动",
            notice_type="集采/接续采购类",
            lead_paragraphs=["采购文件列明最高有效申报价。"],
            sections=[
                ReportSection(
                    heading="价格规则",
                    tables=[
                        ReportTable(
                            title="最高有效申报价表",
                            headers=["品种", "最高有效申报价"],
                            rows=[["冠状动脉药物洗脱合金支架", "999元/个"]],
                        )
                    ],
                )
            ],
        )

        qa = _quality_check_report_against_evidence(report, rich_procurement_evidence(), history_text="")

        self.assertEqual(qa.status, "needs_fix")
        self.assertTrue(any("999元/个" in issue.report_text for issue in qa.unsupported_claims))

    def test_table_value_present_in_evidence_passes_marker_check(self) -> None:
        report = ReportIR(
            title="国采冠脉支架第二轮接续采购启动",
            notice_type="集采/接续采购类",
            lead_paragraphs=[
                "国家组织高值医用耗材联合采购办公室发布采购文件，采购品种为冠状动脉药物洗脱支架。",
                "采购文件列明最高有效申报价、询价基准、拟中选产品确定和采购执行要求。",
            ],
            sections=[
                ReportSection(
                    heading="价格规则",
                    paragraphs=["采购文件列明不同采购品种的最高有效申报价和询价基准。"],
                    tables=[
                        ReportTable(
                            title="最高有效申报价表",
                            headers=["品种", "最高有效申报价", "询价基准"],
                            rows=[["冠状动脉药物洗脱合金支架", "949元/个", "848元/个"]],
                        )
                    ],
                ),
                ReportSection(heading="采购执行", paragraphs=["采购执行以正式文件要求为准。"]),
                ReportSection(heading="非中选产品管理", paragraphs=["采购文件列明非中选产品管理要求。"]),
                ReportSection(heading="名词解释", paragraphs=["采购文件列明相关名词解释。"]),
            ],
        )

        qa = _quality_check_report_against_evidence(report, rich_procurement_evidence(), history_text="")

        self.assertFalse(any("949元/个" in issue.report_text for issue in qa.unsupported_claims))

    def test_calculated_percentage_with_explicit_basis_is_not_unsupported(self) -> None:
        report = ReportIR(
            title="广州医用耗材价格联动信息公示",
            notice_type="价格联动/价格治理类",
            lead_paragraphs=["广州平台公示部分产品价格联动信息。"],
            sections=[
                ReportSection(
                    heading="价格联动信息来源类型分布",
                    paragraphs=["按表内数据测算，总计78条记录。"],
                    tables=[
                        ReportTable(
                            title="价格联动信息来源类型分布",
                            headers=["联动信息类型", "产品记录数", "占比"],
                            rows=[
                                ["省级挂网价", "45", "57.69%"],
                                ["市级参考价", "32", "41.03%"],
                                ["中选价", "1", "1.28%"],
                            ],
                        )
                    ],
                )
            ],
        )
        evidence = "附件表格列出78条记录，其中省级挂网价45条，市级参考价32条，中选价1条。"

        qa = _quality_check_report_against_evidence(report, evidence, history_text="")

        self.assertFalse(any("57.69%" in issue.report_text for issue in qa.unsupported_claims))
        self.assertFalse(any("41.03%" in issue.report_text for issue in qa.unsupported_claims))
        self.assertFalse(any("1.28%" in issue.report_text for issue in qa.unsupported_claims))

    def test_percentage_without_calculation_basis_is_still_unsupported(self) -> None:
        report = ReportIR(
            title="广州医用耗材价格联动信息公示",
            notice_type="价格联动/价格治理类",
            lead_paragraphs=["广州平台公示部分产品价格联动信息。"],
            sections=[
                ReportSection(
                    heading="价格联动信息来源类型分布",
                    tables=[
                        ReportTable(
                            title="价格联动信息来源类型分布",
                            headers=["联动信息类型", "占比"],
                            rows=[["省级挂网价", "57.69%"]],
                        )
                    ],
                )
            ],
        )
        evidence = "附件表格列出省级挂网价、市级参考价和中选价记录，但未披露百分比。"

        qa = _quality_check_report_against_evidence(report, evidence, history_text="")

        self.assertTrue(any("57.69%" in issue.report_text for issue in qa.unsupported_claims))

    def test_parse_report_qa_suppresses_model_false_positive_for_calculated_percentage(self) -> None:
        report = ReportIR(
            title="广州医用耗材价格联动信息公示",
            sections=[
                ReportSection(
                    heading="价格联动信息来源类型分布",
                    paragraphs=["按表内数据测算，总计78条记录。"],
                    tables=[
                        ReportTable(
                            title="价格联动信息来源类型分布",
                            headers=["联动信息类型", "产品记录数", "占比"],
                            rows=[["省级挂网价", "45", "57.69%"]],
                        )
                    ],
                )
            ],
        )
        qa_output = json.dumps(
            {
                "status": "needs_fix",
                "issues": [],
                "unsupported_claims": [
                    {
                        "severity": "major",
                        "category": "unsupported_claims",
                        "report_text": "价格联动信息来源类型分布: 57.69%",
                        "source_quote": "",
                        "fix_instruction": "核对表格中的“57.69%”，删除无证据数值或改为原文披露内容。",
                    }
                ],
                "history_leakage": [],
                "missing_rules": [],
                "language_issues": [],
                "fix_instructions": ["核对表格中的“57.69%”，删除无证据数值或改为原文披露内容。"],
                "summary": "百分比未逐字披露。",
            },
            ensure_ascii=False,
        )

        response = parse_report_qa(
            ReportQAParseRequest(
                qa_output=qa_output,
                report_ir=report,
                evidence_text="附件表格列出78条记录，其中省级挂网价45条。",
            )
        )

        self.assertEqual(response.qa.status, "pass")
        self.assertFalse(response.qa.unsupported_claims)
        self.assertFalse(response.blocked)

    def test_parse_report_qa_keeps_model_percentage_issue_without_calculation_basis(self) -> None:
        report = ReportIR(
            title="广州医用耗材价格联动信息公示",
            sections=[
                ReportSection(
                    heading="价格联动信息来源类型分布",
                    tables=[
                        ReportTable(
                            title="价格联动信息来源类型分布",
                            headers=["联动信息类型", "占比"],
                            rows=[["省级挂网价", "57.69%"]],
                        )
                    ],
                )
            ],
        )
        qa_output = json.dumps(
            {
                "status": "needs_fix",
                "issues": [],
                "unsupported_claims": [
                    {
                        "severity": "major",
                        "category": "unsupported_claims",
                        "report_text": "价格联动信息来源类型分布: 57.69%",
                        "source_quote": "",
                        "fix_instruction": "核对表格中的“57.69%”，删除无证据数值或改为原文披露内容。",
                    }
                ],
                "history_leakage": [],
                "missing_rules": [],
                "language_issues": [],
                "fix_instructions": ["核对表格中的“57.69%”，删除无证据数值或改为原文披露内容。"],
                "summary": "百分比未逐字披露。",
            },
            ensure_ascii=False,
        )

        response = parse_report_qa(
            ReportQAParseRequest(
                qa_output=qa_output,
                report_ir=report,
                evidence_text="附件表格列出省级挂网价记录，但未披露百分比。",
            )
        )

        self.assertEqual(response.qa.status, "needs_fix")
        self.assertTrue(any("57.69%" in issue.report_text for issue in response.qa.unsupported_claims))

    def test_json_analyze_evidence_uses_evidence_for_llm_for_table_marker_check(self) -> None:
        report = ReportIR(
            title="广州医用耗材价格联动信息公示",
            notice_type="价格联动/价格治理类",
            lead_paragraphs=["广州平台公示部分产品价格联动信息。"],
            sections=[
                ReportSection(
                    heading="关键执行要素",
                    tables=[
                        ReportTable(
                            title="关键执行要素",
                            headers=["事项", "要求"],
                            rows=[["公示内容", "2026年2月1日"]],
                        )
                    ],
                )
            ],
        )
        evidence = {
            "title": "关于公示部分医用耗材价格联动信息的通知",
            "page_text": "2026\\n年2月1日-2026年4月31日24时在广州平台进行价格联动申报。",
            "evidence_for_llm": "公示内容：2026\\n年2月1日-2026年4月31日24时在广州平台进行价格联动申报。",
        }

        qa = _quality_check_report_against_evidence(report, json.dumps(evidence, ensure_ascii=False), history_text="")

        self.assertFalse(any("2026年2月1日" in issue.report_text for issue in qa.unsupported_claims))

    def test_json_analyze_evidence_still_flags_truly_absent_table_marker(self) -> None:
        report = ReportIR(
            title="广州医用耗材价格联动信息公示",
            notice_type="价格联动/价格治理类",
            lead_paragraphs=["广州平台公示部分产品价格联动信息。"],
            sections=[
                ReportSection(
                    heading="关键执行要素",
                    tables=[
                        ReportTable(
                            title="关键执行要素",
                            headers=["事项", "要求"],
                            rows=[["公示内容", "2099年1月1日"]],
                        )
                    ],
                )
            ],
        )
        evidence = {
            "evidence_for_llm": "公示内容：2026\\n年2月1日-2026年4月31日24时在广州平台进行价格联动申报。",
        }

        qa = _quality_check_report_against_evidence(report, json.dumps(evidence, ensure_ascii=False), history_text="")

        self.assertEqual(qa.status, "needs_fix")
        self.assertTrue(any("2099年1月1日" in issue.report_text for issue in qa.unsupported_claims))

    def test_bare_count_marker_does_not_create_repeated_table_false_positive(self) -> None:
        report = ReportIR(
            title="吉林省体外诊断试剂挂网产品补充三省价格通知",
            notice_type="挂网价格治理类",
            lead_paragraphs=["吉林省要求体外诊断试剂挂网产品补充三省挂网价格。"],
            sections=[
                ReportSection(
                    heading="关键执行要求与后果",
                    paragraphs=["企业需在过渡期内完成价格补充。"],
                    tables=[
                        ReportTable(
                            title="关键执行要求与后果",
                            headers=["事项", "要求", "后果"],
                            rows=[
                                [
                                    "价格补充",
                                    "自2026年4月1日起设置2个月过渡期，每月1-10日可操作",
                                    "2026年6月1日起暂停挂网",
                                ]
                            ],
                        )
                    ],
                ),
                ReportSection(heading="企业操作建议", paragraphs=["企业需按月核对价格资料。"]),
                ReportSection(heading="影响分析", paragraphs=["未按期补充可能影响挂网状态。"]),
                ReportSection(heading="规则边界", paragraphs=["信用评价按原文执行。"]),
            ],
        )
        evidence = (
            "自2026年4月1日起设置两个月过渡期，企业可在每月1-10日进行价格补充；"
            "相关产品将于2026年6月1日起暂停挂网采购。"
        )

        qa = _quality_check_report_against_evidence(report, evidence, history_text="")

        self.assertFalse(any(issue.category == "table_evidence_mismatch" for issue in qa.unsupported_claims))

    def test_crawl_insufficient_marker_is_block_not_repairable(self) -> None:
        report = ReportIR(
            title="贵州医疗保障公共服务平台公告分析",
            lead_paragraphs=["当前页面未提取到公告正文。"],
        )
        evidence = "抓取状态: login_required\n公司站点需要登录态，未能取得正文。"

        qa = _quality_check_report_against_evidence(report, evidence, history_text="")

        self.assertEqual(qa.status, "block")
        self.assertTrue(any(issue.category == "crawl_insufficient" for issue in qa.issues))

    def test_integrated_history_connection_passes_when_no_history_fact_is_copied(self) -> None:
        report = ReportIR(
            title="山东三类耗材接续采购启动",
            notice_type="集采/接续采购类",
            lead_paragraphs=[
                "山东省公共资源交易中心发布接续采购文件，企业需按申报要求参与。结合既往同类项目经验，企业仍需关注申报节奏和价格口径衔接。"
            ],
            sections=[ReportSection(heading="企业报价", paragraphs=["企业报价不得高于文件列明的相关价格口径。"])],
        )
        history = "历史稿显示：采购周期为3年，最高有效申报价为128元。"
        evidence = "山东省发布接续采购文件，企业需按申报要求参与，公告未披露历史稿价格。"

        qa = _quality_check_report_against_evidence(report, evidence, history_text=history)

        self.assertFalse(qa.history_leakage)

    def test_history_heading_and_copied_price_are_flagged(self) -> None:
        report = ReportIR(
            title="山东三类耗材接续采购启动",
            notice_type="集采/接续采购类",
            lead_paragraphs=["山东省发布接续采购文件。"],
            sections=[
                ReportSection(
                    heading="历史分析",
                    paragraphs=["结合历史项目，本次最高有效申报价为128元。"],
                )
            ],
        )
        history = "历史稿显示：最高有效申报价为128元。"
        evidence = "山东省发布接续采购文件，本次公告未披露最高有效申报价。"

        qa = _quality_check_report_against_evidence(report, evidence, history_text=history)

        self.assertEqual(qa.status, "needs_fix")
        self.assertTrue(any(issue.category == "history_heading" for issue in qa.history_leakage))
        self.assertTrue(any("128元" in issue.report_text for issue in qa.history_leakage))

    def test_parse_report_qa_merges_local_quality_issues(self) -> None:
        response = parse_report_qa(
            ReportQAParseRequest(
                qa_output='{"status":"pass","issues":[],"unsupported_claims":[],"history_leakage":[],"missing_rules":[],"language_issues":[],"fix_instructions":[],"summary":"模型认为通过"}',
                report_text="",
                report_ir=short_procurement_report(),
                evidence_text=rich_procurement_evidence(),
                history_text="",
            )
        )

        self.assertFalse(response.blocked)
        self.assertTrue(response.needs_fix)
        self.assertEqual(response.qa.status, "needs_fix")
        self.assertIn("report_depth", response.qa_summary)

    def test_parse_report_qa_returns_summary_for_invalid_model_output(self) -> None:
        response = parse_report_qa(
            ReportQAParseRequest(
                qa_output="<think>debug</think>{not json",
                report_ir=short_procurement_report(),
                evidence_text=rich_procurement_evidence(),
                history_text="",
            )
        )

        self.assertTrue(response.blocked)
        self.assertEqual(response.qa.status, "block")
        self.assertIn("block", response.qa_summary)
        self.assertIn("质检 JSON 解析失败", response.qa_summary)


    def test_parse_report_qa_prioritizes_crawl_block_over_model_needs_fix(self) -> None:
        response = parse_report_qa(
            ReportQAParseRequest(
                qa_output=json.dumps(
                    {
                        "status": "needs_fix",
                        "issues": [],
                        "unsupported_claims": [],
                        "history_leakage": [],
                        "missing_rules": [],
                        "language_issues": [],
                        "fix_instructions": ["补充正文深度"],
                        "summary": "模型建议补充正文。",
                    },
                    ensure_ascii=False,
                ),
                report_ir=ReportIR(title="甘肃公告分析", lead_paragraphs=["原始公告网页抓取失败。"]),
                evidence_text="抓取状态: crawl_insufficient\n公告网页抓取失败。",
                history_text="",
            )
        )

        self.assertTrue(response.blocked)
        self.assertEqual(response.qa.status, "block")
        self.assertIn("质检状态：block", response.qa_summary)
        self.assertIn("当前链接未取得可用于正式分析", response.qa_summary)

    def test_export_checked_reports_local_unsupported_table_value_without_blocking_download(self) -> None:
        report = ReportIR(
            title="国采冠脉支架第二轮接续采购启动",
            notice_type="集采/接续采购类",
            lead_paragraphs=["采购文件列明最高有效申报价。"],
            sections=[
                ReportSection(
                    heading="价格规则",
                    tables=[
                        ReportTable(
                            title="最高有效申报价表",
                            headers=["品种", "最高有效申报价"],
                            rows=[["冠状动脉药物洗脱合金支架", "999元/个"]],
                        )
                    ],
                )
            ],
        )

        response = export_report_checked(
            CheckedExportReportRequest(
                report_ir=report,
                qa_output='{"status":"pass","issues":[],"unsupported_claims":[],"history_leakage":[],"missing_rules":[],"language_issues":[],"fix_instructions":[],"summary":"模型认为通过"}',
                report_text="",
                evidence_text=rich_procurement_evidence(),
                history_text="",
            )
        )

        self.assertFalse(response.success)
        self.assertTrue(response.blocked)
        self.assertEqual("", response.download_url)
        self.assertIn("999元/个", response.qa_summary)

    def test_glossary_terms_are_numbered_and_split_without_changing_other_sections(self) -> None:
        report = ReportIR(
            title="广东麻醉管路带量联动采购分析",
            lead_paragraphs=["广东省启动麻醉管路等三类耗材带量联动采购。"],
            sections=[
                ReportSection(
                    heading="价格联动",
                    paragraphs=["价格联动规则保持原文表述。"],
                ),
                ReportSection(
                    heading="名词解释",
                    paragraphs=[
                        "文件对核心价格与产品概念进行了明确定义。带量最低价指全国现行省级集中带量采购最低中选价格；报价单元内存在多个不同价格的，按就低原则取值。非带量最低价指全国省级最低挂网价格和广东省最低价两者低值。中选产品是指获得中选资格的产品。非中选产品是指属于本次采购范围内但未获得中选资格的产品。"
                    ],
                ),
            ],
        )

        normalized = _normalize_report_ir(report, fallback_title="")

        self.assertEqual(normalized.sections[0].paragraphs, ["价格联动规则保持原文表述。"])
        glossary = normalized.sections[1].paragraphs
        self.assertIn("文件对核心价格与产品概念进行了明确定义。", glossary)
        self.assertIn("1. 带量最低价：指全国现行省级集中带量采购最低中选价格；报价单元内存在多个不同价格的，按就低原则取值。", glossary)
        self.assertIn("2. 非带量最低价：指全国省级最低挂网价格和广东省最低价两者低值。", glossary)
        self.assertIn("3. 中选产品：是指获得中选资格的产品。", glossary)
        self.assertIn("4. 非中选产品：是指属于本次采购范围内但未获得中选资格的产品。", glossary)

    def test_table_price_with_unit_in_header_matches_numeric_evidence_cell(self) -> None:
        report = ReportIR(
            title="兵团医用耗材阳光挂网价格联动",
            notice_type="价格联动/价格治理类",
            lead_paragraphs=["兵团开展医用耗材阳光挂网价格联动。"],
            sections=[
                ReportSection(
                    heading="价格差异示例",
                    tables=[
                        ReportTable(
                            title="部分联动产品价格差异示例（按附件数据整理）",
                            headers=["耗材名称", "兵团挂网价格（元）", "全国较低价格（元）"],
                            rows=[["一次性使用细胞刷", "58元", "47元"]],
                        )
                    ],
                )
            ],
        )
        evidence = """
# 附件
文件名: 兵团招采子系统阳光挂网医用耗材价格高于自治区平台产品汇总表.xlsx
| 耗材名称 | 兵团挂网价格（元） | 全国较低价格（元） |
| 一次性使用细胞刷 | 58 | 47 |
"""

        qa = _quality_check_report_against_evidence(report, evidence, history_text="")

        self.assertFalse(any(issue.category == "table_evidence_mismatch" for issue in qa.unsupported_claims))

    def test_table_price_with_unit_still_fails_when_same_row_evidence_lacks_value(self) -> None:
        report = ReportIR(
            title="兵团医用耗材阳光挂网价格联动",
            notice_type="价格联动/价格治理类",
            lead_paragraphs=["兵团开展医用耗材阳光挂网价格联动。"],
            sections=[
                ReportSection(
                    heading="价格差异示例",
                    tables=[
                        ReportTable(
                            title="部分联动产品价格差异示例（按附件数据整理）",
                            headers=["耗材名称", "兵团挂网价格（元）", "全国较低价格（元）"],
                            rows=[["一次性使用细胞刷", "58元", "47元"]],
                        )
                    ],
                )
            ],
        )
        evidence = """
# 附件
| 耗材名称 | 兵团挂网价格（元） | 全国较低价格（元） |
| 一次性使用细胞刷 | 59 | 46 |
"""

        qa = _quality_check_report_against_evidence(report, evidence, history_text="")

        self.assertEqual(qa.status, "needs_fix")
        self.assertTrue(any("58元" in issue.report_text for issue in qa.unsupported_claims))
        self.assertTrue(any("47元" in issue.report_text for issue in qa.unsupported_claims))

    def test_multi_model_price_summary_requires_both_values_in_same_product_context(self) -> None:
        report = ReportIR(
            title="兵团医用耗材阳光挂网价格联动",
            notice_type="价格联动/价格治理类",
            lead_paragraphs=["兵团开展医用耗材阳光挂网价格联动。"],
            sections=[
                ReportSection(
                    heading="价格差异示例",
                    tables=[
                        ReportTable(
                            title="部分联动产品价格差异示例（按附件数据整理）",
                            headers=["耗材名称", "兵团挂网价格（元）", "全国较低价格（元）"],
                            rows=[["一次性使用细胞刷", "58元或65元（依型号而定）", "47元"]],
                        )
                    ],
                )
            ],
        )
        evidence = """
# 附件
| 耗材名称 | 规格型号 | 兵团挂网价格（元） | 全国较低价格（元） |
| 一次性使用细胞刷 | A型 | 58 | 47 |
| 一次性使用细胞刷 | B型 | 65 | 47 |
"""

        qa = _quality_check_report_against_evidence(report, evidence, history_text="")

        self.assertFalse(any(issue.category == "table_evidence_mismatch" for issue in qa.unsupported_claims))


if __name__ == "__main__":
    unittest.main()
