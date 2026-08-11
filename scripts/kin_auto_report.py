#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
네이버 지식iN 외부유입 자동화 - 발굴/조사/답변생성/카톡전송
- 이 스크립트는 절대 지식iN에 자동으로 글을 올리지 않습니다.
  (질문 링크 + 답변 초안을 카카오톡 "나에게 보내기"로 전달할 뿐이며,
   실제 게시는 사람이 직접 복사/붙여넣기로 진행합니다.)
- 답변 생성 규칙은 "네이버 지식인 답글 자동화.txt" 원문(SYSTEM_PROMPT)에
  100% 근거해서 동작합니다. 이 파일의 규칙을 바꾸고 싶으면 SYSTEM_PROMPT만 수정하세요.

실행 방식: 하루 3회(아침/점심/저녁) GitHub Actions cron으로 이 스크립트를 실행합니다.
매 실행마다 BATCH_TARGET개(기본 배분: 4 / 3 / 3 = 하루 10개)를 찾아
카카오톡으로 전송하고, 이미 처리한 질문은 state 파일에 기록해 중복을 막습니다.
"""

import os
import sys
import json
import time
import random
import hashlib
import requests
from datetime import datetime, timezone, timedelta

# ============================================================
# 0. 환경변수 / 설정
# ============================================================

NAVER_CLIENT_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_CLIENT_SECRET = os.environ["NAVER_CLIENT_SECRET"]

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

KAKAO_ACCESS_TOKEN = os.environ["KAKAO_ACCESS_TOKEN"]  # 워크플로에서 갱신 후 주입


# 구글 커스텀서치는 "정부 공식 출처 확인"용 (선택, 없으면 네이버 웹문서 검색으로 대체)
GOOGLE_CSE_KEY = os.environ.get("GOOGLE_CSE_KEY", "")
GOOGLE_CSE_ID = os.environ.get("GOOGLE_CSE_ID", "")

# 하루 3회 배치 배분 (아침/점심/저녁 = 4/3/3 = 총 10)
BATCH_TARGET = int(os.environ.get("BATCH_TARGET", "4"))

STATE_PATH = os.environ.get("STATE_PATH", "state/kin_state.json")
MAX_STATE_KEEP = 2000  # state 파일이 무한정 커지지 않도록 최근 N개만 유지

ANSWER_MAX_CHARS = 1000

# 안내 링크 A/B 중 마지막에 어떤 걸 썼는지 state에 저장해서 자동으로 번갈아 씀
LINK_A = "https://m.site.naver.com/2dTSv"  # 숨은 지원금 안내
LINK_B_DEFAULT = "https://m.site.naver.com/2ceCp"  # 미선님 블로그 대표 링크

FALLBACK_KEYWORDS = [
    "지원금", "정부지원금", "청년지원금", "소상공인 지원",
    "근로장려금", "에너지바우처", "주거급여", "출산지원금",
]

HEADERS_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# ============================================================
# 1. "네이버 지식인 답글 자동화.txt" 원문 그대로 (규칙의 100% 근거)
#    ※ 아래 내용은 업로드하신 메모 원문을 그대로 옮긴 것입니다.
#      규칙을 바꾸려면 이 문자열만 수정하면 됩니다.
# ============================================================

SYSTEM_PROMPT = r"""
너는 네이버 지식iN 외부유입을 돕는 어시스턴트야.
아래 순서대로 진행하고, 각 단계 결과를 나한테 보여준 뒤 다음으로 넘어가.

[1단계 - 질문 선별 기준]
다음 기준에 맞는 질문만 다룬다.
- 질문이 구체적이어서 실제 정부 지원 제도와 연결되는 것 (예: "청년 월세 지원 신청 방법")
- 정부 공식 사이트에서 근거를 찾아 정확히 답할 수 있는 것
- "돈 받고 싶어요"처럼 막연하거나, 지원금과 무관한 질문은 제외

[2단계 - 질문 찾기]
네이버 검색 MCP의 지식iN 검색을 사용한다.
- 키워드: 지원금, 정부지원금, 청년지원금, 소상공인 지원, 근로장려금, 에너지바우처, 주거급여, 출산지원금 등에서 하나 또는 조합을 골라 검색
- "지원금" 단독으로 검색하면 무관한 질문이 특히 많이 섞이므로, 검색 후 제목과 내용을 반드시 확인해 진짜 정부 지원 제도 질문인지 거를 것
- 선정한 질문의 제목과 질문 페이지 URL을 가져온다.

