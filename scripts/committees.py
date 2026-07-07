"""22대 국회 상임위 공용 정의 — 단일 출처 (single source of truth).

crawl_pdfs·extractor_v1·manifest_builder·inspect_pdf_samples 4곳에 흩어져 있던
중복 정의 통합 (2026-07-07). 23대 개원이나 위원회 개칭 시 이 파일 하나만 고친다
— 기재위는 이미 기획재정위원회 → 재정경제기획위원회 개칭 이력이 있다.
"""

ASSEMBLY_NO = 22
ASSEMBLY_BEGIN_DATE = "20240530"   # 개원일 — 크롤링 수집 시작일

# (폴더 약칭, 정식명, 국회 포털 committeeCd — API cmmnCdList 실측값)
COMMITTEES = [
    ("과방위",    "과학기술정보방송통신위원회",    "9700479"),
    ("외통위",    "외교통일위원회",              "9700409"),
    ("정무위",    "정무위원회",                  "9700008"),
    ("기재위",    "재정경제기획위원회",           "9700590"),
    ("행안위",    "행정안전위원회",              "9700480"),
    ("복지위",    "보건복지위원회",              "9700341"),
    ("국토위",    "국토교통위원회",              "9700407"),
    ("산자중기위", "산업통상자원중소벤처기업위원회", "9700481"),
    ("국방위",    "국방위원회",                  "9700019"),
]

FOLDER_TO_COMMITTEE = {folder: name for folder, name, _ in COMMITTEES}
FOLDER_TO_CD = {folder: cd for folder, _, cd in COMMITTEES}
