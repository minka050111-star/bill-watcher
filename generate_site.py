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

3) 오늘의 국회 일정: "국회일정 통합 API"(ALLSCHEDULE)도 ASSEMBLY_API_KEY를 그대로
   재사용합니다. 별도 키 발급이 필요 없어요.

4) pip install requests
"""

import calendar
import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

# ============================ CONFIG ============================

ASSEMBLY_API_KEY = os.environ.get("ASSEMBLY_API_KEY", "")

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

# 뉴스 클리핑 검색 키워드 (원하는 만큼 자유롭게 추가/삭제하세요).
# 네이버 검색 API는 "A OR B" 검색을 지원하지 않아서, 키워드마다 따로 검색한 뒤
# 결과를 합치고 중복을 제거하는 방식으로 동작합니다.
NEWS_QUERIES = [
    "카카오모빌리티",
    "카카오T",
    "카모",
]
NEWS_LIMIT_PER_QUERY = 8   # 키워드 하나당 가져올 기사 수
NEWS_LIMIT_TOTAL = 12      # 합친 뒤 최종적으로 페이지에 보여줄 최대 기사 수

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

# 국회일정 API (ALLSCHEDULE) — ASSEMBLY_API_KEY를 그대로 재사용합니다. 별도 키 불필요.
SCHEDULE_API_URL = os.environ.get(
    "SCHEDULE_API_URL", "https://open.assembly.go.kr/portal/openapi/ALLSCHEDULE"
)
# 한 페이지당 몇 건씩 가져올지. 이번 달 데이터를 찾을 때까지 페이지를 넘기며 훑습니다.
SCHEDULE_PAGE_SIZE = 100
SCHEDULE_MAX_PAGES = 60  # 안전장치: 최대 이만큼만 페이지를 넘김 (전체 9만여 건 중 일부만 훑음)

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

    seen_links = set()
    all_news = []

    for query in NEWS_QUERIES:
        try:
            resp = requests.get(
                "https://naverapihub.apigw.ntruss.com/search/v1/news",
                headers={
                    "X-NCP-APIGW-API-KEY-ID": NAVER_CLIENT_ID,
                    "X-NCP-APIGW-API-KEY": NAVER_CLIENT_SECRET,
                },
                params={"query": query, "display": NEWS_LIMIT_PER_QUERY, "sort": "date"},
                timeout=15,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except Exception as e:
            print(f"[경고] 뉴스 조회 실패 (검색어: {query}): {e}")
            continue

        for item in items:
            link = item.get("originallink") or item.get("link") or ""
            if link in seen_links:
                continue  # 다른 키워드에서 이미 가져온 기사면 건너뜀 (중복 제거)
            seen_links.add(link)
            all_news.append(
                {
                    "title": strip_html(item.get("title")),
                    "summary": strip_html(item.get("description")),
                    "link": link,
                    "pub_date": item.get("pubDate", ""),
                }
            )

    # 최신순으로 다시 정렬 (pubDate는 RFC 822 형식이라 문자열 비교가 정확하지 않을 수 있어
    # 파싱 가능한 경우만 정렬하고, 실패하면 원래 순서를 유지한다)
    def parse_date(n):
        try:
            return datetime.strptime(n["pub_date"], "%a, %d %b %Y %H:%M:%S %z")
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    try:
        all_news.sort(key=parse_date, reverse=True)
    except Exception:
        pass

    return all_news[:NEWS_LIMIT_TOTAL]


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


# --------------------------- 3) 오늘의 국회 일정 (월간 달력) ---------------------------

# 응답 안의 값들 중 날짜처럼 보이는 첫 번째 문자열을 찾기 위한 패턴
# (2026.09.12, 2026-09-12, 2026.09.12(14:00) 등 다양한 표기를 커버)
_DATE_PATTERN = re.compile(r"(20\d{2})[.\-](\d{1,2})[.\-](\d{1,2})")


def extract_date_from_row(row):
    """행(row)에서 날짜를 찾는다. ALLSCHEDULE API는 SCH_DT 필드에 'YYYY-MM-DD'로 들어있다.
    혹시 다른 필드 구조의 API로 바뀌어도 동작하도록, 없으면 모든 값에서 날짜 패턴을 찾는다."""
    sch_dt = row.get("SCH_DT")
    if sch_dt:
        m = _DATE_PATTERN.search(str(sch_dt))
        if m:
            y, mo, d = m.groups()
            try:
                return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
            except ValueError:
                pass

    for v in row.values():
        if not v:
            continue
        m = _DATE_PATTERN.search(str(v))
        if m:
            y, mo, d = m.groups()
            try:
                return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
            except ValueError:
                continue
    return None


def summarize_row(row):
    """행을 사람이 읽기 좋은 한 줄로 요약한다.
    ALLSCHEDULE API의 필드(SCH_TM, SCH_KIND, SCH_CN, CMIT_NM, EV_PLC)가 있으면 그걸 활용하고,
    다른 구조의 API라면 값들을 그냥 나열한다."""
    if "SCH_CN" in row:
        parts = []
        if row.get("SCH_TM"):
            parts.append(row["SCH_TM"])
        if row.get("SCH_KIND"):
            parts.append(f"[{row['SCH_KIND']}]")
        if row.get("SCH_CN"):
            parts.append(row["SCH_CN"])
        if row.get("CMIT_NM"):
            parts.append(f"({row['CMIT_NM']})")
        if row.get("EV_PLC"):
            parts.append(f"@ {row['EV_PLC']}")
        return " ".join(parts)

    return " · ".join(str(v) for v in row.values() if v not in (None, ""))


def fetch_month_schedule():
    """이번 달 국회 일정을 날짜별로 묶어서 돌려준다: {'YYYY-MM-DD': [요약문, ...]}

    ALLSCHEDULE API는 전체 9만여 건을 날짜 내림차순(먼 미래 → 과거)으로 돌려주고
    별도의 날짜 필터 파라미터가 없다. 그래서 페이지를 넘기며 훑다가,
    '이번 달 일정을 이미 찾았는데 + 이번 페이지가 전부 이번 달보다 과거'인 시점에
    더 훑어봐야 의미가 없다고 보고 중단한다."""
    if not SCHEDULE_API_URL:
        return None  # 아직 엔드포인트 미설정

    now = get_kst_now()
    year, month = now.year, now.month

    events_by_date = {}
    p_index = 1
    found_target_month = False

    while p_index <= SCHEDULE_MAX_PAGES:
        try:
            resp = requests.get(
                SCHEDULE_API_URL,
                params={
                    "KEY": ASSEMBLY_API_KEY,
                    "Type": "json",
                    "pIndex": p_index,
                    "pSize": SCHEDULE_PAGE_SIZE,
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[경고] 국회일정 API 요청 실패: {e}")
            break

        rows = []
        for key, val in data.items():
            if isinstance(val, list):
                for part in val:
                    if isinstance(part, dict) and "row" in part:
                        rows.extend(part["row"])

        if not rows:
            break

        page_all_before_target = True
        for row in rows:
            date_str = extract_date_from_row(row)
            if not date_str:
                continue
            y, mo, _ = map(int, date_str.split("-"))
            if y == year and mo == month:
                events_by_date.setdefault(date_str, []).append(summarize_row(row))
                found_target_month = True
                page_all_before_target = False
            elif (y, mo) > (year, month):
                page_all_before_target = False  # 아직 미래 달 → 계속 진행

        if found_target_month and page_all_before_target:
            break  # 이번 달을 지나쳐서 과거로 넘어갔으므로 중단

        p_index += 1

    return events_by_date



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


def render_calendar_section(events_by_date):
    not_configured = events_by_date is None
    if not_configured:
        events_by_date = {}  # 빈 달력이라도 그려서 미리 보여주기 위함

    now = get_kst_now()
    year, month = now.year, now.month
    today_str = now.strftime("%Y-%m-%d")

    # 일요일 시작 달력 (한국 관례)
    cal = calendar.Calendar(firstweekday=6)
    weeks = cal.monthdatescalendar(year, month)

    day_names = ["일", "월", "화", "수", "목", "금", "토"]
    header_html = "".join(f'<div class="cal-dayname">{d}</div>' for d in day_names)

    cells_html = []
    for week in weeks:
        for day in week:
            date_str = day.strftime("%Y-%m-%d")
            in_month = day.month == month
            classes = ["cal-day"]
            if not in_month:
                classes.append("cal-day--muted")
            if date_str == today_str:
                classes.append("cal-day--today")
            has_events = date_str in events_by_date and events_by_date[date_str]
            if has_events:
                classes.append("cal-day--has-events")
            class_attr = " ".join(classes)
            dot_html = '<span class="cal-dot"></span>' if has_events else ""
            cells_html.append(
                '<button type="button" class="' + class_attr + '" '
                'data-date="' + date_str + '" '
                "onclick=\"showScheduleDay('" + date_str + "')\">"
                '<span class="cal-day-num">' + str(day.day) + "</span>"
                + dot_html +
                "</button>"
            )

    calendar_html = (
        (
            '<p class="empty">국회일정 API 요청주소(SCHEDULE_API_URL)가 아직 설정되지 않았어요. '
            "설정 방법은 스크립트 상단 주석을 참고하세요. (아래는 미리보기용 빈 달력이에요)</p>"
            if not_configured
            else ""
        )
        + f'<div class="cal-grid cal-grid--header">{header_html}</div>'
        + f'<div class="cal-grid">{"".join(cells_html)}</div>'
        + '<div id="cal-detail" class="cal-detail"></div>'
    )

    # JS에서 쓸 데이터. 날짜별 요약 문자열 리스트를 그대로 넘긴다.
    data_json = json.dumps(events_by_date, ensure_ascii=False)

    script = f"""
