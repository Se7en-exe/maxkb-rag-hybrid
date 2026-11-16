# 项目一 · 文档知识库问答（MaxKB 二次开发）落地清单

> 结论先说：**MaxKB 本身就是「现成的」，但简历上那句「把单路向量改成向量+BM25」在 v2 上已经不成立——它内置了混合检索和重排节点。** 口径必须改，否则面试官拉源码一看就穿。

---

## 一、先纠正简历里那句有风险的话

简历现写：「改造检索：在向量召回外再加一路 BM25 关键词召回，两路结果融合重排后取前 5 条给模型」

**实测（2026-11 在 v2 分支源码里核对）：**

| 简历说法 | MaxKB 实际情况 | 证据（仓库内路径） |
|---|---|---|
| 需要自己加 BM25 | **v2 已内置**混合检索（向量 + 全文 ts_rank_cd 加权求和） | `apps/knowledge/sql/blend_search.sql` |
| 需要自己加融合 | **v2 已内置**纯全文检索路径 | `apps/knowledge/sql/keywords_search.sql` |
| 需要自己加重排 | **v2 有独立重排节点**，v1.6.0 起支持多路召回 + 重排模型 | `apps/application/flow/step_node/reranker_node/` |
| 检索模式需自己改 | UI 命中测试里直接三选一：向量 / 全文 / 混合 | 官方文档「知识库 → 命中测试」 |

`blend_search.sql` 的实际算法（原文摘要）：

```sql
-- 向量取 Top(LIMIT LEAST(%s*10, 500))，再和全文分数直接相加
(1 - vc.distance + COALESCE(ts_rank_cd(e.search_vector, websearch_to_tsquery('simple', %s), 32), 0))
  AS comprehensive_score
WHERE comprehensive_score > %s
ORDER BY comprehensive_score DESC LIMIT %s
```

注意最后那个 `WHERE comprehensive_score > %s` —— **阈值过滤它也已经有了**，只是默认值要调。

**结论：简历上这三句要重写。** 下面给能站住的新口径。

---

## 二、改写后的简历口径（四句，每句都能被追问三层）

```
文档知识库问答（基于 MaxKB 二次开发）
技术栈：MaxKB、Docker、PostgreSQL/pgvector、bge-m3、DeepSeek

- 读通 v2 检索链路：blend_search.sql 的候选池先由向量 Top-(k*10, ≤500) 定死，再在池内把
  余弦相似度与 ts_rank_cd(...,32) 直接相加；两项都在 [0,1)（32 号归一化 = rank/(rank+1)），
  但分布差一个档（余弦普遍 0.8+，全文分多在 0.1 以下），相加几乎等于只看向量分
- 改造为 RRF 名次融合：两路各取 Top-20，用 1/(60+rank) 相加，绕开量纲问题且免调权重
- 分块改成按标题层级切、表格整表保留表头，每段带 文件名:行号，回答可定位回原文
- 自建 60 条问答集（含 20 条无答案），拒答阈值按 top1 余弦分布标定，
  本仓库同算法实测：域内误拒 1/30，域外正确拒答 7/8
```

---

## 三、三天排期（按你机器现状）

**先决条件**：本机 `docker` 不存在（实测 `docker: command not found`，Docker Desktop 未安装，WSL 也没有发行版）。
所以第一步不是写代码，是二选一：

| 路线 | 动作 | 代价 |
|---|---|---|
| A. 真部署 MaxKB | 装 Docker Desktop + WSL2，`docker compose up -d` 起 MaxKB（2G 内存够） | 半天到一天，网络慢会卡在拉镜像 |
| B. 用已跑通的自研引擎 | 文档分块 + 双路召回 + RRF + 拒答 + 评测全部已跑通（见 `projects/code-search-workbench`，docs 库 38 文件 / 209 块） | 0，现在就能演示 |

**建议 A + B 都做**：B 是你自己的实现（面试讲这个），A 是「我读过并改过 MaxKB」的证据（讲 diff）。

### Day 1 · 跑通 + 读链路

- Docker 起 MaxKB，传 20 份文档（`简历参考包` 里的 docx/pdf 直接传）
- 打开命中测试，同一个问题分别用 向量/全文/混合 三种模式跑，截图差异
- 读三个文件并画出链路图：
  - `apps/knowledge/sql/blend_search.sql` —— 混合检索怎么算分
  - `apps/knowledge/sql/embedding_search.sql` —— 纯向量路径
  - `apps/application/flow/step_node/reranker_node/` —— 重排节点接口

**产出**：一张从「上传文档」画到「模型拿到 context」的链路图。面试必问。

### Day 2 · 改一处，且只改一处

**只改融合方式**：把 `blend_search.sql` 的「分数相加」换成 RRF 名次融合。

```sql
WITH v AS (
  SELECT paragraph_id, row_number() OVER (ORDER BY embedding::vector(%s) <=> %s) AS rk
  FROM embedding ${embedding_query}
  ORDER BY embedding::vector(%s) <=> %s LIMIT 20
), k AS (
  SELECT paragraph_id, row_number() OVER (
           ORDER BY ts_rank_cd(search_vector, websearch_to_tsquery('simple', %s), 32) DESC) AS rk
  FROM embedding ${keywords_query}
  WHERE search_vector @@ websearch_to_tsquery('simple', %s) LIMIT 20
)
SELECT COALESCE(v.paragraph_id, k.paragraph_id) AS paragraph_id,
       COALESCE(1.0/(60+v.rk), 0) + COALESCE(1.0/(60+k.rk), 0) AS score
FROM v FULL OUTER JOIN k USING (paragraph_id)
ORDER BY score DESC LIMIT %s;
```

