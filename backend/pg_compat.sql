-- SQLite compatibility layer for the Sports AI PostgreSQL database.
--
-- The backend's SQL was written for SQLite. Rather than rewrite ~1,000 statements, each
-- connection uses search_path = <data schema>, sqlite_compat, pg_catalog. Listing pg_catalog
-- explicitly AFTER sqlite_compat makes PostgreSQL search it after these definitions, so
-- the SQLite built-ins used by the code (julianday, date, datetime, json_extract, json_each,
-- ifnull, two-argument max/min, group_concat, sqlite_master) resolve to these definitions, and
-- division or modulo by zero yields NULL as it does in SQLite instead of raising.
--
-- Every function is idempotent (CREATE OR REPLACE) so the file is re-applied on each startup.
-- Timestamps in this database are TEXT (ISO-8601, UTC), exactly as SQLite stored them.

CREATE SCHEMA IF NOT EXISTS sqlite_compat;

-- ---------------------------------------------------------------- time parsing
-- Text -> timestamp (UTC, without zone). NULL for anything SQLite would not parse.
CREATE OR REPLACE FUNCTION sqlite_compat._ts(value text) RETURNS timestamp
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT CASE
    WHEN value IS NULL THEN NULL
    WHEN lower(value) = 'now' THEN (clock_timestamp() AT TIME ZONE 'UTC')
    WHEN value ~ '^\s*\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?\s*(Z|z|[+-]\d{2}:?\d{2})?\s*$'
      THEN (CASE WHEN value ~ '(Z|z|[+-]\d{2}:?\d{2})\s*$'
                 THEN (value::timestamptz AT TIME ZONE 'UTC')
                 ELSE value::timestamp END)
    WHEN value ~ '^\s*-?\d+(\.\d+)?\s*$'   -- a bare number is a Julian day number in SQLite
      THEN (timestamp '1970-01-01' + ((value::double precision - 2440587.5) * interval '1 day'))
    ELSE NULL
  END
$$;

-- Apply SQLite date modifiers ('+3 days', '-90 minutes', 'start of day', 'utc', ...).
CREATE OR REPLACE FUNCTION sqlite_compat._apply(ts timestamp, modifiers text[]) RETURNS timestamp
LANGUAGE plpgsql STABLE PARALLEL SAFE AS $$
DECLARE
  m text; amount double precision; unit text; parts text[];
BEGIN
  IF ts IS NULL OR modifiers IS NULL THEN RETURN ts; END IF;
  FOREACH m IN ARRAY modifiers LOOP
    IF m IS NULL THEN RETURN NULL; END IF;
    m := lower(btrim(m));
    IF m = 'start of day' THEN ts := date_trunc('day', ts);
    ELSIF m = 'start of month' THEN ts := date_trunc('month', ts);
    ELSIF m = 'start of year' THEN ts := date_trunc('year', ts);
    ELSIF m IN ('utc', 'localtime', 'auto', 'subsec', 'subsecond') THEN NULL;  -- stored values are UTC
    ELSE
      parts := regexp_match(m, '^([+-]?\d+(?:\.\d+)?)\s+([a-z]+?)s?$');
      IF parts IS NULL THEN RETURN NULL; END IF;
      amount := parts[1]::double precision; unit := parts[2];
      IF unit = 'second' THEN ts := ts + amount * interval '1 second';
      ELSIF unit = 'minute' THEN ts := ts + amount * interval '1 minute';
      ELSIF unit = 'hour' THEN ts := ts + amount * interval '1 hour';
      ELSIF unit = 'day' THEN ts := ts + amount * interval '1 day';
      ELSIF unit = 'month' THEN ts := ts + (amount::int) * interval '1 month';
      ELSIF unit = 'year' THEN ts := ts + (amount::int) * interval '1 year';
      ELSE RETURN NULL;
      END IF;
    END IF;
  END LOOP;
  RETURN ts;
END $$;

