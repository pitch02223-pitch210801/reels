#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Actions 에서 도는 인스타 릴스 발행기.
토큰은 Actions Secret(환경변수)에서만 읽고 절대 출력하지 않는다.

MODE=check    최근 게시물 조회 (중복 발행 점검용)
MODE=publish  VIDEO/COVER/CAPTION 으로 릴스 발행
MODE=refresh  60일 장기 토큰 갱신 후 IG_TOKEN 시크릿 갱신
"""
import base64, json, os, sys, time, requests

GRAPH = "https://graph.instagram.com"
API = os.environ.get("API_VERSION", "v23.0")
TOK = os.environ["IG_TOKEN"]
IG = os.environ["IG_USER_ID"]
MODE = os.environ.get("MODE", "check")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
RAW = f"https://raw.githubusercontent.com/{REPO}/main"


def mask(s):
    return str(s).replace(TOK, "***")


def write_status(obj):
    """결과를 저장소 _status/last_run.json 에 남긴다 (로그 다운로드가 막힌 환경 대비)."""
    import base64 as _b64
    gt = os.environ.get("GITHUB_TOKEN")
    if not gt:
        return
    obj = {k: mask(v) if isinstance(v, str) else v for k, v in obj.items()}
    obj["run_url"] = f"https://github.com/{REPO}/actions/runs/{os.environ.get('GITHUB_RUN_ID','')}"
    body = json.dumps(obj, ensure_ascii=False, indent=2)
    h = {"Authorization": f"token {gt}", "Accept": "application/vnd.github+json"}
    api = f"https://api.github.com/repos/{REPO}/contents/_status/last_run.json"
    sha = None
    g = requests.get(api, headers=h, params={"ref": "main"}, timeout=30)
    if g.status_code == 200:
        sha = g.json().get("sha")
    d = {"message": f"status: {obj.get('mode','')}", "branch": "main",
         "content": _b64.b64encode(body.encode()).decode()}
    if sha:
        d["sha"] = sha
    requests.put(api, headers=h, json=d, timeout=60)


def die(msg):
    print("::error::" + mask(msg))
    write_status({"mode": MODE, "ok": False, "error": mask(msg)})
    sys.exit(1)


def recent(limit=30):
    r = requests.get(f"{GRAPH}/{API}/{IG}/media",
                     params={"fields": "id,timestamp,permalink,media_type,caption",
                             "limit": limit, "access_token": TOK}, timeout=60)
    j = r.json()
    if "data" not in j:
        die(f"조회 실패: {json.dumps(j, ensure_ascii=False)}")
    return j["data"]


def show(items):
    print(f"최근 게시물 {len(items)}건")
    for m in items:
        first = (m.get("caption") or "").splitlines()
        first = first[0][:50] if first else ""
        print(f"  {m['timestamp']}  {m['media_type']:<13} {m['permalink']}  {first}")


def do_check():
    items = recent()
    show(items)
    write_status({"mode": "check", "ok": True,
                  "recent": [{"t": m["timestamp"], "type": m["media_type"],
                              "url": m["permalink"],
                              "head": (m.get("caption") or "").splitlines()[0][:60]
                                      if (m.get("caption") or "") else ""}
                             for m in items]})


def do_publish():
    video = os.environ["VIDEO"].strip()
    cover = os.environ["COVER"].strip()
    capf = os.environ["CAPTION"].strip()
    force = os.environ.get("FORCE", "false").lower() == "true"

    if not os.path.exists(capf):
        die(f"캡션 파일 없음: {capf}")
    caption = open(capf, encoding="utf-8").read().strip()
    head = caption.splitlines()[0].strip()

    for f in (video, cover):
        if not os.path.exists(f):
            die(f"파일 없음: {f} (저장소에 push 됐는지 확인)")

    # --- 중복 발행 점검 ---
    items = recent()
    show(items)
    dup = [m for m in items if (m.get("caption") or "").strip().startswith(head[:30])]
    if dup and not force:
        die(f"중복 의심 — 같은 첫 줄의 게시물이 이미 있음: {dup[0]['permalink']} ({dup[0]['timestamp']}). "
            f"의도한 재발행이면 force=true 로 다시 실행.")

    vurl, curl = f"{RAW}/{video}", f"{RAW}/{cover}"
    for u in (vurl, curl):
        if requests.head(u, timeout=60, allow_redirects=True).status_code != 200:
            die(f"공개 URL 접근 불가: {u}")
    print(f"video_url={vurl}")
    print(f"cover_url={curl}")

    r = requests.post(f"{GRAPH}/{API}/{IG}/media", timeout=120,
                      data={"media_type": "REELS", "video_url": vurl,
                            "cover_url": curl, "caption": caption, "access_token": TOK})
    j = r.json()
    if "id" not in j:
        die(f"컨테이너 생성 실패: {json.dumps(j, ensure_ascii=False)}")
    cid = j["id"]
    print(f"container_id={cid}")

    for i in range(20):
        time.sleep(15)
        s = requests.get(f"{GRAPH}/{API}/{cid}", timeout=60,
                         params={"fields": "status_code,status", "access_token": TOK}).json()
        code = s.get("status_code")
        print(f"  [{i+1}] status={code}")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            die(f"처리 실패: {json.dumps(s, ensure_ascii=False)}")
    else:
        die("5분 내 FINISHED 안 됨")

    p = requests.post(f"{GRAPH}/{API}/{IG}/media_publish", timeout=120,
                      data={"creation_id": cid, "access_token": TOK}).json()
    if "id" not in p:
        die(f"발행 실패: {json.dumps(p, ensure_ascii=False)}")
    mid = p["id"]
    link = requests.get(f"{GRAPH}/{API}/{mid}", timeout=60,
                        params={"fields": "permalink", "access_token": TOK}).json().get("permalink", "")
    print(f"::notice::발행 완료 {link}")
    print(f"PERMALINK={link}")
    with open(os.environ.get("GITHUB_STEP_SUMMARY", "/dev/null"), "a", encoding="utf-8") as f:
        f.write(f"## 발행 완료\n\n- {link}\n- media_id: {mid}\n")
    write_status({"mode": "publish", "ok": True, "permalink": link,
                  "media_id": mid, "video": video, "cover": cover, "caption_file": capf})


def do_refresh():
    j = requests.get(f"{GRAPH}/refresh_access_token", timeout=60,
                     params={"grant_type": "ig_refresh_token", "access_token": TOK}).json()
    new = j.get("access_token")
    if not new:
        die(f"토큰 갱신 실패: {json.dumps(j, ensure_ascii=False)}")
    pat = os.environ.get("GH_PAT")
    if not pat:
        die("GH_PAT 시크릿이 없어 갱신된 토큰을 저장할 수 없음")
    from nacl import encoding, public
    h = {"Authorization": f"token {pat}", "Accept": "application/vnd.github+json"}
    base = f"https://api.github.com/repos/{REPO}/actions/secrets"
    pk = requests.get(base + "/public-key", headers=h, timeout=30).json()
    box = public.SealedBox(public.PublicKey(pk["key"].encode(), encoding.Base64Encoder()))
    enc = base64.b64encode(box.encrypt(new.encode())).decode()
    rr = requests.put(f"{base}/IG_TOKEN", headers=h, timeout=30,
                      json={"encrypted_value": enc, "key_id": pk["key_id"]})
    if rr.status_code not in (201, 204):
        die(f"시크릿 저장 실패 {rr.status_code}")
    print(f"::notice::토큰 갱신 완료 (만료 {j.get('expires_in','?')}초 뒤)")
    write_status({"mode": "refresh", "ok": True, "expires_in": j.get("expires_in")})


{"check": do_check, "publish": do_publish, "refresh": do_refresh}.get(MODE, do_check)()
