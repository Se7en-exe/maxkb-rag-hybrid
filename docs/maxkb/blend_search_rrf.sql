-- MaxKB v2.10.8-lts 混合检索改造：文档级 RRF 名次融合
--
-- 替换目标：apps/knowledge/sql/blend_search.sql（整文件替换，不改 Python）
-- 调用方：apps/knowledge/vector/pg_vector.py  BlendSearch.handle
--         参数顺序固定：len, json, *exec_params, len, json, top_number, to_query, similarity, top_number
--
-- 原版两个问题：
--   1. 候选池 = 向量 Top-(top_number*10, 上限 500)，关键词通道只能给池内候选加分，
--      纯字面命中、语义不相似的段落进不了池子。
--   2. 最终分 = (1 - distance) + ts_rank_cd(...,32)，两项都在 [0,1)：
--      32 号归一化 = rank/(rank+1)，而余弦普遍 0.8+、全文分多在 0.1 以下 → 相加等于只看向量分。
--
-- 本版：两路各自独立召回，先在「文档」维度取每篇的最好名次，再 RRF 融合
--   score = SUM(1/(60+rank))，返回该文档名次最好的段落。
--   为什么在文档维度融合：同一篇文档里往往有多个段落同时命中，
--   段落维度融合会让长文档用段落数量堆分；文档维度只用「这篇文档排第几」这一个信号。
--
-- 阈值语义变更：原版 similarity 是「综合分 > similarity」（综合分最大约 1.9）；
--   RRF 是 1/61 量级，套 0.6 会全滤光。改作「两路是否都要命中」开关：
--   similarity >= 0.5 → 交集；< 0.5 → 并集。
WITH vec_raw AS (
	SELECT document_id,
	       paragraph_id,
	       ROW_NUMBER() OVER (ORDER BY (embedding::vector(%s) <=> %s)) AS rnk
	FROM embedding ${embedding_query}
	ORDER BY (embedding::vector(%s) <=> %s)
	LIMIT LEAST(%s * 10, 500)
),
vec_doc AS (
	SELECT DISTINCT ON (document_id) document_id, paragraph_id, rnk
	FROM vec_raw
	ORDER BY document_id, rnk
),
tsq AS (
	SELECT websearch_to_tsquery('simple', %s) AS q
),
kw_raw AS (
	SELECT e.document_id,
	       e.paragraph_id,
	       ROW_NUMBER() OVER (ORDER BY ts_rank_cd(e.search_vector, tsq.q, 32) DESC) AS rnk
	FROM embedding e, tsq
	WHERE e.search_vector @@ tsq.q
	LIMIT 500
),
kw_doc AS (
	SELECT DISTINCT ON (document_id) document_id, paragraph_id, rnk
	FROM kw_raw
	ORDER BY document_id, rnk
),
fused AS (
	SELECT document_id,
	       SUM(1.0 / (60 + rnk)) AS doc_score,
	       COUNT(*) AS path_hits,
	       (ARRAY_AGG(paragraph_id ORDER BY rnk))[1] AS best_paragraph
	FROM (
		SELECT document_id, paragraph_id, rnk FROM vec_doc
		UNION ALL
		SELECT document_id, paragraph_id, rnk FROM kw_doc
	) u
	GROUP BY document_id
	HAVING COUNT(*) >= (CASE WHEN %s >= 0.5 THEN 2 ELSE 1 END)
)
SELECT best_paragraph AS paragraph_id,
       doc_score AS comprehensive_score,
       doc_score AS similarity
FROM fused
ORDER BY doc_score DESC
LIMIT %s;