-- julianday(): fractional Julian day number.
CREATE OR REPLACE FUNCTION sqlite_compat.julianday(value text) RETURNS double precision
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT extract(epoch FROM sqlite_compat._ts(value))::double precision / 86400.0 + 2440587.5
$$;
CREATE OR REPLACE FUNCTION sqlite_compat.julianday(value text, VARIADIC modifiers text[]) RETURNS double precision
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT extract(epoch FROM sqlite_compat._apply(sqlite_compat._ts(value), modifiers))::double precision / 86400.0 + 2440587.5
$$;

-- date() -> sqlite_date(): 'YYYY-MM-DD' text, as SQLite returns it. Named differently because
-- PostgreSQL reads date('literal') as a type cast; the connection's SQL translator renames calls.
CREATE OR REPLACE FUNCTION sqlite_compat.sqlite_date(value text) RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT to_char(sqlite_compat._ts(value), 'YYYY-MM-DD')
$$;
CREATE OR REPLACE FUNCTION sqlite_compat.sqlite_date(value text, VARIADIC modifiers text[]) RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT to_char(sqlite_compat._apply(sqlite_compat._ts(value), modifiers), 'YYYY-MM-DD')
$$;

-- datetime() -> sqlite_datetime(): 'YYYY-MM-DD HH:MM:SS' text.
CREATE OR REPLACE FUNCTION sqlite_compat.sqlite_datetime(value text) RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT to_char(sqlite_compat._ts(value), 'YYYY-MM-DD HH24:MI:SS')
$$;
CREATE OR REPLACE FUNCTION sqlite_compat.sqlite_datetime(value text, VARIADIC modifiers text[]) RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT to_char(sqlite_compat._apply(sqlite_compat._ts(value), modifiers), 'YYYY-MM-DD HH24:MI:SS')
$$;

-- time() -> sqlite_time(): 'HH:MM:SS' text.
CREATE OR REPLACE FUNCTION sqlite_compat.sqlite_time(value text) RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT to_char(sqlite_compat._ts(value), 'HH24:MI:SS')
$$;

-- CURRENT_TIMESTAMP as SQLite renders it (used for TEXT column defaults).
CREATE OR REPLACE FUNCTION sqlite_compat.sqlite_now() RETURNS text
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')
$$;

-- ---------------------------------------------------------------- scalar helpers
CREATE OR REPLACE FUNCTION sqlite_compat.ifnull(a anycompatible, b anycompatible) RETURNS anycompatible
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT COALESCE(a, b) $$;

-- Two/three-argument max()/min() are scalar in SQLite and return NULL when any argument is NULL.
CREATE OR REPLACE FUNCTION sqlite_compat.max(a anycompatible, b anycompatible) RETURNS anycompatible
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN a IS NULL OR b IS NULL THEN NULL ELSE GREATEST(a, b) END $$;
CREATE OR REPLACE FUNCTION sqlite_compat.max(a anycompatible, b anycompatible, c anycompatible) RETURNS anycompatible
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN a IS NULL OR b IS NULL OR c IS NULL THEN NULL ELSE GREATEST(a, b, c) END $$;
CREATE OR REPLACE FUNCTION sqlite_compat.min(a anycompatible, b anycompatible) RETURNS anycompatible
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN a IS NULL OR b IS NULL THEN NULL ELSE LEAST(a, b) END $$;
CREATE OR REPLACE FUNCTION sqlite_compat.min(a anycompatible, b anycompatible, c anycompatible) RETURNS anycompatible
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN a IS NULL OR b IS NULL OR c IS NULL THEN NULL ELSE LEAST(a, b, c) END $$;

-- round(double precision, n): PostgreSQL only has round(numeric, n).
CREATE OR REPLACE FUNCTION sqlite_compat.round(value double precision, digits integer) RETURNS double precision
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT round(value::numeric, digits)::double precision $$;
CREATE OR REPLACE FUNCTION sqlite_compat.round(value double precision, digits bigint) RETURNS double precision
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT round(value::numeric, digits::integer)::double precision $$;

-- lower(hex(randomblob(16))) is used for row ids.
CREATE OR REPLACE FUNCTION sqlite_compat.randomblob(n bigint) RETURNS bytea
LANGUAGE sql VOLATILE PARALLEL SAFE AS $$
  SELECT substring(string_agg(uuid_send(gen_random_uuid()), ''::bytea) FROM 1 FOR GREATEST(n, 1)::int)
  FROM generate_series(1, GREATEST(1, ((n + 15) / 16)::int))
