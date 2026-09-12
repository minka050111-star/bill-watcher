# -*- coding: utf-8 -*-
"""
국회 법안 모니터링 → 디스코드 알림 봇
======================================
국회 "열린국회정보" Open API(open.assembly.go.kr)에서 새로 발의된 법률안을 조회하고,
법안 제목 / 소관위원회 / 제안자, (옵션) 제안이유·주요내용에
지정한 키워드가 포함되어 있으면 디스코드 웹훅으로 알림을 보냅니다.

──────────────────────────────────────────────
■ 사용 전 준비
──────────────────────────────────────────────
1) 열린국회정보(https://open.assembly.go.kr) 회원가입 → 로그인 →
   [Open API] > [마이페이지] > [인증키 발급]에서 API KEY를 발급받으세요.
   ※ 샘플키(sample key)는 한 번에 5건만 조회되어 실사용에 부적합합니다.
     반드시 본인 명의 API KEY를 발급받아 아래 API_KEY에 넣어주세요.

2) 필요한 패키지 설치
   pip install requests beautifulsoup4

3) 아래 "CONFIG" 영역에서 API_KEY, KEYWORDS 등을 확인/수정하세요.
   (디스코드 웹훅 주소는 이미 채워져 있습니다)

4) 매일 자동 실행되도록 예약해두세요.
   - 리눅스 / macOS (cron): 터미널에서 `crontab -e` 실행 후 아래 한 줄 추가
       0 9 * * * /usr/bin/python3 /절대경로/bill_monitor.py >> /절대경로/bill_monitor.log 2>&1
     (매일 오전 9시 실행 예시)
   - Windows: [작업 스케줄러]에서 "매일 특정 시각에 python.exe bill_monitor.py 실행"으로 등록

5) 처음 실행하면 스크립트와 같은 폴더에 seen_bills.json 파일이 생성됩니다.
   이미 알림을 보낸 법안 ID를 기록해서 중복 알림을 막는 용도이니 삭제하지 마세요.
"""

import json
import os
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

# ============================ CONFIG ============================

# 열린국회정보 Open API 인증키 (필수! 발급받은 키로 교체하세요)
API_KEY = os.environ.get("ASSEMBLY_API_KEY", "여기에_발급받은_API_KEY_입력")

# 디스코드 웹훅 주소
DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/1548224016337084519/EdDTCaSHoqpP1gti_iOeGyDjS_0iFbOJeD3NBzAI1yAuIdcs_IBGXjA1yaAV0tGXs534",
)

# 감시할 키워드 목록 (제목/위원회/제안자/상세내용에서 검사)
KEYWORDS = [
    "생활물류", "퀵", "배송",
    "국토교통위원회", "국토부", "국토위", "국토교통부",
    "플랫폼", "모빌리티", "자율주행",
]

# 국회 대수. 22대 국회 기준 "22". 다음 대수로 넘어가면 이 값을 바꿔주세요.
AGE = "22"

# 매일 실행 시 실행이 하루 이틀 밀려도 놓치지 않도록 최근 N일치를 넉넉히 조회
LOOKBACK_DAYS = 3

# 한 페이지에 가져올 건수
PAGE_SIZE = 300

# 상세페이지(제안이유·주요내용)까지 열어서 키워드를 검사할지 여부
# True로 하면 더 정확하지만, 법안 수가 많은 날은 다소 느려질 수 있습니다.
FETCH_DETAIL = True

# 이미 알림을 보낸 법안 ID를 기록해두는 파일 (중복 알림 방지)
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_bills.json")

# 의안정보 통합 API 요청 주소 (의안검색, 필수 파라미터 없음)
BILL_LIST_URL = "https://open.assembly.go.kr/portal/openapi/TVBPMBILL11"

# =================================================================


def load_seen_ids():
    """이전에 알림을 보낸 법안 ID 목록을 불러온다."""
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen_ids(seen_ids):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_ids), f, ensure_ascii=False, indent=2)