[3단계 - 질문 의도 파악 + 답변 내용 조사]
먼저 이 질문이 "진짜로 무엇을 묻는지" 정확히 파악한다. 이게 제일 중요하다.
- 질문 본문을 끝까지 읽고, 질문자가 알고 싶은 핵심 포인트를 1~2개로 정리한다.
- 질문 유형을 구분한다.
  · "신청 방법", "어떻게 하나요"처럼 절차를 묻는 질문 → 단계별 방법을 정확히 줘야 함
  · "주의사항", "해보신 분" → 경험/팁/주의사항 중심으로 줘야 함
그다음 실제로 검색해서 사실을 확인한다. 지어내지 말 것.
- 질문이 물어본 핵심에 직접 관련된 내용 위주로 확인
- 절차를 묻는 질문이면, 신청 단계를 1번부터 끝까지 빠짐없이 확인
- 금액·날짜 등 확실하지 않은 정보는 "정확한 내용은 직접 확인 필요"라고 표시

[4단계 - 정부 공식 링크]
조사 내용의 근거가 되는 정부 공식 페이지 링크를 찾는다.
- 정부24, 복지로, korea.kr 등 공식 사이트 우선
- 답변에 넣을 공식 링크 1개를 확정

[5단계 - 답변 작성 규칙]
전체 답변은 1,000자 이내. 진짜 사람이 정성껏 단 댓글처럼 자연스럽게 쓴다.

■ 질문에 진짜로 답하기 (제일 중요)
- 3단계에서 정리한 "질문자가 묻는 핵심"에 직접 답하는 걸 최우선으로 한다.
- 질문과 상관없는 정보를 백과사전처럼 나열하지 말 것.

■ 답변 구조 (질문 종류에 맞게)
- "신청 방법", "어떻게 하나요"처럼 절차를 묻는 질문이면,
  방법은 반드시 1. 2. 3. 순서대로 명확하게 적는다. (이건 번호 매겨도 됨)
- 그 외 일반 설명은 보고서처럼 딱딱하게 나열하지 말고 말하듯이 풀어쓴다.
- 단, 어떤 질문이든 아래 3가지는 꼭 들어가게 한다.
  ① 핵심 답변 (방법을 물었으면 단계별로 정확히)
  ② 실제 해본 사람 입장의 주의사항 1~2개
  ③ 구체적 예시 1개

■ AI 티 내지 않기 (꼭 지킬 것)
- "~할 수 있습니다", "~하시기 바랍니다" 같은 딱딱한 안내문 말투 금지. 말하듯이 편하게.
- "제 경험상 다음과 같이 정리해보았습니다" 같은 기계적인 도입 금지.
- 절차(방법) 외의 일반 설명까지 1.2.3. 번호로 보고서처럼 만들지 말 것.
- 모든 답변을 똑같은 틀로 찍어내지 말 것. 인사·표현·문장 길이를 매번 조금씩 다르게.
- 과한 이모티콘·과한 친절 금지. 딱 사람이 댓글 달듯이.

(1) 도입 - 인사 + 상황 되짚기 + 공감
- "안녕하세요~"로 시작하되, 질문 상황을 한 문장으로 되짚어준다.
- 그 뒤에 짧게 공감 한마디.
- 호칭은 "질문자님".

(2) 경험 말투
- 내가 직접 알아보거나 해본 것처럼 풀어쓴다.
  "저도 알아보니까~", "저도 해보니까 이 부분이~", "주변에서 받아본 분들 보니까~"

(3) 핵심 답변 - 단정하지 말고 현실적으로
- 질문 핵심에 먼저 답한다.
- "무조건 됩니다/안 됩니다"로 단정하지 말 것. "~인 경우가 많아요", "지자체·제도마다 다를 수 있어요"처럼.
- "무조건", "꿀팁" 같은 광고성 표현 금지.

(4) 주의사항 (꼭 포함)
- 실제 신청해본 사람 입장에서 놓치기 쉬운 점 1~2개를 알려준다.

(5) 구체적 예시 (꼭 포함)
- "예를 들어 [나이/지역/소득/상황]이면 대략 [금액/혜택]~" 형식으로 하나.
- 금액은 조사 기준, 불확실하면 '대략/예상'으로.