$$;
CREATE OR REPLACE FUNCTION sqlite_compat.hex(value bytea) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT upper(encode(value, 'hex')) $$;
CREATE OR REPLACE FUNCTION sqlite_compat.hex(value text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT upper(encode(convert_to(value, 'UTF8'), 'hex')) $$;

CREATE OR REPLACE FUNCTION sqlite_compat.instr(haystack text, needle text) RETURNS bigint
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT strpos(haystack, needle)::bigint $$;

-- ---------------------------------------------------------------- JSON (stored as TEXT)
-- SQLite path '$.a.b[0]' -> text[] path for #>>
CREATE OR REPLACE FUNCTION sqlite_compat._json_path(path text) RETURNS text[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT COALESCE(array_agg(COALESCE(m[1], m[2], m[3]) ORDER BY n), '{}'::text[])
  FROM regexp_matches(path, '\.([A-Za-z0-9_]+)|\."([^"]*)"|\[(\d+)\]', 'g') WITH ORDINALITY AS t(m, n)
$$;

-- json_extract(): text for scalars ('1'/'0' for booleans, as SQLite returns 1/0), JSON text for
-- objects and arrays, NULL for JSON null or a missing path.
CREATE OR REPLACE FUNCTION sqlite_compat.json_extract(document text, path text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE jsonb_typeof(j)
           WHEN 'boolean' THEN CASE WHEN j = 'true'::jsonb THEN '1' ELSE '0' END
           WHEN 'null' THEN NULL
           WHEN 'object' THEN j::text
           WHEN 'array' THEN j::text
           ELSE j #>> '{}' END
  FROM (SELECT CASE WHEN path = '$' THEN document::jsonb
                    ELSE document::jsonb #> sqlite_compat._json_path(path) END AS j) s
$$;

-- json_each(): one row per array element / object member (key, value, type).
CREATE OR REPLACE FUNCTION sqlite_compat.json_each(document text)
RETURNS TABLE(key text, value text, type text)
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT e.key, CASE WHEN jsonb_typeof(e.value) IN ('object','array') THEN e.value::text ELSE e.value #>> '{}' END,
         jsonb_typeof(e.value)
  FROM jsonb_each(CASE WHEN jsonb_typeof(document::jsonb) = 'object' THEN document::jsonb ELSE '{}'::jsonb END) e
  UNION ALL
  SELECT (a.n - 1)::text, CASE WHEN jsonb_typeof(a.v) IN ('object','array') THEN a.v::text ELSE a.v #>> '{}' END,
         jsonb_typeof(a.v)
  FROM jsonb_array_elements(CASE WHEN jsonb_typeof(document::jsonb) = 'array' THEN document::jsonb ELSE '[]'::jsonb END)
       WITH ORDINALITY AS a(v, n)
$$;

-- json_set(doc, path, value, ...) with text values (string literals and text parameters).
CREATE OR REPLACE FUNCTION sqlite_compat.json_set(document text, VARIADIC args text[]) RETURNS text
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
DECLARE
  result jsonb := COALESCE(document, '{}')::jsonb; i integer := 1;
BEGIN
  WHILE i < COALESCE(array_length(args, 1), 0) LOOP
    result := jsonb_set(result, sqlite_compat._json_path(args[i]), to_jsonb(args[i + 1]), true);
    i := i + 2;
  END LOOP;
  RETURN result::text;
END $$;

-- Booleans assigned to the BIGINT flag columns (SET enabled = (id = 'x')), as SQLite stored 1/0.
CREATE OR REPLACE FUNCTION sqlite_compat.bool_to_bigint(value boolean) RETURNS bigint
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT value::int::bigint $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_cast WHERE castsource = 'boolean'::regtype AND casttarget = 'bigint'::regtype) THEN
    CREATE CAST (boolean AS bigint) WITH FUNCTION sqlite_compat.bool_to_bigint(boolean) AS ASSIGNMENT;
  END IF;
END $$;

CREATE OR REPLACE FUNCTION sqlite_compat.json_valid(document text) RETURNS integer
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
BEGIN
  IF document IS NULL THEN RETURN NULL; END IF;
  PERFORM document::jsonb; RETURN 1;
EXCEPTION WHEN others THEN RETURN 0;
END $$;

CREATE OR REPLACE FUNCTION sqlite_compat.json_array_length(document text) RETURNS bigint
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN jsonb_typeof(document::jsonb) = 'array' THEN jsonb_array_length(document::jsonb) ELSE 0 END $$;
CREATE OR REPLACE FUNCTION sqlite_compat.json_array_length(document text, path text) RETURNS bigint
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT sqlite_compat.json_array_length(sqlite_compat.json_extract(document, path)) $$;

-- ---------------------------------------------------------------- aggregates
CREATE OR REPLACE FUNCTION sqlite_compat._gc_step(acc text, value anyelement, sep text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN value IS NULL THEN acc WHEN acc IS NULL THEN value::text ELSE acc || sep || value::text END $$;
CREATE OR REPLACE FUNCTION sqlite_compat._gc_step1(acc text, value anyelement) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT sqlite_compat._gc_step(acc, value, ',') $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'sqlite_compat' AND p.proname = 'group_concat' AND p.pronargs = 1) THEN
    CREATE AGGREGATE sqlite_compat.group_concat(anyelement) (SFUNC = sqlite_compat._gc_step1, STYPE = text);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'sqlite_compat' AND p.proname = 'group_concat' AND p.pronargs = 2) THEN
    CREATE AGGREGATE sqlite_compat.group_concat(anyelement, text) (SFUNC = sqlite_compat._gc_step, STYPE = text);
  END IF;
END $$;

-- sum()/avg()/total() over boolean comparisons (SQLite treats them as 0/1).
CREATE OR REPLACE FUNCTION sqlite_compat._bool_sum(acc bigint, value boolean) RETURNS bigint
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN value IS NULL THEN acc ELSE COALESCE(acc, 0) + value::int END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'sqlite_compat' AND p.proname = 'sum'
                   AND p.proargtypes::text = 'boolean'::regtype::oid::text) THEN
    CREATE AGGREGATE sqlite_compat.sum(boolean) (SFUNC = sqlite_compat._bool_sum, STYPE = bigint);
  END IF;
END $$;
CREATE OR REPLACE FUNCTION sqlite_compat._bool_avg_step(acc double precision[], value boolean) RETURNS double precision[]
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN value IS NULL THEN acc ELSE ARRAY[acc[1] + value::int, acc[2] + 1] END $$;
CREATE OR REPLACE FUNCTION sqlite_compat._bool_avg_final(acc double precision[]) RETURNS double precision
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT CASE WHEN acc[2] = 0 THEN NULL ELSE acc[1] / acc[2] END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'sqlite_compat' AND p.proname = 'avg'
                   AND p.proargtypes::text = 'boolean'::regtype::oid::text) THEN
    CREATE AGGREGATE sqlite_compat.avg(boolean) (SFUNC = sqlite_compat._bool_avg_step, STYPE = double precision[],
      FINALFUNC = sqlite_compat._bool_avg_final, INITCOND = '{0,0}');
  END IF;
