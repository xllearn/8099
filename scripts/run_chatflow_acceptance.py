from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen


HENAN_URL = "https://ypnew.hnsggzyjy.henan.gov.cn/cms/detail.html?infoId=1330&CatalogId=2"
DEFAULT_TEST_TEXT = """
关于调整医用耗材申报挂网操作流程的通知
https://ypnew.hnsggzyjy.henan.gov.cn/cms/detail.html?infoId=1330&CatalogId=2

关于开展兵团招采子系统医用耗材阳光挂网价格联动工作的通知
https://gwt.xjbtylbz.cn/hallEnter/#/news/320

深圳公共资源交易中心关于开展广东省麻醉管路等三类医用耗材带量联动采购的公告
https://www.szggzy.com/jygg/details.html?contentId=20426283

甘肃省关于规范医用耗材挂网采购工作的通知
https://ylbz.gansu.gov.cn/ylbzj/c107125/202606/174341234.shtml

[贵州]省医保局关于开展医用耗材阳光挂网采购申报工作的通知
https://fuwu.pubs.ylbzj.guizhou.gov.cn/hsa-pass-hallEnter/index.html#/announcementDetail?type=5&cont=%5Bobject%20Object%5D&artContId=2060175820995837953

宁夏自治区医保局关于公开征求《自治区公立医疗机构医用耗材挂网采购实施方案（试行）（征求意见稿）》意见的公告
https://ylbz.nx.gov.cn/zfxxgk/fdzdgknr/zqyj/202604/t20260407_5211295.html

广东省药品交易中心关于开展骨科类集采产品数据核对工作的通知
https://www.gdmede.com.cn/announcement/announcement/detail?id=2038964906916581376

关于吉林省体外诊断试剂挂网产品需补充三省挂网价格的通知
http://www.ggzyzx.jl.gov.cn/jyxx/yxcg/sj/202603/t20260330_3598680.html

关于开展药品和医用耗材挂网价格专项治理的通知
https://ybj.jiangsu.gov.cn/art/2026/2/28/art_85482_11733723.html

重庆关于开展部分医用耗材挂网价格信息核实确认的通知
https://www.yjsds.com/web/article/1482056794886729728/web/content_1482056794886729728.html
"""


def read_text_auto(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"https?://[^\s<>\"']+", str(text or "")):
        url = match.group(0).rstrip("。；;，,)")
        if url not in seen:
            urls.append(url)
            seen.add(url)
    return urls


def title_url_pairs(text: str) -> list[tuple[str, str]]:
    lines = [line.strip() for line in str(text or "").splitlines()]
    pairs: list[tuple[str, str]] = []
    previous_title = ""
    for line in lines:
        if not line:
            continue
        if re.match(r"^https?://", line):
            pairs.append((previous_title, line.rstrip("。；;，,)")))
            previous_title = ""
        else:
            previous_title = line
    return pairs


def select_acceptance_urls(urls: list[str], count: int = 3, seed: int | None = None) -> list[str]:
    unique = list(dict.fromkeys(urls))
    if HENAN_URL not in unique:
        raise ValueError("河南测试 URL 不在候选列表中")
    if count < 1:
        return []
    rng = random.Random(seed)
    others = [url for url in unique if url != HENAN_URL]
    selected = [HENAN_URL]
    selected.extend(rng.sample(others, min(count - 1, len(others))))
    return selected


def create_output_dir(desktop: Path, timestamp: str | None = None) -> Path:
    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    base = desktop / f"新版系统测试报告_{stamp}"
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = desktop / f"{base.name}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def http_json(url: str, api_key: str, payload: dict[str, Any], timeout: int = 900) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Dify API HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Dify API request failed: {exc}") from exc


def safe_download_filename(url: str) -> str:
    parsed = urlparse(url)
    raw_name = unquote(Path(parsed.path).name or "report.docx")
    filename = re.sub(r'[\\/:*?"<>|]+', "_", raw_name)
    if not filename.lower().endswith(".docx"):
        filename += ".docx"
    if len(filename) > 120:
        filename = filename[:115].rstrip(" ._") + ".docx"
    return filename


def download_file(url: str, output_dir: Path) -> Path:
    filename = safe_download_filename(url)
    target = output_dir / filename
    with urlopen(url, timeout=300) as resp:
        target.write_bytes(resp.read())
    return target


def extract_download_url(answer: str) -> str:
    match = re.search(r"https?://[^\s)）]+/download/[^\s)）]+", str(answer or ""))
    return match.group(0) if match else ""


def extract_qa_feedback(answer: str) -> str:
    text = str(answer or "")
    marker = "质检摘要："
    if marker in text:
        return text[text.index(marker) :].strip()
    return "请根据本地质检缺陷补充报告深度、结构、企业关注点，并保持事实依据来自原文。"


def score_answer(answer: str, downloaded: bool) -> tuple[int, str]:
    text = str(answer or "")
    score = 100
    issues: list[str] = []
    if "报告解析失败" in text or "无法导出" in text:
        score -= 35
        issues.append("出现解析或导出失败提示")
    if "质检状态：needs_fix" in text:
        score -= 10
        issues.append("质检仍提示 needs_fix")
    if len(re.sub(r"\s+", "", text)) < 1200:
        score -= 15
        issues.append("报告展示文本偏短")
    if not downloaded:
        score -= 25
        issues.append("未获得 Word 下载文件")
    return max(score, 0), "；".join(issues) or "未发现硬性失败"


