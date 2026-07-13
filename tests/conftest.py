"""pytest 전역 설정 — 배포 방어선(guard) 한도를 테스트에서 끈다.

main.py 가 import 시점에 한도 env 를 읽으므로, 어떤 테스트 모듈보다 먼저 실행되는
conftest 에서 0(끔)으로 고정 — 기존 TestClient 스위트가 연속 호출로 429 를 맞아
무작위 실패하는 것을 방지. 429 경로 자체는 test_api 가 리미터 객체 교체로 검증.
"""
import os

os.environ.setdefault("RATE_LIMIT_LLM_PER_MIN", "0")
os.environ.setdefault("RATE_LIMIT_PER_MIN", "0")
os.environ.setdefault("DAILY_COST_LIMIT_USD", "0")
