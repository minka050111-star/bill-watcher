# -*- coding: utf-8 -*-
"""
대외협력 데일리 브리핑 사이트 생성기
======================================
아래 3가지 정보를 모아서 하나의 정적 웹페이지(index.html)를 만듭니다.
  1) 카카오모빌리티 관련 뉴스 클리핑 (네이버 뉴스 검색 API)
  2) 법안 발의 현황 (열린국회정보 API, bill_monitor.py와 동일한 데이터소스)
  3) 오늘의 국회 일정 (국회일정 통합 API, 엔드포인트는 아래 안내에 따라 확인 필요)

이 스크립트는 그 자체로 웹사이트를 "띄우는" 게 아니라, docs/index.html 파일을
만들어내기만 합니다. 그 파일을 GitHub Pages가 자동으로 웹사이트로 보여줍니다.

──────────────────────────────────────────────
■ 사용 전 준비
──────────────────────────────────────────────
1) 뉴스 클리핑용 네이버 오픈API 키 발급 (무료)
   - https://developers.naver.com/apps/#/register 에서 애플리케이션 등록
   - 사용 API로 "검색" 선택
   - 발급받은 Client ID / Client Secret을 아래 NAVER_CLIENT_ID / SECRET에 연결
     (GitHub Actions에서는 secrets로 관리합니다)

2) 법안 발의 현황: bill_monitor.py에서 쓰던 ASSEMBLY_API_KEY 그대로 재사용합니다.

3) 오늘의 국회 일정: "국회일정 통합 API"의 실제 요청주소(짧은 해시 URL)를
   확인해서 SCHEDULE_API_URL 에 넣어야 합니다.
   확인 방법: open.assembly.go.kr 에 로그인 후
   https://open.assembly.go.kr/portal/data/service/selectAPIServicePage.do/OOWY4R001216HX11437
   페이지에서 "요청주소" 부분을 확인하세요 (로그인해야 정확히 보입니다).
   아직 못 찾으셨으면 SCHEDULE_API_URL을 비워두세요 — 그 섹션은
   "설정 필요" 안내만 표시되고 나머지는 정상 작동합니다.

4) pip install requests
"""

import html
import os
import re
import time
from datetime import datetime, timedelta

import requests

# ============================ CONFIG ============================

ASSEMBLY_API_KEY = os.environ.get("ASSEMBLY_API_KEY", "")

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
NEWS_QUERY = "카카오모빌리티"
NEWS_LIMIT = 8

# 법안 발의 현황에서 필터링할 키워드 (bill_monitor.py와 동일한 관심사)
BILL_KEYWORDS = [
    "카카오모빌리티", "생활물류", "퀵", "배송",
    "국토교통위원회", "국토부", "국토위", "국토교통부",
    "플랫폼", "모빌리티", "자율주행",
]
BILL_LOOKBACK_DAYS = 7
BILL_LIMIT = 15
BILL_LIST_URL = "https://open.assembly.go.kr/portal/openapi/TVBPMBILL11"
BILL_AGE = "22"

# 국회일정 API (요청주소를 확인한 뒤 채워주세요. 비워두면 "설정 필요"로 표시됩니다)
SCHEDULE_API_URL = os.environ.get("SCHEDULE_API_URL", "")
SCHEDULE_LIMIT = 15

OUTPUT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", "index.html"
)

# =================================================================


def get_kst_now():
    return datetime.utcnow() + timedelta(hours=9)


