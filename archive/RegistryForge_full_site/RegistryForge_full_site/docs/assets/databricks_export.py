# ============================================================================
# Databricks notebook source
#
# ARC pipeline data export
# ------------------------
# Exports CCDA documents and FHIR bundles from a clinical data warehouse as
# chunked CSV files suitable for the ARC ETL pipeline.
#
# Input: any Spark-accessible tables holding CCDA documents (typically
# base64-encoded) and FHIR bundles (typically raw JSON text).
#
# Output: three CSV files written to DBFS or a cloud storage path:
#   ccda_chunks.csv   - id, chunk_index, chunk_data (base64 CCDA text, 30k chars max per row)
#   fhir_chunks.csv   - id, chunk_index, chunk_data (FHIR JSON text, 30k chars max per row)
#   uuid_mapping.csv  - document_uuid, patient_id (and optional demographic columns)
#
# Adjust the configuration block to match your warehouse schema, then run all
# cells. The output is rfc4180-compliant CSV that the ARC pipeline reads as-is.
# ============================================================================

# COMMAND ----------

# MAGIC %md
# MAGIC ## Configuration -- edit these to match your warehouse

# COMMAND ----------

# Source tables -- replace with your actual table names
CCDA_TABLE = "your_database.documents"            # one row per CCDA document
FHIR_TABLE = "your_database.fhir_bundles"         # one row per FHIR bundle

# Source columns
CCDA_ID_COL       = "document_uuid"               # primary key
CCDA_CONTENT_COL  = "content_base64"              # base64-encoded CCDA bytes
CCDA_FORMAT_COL   = "document_format"             # filter column
CCDA_FORMAT_VALUE = "ccda"                        # filter value

FHIR_ID_COL      = "bundle_uuid"
FHIR_CONTENT_COL = "bundle_json"                  # raw FHIR JSON text

# Patient mapping table (one row per document, links document UUID to patient)
MAPPING_TABLE         = "your_database.documents"
MAPPING_DOC_ID_COL    = "document_uuid"
MAPPING_PATIENT_COL   = "patient_id"
# Optional demographic columns -- include any that exist in your table.
# Leave the value as None for any that don't:
MAPPING_FIRST_NAME_COL = "first_name"             # or None
MAPPING_LAST_NAME_COL  = "last_name"              # or None
MAPPING_MRN_COL        = "mrn"                    # or None
MAPPING_DOB_COL        = "date_of_birth"          # or None
MAPPING_GENDER_COL     = "gender"                 # or None

# Output location -- DBFS path or s3a:// / abfss:// URL
OUTPUT_DIR = "/dbfs/FileStore/arc_pipeline_export"

# Chunk size in characters. The ARC pipeline expects up to 30,000 chars per row.
# Smaller chunks produce more rows but read faster; larger chunks the reverse.
# 30,000 is a reasonable balance for warehouses with 32k cell-size limits.
CHUNK_SIZE = 30000

# COMMAND ----------

# MAGIC %md
# MAGIC ## Helper -- chunk a content column into rows

# COMMAND ----------

from pyspark.sql import functions as F
from pyspark.sql.types import StringType

def chunk_table(table_name, id_col, content_col, output_filename, where_clause=None):
    """
    Read a table, split each row's content column into <=CHUNK_SIZE-character
    chunks, and write the result as a single CSV file.

    The chunking uses Spark SQL only -- no UDFs, no Python serialization
    overhead. For a 100 GB source table this runs in a few minutes on a
    standard cluster.
    """
    df = spark.table(table_name)
    if where_clause:
        df = df.where(where_clause)

    # Use sequence + transform + posexplode to split content without a UDF.
    # ceil(length/CHUNK_SIZE) gives the number of chunks; sequence(0, n-1)
    # generates chunk indices; transform() slices content for each index;
    # posexplode flattens the array into one row per chunk.
    chunked = df.selectExpr(
        f"{id_col} AS id",
        f"posexplode("
        f"  transform("
        f"    sequence(0, CAST(ceil(length({content_col}) / {CHUNK_SIZE}.0) AS INT) - 1),"
        f"    i -> substring({content_col}, i * {CHUNK_SIZE} + 1, {CHUNK_SIZE})"
        f"  )"
        f") AS (chunk_index, chunk_data)"
    )

    # Drop any rows where content was null (no chunks were produced)
    chunked = chunked.where(F.col("chunk_data").isNotNull())

    # Sort for stable, reproducible output
    chunked = chunked.orderBy("id", "chunk_index")

    # Write a single CSV file. coalesce(1) forces one part-file; the surrounding
    # logic below renames it to the target filename.
    output_path = f"{OUTPUT_DIR}/{output_filename}_csv"
    (chunked
        .coalesce(1)
        .write
        .mode("overwrite")
        .option("header", "true")
        .option("quoteAll", "true")
        .csv(output_path)
    )

    n = chunked.count()
    print(f"  {output_filename}: {n:,} chunks written to {output_path}")
    return n

# COMMAND ----------

# MAGIC %md
# MAGIC ## Export CCDA chunks