END $$;
-- max()/min() over boolean expressions (MAX(EXISTS (...))): SQLite returns 1/0.
CREATE OR REPLACE FUNCTION sqlite_compat._bool_max_step(acc integer, value boolean) RETURNS integer
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN value IS NULL THEN acc ELSE GREATEST(COALESCE(acc, 0), value::int) END $$;
CREATE OR REPLACE FUNCTION sqlite_compat._bool_min_step(acc integer, value boolean) RETURNS integer
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT CASE WHEN value IS NULL THEN acc ELSE LEAST(COALESCE(acc, 1), value::int) END $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'sqlite_compat' AND p.proname = 'max' AND p.prokind = 'a') THEN
    CREATE AGGREGATE sqlite_compat.max(boolean) (SFUNC = sqlite_compat._bool_max_step, STYPE = integer);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'sqlite_compat' AND p.proname = 'min' AND p.prokind = 'a') THEN
    CREATE AGGREGATE sqlite_compat.min(boolean) (SFUNC = sqlite_compat._bool_min_step, STYPE = integer);
  END IF;
END $$;

CREATE OR REPLACE FUNCTION sqlite_compat._total_step(acc double precision, value double precision) RETURNS double precision
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$ SELECT acc + COALESCE(value, 0) $$;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                 WHERE n.nspname = 'sqlite_compat' AND p.proname = 'total') THEN
    CREATE AGGREGATE sqlite_compat.total(double precision) (SFUNC = sqlite_compat._total_step,
      STYPE = double precision, INITCOND = '0');
  END IF;