def fetch_recent_bills():
    """최근 LOOKBACK_DAYS일 이내에 발의된 법률안 목록을 가져온다."""
    since = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    all_rows = []
    p_index = 1

    while True:
        params = {
            "KEY": API_KEY,
            "Type": "json",
            "pIndex": p_index,
            "pSize": PAGE_SIZE,
            "AGE": AGE,
        }
        try:
            resp = requests.get(BILL_LIST_URL, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[오류] API 요청 실패: {e}")
            break

        if "TVBPMBILL11" not in data:
            print("[오류] API 응답 형식이 예상과 다릅니다:", data)
            break

        rows = []
        for part in data["TVBPMBILL11"]:
            if isinstance(part, dict) and "row" in part:
                rows = part["row"]

        if not rows:
            break

        all_rows.extend(rows)

        oldest_in_page = min(
            (r.get("PROPOSE_DT", "9999-99-99") for r in rows), default="9999-99-99"
        )
        if oldest_in_page < since:
            # 이번 페이지에서 이미 조회 기준일보다 오래된 법안이 나왔으므로 중단
            break

        p_index += 1
        if p_index > 20:
            # 안전장치: 페이지가 비정상적으로 많으면 중단
            break

    recent = [r for r in all_rows if r.get("PROPOSE_DT", "") >= since]
    return recent


def fetch_detail_text(detail_link):
    """법안 상세페이지에서 텍스트(제안이유 및 주요내용 포함)를 가져온다."""
    if not detail_link or not detail_link.startswith("http"):
        return ""
    try:
        resp = requests.get(detail_link, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        return soup.get_text(separator=" ", strip=True)
    except Exception as e:
        print(f"[경고] 상세페이지 조회 실패: {detail_link} ({e})")
        return ""


def find_matched_keywords(bill, detail_text=""):
    haystack = " ".join(
        [
            bill.get("BILL_NAME", "") or "",
            bill.get("COMMITTEE", "") or "",
            bill.get("PROPOSER", "") or "",
            detail_text,
        ]
    )
    return [kw for kw in KEYWORDS if kw in haystack]


def send_discord_notification(bill, matched):
    title = bill.get("BILL_NAME", "(제목 없음)")
    committee = bill.get("COMMITTEE", "-") or "-"
    proposer = bill.get("PROPOSER", "-") or "-"
    propose_dt = bill.get("PROPOSE_DT", "-") or "-"
    link = bill.get("DETAIL_LINK") or bill.get("LINK_URL") or ""

    embed = {
        "title": f"📢 새 발의법안: {title}",
        "color": 3447003,
        "fields": [
            {"name": "소관위원회", "value": committee, "inline": True},
            {"name": "제안일자", "value": propose_dt, "inline": True},
            {"name": "제안자", "value": proposer, "inline": False},
            {"name": "매칭 키워드", "value": ", ".join(matched), "inline": False},
        ],
    }
    if link.startswith("http"):
        embed["url"] = link

    payload = {"embeds": [embed]}

    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        if resp.status_code >= 300:
            print(f"[경고] 디스코드 전송 실패: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"[경고] 디스코드 전송 중 오류: {e}")


def main():
    if not API_KEY or "여기에" in API_KEY:
        print("[경고] API_KEY가 설정되지 않았습니다. 열린국회정보에서 발급받아 입력하세요.")

    seen_ids = load_seen_ids()
    bills = fetch_recent_bills()
    print(f"[정보] 최근 {LOOKBACK_DAYS}일 이내 발의 법률안 {len(bills)}건 조회됨")

    new_alert_count = 0
    for bill in bills:
        bill_id = bill.get("BILL_ID") or bill.get("BILL_NO")
        if not bill_id or bill_id in seen_ids:
            continue

        # 1차: 제목 / 위원회 / 제안자 기준 키워드 검사
        matched = find_matched_keywords(bill)

        # 2차: 1차에서 매칭이 없고 옵션이 켜져 있으면 상세페이지(제안이유·주요내용)까지 검사
        if not matched and FETCH_DETAIL:
            link = bill.get("DETAIL_LINK") or bill.get("LINK_URL")
            detail_text = fetch_detail_text(link)
            matched = find_matched_keywords(bill, detail_text)
            time.sleep(0.5)  # 상세페이지 서버 부담 완화용 딜레이

        if matched:
            send_discord_notification(bill, matched)
            new_alert_count += 1
            print(f"  → 알림 전송: {bill.get('BILL_NAME')} (키워드: {', '.join(matched)})")

        seen_ids.add(bill_id)

    save_seen_ids(seen_ids)
    print(f"[완료] 신규 알림 {new_alert_count}건 전송, seen_bills.json 업데이트됨")


if __name__ == "__main__":
    main()
