"""최종 평가에 사용할 백엔드 코드·모델 설정 해시를 동결하거나 검증한다."""

from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import datetime
from pathlib import Path


def load_runner(script_dir: Path):
    path = script_dir / "run_baseline.py"
    spec = importlib.util.spec_from_file_location("run_baseline_runtime_freeze", path)
    if not spec or not spec.loader:
        raise RuntimeError("run_baseline module load failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot(repo: Path, runner) -> dict:
    return {
        "git_head": runner.git_head(repo),
        "safe_settings": runner.safe_settings(repo / ".env"),
        "runtime_sha256": runner.runtime_hashes(repo),
    }


def differences(expected: dict, actual: dict) -> list[str]:
    changed: list[str] = []
    for key in ("git_head", "safe_settings", "runtime_sha256"):
        if expected.get(key) != actual.get(key):
            changed.append(key)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    runner = load_runner(Path(__file__).parent)
    current = snapshot(args.repo, runner)
    if args.verify:
        if not args.manifest.exists():
            raise SystemExit("runtime manifest missing")
        expected = json.loads(args.manifest.read_text(encoding="utf-8"))
        changed = differences(expected, current)
        result = {"status": "PASS" if not changed else "FAIL", "changed": changed}
        print(json.dumps(result, ensure_ascii=False))
        return 0 if not changed else 1
    if args.manifest.exists():
        raise SystemExit("runtime manifest already exists; use --verify instead of overwriting")
    manifest = {
        "schema_version": "1.0",
        "frozen_at": datetime.now().astimezone().isoformat(),
        **current,
        "runtime_file_count": len(current["runtime_sha256"]),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "FROZEN", "files": manifest["runtime_file_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
