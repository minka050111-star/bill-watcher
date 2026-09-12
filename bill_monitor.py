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

# 디스코드 웹훅 주소 (GitHub Secrets의 DISCORD_WEBHOOK_URL에서만 가져옵니다.
# 저장소가 공개(Public)일 수 있으므로 코드 안에 실제 주소를 절대 직접 적지 마세요.)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# 슬랙 웹훅 주소 (선택). 비워두면 슬랙으로는 전송하지 않습니다.
# 만드는 법: https://api.slack.com/apps 에서 앱 생성 → Incoming Webhooks 켜기
#          → 원하는(비공개 포함) 채널 선택 후 웹훅 URL 발급
# ※ 주의: Incoming Webhook은 스레드 답글을 지원하지 않습니다.
#         스레드로 묶어서 받고 싶으면 아래 SLACK_BOT_TOKEN 방식을 쓰세요.
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# 슬랙 스레드 알림용 Bot Token 방식 (선택).
# 둘 다 설정하면 이 방식이 우선 사용되고(스레드로 묶임), 안 하면 위 웹훅 방식이 사용됩니다.
# 만드는 법:
#   1) https://api.slack.com/apps 에서 앱 생성 (또는 기존 앱 사용)
#   2) 왼쪽 메뉴 OAuth & Permissions → Bot Token Scopes에 "chat:write" 추가
#   3) 상단 "Install to Workspace" → Bot User OAuth Token(xoxb-로 시작) 복사
#   4) 슬랙에서 알림받을 채널에 그 앱을 초대: 채널에서 "/invite @앱이름"
#   5) 채널 ID 확인: 채널 이름 클릭 → 맨 아래 "채널 ID" 복사 (C로 시작)
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "")

# 슬랙 스레드의 "오늘 부모 메시지" 정보를 기록해두는 파일 (하루에 한 번만 새로 만들기 위함)
SLACK_THREAD_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "slack_thread_state.json"
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

# 디스코드 메시지에 넣을 요약문 최대 길이(글자 수)
SUMMARY_MAX_LEN = 400

# --- AI 요약 관련 설정 ---
# Claude API로 "제안이유 및 주요내용"을 진짜 요약해서 보낼지 여부.
# True인데 ANTHROPIC_API_KEY가 없으면, 자동으로 "원문 일부 발췌" 방식으로 대체됩니다.
USE_AI_SUMMARY = True
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"  # 저렴하고 빠른 모델
# Claude에게 넘겨줄 원문 발췌 최대 길이 (너무 길면 비용/속도에 영향)
RAW_EXCERPT_MAX_LEN = 3000

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


def extract_summary(detail_text, max_len=RAW_EXCERPT_MAX_LEN):
    """상세페이지 텍스트에서 실제 '제안이유 및 주요내용' 본문을 찾아 발췌한다.

    주의: 국회 의안정보시스템 페이지에는 실제 본문과 별개로
    "문서검색: 제안이유 및 주요내용, 의안원문, 검토보고서..." 같은
    메뉴/안내 문구에도 '제안이유'라는 단어가 등장한다.
    그래서 마커가 나오는 지점 주변에 이런 메뉴성 단어가 붙어 있으면
    진짜 본문이 아니라고 보고 건너뛰고, 그 다음 등장 위치를 찾는다.
    """
    if not detail_text:
        return ""

    NAV_JUNK_WORDS = [
        "의안원문", "검토보고서", "심사보고서", "문서 내용 검색",
        "지능형검색", "기본검색", "이슈 키워드", "국회의원 검색",
    ]
    MARKERS = ["제안이유 및 주요내용", "제안이유", "주요내용"]

    best_snippet = ""
    for marker in MARKERS:
        search_from = 0
        while True:
            idx = detail_text.find(marker, search_from)
            if idx == -1:
                break
            candidate = detail_text[idx: idx + max_len]
            # 마커 바로 뒤 120자 안에 메뉴성 단어가 있으면 진짜 본문이 아닐 가능성이 큼
            nearby = candidate[:120]
            if any(junk in nearby for junk in NAV_JUNK_WORDS):
                search_from = idx + 1
                continue
            best_snippet = candidate
            break
        if best_snippet:
            break

    if not best_snippet:
        # 마커를 하나도 못 찾았으면 전체 텍스트 앞부분이라도 사용
        best_snippet = detail_text[:max_len]

    return " ".join(best_snippet.split())  # 연속 공백/줄바꿈 정리