(6) 행동 가이드
- 질문자가 다음에 뭘 하면 되는지 한 가지 알려준다.

(7) 정부 공식 링크
- 확정한 정부 공식 링크를 자연스럽게 포함.
- 정보 출처를 솔직하게 밝힌다.

(8) 마지막 안내 링크 (매번 다르게, 번갈아 사용)
- 답변 끝쯤에 "이거 말고도 받을 수 있는 거 더 있어요" 식 안내를 한 줄.
- 멘트는 질문 상황(청년/소상공인/육아·출산/주거·에너지)에 맞춰 매번 새로. 같은 문장 반복 금지.
- 링크는 아래 둘 중 하나만. 매번 같은 걸 쓰지 말고 번갈아 사용. 한 답변에 둘 다 넣지 말 것.
    A) 숨은 지원금 안내: https://m.site.naver.com/2dTSv
    B) 내 블로그 정리글: (질문 내용과 맞는 블로그 글이 있으면 그 주소)
  · 질문 내용이 내 블로그 정리글과 잘 맞으면 B, 아니면 A.

(9) 마무리 - 채택 유도 (매번 살짝 변형)
- 답변 맨 끝에 친근하게 채택을 부탁하는 한 줄.

■ 언어 규칙 (꼭 지킬 것)
- 반드시 한국어(한글)로만 작성해라.
- 한자, 일본어(히라가나/가타카나), 영어 단어를 절대 섞지 마라.
  (숫자, %, 원, URL 주소는 예외로 허용한다)
- "行政", "こちら", "questions" 같은 다른 언어 단어가 한 글자라도 섞이면 안 된다.

체크리스트: ① 질문 핵심에 답했는가 ② 방법 질문이면 단계가 1.2.3.으로 정확한가
③ 주의사항이 들어갔는가 ④ 구체적 예시가 들어갔는가 ⑤ 1,000자 이내인가
"""

CLASSIFY_INSTRUCTIONS = """
너는 아래 질문 하나가 "다뤄도 되는 질문"인지 판정한다. (정부 지원금·정책에 국한하지 않는다)
- 질문이 구체적이고, 주어진 "매칭 블로그 주제"와 실제로 관련이 있어서
  그 주제 지식으로 정확히 답할 수 있는가