END $$;

-- ---------------------------------------------------------------- division / modulo by zero -> NULL
-- Shadow every numeric "/" and "%" operator of pg_catalog with one that returns NULL on a zero
-- divisor. Because sqlite_compat precedes pg_catalog on the search_path, an operator with an
-- identical signature here hides the built-in one. The bodies name pg_catalog explicitly.
DO $$
DECLARE
  op record; fname text;
BEGIN
  FOR op IN
    SELECT o.oprname, format_type(o.oprleft, NULL) AS l, format_type(o.oprright, NULL) AS r,
           format_type(o.oprresult, NULL) AS res
    FROM pg_operator o JOIN pg_namespace n ON n.oid = o.oprnamespace
    WHERE n.nspname = 'pg_catalog' AND o.oprname IN ('/', '%') AND o.oprkind = 'b'
      AND o.oprleft IN ('smallint'::regtype, 'integer'::regtype, 'bigint'::regtype, 'real'::regtype,
                        'double precision'::regtype, 'numeric'::regtype)
      AND o.oprright IN ('smallint'::regtype, 'integer'::regtype, 'bigint'::regtype, 'real'::regtype,
                         'double precision'::regtype, 'numeric'::regtype)
  LOOP
    fname := format('_%s_%s_%s', CASE op.oprname WHEN '/' THEN 'div' ELSE 'mod' END,
                    replace(op.l, ' ', '_'), replace(op.r, ' ', '_'));
    EXECUTE format(
      'CREATE OR REPLACE FUNCTION sqlite_compat.%I(a %s, b %s) RETURNS %s LANGUAGE sql IMMUTABLE PARALLEL SAFE AS '
      '$f$ SELECT CASE WHEN b = 0 THEN NULL ELSE a OPERATOR(pg_catalog.%s) b END $f$',
      fname, op.l, op.r, op.res, op.oprname);
    IF NOT EXISTS (SELECT 1 FROM pg_operator o2 JOIN pg_namespace n2 ON n2.oid = o2.oprnamespace
                   WHERE n2.nspname = 'sqlite_compat' AND o2.oprname = op.oprname
                     AND o2.oprleft = op.l::regtype AND o2.oprright = op.r::regtype) THEN
      EXECUTE format('CREATE OPERATOR sqlite_compat.%s (LEFTARG = %s, RIGHTARG = %s, FUNCTION = sqlite_compat.%I)',
                     op.oprname, op.l, op.r, fname);
    END IF;
  END LOOP;
END $$;

-- ---------------------------------------------------------------- catalog
-- sqlite_master for the few places that ask whether a table or index exists. Resolves against
-- the first schema of the caller's search_path after sqlite_compat (the data schema).
CREATE OR REPLACE VIEW sqlite_compat.sqlite_master AS
  SELECT CASE c.relkind WHEN 'i' THEN 'index' ELSE 'table' END::text AS type,
         c.relname::text AS name,
         COALESCE(t.relname, c.relname)::text AS tbl_name,
         0::bigint AS rootpage,
         NULL::text AS sql
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  LEFT JOIN pg_index i ON i.indexrelid = c.oid
  LEFT JOIN pg_class t ON t.oid = i.indrelid
  WHERE c.relkind IN ('r', 'p', 'i')
    AND n.nspname = (SELECT s FROM unnest(current_schemas(false)) s
                     WHERE s NOT IN ('sqlite_compat', 'pg_catalog') LIMIT 1);

-- Immutable-evidence triggers (SQLite: SELECT RAISE(ABORT, '...')).
CREATE OR REPLACE FUNCTION sqlite_compat.raise_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '%', TG_ARGV[0] USING ERRCODE = 'integrity_constraint_violation';
END $$;
