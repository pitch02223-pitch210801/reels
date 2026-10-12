#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""예약 발행 — 저장소 _queue/next.json 에 건이 들어 있으면 그걸 발행한다.

왜 이렇게 하나:
  '토요일에 올려줘' 같은 예약을 대표님 컴퓨터의 예정작업에 걸면,
  그날 컴퓨터가 꺼져 있거나 세션이 안 붙으면 그냥 안 나간다
  (2026-08-27 의왕 윤경 릴스가 실제로 그렇게 한 달 밀렸다).
  → 예약을 GitHub Actions 스케줄로 옮긴다. 사람도 Claude 도 그날 필요 없다.

_queue/next.json 형식:
  {"kind":"reel","video":"x.mp4","cover":"x.jpg","caption":"x.txt","not_before":"2026-10-17T08:00:00Z"}
  {"kind":"carousel","images":["cw/01.jpg", ...],"caption":"x.txt","not_before":"..."}
발행에 성공하면 이 파일을 {} 로 비운다. 비어 있으면 아무 것도 안 하고 끝난다.
중복 점검은 publish_reel.py 쪽 로직을 그대로 탄다.
"""
import base64, json, os, subprocess, sys, time
from datetime import datetime, timezone
import requests

REPO = os.environ.get("GITHUB_REPOSITORY", "")
GT = os.environ.get("GITHUB_TOKEN")
QPATH = "_queue/next.json"
API = f"https://api.github.com/repos/{REPO}/contents/{QPATH}"
H = {"Authorization": f"token {GT}", "Accept": "application/vnd.github+json"}


def read_queue():
    r = requests.get(API, headers=H, params={"ref": "main"}, timeout=60)
    if r.status_code != 200:
        return None, None
    j = r.json()
    try:
        return json.loads(base64.b64decode(j["content"]).decode() or "{}"), j["sha"]
    except Exception:
        return {}, j["sha"]


def clear_queue(sha, note):
    body = json.dumps({"cleared_at": datetime.now(timezone.utc).isoformat(),
                       "note": note}, ensure_ascii=False, indent=2)
    requests.put(API, headers=H, timeout=60,
                 json={"message": "queue: 발행 완료 후 비움", "branch": "main",
                       "sha": sha, "content": base64.b64encode(body.encode()).decode()})


q, sha = read_queue()
if not q or not q.get("kind"):
    print("::notice::예약 대기열이 비어 있음 — 할 일 없음")
    sys.exit(0)

nb = q.get("not_before")
if nb:
    try:
        if datetime.now(timezone.utc) < datetime.fromisoformat(nb.replace("Z", "+00:00")):
            print(f"::notice::아직 예약 시각 전({nb}) — 건너뜀")
            sys.exit(0)
    except ValueError:
        print(f"::warning::not_before 파싱 실패({nb}) — 무시하고 진행")

env = dict(os.environ)
if q["kind"] == "reel":
    env.update(MODE="publish", VIDEO=q["video"], COVER=q["cover"], CAPTION=q["caption"])
elif q["kind"] == "carousel":
    env.update(MODE="carousel", IMAGES=",".join(q["images"]), CAPTION=q["caption"])
else:
    print(f"::error::알 수 없는 kind: {q['kind']}")
    sys.exit(1)
env["FORCE"] = str(q.get("force", False)).lower()

rc = subprocess.run([sys.executable, "publish_reel.py"], env=env).returncode
if rc == 0:
    clear_queue(sha, f"{q['kind']} 발행 완료")
    print("::notice::예약 발행 완료 — 대기열 비움")
sys.exit(rc)