- 신뢰할 수 있는 근거(공식 사이트, 검증 가능한 정보)로 답할 수 있는 내용인가
- "그냥 도와주세요", "아무거나 알려주세요"처럼 막연하거나, 매칭 주제와 무관하면 탈락
반드시 아래 JSON 형식으로만 답해라. 다른 말은 절대 추가하지 마라.
{"fit": true 또는 false, "reason": "한 문장 이유"}
"""

TOPIC_ANCHORED_ANSWER_NOTE = """
[중요 - 주제 범위 안내]
이 자동화는 정부 지원금/정책에 국한하지 않고, 미선님의 블로그가 다루는
다양한 생활정보 주제(교통, 건강, 소비자 정보, 여행, 심리테스트 등 포함) 전반에서
지식iN 질문을 찾는다. 아래 5단계 답변 작성 규칙(어투, 구조, 체크리스트)은
주제와 무관하게 그대로 적용하되, [4단계 정부 공식 링크] 부분은
"이 주제에 맞는 신뢰할 수 있는 출처"로 유연하게 적용해라
(정부 지원 주제면 정부24/복지로/korea.kr, 그 외 주제면 공식 기관·검증된 정보처).
"""

# ============================================================
# 2. 유틸
# ============================================================

def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", file=sys.stderr)


def kst_now():
    return datetime.now(timezone.utc) + timedelta(hours=9)


def load_state():
    if not os.path.exists(STATE_PATH):
        return {"used_ids": [], "last_link_choice": "A", "log": []}
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    state["used_ids"] = state["used_ids"][-MAX_STATE_KEEP:]
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def qid_of(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


# ============================================================
# 3. 블로그 주제 가져오기 (쓰레드 자동화에서 쓰는 posts/shared.json과 같은 구조)
#    각 항목: id, text(글 본문), link_label, link(그 글 전용 단축링크)
#    -> 지식iN 질문 검색 키워드도 이 주제들에서 뽑고,
#       매칭된 질문의 답변에는 그 주제의 link/link_label을 그대로 사용한다.
# ============================================================

BLOG_TOPICS_PATH = os.environ.get("BLOG_TOPICS_PATH", "posts/blog_topics.json")


def load_blog_topics():
    if not os.path.exists(BLOG_TOPICS_PATH):
        log(f"블로그 주제 파일 없음({BLOG_TOPICS_PATH}) - FALLBACK_KEYWORDS만 사용")
        return []
    with open(BLOG_TOPICS_PATH, "r", encoding="utf-8") as f:
        topics = json.load(f)
    log(f"블로그 주제 {len(topics)}개 로드")
    return topics


def topic_search_query(topic):
    """포스트 본문 첫 줄에서 이모지/특수문자를 걷어내고 검색 쿼리로 쓸 문구를 뽑는다."""
    import re
    first_line = (topic.get("text") or "").split("\n")[0]
    cleaned = re.sub(r"[^\w가-힣0-9?~\s]", " ", first_line)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:40]


# ============================================================
# 4. 네이버 지식iN 검색
# ============================================================

def search_kin(query, display=20, sort="date"):
    url = "https://openapi.naver.com/v1/search/kin.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {"query": query, "display": display, "sort": sort}
    r = requests.get(url, headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("items", [])


def strip_tags(s):
    return (s or "").replace("<b>", "").replace("</b>", "")


# ============================================================
# 5. Groq 호출 (분류 + 답변 생성)
# ============================================================

def groq_chat(messages, temperature=0.7, max_tokens=1200, retries=3):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=60)
            if r.status_code == 429:
                wait = 5 * (attempt + 1)
                log(f"Groq 429(레이트리밋), {wait}초 대기 후 재시도")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            log(f"Groq 호출 실패(시도 {attempt+1}/{retries}): {e}")
            time.sleep(3)
    raise RuntimeError("Groq 호출이 반복적으로 실패했습니다.")


def classify_question(title, description, matched_topic=None):
    topic_hint = ""
    if matched_topic:
        topic_first_line = (matched_topic.get("text") or "").split("\n")[0]
        topic_hint = f"\n매칭 블로그 주제: {topic_first_line} ({matched_topic.get('link_label','')})"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + CLASSIFY_INSTRUCTIONS},
        {"role": "user", "content": f"질문 제목: {title}\n질문 요약(검색결과 스니펫): {description}{topic_hint}"},
    ]
    raw = groq_chat(messages, temperature=0.0, max_tokens=200)
    try:
        # 코드펜스가 붙어 나오는 경우 제거
        cleaned = raw.strip().strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
        parsed = json.loads(cleaned)
        return bool(parsed.get("fit")), parsed.get("reason", "")
    except Exception:
        log(f"분류 응답 파싱 실패, 탈락 처리: {raw[:200]}")
        return False, "파싱 실패"


def fetch_question_body(url):
    """지식iN 질문 페이지에서 본문 텍스트를 최대한 뽑아온다.
    실패하면 빈 문자열을 반환하고, 검색 스니펫으로 대체한다."""
    try:
        r = requests.get(url, headers=HEADERS_UA, timeout=15)
        r.raise_for_status()
        html = r.text
        # 아주 러프한 본문 추출 (구조 바뀌면 실패할 수 있음 - 실패해도 스니펫으로 대체됨)
        import re
        text = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
        text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:4000]
    except Exception as e:
        log(f"질문 본문 크롤링 실패(스니펫으로 대체): {e}")
        return ""


def research_official_source(topic_query):
    """신뢰할 수 있는 출처 1개를 찾는다.
    구글 CSE는 정부기관 도메인(go.kr/korea.kr 등)으로 한정 설정되어 있어서
    정부 지원 관련 주제에는 잘 맞고, 그 외 일반 생활정보 주제에서는
    결과가 없을 수 있다 - 이 경우 네이버 웹문서 검색(전체 대상)으로 대체한다."""
    if GOOGLE_CSE_KEY and GOOGLE_CSE_ID:
        try:
            r = requests.get(
                "https://www.googleapis.com/customsearch/v1",
                params={"key": GOOGLE_CSE_KEY, "cx": GOOGLE_CSE_ID, "q": topic_query, "num": 3},
                timeout=15,
            )
            r.raise_for_status()
            items = r.json().get("items", [])
            if items:
                return {"title": items[0].get("title", ""), "link": items[0].get("link", "")}
        except Exception as e:
            log(f"Google CSE 조회 실패: {e}")

    # 대체: 네이버 웹문서 검색 (주제 제한 없이 일반 검색)
    try:
        headers = {
            "X-Naver-Client-Id": NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        }
        r = requests.get(
            "https://openapi.naver.com/v1/search/webkr.json",
            headers=headers,
            params={"query": topic_query, "display": 5},
            timeout=15,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
        # 정부기관 도메인이 섞여 있으면 우선하되(가능하면 공식 출처가 더 신뢰도 높음),
        # 없으면 첫 번째 검색결과를 그대로 사용한다.
        official = [it for it in items if any(
            d in it.get("link", "") for d in ["go.kr", "korea.kr", "or.kr"]
        )]
        pick = (official or items or [None])[0]
        if pick:
            return {"title": strip_tags(pick.get("title", "")), "link": pick.get("link", "")}
    except Exception as e:
        log(f"네이버 웹문서 검색 실패: {e}")

    return {"title": "", "link": ""}


def build_answer(title, body_text, official_source, link_choice, blog_url_hint="", blog_link_label=""):
    b_desc = ""
    if blog_url_hint:
        b_desc = f"{blog_url_hint}" + (f" (이 글 주제: {blog_link_label})" if blog_link_label else "")

    context = f"""