def reference_tokens(title: str, url: str = "") -> list[str]:
    text = f"{title}\n{url}"
    tokens: list[str] = []
    host_map = {
        "henan.gov.cn": "河南",
        "hnsggzyjy": "河南",
        "szggzy.com": "广东",
        "gdmede.com": "广东",
        "xjbtylbz": "新疆",
        "gansu.gov.cn": "甘肃",
        "guizhou.gov.cn": "贵州",
        "ylbz.nx.gov.cn": "宁夏",
        "ggzyzx.jl.gov.cn": "吉林",
        "jiangsu.gov.cn": "江苏",
        "yjsds.com": "重庆",
    }
    for marker, token in host_map.items():
        if marker in text and token not in tokens:
            tokens.append(token)
    for token in ["河南", "广东", "甘肃", "贵州", "宁夏", "吉林", "江苏", "重庆", "新疆", "兵团", "深圳"]:
        if token in text and token not in tokens:
            tokens.append(token)
    return tokens


def find_reference(title: str, folder: Path, url: str = "") -> str:
    if not folder.exists():
        return "缺少基准文件夹"
    candidates = list(folder.glob("*.docx"))
    for token in reference_tokens(title, url):
        for file in candidates:
            if token in file.name:
                return str(file)
    return "缺少基准文件"


def run_acceptance(args: argparse.Namespace) -> int:
    source_text = DEFAULT_TEST_TEXT
    if args.url_file and Path(args.url_file).exists():
        source_text = read_text_auto(Path(args.url_file))
    pairs = title_url_pairs(source_text)
    urls = extract_urls(source_text)
    title_by_url = {url: title for title, url in pairs}
    selected = select_acceptance_urls(urls, count=args.count, seed=args.seed)
    output_dir = create_output_dir(Path(args.desktop))

    summary_rows: list[dict[str, Any]] = []
    api_url = args.api_url.rstrip("/") + "/chat-messages"
    user_id = f"acceptance-{int(time.time())}"

    for index, url in enumerate(selected, start=1):
        title = title_by_url.get(url, "")
        slug = f"{index:02d}_{quote(title or 'notice', safe='')[:40]}"
        record_path = output_dir / f"{slug}_记录.md"
        started = time.time()
        row: dict[str, Any] = {
            "index": index,
            "title": title,
            "url": url,
            "word_file": "",
            "score": 0,
            "issues": "",
            "elapsed_seconds": 0,
            "manual_reference": find_reference(title, Path(args.manual_dir), url),
            "old_system_reference": find_reference(title, Path(args.old_system_dir), url),
        }
        try:
            first = http_json(
                api_url,
                args.api_key,
                {
                    "inputs": {"notice_url": url},
                    "query": "开始分析",
                    "response_mode": "blocking",
                    "user": user_id,
                },
                timeout=args.timeout,
            )
            conversation_id = first.get("conversation_id") or ""
            first_answer = first.get("answer") or ""
            feedback = "请按以下质检缺陷修订报告，只修改相关问题，保留原报告其他合格内容：\n" + extract_qa_feedback(first_answer)
            revised = http_json(
                api_url,
                args.api_key,
                {
                    "inputs": {"notice_url": url},
                    "query": feedback,
                    "response_mode": "blocking",
                    "conversation_id": conversation_id,
                    "user": user_id,
                },
                timeout=args.timeout,
            )
            revised_answer = revised.get("answer") or ""
            downloaded = http_json(
                api_url,
                args.api_key,
                {
                    "inputs": {"notice_url": url},
                    "query": "确认下载 Word",
                    "response_mode": "blocking",
                    "conversation_id": conversation_id,
                    "user": user_id,
                },
                timeout=args.timeout,
            )
            download_answer = downloaded.get("answer") or ""
            download_url = extract_download_url(download_answer)
            word_path = download_file(download_url, output_dir) if download_url else None
            row["word_file"] = str(word_path) if word_path else ""
            score, issues = score_answer(revised_answer + "\n" + download_answer, bool(word_path))
            row["score"] = score
            row["issues"] = issues
            record_path.write_text(
                "\n\n".join(
                    [
                        f"# {title or url}",
                        f"URL: {url}",
                        "## 首轮回答",
                        first_answer,
                        "## 反馈修订回答",
                        revised_answer,
                        "## 确认下载回答",
                        download_answer,
                    ]
                ),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001 - acceptance records should continue.
            row["issues"] = str(exc)
            record_path.write_text(f"# {title or url}\n\nURL: {url}\n\n失败：{exc}\n", encoding="utf-8")
        finally:
            row["elapsed_seconds"] = round(time.time() - started, 3)
            summary_rows.append(row)

    summary_path = output_dir / "验收汇总.csv"
    with summary_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)
    print(json.dumps({"output_dir": str(output_dir), "summary": str(summary_path), "rows": summary_rows}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run three-URL acceptance for the Dify Chatflow.")
    parser.add_argument("--api-url", default="http://localhost/v1")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--url-file", default=r"C:\Users\admin\Desktop\测试网址.txt")
    parser.add_argument("--desktop", default=r"C:\Users\admin\Desktop")
    parser.add_argument("--manual-dir", default=r"C:\Users\admin\Desktop\人工分析")
    parser.add_argument("--old-system-dir", default=r"C:\Users\admin\Desktop\系统生成")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    return run_acceptance(args)


if __name__ == "__main__":
    raise SystemExit(main())
