-- ============================================================================
-- ARC pipeline data export -- pure SQL version
--
-- Generates the three CSV files the ARC pipeline reads:
--   ccda_chunks.csv, fhir_chunks.csv, uuid_mapping.csv
--
-- Replace the placeholders in the CREATE TEMPORARY VIEW statements with your
-- actual table and column names, then run the full script in a Databricks
-- SQL or Spark SQL session.
-- ============================================================================

-- ---------- Chunk size constant ----------
-- Spark SQL doesn't have user variables outside notebooks, so the chunk size
-- is inlined as 30000 below. To change it, replace every occurrence of 30000.

-- ---------- Source views ----------
CREATE OR REPLACE TEMPORARY VIEW ccda_source AS
SELECT
    document_uuid       AS id,
    content_base64      AS content
FROM your_database.documents
WHERE document_format = 'ccda';

CREATE OR REPLACE TEMPORARY VIEW fhir_source AS
SELECT
    bundle_uuid AS id,
    bundle_json AS content
FROM your_database.fhir_bundles;

CREATE OR REPLACE TEMPORARY VIEW mapping_source AS
SELECT
    document_uuid AS document_uuid,
    patient_id    AS patient_id
    -- Add demographic columns here if your warehouse has them:
    -- , first_name
    -- , last_name
    -- , mrn
    -- , date_of_birth AS dob
    -- , gender
FROM your_database.documents
WHERE document_uuid IS NOT NULL
  AND patient_id    IS NOT NULL;

-- ---------- Generic chunking pattern ----------
-- Each source row produces ceil(length(content) / 30000) chunk rows.
-- posexplode unpacks the array; substring extracts the right slice.

CREATE OR REPLACE TEMPORARY VIEW ccda_chunks AS
SELECT id, chunk_index, chunk_data
FROM (
  SELECT
    id,
    posexplode(
      transform(
        sequence(0, CAST(ceil(length(content) / 30000.0) AS INT) - 1),
        i -> substring(content, i * 30000 + 1, 30000)
      )
    ) AS (chunk_index, chunk_data)
  FROM ccda_source
)
WHERE chunk_data IS NOT NULL
ORDER BY id, chunk_index;

CREATE OR REPLACE TEMPORARY VIEW fhir_chunks AS
SELECT id, chunk_index, chunk_data
FROM (
  SELECT
    id,
    posexplode(
      transform(
        sequence(0, CAST(ceil(length(content) / 30000.0) AS INT) - 1),
        i -> substring(content, i * 30000 + 1, 30000)
      )
    ) AS (chunk_index, chunk_data)
  FROM fhir_source
)
WHERE chunk_data IS NOT NULL
ORDER BY id, chunk_index;

CREATE OR REPLACE TEMPORARY VIEW uuid_mapping AS
SELECT DISTINCT * FROM mapping_source;

-- ---------- Write outputs ----------
-- Replace `/dbfs/FileStore/arc_pipeline_export` with your output path.

CREATE OR REPLACE TABLE ccda_chunks_export
USING CSV
OPTIONS (header 'true', quoteAll 'true')
LOCATION '/dbfs/FileStore/arc_pipeline_export/ccda_chunks_csv'
AS SELECT * FROM ccda_chunks;

CREATE OR REPLACE TABLE fhir_chunks_export
USING CSV
OPTIONS (header 'true', quoteAll 'true')
LOCATION '/dbfs/FileStore/arc_pipeline_export/fhir_chunks_csv'
AS SELECT * FROM fhir_chunks;

CREATE OR REPLACE TABLE uuid_mapping_export
USING CSV
OPTIONS (header 'true', quoteAll 'true')
LOCATION '/dbfs/FileStore/arc_pipeline_export/uuid_mapping_csv'
AS SELECT * FROM uuid_mapping;

-- After running, the three output directories each contain a single
-- part-NNNNN-...csv file. Use shell or the Databricks CLI to rename them to
-- ccda_chunks.csv, fhir_chunks.csv, uuid_mapping.csv -- the SQL writer cannot
-- do this directly.