[이번에 답변할 질문]
제목: {title}
본문(크롤링 결과, 없을 수도 있음): {body_text[:2500] if body_text else "(본문을 가져오지 못함 - 제목만으로 신중하게 판단)"}

[조사된 정부 공식 출처]
제목: {official_source.get('title','')}
링크: {official_source.get('link','') or '(확실한 공식 링크를 못 찾음 - 이 경우 "정확한 내용은 관할 기관에 직접 확인 필요"라고 답변에 명시할 것)'}

[8단계 마지막 안내 링크 지정]
이번 답변에는 반드시 "{link_choice}" 링크만 사용해라.
- A로 지정된 경우: https://m.site.naver.com/2dTSv
- B로 지정된 경우: {b_desc or "(적합한 블로그 글 주소가 없으면 A를 대신 사용해라)"}
  (B는 이 질문 주제와 실제로 매칭된 미선님의 블로그 글이다. link_label이 있으면
   그 글이 어떤 내용인지 알려주는 힌트이니, 안내 문구를 그 글 내용에 맞게 자연스럽게 써라.
   예: link_label이 "신청 방법 및 상세 조건"이면 "신청 방법 정리해둔 글이 있어요" 같은 식으로.)

지금부터 위 시스템 규칙(1~9단계, 특히 5단계 작성 규칙)을 100% 지켜서
이 질문에 대한 최종 답변 "본문 텍스트만" 출력해라.
- 절대 1,000자를 넘기지 마라.
- 제목, 안내문, "답변:" 같은 라벨을 붙이지 말고 댓글 본문만 출력해라.
"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + TOPIC_ANCHORED_ANSWER_NOTE},
        {"role": "user", "content": context},
    ]
    answer = groq_chat(messages, temperature=0.85, max_tokens=900)
    answer = answer.strip().strip('"').strip()

    import re
    # 한글 + 영어/숫자/기본 기호(URL, 문장부호용) + 자주 쓰는 특수기호만 허용.
    # 그 외 문자(한자, 일본어, 베트남어 등 어떤 다른 언어든)는 전부 "이물질"로 간주.
    allowed_pattern = re.compile(
        r"[\uAC00-\uD7A3\u1100-\u11FF\u3130-\u318F"  # 한글(완성형/자모)
        r"\u0020-\u007E"                              # 영어/숫자/ASCII 기호(URL 포함)
        r"\u00B7\u2013\u2014\u2018\u2019\u201C\u201D"  # 가운뎃점, 대시, 스마트따옴표
        r"\u2022\u2026\u00B0\u20A9\n\r\t]"             # 불릿, 말줄임표, 도, 원화기호
    )
    foreign_pattern = re.compile(f"[^{allowed_pattern.pattern[1:-1]}]")

    if foreign_pattern.search(answer):
        found = set(foreign_pattern.findall(answer))
        log(f"답변에 이물질 문자 발견({found}) - 순수 한국어로 재요청")
        clean_messages = messages + [
            {"role": "assistant", "content": answer},
            {"role": "user", "content": "방금 답변에 한글이 아닌 다른 언어 문자(한자, 일본어, "
                                         "베트남어, 기타 외국어 등)가 섞여 나왔다. "
                                         "같은 내용을 유지하되 100% 한국어(한글)로만 다시 써라. "
                                         "URL과 숫자를 제외한 다른 언어 문자를 절대 쓰지 마라. "
                                         "본문만 출력해라."},
        ]
        answer = groq_chat(clean_messages, temperature=0.5, max_tokens=900).strip().strip('"').strip()

    if foreign_pattern.search(answer):
        log("재요청에도 여전히 이물질 문자 발견 - 강제로 제거")
        answer = foreign_pattern.sub("", answer)
        answer = re.sub(r"\s{2,}", " ", answer).strip()

    if len(answer) > ANSWER_MAX_CHARS:
        log(f"답변이 {len(answer)}자라 축약 재요청")
        shrink_messages = messages + [
            {"role": "assistant", "content": answer},
            {"role": "user", "content": f"방금 답변이 {len(answer)}자로 1,000자를 넘었다. "
                                         f"같은 내용을 유지하되 반드시 1,000자 이내로 다시 써라. "
                                         f"본문만 출력해라."},
        ]
        answer = groq_chat(shrink_messages, temperature=0.5, max_tokens=900).strip().strip('"').strip()

    if len(answer) > ANSWER_MAX_CHARS:
        log("재요청에도 여전히 길어서 하드컷 + 경고 표시")
        answer = answer[:ANSWER_MAX_CHARS - 20].rstrip() + " ...(자동 절삭됨, 확인 필요)"

    return answer


