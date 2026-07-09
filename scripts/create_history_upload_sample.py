from __future__ import annotations

import argparse
import html
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


DEFAULT_OUTPUT = Path("reports") / "广东麻醉管路等三类耗材带量联动采购串联分析测试材料.docx"

PARAGRAPHS = [
    "广东麻醉管路等三类耗材带量联动采购串联分析材料",
    "材料用途",
    "本材料用于测试 Dify Chatflow 的可选历史 Word 上传功能。内容仅作为关联项目、写作风格和企业关注点参考，不应被写成本次公告事实来源。",
    "关联项目概况",
    "2026年5月31日，深圳公共资源交易中心发布广东省麻醉管路、一次性使用有创血压传感器、医用高分子夹板三类医用耗材带量联动采购公告，采购文件编号为 SZGGZYHCDL202601。",
    "该公告同时附带广东省血流导向密网支架类医用耗材带量联动采购文件，采购文件编号为 SZGGZYHCDL202501。两个项目均由深圳公共资源交易中心组织，均服务于广东省医用耗材带量联动采购规则体系。",
    "串联分析要点",
    "两份文件体现出同一地区在不同耗材品类上延续带量联动、价格联动和协议量管理的政策框架。麻醉管路等三类项目产品分类更细，按采购品种、类别、注册证或规格型号设置报价单元；密网支架项目品种更集中，不设产品分类，最高有效申报价 P0 为 68999 元/根。",
    "从企业应对角度看，两类项目都要求企业同时关注申报价、历史价格、后续省级或省际联盟更低中选价以及不接受价格联动的影响。差异在于，麻醉管路等三类项目需要分别处理带量最低价、非带量最低价、同类中位数和参考价等多重口径，密网支架则更适合围绕单一品种的报价上限和价格联动义务进行测算。",
    "上传测试边界",
    "Chatflow 使用本材料时，应只提炼项目延续性、写作风格和企业关注点。不得把本材料中的 P0、采购文件编号、产品范围、采购周期或采购量写成另一份公告的事实。若当前公告未披露相关信息，应以当前公告正文和附件为准。",
]


CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

ROOT_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""


def _paragraph_xml(text: str, *, heading: bool = False) -> str:
    escaped = html.escape(text)
    if heading:
        return (
            "<w:p><w:pPr><w:pStyle w:val=\"Heading1\"/></w:pPr>"
            "<w:r><w:rPr><w:b/><w:sz w:val=\"28\"/></w:rPr>"
            f"<w:t>{escaped}</w:t></w:r></w:p>"
        )
    return (
        "<w:p><w:r><w:rPr><w:sz w:val=\"24\"/></w:rPr>"
        f"<w:t>{escaped}</w:t></w:r></w:p>"
    )


def _document_xml() -> str:
    body = []
    for index, paragraph in enumerate(PARAGRAPHS):
        body.append(_paragraph_xml(paragraph, heading=index == 0 or paragraph in {"材料用途", "关联项目概况", "串联分析要点", "上传测试边界"}))
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        "<w:body>"
        + "".join(body)
        + "<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/><w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\"/></w:sectPr>"
        "</w:body></w:document>"
    )


def write_history_upload_sample(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        zf.writestr("_rels/.rels", ROOT_RELS_XML)
        zf.writestr("word/document.xml", _document_xml())
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Word sample for Chatflow history_report upload testing.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = write_history_upload_sample(args.output)
    print(output)


if __name__ == "__main__":
    main()