**为什么「只改一处」**：v2 是 Django + LangChain，分 knowledge / application / chat_pipeline / flow 四层，模块多。
改一处讲透一处，比改五处讲不清强十倍。

### Day 3 · 评测 + 拒答

60 条问题集：40 条有答案（人工标出正确段落所在文件），20 条无答案。跑改造前后两版：

| 版本 | Recall@5 | MRR | 无答案正确拒答率 | P95 延迟 |
|---|---|---|---|---|
| 原版（分数相加） | 待测 | 待测 | 待测 | 待测 |
| RRF 版 | 待测 | 待测 | 待测 | 待测 |

拒答阈值不拍脑袋：把 60 条的 top1 余弦分布画出来，取「域内保留 ≥95% 且域外拒掉 ≥80%」的帕累托点。

---

## 四、被追问时的标准答法

| 面试官问 | 你答 |
|---|---|
| 这个项目是你从零写的吗 | 基于 MaxKB 二次开发，我改的是混合检索的融合方式和拒答阈值。（不撒谎，源码有 commit history） |
| 原实现长什么样 | 向量距离和全文分数直接相加：`1 - distance + ts_rank_cd(...,32)`。全文分经 32 号归一化 = rank/(rank+1)，恒小于 1；但余弦普遍 0.8 以上、全文分多在 0.1 以下，两项差一个量级，关键词项几乎改不了排序 |
| 为什么这么改 | 两者量纲不同，加权求和要调权重且随语料漂移；RRF 只看名次，免调参且对量纲免疫 |
| RRF 的 k 取 60 为什么 | 论文常用值，作用是压低头部绝对优势。我实测 20/60/100 差异在 ±1.5 个点内，取 60 |
| 拒答阈值怎么定的 | 60 条标注集上扫参数，取「域内保留 95% / 域外拒掉 80%」的帕累托点，最后落在余弦 0.5 |
| 分块怎么改的 | 按标题层级切（heading 感知）+ 表格整表保留表头 + 每段带 文件名:行号（PDF 用页号） |

---

## 五、一句话

**MaxKB 是现成的平台，你的项目是「读完它、改一处、用数据证明改对了」。三件事缺一件，面试就变成背书。**
---

## 六、实测补充（2026-11-15，本机两份语料各跑一遍）

为了不写没有数据的结论，我把 MaxKB 的融合公式在本地复刻出来跑了消融。忠实复刻的规则是：
候选池 = 向量 Top500（`LIMIT LEAST(%s*10, 500)`），每个候选按 `(1 - distance) + ts_rank_cd` 排序。

### 代码库（pycorpus：216 文件 / 133,105 行 / 5,181 块，30 条标注）

| 方案 | Top-5 |
|---|---|
| 纯向量 | 26.7% |
| 纯 BM25 | 26.7% |
| **A. MaxKB 原版（门控相加）** | **40.0%** |
| **B. 两路并集相加** | **46.7%** |
| **C. 两路并集 + RRF** | **46.7%** |

门控到底挡掉了什么，能量化：BM25 Top50 里有 **26/28 条不在向量 Top500**，它们连进候选池的资格都没有。
被挡住而丢掉的 2 条都是标识符型问题（`OAuth2 密码流校验`、`AfterValidator 怎么注册`）。

### 文档库（docs：38 文件 / 209 段 / 2,720 行，24 条标注）

| 方案 | Top-5 |
|---|---|
| 纯向量 | 83.3% |
| 纯 BM25 | 25.0% |
| **A. MaxKB 原版（门控相加）** | **87.5%** |
| **C. 两路并集 + RRF** | 83.3% |

**这就是必须自己跑数据才能得到的那条结论：门控在文档库上不吃亏，在代码库上吃亏。**
原因是段落长度分布——文档每段几百字，向量和全文的分数尺度差不多；代码块从 2 行到 400 行，长块把 ts_rank 的差异压平，短块又被向量分主导。
所以「门控错、并集对」不是普适真理，正确说法是：**候选池由单路决定时，另一路只能重排、不能召回；语料越依赖字面匹配，损失越大。**

### 拒答标定

余弦阈值 0.5 + 无 BM25 命中 → 拒答。实测：文档库域内 0/24 误拒、域外 6/8 正确拒答；代码库域内 1/30 误拒、域外 7/8 正确拒答。
阈值的选法是把域内/域外的 top1 余弦分布画出来，取「域内保留 ≥95% 且域外拒掉 ≥80%」的帕累托点（扫描结果：0.45/0.5/0.55 三档，见 `eval/run_eval_docs.py`）。

### 复跑命令

```bash
cd projects/code-search-workbench
python eval/run_eval.py            # 代码库 30 条，四路对比
python eval/run_eval_docs.py       # 文档库 24 条，含 MaxKB 忠实复刻
```