# ============================================================
# 6. 카카오톡 "나에게 보내기" - 메시지 2개로 나눠 전송
#    카카오 기본 텍스트 템플릿은 미리보기가 길면 잘리고, "자세히 보기"가
#    안정적으로 안 열리는 문제가 있었다. 외부 리소스(Gist 등)를 자동 생성하는
#    방식은 GitHub 어뷰징 탐지에 걸릴 위험이 있어서 완전히 제거했고,
#    대신 답변을 앞/뒤 2개 메시지로 나눠 각각 그대로 전송한다.
#    (미선님 확인 요청에 따른 방식 - 각 메시지는 미리보기 잘림이 없도록
#    충분히 짧게 나눈다)
# ============================================================

def split_answer(answer, max_len=450):
    """답변을 자연스러운 지점(줄바꿈 또는 공백)에서 앞/뒤 2부분으로 나눈다."""
    if len(answer) <= max_len:
        return [answer]

    mid = len(answer) // 2
    # 중간 지점 근처에서 줄바꿈을 먼저 찾고, 없으면 공백을 찾는다.
    cut = answer.rfind("\n\n", 0, mid + 100)
    if cut < mid * 0.4:
        cut = answer.rfind("\n", 0, mid + 100)
    if cut < mid * 0.4:
        cut = answer.rfind(" ", 0, mid + 100)
    if cut < mid * 0.4:
        cut = mid

    part1 = answer[:cut].rstrip()
    part2 = answer[cut:].lstrip()
    return [part1, part2]


def send_kakao_text(text, url):
    template_object = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": url, "mobile_web_url": url},
    }
    resp = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={
            "Authorization": f"Bearer {KAKAO_ACCESS_TOKEN}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"template_object": json.dumps(template_object, ensure_ascii=False)},
        timeout=15,
    )
    if resp.status_code != 200:
        log(f"카카오 전송 실패({resp.status_code}): {resp.text}")
        return False, resp.text
    return True, None


def send_kakao_memo(title, url, answer):
    parts = split_answer(answer)

    if len(parts) == 1:
        text = f"[질문 제목]\n{title}\n\n[질문 링크]\n{url}\n\n[복붙용 답변]\n{parts[0]}"
        return send_kakao_text(text, url)

    # 1/2: 질문 정보 + 답변 앞부분
    text1 = (
        f"[질문 제목]\n{title}\n\n[질문 링크]\n{url}\n\n"
        f"[복붙용 답변 1/2]\n{parts[0]}"
    )
    # 2/2: 답변 뒷부분 (이어붙이면 전체 답변이 되도록)
    text2 = f"[복붙용 답변 2/2]\n{parts[1]}"

    ok1, err1 = send_kakao_text(text1, url)
    if not ok1:
        return False, err1

    time.sleep(1)  # 두 메시지가 순서대로 도착하도록 약간의 간격

    ok2, err2 = send_kakao_text(text2, url)
    if not ok2:
        log(f"1/2는 전송됐지만 2/2 전송 실패: {err2}")
        return False, err2

    return True, None


