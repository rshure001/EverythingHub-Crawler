-- PROPOSAL ONLY — do not apply to production until validated.
-- Replaces the existing public.eh_search_pages(text, integer) implementation
-- without changing its argument names, return contract, owner, or existing ACL.
--
-- Main optimization: rank/sort only narrow row identifiers and score fields;
-- fetch the potentially large content_text column only for the final top-N rows.
-- The current implementation carries content_text through the ranked sort.

CREATE OR REPLACE FUNCTION public.eh_search_pages(
  search_query text,
  result_limit integer DEFAULT 10
)
RETURNS TABLE(
  title text,
  url text,
  description text,
  content_text text,
  host text,
  fetched_at timestamptz,
  relevance real
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path TO pg_catalog, public
AS $function$
  WITH parsed AS MATERIALIZED (
    SELECT
      websearch_to_tsquery(
        'english',
        left(trim(coalesce(search_query, '')), 160)
      ) AS tsq,
      lower(trim(coalesce(search_query, ''))) AS phrase
  ),
  top_hits AS MATERIALIZED (
    SELECT
      p.id,
      (
        ts_rank_cd(p.search_vector, parsed.tsq)
        + CASE
            WHEN lower(trim(coalesce(p.title, ''))) = parsed.phrase
            THEN 50.0 ELSE 0.0
          END
        + CASE
            WHEN lower(coalesce(p.title, '')) LIKE '%' || parsed.phrase || '%'
            THEN 12.0 ELSE 0.0
          END
        + CASE
            WHEN lower(coalesce(p.host, '')) LIKE '%' || parsed.phrase || '%'
            THEN 4.0 ELSE 0.0
          END
      )::real AS relevance,
      p.fetched_at
    FROM public.eh_pages AS p
    CROSS JOIN parsed
    WHERE numnode(parsed.tsq) > 0
      AND p.search_vector @@ parsed.tsq
    ORDER BY relevance DESC, p.fetched_at DESC, p.id
    LIMIT greatest(1, least(coalesce(result_limit, 10), 20))
  )
  SELECT
    p.title,
    p.url,
    p.description,
    p.content_text,
    p.host,
    p.fetched_at,
    hits.relevance
  FROM top_hits AS hits
  JOIN public.eh_pages AS p ON p.id = hits.id
  ORDER BY hits.relevance DESC, hits.fetched_at DESC, hits.id;
$function$;
