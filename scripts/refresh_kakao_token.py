#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
카카오 액세스 토큰 갱신.
- refresh_token으로 새 access_token(+ 필요시 새 refresh_token)을 발급받는다.
- 새 토큰 값을 $GITHUB_OUTPUT에 써서, 이후 워크플로 스텝에서
  1) 이번 실행에 바로 사용하고
  2) gh secret set 으로 리포지토리 시크릿을 갱신하는 데 쓴다.

필요 환경변수:
  KAKAO_REST_API_KEY   : 카카오 앱의 REST API 키
  KAKAO_REFRESH_TOKEN  : 최초 1회 수동 발급받은 refresh_token (이후 자동 갱신)
"""

import os
import sys
import requests

REST_API_KEY = os.environ["KAKAO_REST_API_KEY"]
REFRESH_TOKEN = os.environ["KAKAO_REFRESH_TOKEN"]
CLIENT_SECRET = os.environ.get("KAKAO_CLIENT_SECRET", "")  # 활성화된 경우 필수

data = {
    "grant_type": "refresh_token",
    "client_id": REST_API_KEY,
    "refresh_token": REFRESH_TOKEN,
}
if CLIENT_SECRET:
    data["client_secret"] = CLIENT_SECRET

resp = requests.post(
    "https://kauth.kakao.com/oauth/token",
    data=data,
    timeout=15,
)

if resp.status_code != 200:
    print(f"토큰 갱신 실패: {resp.status_code} {resp.text}", file=sys.stderr)
    sys.exit(1)

data = resp.json()
access_token = data["access_token"]
# 카카오는 남은 유효기간에 따라 refresh_token을 새로 안 줄 수도 있음(그럴 땐 기존 값 유지)
new_refresh_token = data.get("refresh_token", REFRESH_TOKEN)

github_output = os.environ.get("GITHUB_OUTPUT")
if github_output:
    with open(github_output, "a", encoding="utf-8") as f:
        f.write(f"access_token={access_token}\n")
        f.write(f"refresh_token={new_refresh_token}\n")
else:
    # 로컬 테스트용
    print(f"ACCESS_TOKEN={access_token}")
    print(f"REFRESH_TOKEN={new_refresh_token}")