# ============================================================
# 7. 메인 파이프라인
# ============================================================

def main():
    state = load_state()
    used_ids = set(state.get("used_ids", []))
    last_choice = state.get("last_link_choice", "A")

    blog_topics = load_blog_topics()
    random.shuffle(blog_topics)

    # (검색쿼리, 매칭된 블로그 주제 dict 또는 None) 목록. 블로그 주제를 우선 쓰고,
    # 그걸로 목표치를 못 채우면 FALLBACK_KEYWORDS로 보충한다.
    search_plan = [(topic_search_query(t), t) for t in blog_topics]
    search_plan += [(kw, None) for kw in FALLBACK_KEYWORDS]

    log(f"검색 계획 {len(search_plan)}건 (블로그 주제 {len(blog_topics)}개 + 보충 키워드 {len(FALLBACK_KEYWORDS)}개)")

    picked = []  # [(title, url, description, matched_topic_or_None)]
    tried = 0

    for query, matched_topic in search_plan:
        if len(picked) >= BATCH_TARGET:
            break
        if not query:
            continue
        tried += 1
        try:
            items = search_kin(query, display=20, sort="date")
        except Exception as e:
            log(f"'{query}' 검색 실패: {e}")
            continue

        random.shuffle(items)
        for it in items:
            if len(picked) >= BATCH_TARGET:
                break
            link = it.get("link", "")
            if not link:
                continue
            qid = qid_of(link)
            if qid in used_ids:
                continue
            title = strip_tags(it.get("title", ""))
            desc = strip_tags(it.get("description", ""))

            fit, reason = classify_question(title, desc, matched_topic)
            log(f"[분류] {fit} | {title[:40]} | (검색어: {query}) | {reason}")
            if not fit:
                used_ids.add(qid)  # 탈락한 것도 재검토 방지 위해 기록(선택 사항)
                continue

            picked.append((title, link, desc, matched_topic))
            used_ids.add(qid)
            time.sleep(1)  # Groq/네이버 호출 사이 완충

    log(f"이번 배치에서 선정된 질문 수: {len(picked)} (시도한 검색어 {tried}개)")

    sent_count = 0
    for title, link, desc, matched_topic in picked:
        try:
            body = fetch_question_body(link)
            topic_query = title[:40]
            official = research_official_source(topic_query)

            # A/B를 번갈아 쓴다. B는 이 질문과 매칭된 블로그 글의 전용 링크,
            # 매칭된 글이 없으면(보충 키워드로 찾은 경우) 대표 블로그 링크로 대체.
            link_choice = "A" if last_choice == "B" else "B"
            if matched_topic:
                blog_url_hint = matched_topic.get("link", "") or LINK_B_DEFAULT
                blog_link_label = matched_topic.get("link_label", "")
            else:
                blog_url_hint = LINK_B_DEFAULT
                blog_link_label = ""
            last_choice = link_choice

            answer = build_answer(title, body, official, link_choice, blog_url_hint, blog_link_label)

            ok, err = send_kakao_memo(title, link, answer)
            if ok:
                sent_count += 1
                log(f"카톡 전송 완료: {title[:40]}")
            else:
                log(f"카톡 전송 실패로 이 건은 건너뜀: {title[:40]} / {err}")

            time.sleep(2)
        except Exception as e:
            log(f"카톡 전송 중 오류(건너뜀): {title[:40]} / {e}")
            continue

    state["used_ids"] = list(used_ids)
    state["last_link_choice"] = last_choice
    state.setdefault("log", [])
    state["log"].append({
        "run_at_kst": kst_now().isoformat(),
        "batch_target": BATCH_TARGET,
        "picked": len(picked),
        "sent": sent_count,
    })
    state["log"] = state["log"][-200:]
    save_state(state)

    log(f"완료: 목표 {BATCH_TARGET} / 선정 {len(picked)} / 전송 성공 {sent_count}")


if __name__ == "__main__":
    main()
