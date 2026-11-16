# -*- coding: utf-8 -*-
"""把本地文档目录灌进 MaxKB 知识库（md / docx / pdf / txt）。

已验证的 MaxKB v2.10.8 接口（前缀是 /admin/api，不是 /api）：
  POST {API}/user/login                    {username, password} -> data.token
  GET  {API}/workspace/{ws}/knowledge/base 知识库列表（按 name 找到 id）
  POST {API}/workspace/{ws}/knowledge/{kid}/document/split   multipart file -> data（切分结果）
  POST {API}/workspace/{ws}/knowledge/{kid}/document         {name, paragraphs} -> 入库

注意：直接 POST document 只会建一个空壳（char_length=0、段落 0），
必须先 split 拿切分结果，再把 paragraphs 回传。

用法：
  export MAXKB_API=http://127.0.0.1:8080/admin/api
  export MAXKB_USER=admin MAXKB_PWD=你的密码
  python scripts/ingest_docs.py --dir ./my-docs --kb my-kb
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import requests

API = os.environ.get("MAXKB_API", "http://127.0.0.1:8080/admin/api").rstrip("/")
USER = os.environ.get("MAXKB_USER", "admin")
PWD = os.environ.get("MAXKB_PWD", "")
WS = os.environ.get("MAXKB_WS", "default")
EXTS = {".md", ".txt", ".docx", ".pdf"}


def login() -> requests.Session:
    if not PWD:
        sys.exit("请设置环境变量 MAXKB_PWD（MaxKB 管理员密码）")
    s = requests.Session()
    r = s.post(API + "/user/login", json={"username": USER, "password": PWD}, timeout=20)
    r.raise_for_status()
    body = r.json()
    if body.get("code") != 200:
        sys.exit("登录失败: " + str(body))
    s.headers["Authorization"] = "Bearer " + body["data"]["token"]
    return s


def find_kb(s: requests.Session, name: str) -> str | None:
    r = s.get(API + "/workspace/" + WS + "/knowledge/base", timeout=30)
    body = r.json()
    for item in (body.get("data") or []):
        if item.get("name") == name:
            return item.get("id")
    return None


def upload(s: requests.Session, kid: str, path: Path) -> bool:
    base = API + "/workspace/" + WS + "/knowledge/" + kid + "/document"
    with path.open("rb") as fh:
        r = s.post(base + "/split", files={"file": (path.name, fh)}, timeout=300)
    body = r.json()
    if body.get("code") != 200:
        print("  split 失败:", body)
        return False
    paragraphs = body["data"]
    r = s.post(base, json={"name": path.name, "paragraphs": paragraphs}, timeout=300)
    ok = r.json().get("code") == 200
    print("  %s %s (%d 段)" % ("OK  " if ok else "FAIL", path.name, len(paragraphs)))
    return ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="文档目录")
    ap.add_argument("--kb", required=True, help="知识库名称（需先在界面里建好）")
    args = ap.parse_args()

    kid = os.environ.get("MAXKB_KID") or None
    s = login()
    if not kid:
        kid = find_kb(s, args.kb)
    if not kid:
        sys.exit("没找到知识库 %r：请先在 MaxKB 界面建一个，或直接传 MAXKB_KID" % args.kb)

    files = sorted(p for p in Path(args.dir).rglob("*") if p.suffix.lower() in EXTS)
    print("知识库 id:", kid, " 待灌文件:", len(files))
    ok = sum(1 for p in files if upload(s, kid, p))
    print("完成 %d/%d，把 id 记下来：export MAXKB_KID=%s" % (ok, len(files), kid))


if __name__ == "__main__":
    main()
