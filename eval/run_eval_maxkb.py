# -*- coding: utf-8 -*-
"""在真实 MaxKB 容器上跑四路对比：纯向量 / 纯全文 / 原版混合 / RRF。

前提：MaxKB 已跑起来（docker run -p 8080:8080 1panel/maxkb:v2.10.8-lts），
且 corpus/docs 已通过 document/split + document 两步灌进某个知识库。

用法：
  set MAXKB_KID=<知识库 id>
  python eval/run_eval_maxkb.py
"""
from __future__ import annotations

import io
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cases_docs import CASES, OFF_DOMAIN  # noqa: E402

API = os.environ.get("MAXKB_API", "http://127.0.0.1:8080/admin/api")
USER = os.environ.get("MAXKB_USER", "admin")
PWD = os.environ.get("MAXKB_PWD", "")
if not PWD:
    raise SystemExit("请设置环境变量 MAXKB_PWD（MaxKB 管理员密码）")
KID = os.environ.get("MAXKB_KID", "")
RRF_K = 60
TOP = 20  # 每个模式取 20 段，按文档去重后看前 5


def session():
    s = requests.Session()
    tok = s.post(API + "/user/login", json={"username": USER, "password": PWD}, timeout=20)
    s.headers["Authorization"] = "Bearer " + tok.json()["data"]["token"]
    return s


def names(s, q, mode, top=TOP):
    url = API + "/workspace/default/knowledge/" + KID + "/hit_test"
    r = s.post(url, json={"query_text": q, "top_number": top,
                          "similarity": 0.0, "search_mode": mode}, timeout=120).json()
    out = []
    for x in (r.get("data") or []):
        n = x.get("document_name") or ""
        if n not in out:
            out.append(n)
    return out


def rrf(a, b, k=RRF_K):
    d = {}
    for lst in (a, b):
        for i, n in enumerate(lst):
            d[n] = d.get(n, 0.0) + 1.0 / (k + i + 1)
    return [n for n, _ in sorted(d.items(), key=lambda x: -x[1])]


def rank(lst, exp):
    for i, n in enumerate(lst):
        if exp in n:
            return i + 1
    return 99


def main():
    if not KID:
        print("请先设置环境变量 MAXKB_KID"); return
    s = session()
    agg = {"embedding": 0, "keywords": 0, "blend": 0, "rrf": 0}
    rows = []
    for q, exp_file, _ in CASES:
        exp = os.path.splitext(exp_file)[0]
        ve = names(s, q, "embedding")
        kw = names(s, q, "keywords")
        bl = names(s, q, "blend")
        rf = rrf(ve, kw)
        for k, name in (("embedding", ve), ("keywords", kw), ("blend", bl), ("rrf", rf)):
            if any(exp in n for n in name[:5]):
                agg[k] += 1
        rows.append({"q": q, "exp": exp,
                     "rank_emb": rank(ve, exp), "rank_kw": rank(kw, exp),
                     "rank_blend": rank(bl, exp), "rank_rrf": rank(rf, exp)})
    n = len(CASES)
    print("n =", n, " 协议：各模式 top_number=%d，按文档去重取前 5" % TOP)
    for k in ("embedding", "keywords", "blend", "rrf"):
        print("  %-10s %2d/%d = %.1f%%" % (k, agg[k], n, 100.0 * agg[k] / n))
    rej = 0
    for q in OFF_DOMAIN:
        url = API + "/workspace/default/knowledge/" + KID + "/hit_test"
        d = s.post(url, json={"query_text": q, "top_number": 5,
                              "similarity": 0.5, "search_mode": "blend"}, timeout=120).json()
        if d.get("code") == 200 and not (d.get("data") or []):
            rej += 1
    print("  域外拒答(blend, sim=0.5): %d/%d" % (rej, len(OFF_DOMAIN)))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maxkb_results.json")
    io.open(out, "w", encoding="utf-8").write(
        json.dumps({"agg": agg, "n": n, "offdomain_reject": rej, "rows": rows},
                   ensure_ascii=False, indent=1))
    print("明细写入", out)


if __name__ == "__main__":
    main()