# COMMAND ----------

ccda_count = chunk_table(
    table_name=CCDA_TABLE,
    id_col=CCDA_ID_COL,
    content_col=CCDA_CONTENT_COL,
    output_filename="ccda_chunks",
    where_clause=f"{CCDA_FORMAT_COL} = '{CCDA_FORMAT_VALUE}'"
                  if CCDA_FORMAT_COL else None,
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Export FHIR chunks

# COMMAND ----------

fhir_count = chunk_table(
    table_name=FHIR_TABLE,
    id_col=FHIR_ID_COL,
    content_col=FHIR_CONTENT_COL,
    output_filename="fhir_chunks",
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Export the document-to-patient mapping

# COMMAND ----------

select_cols = [
    F.col(MAPPING_DOC_ID_COL).alias("document_uuid"),
    F.col(MAPPING_PATIENT_COL).alias("patient_id"),
]
for src, alias in [
    (MAPPING_FIRST_NAME_COL, "first_name"),
    (MAPPING_LAST_NAME_COL,  "last_name"),
    (MAPPING_MRN_COL,        "mrn"),
    (MAPPING_DOB_COL,        "dob"),
    (MAPPING_GENDER_COL,     "gender"),
]:
    if src:
        select_cols.append(F.col(src).alias(alias))

mapping_df = (
    spark.table(MAPPING_TABLE)
    .select(*select_cols)
    .where(F.col("document_uuid").isNotNull() & F.col("patient_id").isNotNull())
    .dropDuplicates(["document_uuid"])
)

(mapping_df
    .coalesce(1)
    .write
    .mode("overwrite")
    .option("header", "true")
    .option("quoteAll", "true")
    .csv(f"{OUTPUT_DIR}/uuid_mapping_csv")
)

print(f"  uuid_mapping: {mapping_df.count():,} rows written")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Consolidate Spark output into single named files
# MAGIC
# MAGIC Spark writes each output as a directory containing a part-file. The
# MAGIC ARC pipeline expects three plain CSV files. This cell renames the
# MAGIC part-files to the expected names.

# COMMAND ----------

import os, glob, shutil

def collapse_to_single_csv(spark_output_dir, target_csv_name):
    """Rename the Spark part-NNNNN-...csv inside spark_output_dir to target_csv_name
    in the parent directory, then remove the spark output directory."""
    parts = glob.glob(os.path.join(spark_output_dir, "part-*.csv"))
    if not parts:
        raise FileNotFoundError(f"no part file in {spark_output_dir}")
    target = os.path.join(os.path.dirname(spark_output_dir), target_csv_name)
    shutil.move(parts[0], target)
    shutil.rmtree(spark_output_dir, ignore_errors=True)
    return target

ccda_path    = collapse_to_single_csv(f"{OUTPUT_DIR}/ccda_chunks_csv",   "ccda_chunks.csv")
fhir_path    = collapse_to_single_csv(f"{OUTPUT_DIR}/fhir_chunks_csv",   "fhir_chunks.csv")
mapping_path = collapse_to_single_csv(f"{OUTPUT_DIR}/uuid_mapping_csv", "uuid_mapping.csv")

print(f"  Final outputs:")
print(f"    {ccda_path}")
print(f"    {fhir_path}")
print(f"    {mapping_path}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Validate
# MAGIC
# MAGIC Confirm the outputs are well-formed and match the chunk counts.

# COMMAND ----------

ccda_check = spark.read.option("header", "true").csv(f"file://{ccda_path}")
print(f"ccda_chunks: {ccda_check.count():,} rows, {ccda_check.select('id').distinct().count():,} distinct documents")

fhir_check = spark.read.option("header", "true").csv(f"file://{fhir_path}")
print(f"fhir_chunks: {fhir_check.count():,} rows, {fhir_check.select('id').distinct().count():,} distinct documents")

mapping_check = spark.read.option("header", "true").csv(f"file://{mapping_path}")
print(f"uuid_mapping: {mapping_check.count():,} rows")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Download
# MAGIC
# MAGIC The three CSV files are now at:
# MAGIC
# MAGIC - `<OUTPUT_DIR>/ccda_chunks.csv`
# MAGIC - `<OUTPUT_DIR>/fhir_chunks.csv`
# MAGIC - `<OUTPUT_DIR>/uuid_mapping.csv`
# MAGIC
# MAGIC If `OUTPUT_DIR` starts with `/dbfs/FileStore/`, you can download them
# MAGIC via the URL pattern
# MAGIC `https://<workspace>/files/arc_pipeline_export/<filename>`. Otherwise
# MAGIC use your storage account's normal access path.
# MAGIC
# MAGIC Place the three files in the ARC pipeline working directory:
# MAGIC
# MAGIC ```
# MAGIC <work_dir>/
# MAGIC ├── CCDA and FHIR data/
# MAGIC │   ├── ccda_chunks.csv
# MAGIC │   └── fhir_chunks.csv
# MAGIC └── uuid_mapping.csv
# MAGIC ```
