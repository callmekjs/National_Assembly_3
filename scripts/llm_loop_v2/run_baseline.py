"""신규 개발문제를 현재 /query API에 보내 원본 결과를 보존한다.

채점 로직과 분리되어 있으며, 정답 데이터는 API 요청에 포함하지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


SAFE_ENV_KEYS = {
    "ANSWER_MODEL",
    "ANSWER_EFFORT",
    "RERANKER_ENABLED",
    "RERANKER_MODEL",
    "RERANKER_EFFORT",
}

RUNTIME_FILES = (
    "backend/answer.py",
    "backend/verification.py",
    "backend/party.py",
    "backend/search_hybrid.py",
    "backend/search_vector.py",
    "backend/reranker.py",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_settings(env_path: Path) -> dict[str, str]:
    settings: dict[str, str] = {}
    if not env_path.exists():
        return settings
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        if not raw or raw.lstrip().startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        if key.strip() in SAFE_ENV_KEYS:
            settings[key.strip()] = value.strip()
    # 실제 서버를 시작한 셸의 값이 .env보다 우선한다. 비밀 키는 허용 목록에 없으므로
    # 기록되지 않는다.
    for key in SAFE_ENV_KEYS:
        if key in os.environ:
            settings[key] = os.environ[key]
    return settings


def runtime_hashes(repo: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    relatives = set(RUNTIME_FILES)
    backend = repo / "backend"
    if backend.exists():
        relatives.update(
            path.relative_to(repo).as_posix()
            for path in backend.rglob("*.py")
            if ".venv" not in path.parts and "__pycache__" not in path.parts
        )
        if (backend / "requirements.txt").exists():
            relatives.add("backend/requirements.txt")
    evaluator_dir = repo / "scripts" / "llm_loop_v2"
    if evaluator_dir.exists():
        relatives.update(
            path.relative_to(repo).as_posix()
            for path in evaluator_dir.glob("*.py")
            if "__pycache__" not in path.parts
        )
    frontend_api = repo / "frontend" / "src" / "api.js"
    if frontend_api.exists():
        relatives.add("frontend/src/api.js")
    for relative in sorted(relatives):
        path = repo / relative
        if not path.exists():
            raise FileNotFoundError(f"runtime file missing: {relative}")
        hashes[relative] = sha256(path)
    return hashes


def write_or_verify_config(path: Path, current: dict[str, Any]) -> dict[str, Any]:
    immutable = {
        "git_head", "dataset", "dataset_sha256", "base_url", "requested_records",
        "safe_settings", "runtime_sha256", "max_additional_cost", "evaluation_mode",
    }
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        changed = [key for key in sorted(immutable) if existing.get(key) != current.get(key)]
        if changed:
            raise ValueError(f"refusing mixed resume; config changed: {changed}")
        return existing
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return current


def git_head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def accumulated_cost(rows: list[dict[str, Any]]) -> float:
    total = 0.0
    for row in rows:
        response = row.get("response") or {}
        usage = response.get("usage") or {}
        total += float(usage.get("est_cost_usd") or 0.0)
    return total


def successful_result_ids(rows: list[dict[str, Any]]) -> set[str]:
    """검증 가능한 성공 결과만 재시작 완료 ID로 인정한다.

    ``results.jsonl``은 문항별 성공 결과가 정확히 한 줄인 감사 원장이다. 실패 시도는
    ``errors.jsonl``에 분리해야 재시도 가능하고, 나중에 성공해도 중복 ID가 생기지 않는다.
    """
    ids = [str(row.get("id") or "") for row in rows]
    if any(not row_id for row_id in ids):
        raise ValueError("results.jsonl contains a row without id")
    if len(ids) != len(set(ids)):
        raise ValueError("results.jsonl contains duplicate ids; use a fresh output-dir")
    failed = [
        row_id for row_id, row in zip(ids, rows)
        if row.get("http_status") != 200 or row.get("error")
    ]
    if failed:
        raise ValueError(
            "results.jsonl contains failed attempts; use a fresh output-dir: "
            + ", ".join(failed)
        )
    return set(ids)


def query_body(record: dict[str, Any]) -> dict[str, Any]:
    """프론트와 같은 의미 입력을 사용한다. gold 필터는 API에 정답 힌트로 넣지 않는다."""
    return {
        "question": record["question"],
        "mode": record["mode"],
        # 일반 UI 응답에는 노출하지 않고 평가 실행에서만 검색 단계별 순위를 보존한다.
        "include_trace": True,
    }


def request_query(base_url: str, record: dict[str, Any], timeout: int) -> tuple[int, dict[str, Any]]:
    body = query_body(record)
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/query",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(payload)
        except json.JSONDecodeError:
            detail = {"raw": payload}
        return exc.code, {"error": detail}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids", nargs="*", help="지정한 문항 ID만 실행")
    parser.add_argument(
        "--max-additional-cost", type=float,
        help="이 output-dir 실행에서 허용할 OpenAI 추정 비용 상한(USD)",
    )
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[2]
    records = load_jsonl(args.dataset)
    if args.ids:
        requested_ids = set(args.ids)
        records = [record for record in records if record["id"] in requested_ids]
        missing = sorted(requested_ids - {record["id"] for record in records})
        if missing:
            raise SystemExit(f"unknown ids: {missing}")
    if args.limit is not None:
        records = records[: args.limit]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result_path = args.output_dir / "results.jsonl"
    error_path = args.output_dir / "errors.jsonl"
    existing_results = load_jsonl(result_path) if result_path.exists() else []
    existing_errors = load_jsonl(error_path) if error_path.exists() else []
    completed = successful_result_ids(existing_results)
    # 실패한 API 시도도 usage를 포함할 수 있으므로 비용 상한에서는 제외하지 않는다.
    spent = accumulated_cost(existing_results) + accumulated_cost(existing_errors)

    config = {
        "run_id": args.output_dir.name,
        "started_at": datetime.now().astimezone().isoformat(),
        "git_head": git_head(repo),
        "dataset": str(args.dataset),
        "dataset_sha256": sha256(args.dataset),
        "base_url": args.base_url,
        "requested_records": len(records),
        "max_additional_cost": args.max_additional_cost,
        "evaluation_mode": "retrieval_e2e",
        "safe_settings": safe_settings(repo / ".env"),
        "runtime_sha256": runtime_hashes(repo),
    }
    write_or_verify_config(args.output_dir / "config.json", config)

    with (
        result_path.open("a", encoding="utf-8", newline="\n") as output,
        error_path.open("a", encoding="utf-8", newline="\n") as errors,
    ):
        for index, record in enumerate(records, start=1):
            if record["id"] in completed:
                continue
            if args.max_additional_cost is not None and spent >= args.max_additional_cost:
                raise SystemExit(f"baseline cost cap reached: ${spent:.4f}")
            started = time.perf_counter()
            try:
                status, response = request_query(args.base_url, record, args.timeout)
                error = None
            except Exception as exc:
                status, response = 0, {}
                error = {"type": type(exc).__name__, "message": str(exc)}
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            row = {
                "id": record["id"],
                "question": record["question"],
                "request": {
                    key: value for key, value in query_body(record).items() if key != "question"
                },
                "http_status": status,
                "elapsed_ms": elapsed_ms,
                "response": response,
                "error": error,
                "finished_at": datetime.now().astimezone().isoformat(),
            }
            success = status == 200 and error is None
            ledger = output if success else errors
            ledger.write(json.dumps(row, ensure_ascii=False) + "\n")
            ledger.flush()
            spent += accumulated_cost([row])
            grounding = response.get("grounding", "ERROR") if isinstance(response, dict) else "ERROR"
            print(
                f"[{index}/{len(records)}] {record['id']} status={status} "
                f"grounding={grounding} {elapsed_ms}ms cost=${spent:.4f}",
                flush=True,
            )
            if not success:
                raise SystemExit(f"baseline call failed: {record['id']} status={status}")
            if args.max_additional_cost is not None and spent > args.max_additional_cost:
                raise SystemExit(f"baseline cost cap crossed by final recorded call: ${spent:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
