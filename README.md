# MaxKB 混合检索复现 · RRF 文档级融合

在真实部署的 [MaxKB](https://github.com/1Panel-dev/MaxKB) v2.10.8 上，把它的混合检索拆开、量化、再换一种融合方式，用 24 题标注集证明「瓶颈在候选池，不在公式」。

> 结论一句话：**原版把候选池锁死在向量 Top-500 再相加分数，字面命中的段落根本进不了池；改成两路独立召回 + 文档级 RRF 后，文档级 Top-5 从 54.2% 到 83.3%，而纯向量、纯全文各只有 50%。**

## 结果（24 题域内 + 8 题域外，本机实测）

协议：每个模式取 `top_number=20` → 按**文档名**去重 → 取前 5 篇 → 标准答案所在文件在这 5 篇里即算命中（即「文档级 Top-5」）。

| 检索方式 | Top-5 命中 | 备注 |
|---|---|---|
| 纯向量（embedding） | 12/24 = **50.0%** | 语义强、编号弱 |
| 纯全文（keywords） | 12/24 = **50.0%** | 字面强、换说法就丢 |
| 原版混合（blend） | 13/24 = **54.2%** | 候选池被向量锁死 |
| **两路独立召回 + 文档级 RRF** | 20/24 = **83.3%** | 本文方案 |

稳健性（防止「调参调出来的」）：

| 变体 | Top-5 |
|---|---|
| RRF，两路各取 Top-10 | 18/24 = 75.0% |
| 原版 blend，请求 top_number=5（池 50） | 18/24 = 75.0% |
| 原版 blend，top_number=50（池 500） | 10/24 = 41.7% |

拒答（域外 8 题，blend + 余弦阈值 0.5）：**7/8 正确拒答**，域内 0 误拒。

## 把 RRF 直接写进 SQL 会怎样（这是最有价值的部分）

四种融合粒度，只改「在哪一层合并」这一个变量：

| 做法 | Top-5 |
|---|---|
| 原版 blend（池内加全文分） | 54.2% |
| SQL 内按段落融合 RRF | 50.0% |
| SQL 内按文档聚合 RRF | 37.5% |
| 离线、按段落 id 融合两路 top-20 | 58.3% |
| **离线、按文档名次融合两路 top-20** | **83.3%** |

段落级融合会被「这篇文档有多少段」带偏——段多就等于票多。83.3% 的来源是**文档级 + 每路只取前 20** 这个融合协议，不是 RRF 公式本身。想只改一条 SQL 拿到 83.3%，做不到。

## 原版为什么不行

原始 SQL 在 `apps/knowledge/sql/blend_search.sql`（本仓库 `docs/maxkb/blend_search.sql` 是副本，672 字节）：

```sql
WITH vector_top AS (
    SELECT id, paragraph_id, (embedding::vector(%s) <=> %s) AS distance
    FROM embedding ${embedding_query}
    ORDER BY (embedding::vector(%s) <=> %s)
    LIMIT LEAST(%s * 10, 500)          -- ← 病根
)
SELECT DISTINCT ON (paragraph_id) ...
    (1 - distance + COALESCE(ts_rank_cd(search_vector,
        websearch_to_tsquery('simple', %s), 32), 0)) AS comprehensive_score
...
```

1. **候选池是死的**：全文分只能给已进池的段落加分。字面完全命中但语义不像的段落（编号、错误码、函数名）进不了池，关键词再准也加不上。旁证：池子 50→500，命中率反而 75%→41.7%，说明瓶颈是「池子由谁决定」而不是容量。
2. **两项量级不对等**：`ts_rank_cd(...,32)` 的 32 是归一化开关，输出 = rank/(rank+1)，恒 < 1。两项都在 [0,1)，但余弦普遍 0.8+、全文分多在 0.1 以下，相加基本等于只看向量。

## 复现

```bash
pip install -r requirements.txt

# 1) 起 MaxKB（自带 PostgreSQL + pgvector + Redis + Celery）
docker run -d --name maxkb -p 8080:8080 \
  -v ~/.maxkb:/var/lib/postgresql/data 1panel/maxkb:v2.10.8-lts

# 2) 灌语料（md/docx/pdf，两步：先 split 再 document）
export MAXKB_API=http://127.0.0.1:8080/admin/api
export MAXKB_USER=admin
export MAXKB_PWD='你的密码'
python scripts/ingest_docs.py --dir ./my-docs --kb my-kb
#   脚本会打印知识库 id，例如 01a1db74-xxxx

# 3) 跑 24 题四路对比 + 域外拒答
export MAXKB_KID=<上一步打印的 id>
python eval/run_eval_maxkb.py
```

输出会写回 `eval/maxkb_results.json`（含逐题四种模式的名次）。本仓库里这份 json 就是本机跑出来的原始结果。

## 目录

```
eval/
  cases_docs.py        24 题域内标注 + 8 题域外（question, 期望文件）
  run_eval_maxkb.py    四路对比 + 文档级 RRF + 域外拒答
  maxkb_results.json   本机实测原始输出（逐题名次）
scripts/
  ingest_docs.py       把目录里的文档灌进 MaxKB（split → document 两步）
docs/
  项目一_MaxKB改造清单.md   改造思路与四路对比表
  项目一_从零讲透.md        13 节讲解（含手算 RRF）
  tutorial.html             同一份内容的交互网页（可双击打开）
  maxkb/blend_search.sql    原版 SQL 副本（672 字节）
  maxkb/blend_search_rrf.sql  RRF 改写版（实测更差，留作反例）
  maxkb/实测结果.md          全部实测数据与复现命令
  maxkb/改造指南.md          改 SQL 的硬约束（8 个 %s 等）
```

## 踩过的坑

1. **SQL 里必须恰好 8 个 `%s`**：`BlendSearch.handle` 固定按顺序传 8 个参数，多加一个 `LIMIT %s` 就报 `the query has 10 placeholders but 9 parameters were passed`。
2. **注释里的 `%` 也算占位符**：首行写 `-- % MaxKB`，psycopg 直接 `incomplete placeholder: '%'`。
3. **一段多向量**：MaxKB 为每段自动生成关联问题，一段可能占多个名次，必须按段落去重。
4. **API 前缀是 `/admin/api/`**，不是 `/api/`；上传文档要两步（`document/split` → `document`），直接 POST 只建空壳。
5. **容器重建丢改动**：`docker cp` 进容器的 SQL，容器一重建就没了，要 bind mount。
6. **psql 用户是 root**：`docker exec -it maxkb psql -h 127.0.0.1 -U root -d maxkb`，没有 postgres 角色。

## 能力边界

- ✅ 文档上传 / 切分 / 向量化 / 命中测试（向量·全文·混合三选一）/ 24 题可复跑
- ❌ 对话问答（本机只配了本地 embedding，未接对话模型）
- ❌ 线上并发、多用户权限
- ❌ Reranker 精排（没做）
- ❌ 融合逻辑打进 MaxKB 内部：目前 RRF 在离线脚本里做，因为塞进 SQL 实测更差

## 许可与致谢

本仓库代码 MIT。MaxKB 本体为 GPL-3.0，本仓库只包含**自己写的**评测脚本、数据集、文档，以及原版 SQL 的副本（用于对照分析，非分发修改版）。
文档语料未随仓库提供——`--dir` 指向你自己的文档即可复跑。