<script>
  const scheduleData = {data_json};
  function showScheduleDay(dateStr) {{
    document.querySelectorAll('.cal-day').forEach(el => el.classList.remove('cal-day--selected'));
    const btn = document.querySelector('.cal-day[data-date="' + dateStr + '"]');
    if (btn) btn.classList.add('cal-day--selected');

    const events = scheduleData[dateStr] || [];
    const detail = document.getElementById('cal-detail');
    const [y, m, d] = dateStr.split('-');
    let html = '<p class="cal-detail-date">' + y + '년 ' + parseInt(m) + '월 ' + parseInt(d) + '일</p>';
    if (events.length === 0) {{
      html += '<p class="empty">이 날짜에 예정된 일정이 없습니다.</p>';
    }} else {{
      html += '<ul class="list">' + events.map(e =>
        '<li class="row"><p class="row-desc">' + e.replace(/</g, '&lt;') + '</p></li>'
      ).join('') + '</ul>';
    }}
    detail.innerHTML = html;
  }}
  showScheduleDay('{today_str}');
</script>
"""

    return calendar_html + script


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
  /* 달력 */
  .cal-grid {{
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    gap: 4px;
  }}
  .cal-grid--header {{
    margin-bottom: 6px;
  }}
  .cal-dayname {{
    text-align: center;
    font-size: 12px;
    color: var(--muted);
    padding-bottom: 4px;
  }}
  .cal-day {{
    position: relative;
    aspect-ratio: 1 / 1;
    border: 1px solid var(--line);
    background: transparent;
    border-radius: 4px;
    cursor: pointer;
    font-family: inherit;
    display: flex;
    align-items: center;
    justify-content: center;
  }}
  .cal-day-num {{
    font-size: 13px;
    color: var(--text);
  }}
  .cal-day--muted .cal-day-num {{
    color: var(--line);
  }}
  .cal-day--today {{
    border-color: var(--ink);
    border-width: 2px;
  }}
  .cal-day--selected {{
    background: var(--ink);
  }}
  .cal-day--selected .cal-day-num {{
    color: var(--paper);
  }}
  .cal-dot {{
    position: absolute;
    bottom: 5px;
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--accent);
  }}
  .cal-day--selected .cal-dot {{
    background: var(--paper);
  }}
  .cal-detail {{
    margin-top: 20px;
  }}
  .cal-detail-date {{
    font-size: 13.5px;
    font-weight: 600;
    color: var(--ink);
    margin: 0 0 8px;
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
      <p class="section-sub">날짜를 클릭하면 그날 일정을 볼 수 있어요</p>
      {calendar_html}
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
        calendar_html=render_calendar_section(schedule),
    )


def main():
    news = fetch_mobility_news()
    bills = fetch_matched_bills()
    schedule = fetch_month_schedule()

    html_content = build_html(news, bills, schedule)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html_content)

    total_events = sum(len(v) for v in schedule.values()) if schedule is not None else 0
    print(f"[완료] {OUTPUT_PATH} 생성됨")
    print(f"  - 뉴스: {'설정 안 됨' if news is None else f'{len(news)}건'}")
    print(f"  - 법안: {'설정 안 됨' if bills is None else f'{len(bills)}건'}")
    print(f"  - 일정: {'설정 안 됨' if schedule is None else f'이번 달 {total_events}건'}")


if __name__ == "__main__":
    main()