def strip_html(text):
    """네이버 API가 <b>매칭어</b> 형태로 감싸서 주는 태그를 제거하고 특수문자를 정리한다."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


# --------------------------- 1) 뉴스 클리핑 ---------------------------

def fetch_mobility_news():
    if not (NAVER_CLIENT_ID and NAVER_CLIENT_SECRET):
        return None  # 설정 안 됨 (섹션에 안내 문구 표시용)

    try:
        resp = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers={
                "X-Naver-Client-Id": NAVER_CLIENT_ID,
                "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
            },
            params={"query": NEWS_QUERY, "display": NEWS_LIMIT, "sort": "date"},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        news = []
        for item in items:
            news.append(
                {
                    "title": strip_html(item.get("title")),
                    "summary": strip_html(item.get("description")),
                    "link": item.get("originallink") or item.get("link") or "",
                    "pub_date": item.get("pubDate", ""),
                }
            )
        return news
    except Exception as e:
        print(f"[경고] 뉴스 조회 실패: {e}")
        return []


# --------------------------- 2) 법안 발의 현황 ---------------------------

def fetch_matched_bills():
    if not ASSEMBLY_API_KEY:
        return None

    since = (get_kst_now() - timedelta(days=BILL_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    matched_bills = []
    p_index = 1

    while True:
        try:
            resp = requests.get(
                BILL_LIST_URL,
                params={
                    "KEY": ASSEMBLY_API_KEY,
                    "Type": "json",
                    "pIndex": p_index,
                    "pSize": 300,
                    "AGE": BILL_AGE,
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[경고] 법안 API 요청 실패: {e}")
            break

        if "TVBPMBILL11" not in data:
            break

        rows = []
        for part in data["TVBPMBILL11"]:
            if isinstance(part, dict) and "row" in part:
                rows = part["row"]
        if not rows:
            break

        for row in rows:
            propose_dt = row.get("PROPOSE_DT", "")
            if propose_dt < since:
                continue
            haystack = " ".join(
                [
                    row.get("BILL_NAME", "") or "",
                    row.get("COMMITTEE", "") or "",
                    row.get("PROPOSER", "") or "",
                ]
            )
            matched = [kw for kw in BILL_KEYWORDS if kw in haystack]
            if matched:
                matched_bills.append(
                    {
                        "title": row.get("BILL_NAME", "(제목 없음)"),
                        "committee": row.get("COMMITTEE", "-"),
                        "propose_dt": propose_dt,
                        "proposer": row.get("PROPOSER", "-"),
                        "link": row.get("DETAIL_LINK") or row.get("LINK_URL") or "",
                        "keywords": matched,
                    }
                )

        oldest_in_page = min((r.get("PROPOSE_DT", "9999") for r in rows), default="9999")
        if oldest_in_page < since:
            break
        p_index += 1
        if p_index > 20:
            break

    matched_bills.sort(key=lambda b: b["propose_dt"], reverse=True)
    return matched_bills[:BILL_LIMIT]


# --------------------------- 3) 오늘의 국회 일정 ---------------------------

def fetch_today_schedule():
    if not SCHEDULE_API_URL:
        return None  # 아직 엔드포인트 미설정

    today_dot = get_kst_now().strftime("%Y.%m.%d")
    today_dash = get_kst_now().strftime("%Y-%m-%d")

    try:
        resp = requests.get(
            SCHEDULE_API_URL,
            params={
                "KEY": ASSEMBLY_API_KEY,
                "Type": "json",
                "pIndex": 1,
                "pSize": 100,
            },
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[경고] 국회일정 API 요청 실패: {e}")
        return []

    # 필드 이름을 몰라도 동작하도록, 응답 안의 모든 row를 일단 그대로 가져온다.
    rows = []
    for key, val in data.items():
        if isinstance(val, list):
            for part in val:
                if isinstance(part, dict) and "row" in part:
                    rows.extend(part["row"])

    # 오늘 날짜가 값 어딘가에 포함된 row만 필터링 (필드명을 몰라도 되는 방식)
    todays = []
    for row in rows:
        row_text = " ".join(str(v) for v in row.values())
        if today_dot in row_text or today_dash in row_text:
            todays.append(row)

    return todays[:SCHEDULE_LIMIT]


# --------------------------- HTML 생성 ---------------------------

def render_news_section(news):
    if news is None:
        return (
            '<p class="empty">네이버 뉴스 API 키가 아직 설정되지 않았어요. '
            "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET을 등록하면 이 자리에 뉴스가 표시됩니다.</p>"
        )
    if not news:
        return '<p class="empty">최근 관련 뉴스가 없습니다.</p>'

    items = []
    for n in news:
        link = html.escape(n["link"])
        title = html.escape(n["title"])
        summary = html.escape(n["summary"])
        items.append(
            f'<li class="row"><a class="row-title" href="{link}" target="_blank" '
            f'rel="noopener">{title}</a><p class="row-desc">{summary}</p></li>'
        )
    return f'<ul class="list">{"".join(items)}</ul>'


def render_bills_section(bills):
    if bills is None:
        return '<p class="empty">ASSEMBLY_API_KEY가 설정되지 않았어요.</p>'
    if not bills:
        return '<p class="empty">최근 7일 이내 관련 키워드가 포함된 발의 법안이 없습니다.</p>'

    items = []
    for b in bills:
        link = html.escape(b["link"])
        title = html.escape(b["title"])
        meta = f'{html.escape(b["committee"])} · {html.escape(b["propose_dt"])} · {html.escape(b["proposer"])}'
        kw = ", ".join(b["keywords"])
        title_html = f'<a class="row-title" href="{link}" target="_blank" rel="noopener">{title}</a>' if link.startswith("http") else f'<span class="row-title">{title}</span>'
        items.append(
            f'<li class="row">{title_html}<p class="row-meta">{meta}</p>'
            f'<p class="row-tag">키워드: {html.escape(kw)}</p></li>'
        )
    return f'<ul class="list">{"".join(items)}</ul>'


def render_schedule_section(schedule):
    if schedule is None:
        return (
            '<p class="empty">국회일정 API 요청주소(SCHEDULE_API_URL)가 아직 설정되지 않았어요. '
            "설정 방법은 스크립트 상단 주석을 참고하세요.</p>"
        )
    if not schedule:
        return '<p class="empty">오늘 예정된 일정이 없습니다.</p>'

    items = []
    for row in schedule:
        fields = " · ".join(
            html.escape(str(v)) for v in row.values() if v not in (None, "")
        )
        items.append(f'<li class="row"><p class="row-desc">{fields}</p></li>')
    return f'<ul class="list">{"".join(items)}</ul>'


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>대외협력 데일리 브리핑</title>
<style>
  :root {{
    --ink: #14213D;
    --paper: #FBF9F4;
    --accent: #B8873A;
    --text: #232017;
    --muted: #6B6558;
    --line: #DCD5C5;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--ink);
    color: var(--text);
    font-family: -apple-system, "Pretendard", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
  }}
  .sheet {{
    max-width: 760px;
    margin: 0 auto;
    background: var(--paper);
    min-height: 100vh;
    padding: 56px 40px 80px;
  }}
  header.brief-head {{
    border-bottom: 2px solid var(--ink);
    padding-bottom: 20px;
    margin-bottom: 36px;
  }}
  header.brief-head .kicker {{
    font-size: 13px;
    letter-spacing: 0.04em;
    color: var(--accent);
    margin: 0 0 6px;
  }}
  header.brief-head h1 {{
    font-family: Georgia, "Noto Serif KR", serif;
    font-size: 30px;
    font-weight: 600;
    margin: 0 0 8px;
    color: var(--ink);
  }}
  header.brief-head .date {{
    font-size: 14px;
    color: var(--muted);
    margin: 0;
  }}
  section.block {{
    margin-bottom: 44px;
  }}
  section.block h2 {{
    font-family: Georgia, "Noto Serif KR", serif;
    font-size: 20px;
    color: var(--ink);
    margin: 0 0 4px;
  }}
  section.block .section-sub {{
    font-size: 13px;
    color: var(--muted);
    margin: 0 0 18px;
  }}
  .list {{
    list-style: none;
    margin: 0;
    padding: 0;
  }}
  .row {{
    padding: 16px 0;
    border-top: 1px solid var(--line);
  }}
  .row:last-child {{
    border-bottom: 1px solid var(--line);
  }}
  .row-title {{
    display: block;
    font-size: 15.5px;
    font-weight: 600;
    color: var(--text);
    text-decoration: none;
    line-height: 1.4;
  }}
  a.row-title:hover {{
    color: var(--accent);
  }}
  .row-desc {{
    font-size: 13.5px;
    color: var(--muted);
    margin: 6px 0 0;
    line-height: 1.5;
  }}
  .row-meta {{
    font-size: 12.5px;
    color: var(--muted);
    margin: 6px 0 0;
  }}
  .row-tag {{
    font-size: 12px;
    color: var(--accent);
    margin: 4px 0 0;
  }}
  .empty {{
    font-size: 13.5px;
    color: var(--muted);
    padding: 14px 0;
    border-top: 1px solid var(--line);
    border-bottom: 1px solid var(--line);
  }}
  footer.brief-foot {{
    margin-top: 56px;
    padding-top: 16px;
    border-top: 1px solid var(--line);
    font-size: 12px;
    color: var(--muted);
  }}
</style>
</head>
<body>
  <div class="sheet">
    <header class="brief-head">
      <p class="kicker">대외협력 데일리 브리핑</p>
      <h1>오늘의 모빌리티 · 입법 동향</h1>
      <p class="date">{today_label} 기준 자동 업데이트</p>
    </header>

    <section class="block">
      <h2>카카오모빌리티 뉴스 클리핑</h2>
      <p class="section-sub">네이버 뉴스 검색 기준 최신순</p>
      {news_html}
    </section>

    <section class="block">
      <h2>법안 발의 현황</h2>
      <p class="section-sub">최근 7일, 관심 키워드 매칭</p>
      {bills_html}
    </section>

    <section class="block">
      <h2>오늘의 국회 일정</h2>
      <p class="section-sub">국회일정 통합 API 기준</p>
      {schedule_html}
    </section>

    <footer class="brief-foot">
      이 페이지는 매일 자동으로 새로 생성됩니다. 문의: 대외협력팀
    </footer>
  </div>
</body>
</html>
"""


def build_html(news, bills, schedule):
    today_label = get_kst_now().strftime("%Y년 %m월 %d일")
    return PAGE_TEMPLATE.format(
        today_label=today_label,
        news_html=render_news_section(news),
        bills_html=render_bills_section(bills),
        schedule_html=render_schedule_section(schedule),
    )


def main():
    news = fetch_mobility_news()
    bills = fetch_matched_bills()
    schedule = fetch_today_schedule()

    html_content = build_html(news, bills, schedule)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"[완료] {OUTPUT_PATH} 생성됨")
    print(f"  - 뉴스: {'설정 안 됨' if news is None else f'{len(news)}건'}")
    print(f"  - 법안: {'설정 안 됨' if bills is None else f'{len(bills)}건'}")
    print(f"  - 일정: {'설정 안 됨' if schedule is None else f'{len(schedule)}건'}")


if __name__ == "__main__":
    main()