def truncate(text, max_len=SUMMARY_MAX_LEN):
    if len(text) > max_len:
        return text[:max_len].rstrip() + " …"
    return text


def summarize_with_claude(raw_excerpt):
    """Claude API로 법안 제안이유·주요내용을 2~3문장으로 요약한다.
    실패하거나 API 키가 없으면 None을 반환한다.
    """
    if not raw_excerpt or not ANTHROPIC_API_KEY:
        return None

    prompt = (
        "다음은 국회에 발의된 법률안의 '제안이유 및 주요내용' 원문 일부야. "
        "핵심만 2~3문장, 자연스러운 한국어 평서문으로 간단히 요약해줘. "
        "불필요한 서두나 '요약하면' 같은 말 없이 바로 본론만 말해줘.\n\n"
        f"[원문]\n{raw_excerpt}"
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        parts = [c.get("text", "") for c in data.get("content", []) if c.get("type") == "text"]
        summary = " ".join(parts).strip()
        return summary or None
    except Exception as e:
        print(f"[경고] Claude 요약 실패, 원문 발췌로 대체합니다: {e}")
        return None


def build_summary(detail_text):
    """상세 텍스트에서 요약(가능하면 AI 요약, 아니면 원문 발췌)을 만든다."""
    raw_excerpt = extract_summary(detail_text)
    if not raw_excerpt:
        return ""

    if USE_AI_SUMMARY and ANTHROPIC_API_KEY:
        ai_summary = summarize_with_claude(raw_excerpt)
        if ai_summary:
            return truncate(ai_summary)

    # AI 요약을 안 쓰거나 실패한 경우: 원문 앞부분을 그대로 발췌
    return truncate(raw_excerpt)


def send_discord_notification(bill, matched, summary=""):
    title = bill.get("BILL_NAME", "(제목 없음)")
    committee = bill.get("COMMITTEE", "-") or "-"
    proposer = bill.get("PROPOSER", "-") or "-"
    propose_dt = bill.get("PROPOSE_DT", "-") or "-"
    link = bill.get("DETAIL_LINK") or bill.get("LINK_URL") or ""

    fields = [
        {"name": "소관위원회", "value": committee, "inline": True},
        {"name": "제안일자", "value": propose_dt, "inline": True},
        {"name": "제안자", "value": proposer, "inline": False},
        {"name": "매칭 키워드", "value": ", ".join(matched), "inline": False},
    ]
    if summary:
        fields.append({"name": "세부내용 요약", "value": summary, "inline": False})

    embed = {
        "title": f"📢 새 발의법안: {title}",
        "color": 3447003,
        "fields": fields,
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


def send_slack_notification(bill, matched, summary=""):
    if not SLACK_WEBHOOK_URL:
        return  # 슬랙 웹훅이 설정 안 되어 있으면 그냥 건너뜀

    title = bill.get("BILL_NAME", "(제목 없음)")
    committee = bill.get("COMMITTEE", "-") or "-"
    proposer = bill.get("PROPOSER", "-") or "-"
    propose_dt = bill.get("PROPOSE_DT", "-") or "-"
    link = bill.get("DETAIL_LINK") or bill.get("LINK_URL") or ""

    lines = [
        f"*:mega: 새 발의법안: {title}*",
        f"> *소관위원회*: {committee}",
        f"> *제안일자*: {propose_dt}",
        f"> *제안자*: {proposer}",
        f"> *매칭 키워드*: {', '.join(matched)}",
    ]
    if summary:
        lines.append(f"> *세부내용 요약*: {summary}")
    if link.startswith("http"):
        lines.append(f"<{link}|법안 상세보기 →>")

    payload = {"text": "\n".join(lines)}

    try:
        resp = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=15)
        if resp.status_code >= 300:
            print(f"[경고] 슬랙 전송 실패: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"[경고] 슬랙 전송 중 오류: {e}")


# --------------------- 슬랙 스레드(Bot Token) 방식 ---------------------

def get_kst_today_str():
    """한국시간(KST) 기준 오늘 날짜를 'YYMMDD' 형식으로 반환한다. (예: 260912)"""
    kst_now = datetime.utcnow() + timedelta(hours=9)
    return kst_now.strftime("%y%m%d")


def load_slack_thread_state():
    if os.path.exists(SLACK_THREAD_STATE_FILE):
        try:
            with open(SLACK_THREAD_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_slack_thread_state(state):
    with open(SLACK_THREAD_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_or_create_today_thread_ts():
    """오늘 날짜의 슬랙 스레드 부모 메시지 ts를 가져오거나, 없으면 새로 만든다."""
    today_str = get_kst_today_str()
    state = load_slack_thread_state()

    if state.get("date") == today_str and state.get("ts"):
        return state["ts"]

    try:
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json={
                "channel": SLACK_CHANNEL_ID,
                "text": f"📋 {today_str} 발의된 의안 목록",
            },
            timeout=15,
        )
        data = resp.json()
        if not data.get("ok"):
            print(f"[경고] 슬랙 스레드 생성 실패: {data}")
            return None
        ts = data["ts"]
        save_slack_thread_state({"date": today_str, "ts": ts, "channel": SLACK_CHANNEL_ID})
        return ts
    except Exception as e:
        print(f"[경고] 슬랙 스레드 생성 중 오류: {e}")
        return None


def send_slack_thread_reply(thread_ts, bill, matched, summary=""):
    title = bill.get("BILL_NAME", "(제목 없음)")
    committee = bill.get("COMMITTEE", "-") or "-"
    proposer = bill.get("PROPOSER", "-") or "-"
    propose_dt = bill.get("PROPOSE_DT", "-") or "-"
    link = bill.get("DETAIL_LINK") or bill.get("LINK_URL") or ""

    lines = [
        f"*:mega: {title}*",
        f"> *소관위원회*: {committee}",
        f"> *제안일자*: {propose_dt}",
        f"> *제안자*: {proposer}",
        f"> *매칭 키워드*: {', '.join(matched)}",
    ]
    if summary:
        lines.append(f"> *세부내용 요약*: {summary}")
    if link.startswith("http"):
        lines.append(f"<{link}|법안 상세보기 →>")

    try:
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json={
                "channel": SLACK_CHANNEL_ID,
                "text": "\n".join(lines),
                "thread_ts": thread_ts,
            },
            timeout=15,
        )
        data = resp.json()
        if not data.get("ok"):
            print(f"[경고] 슬랙 스레드 답글 실패: {data}")
    except Exception as e:
        print(f"[경고] 슬랙 스레드 답글 중 오류: {e}")


def main():
    if not API_KEY or "여기에" in API_KEY:
        print("[경고] API_KEY가 설정되지 않았습니다. 열린국회정보에서 발급받아 입력하세요.")

    seen_ids = load_seen_ids()
    bills = fetch_recent_bills()
    print(f"[정보] 최근 {LOOKBACK_DAYS}일 이내 발의 법률안 {len(bills)}건 조회됨")

    use_slack_thread = bool(SLACK_BOT_TOKEN and SLACK_CHANNEL_ID)
    slack_thread_ts = None  # 오늘 첫 매칭이 나올 때 한 번만 만들어서 재사용

    new_alert_count = 0
    for bill in bills:
        bill_id = bill.get("BILL_ID") or bill.get("BILL_NO")
        if not bill_id or bill_id in seen_ids:
            continue

        # 1차: 제목 / 위원회 / 제안자 기준 키워드 검사
        matched = find_matched_keywords(bill)
        detail_text = ""

        # 2차: 옵션이 켜져 있으면 상세페이지(제안이유·주요내용)를 가져온다.
        #     - 1차에서 매칭이 안 됐으면: 여기서도 키워드 검사
        #     - 1차에서 이미 매칭됐으면: 요약문을 만들기 위해서만 사용
        if FETCH_DETAIL:
            link = bill.get("DETAIL_LINK") or bill.get("LINK_URL")
            detail_text = fetch_detail_text(link)
            if not matched:
                matched = find_matched_keywords(bill, detail_text)
            time.sleep(0.5)  # 상세페이지 서버 부담 완화용 딜레이

        if matched:
            summary = build_summary(detail_text)
            send_discord_notification(bill, matched, summary)

            if use_slack_thread:
                if slack_thread_ts is None:
                    slack_thread_ts = get_or_create_today_thread_ts()
                if slack_thread_ts:
                    send_slack_thread_reply(slack_thread_ts, bill, matched, summary)
            else:
                send_slack_notification(bill, matched, summary)  # 웹훅 방식(스레드 없음)

            new_alert_count += 1
            print(f"  → 알림 전송: {bill.get('BILL_NAME')} (키워드: {', '.join(matched)})")

        seen_ids.add(bill_id)

    save_seen_ids(seen_ids)
    print(f"[완료] 신규 알림 {new_alert_count}건 전송, seen_bills.json 업데이트됨")


if __name__ == "__main__":
    main()
