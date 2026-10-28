from __future__ import annotations

import os
import sys
from functools import cache

from pyspark.sql import SparkSession


# Session locale unique (démarrage ~5 s) : fuseau UTC, sans interface web, échanges via Arrow
@cache
def session() -> SparkSession:
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    spark = (
        SparkSession.builder.master("local[2]")
        .appName("ou-brancher-mon-entreprise")
        # 1 Go par défaut ne suffit pas aux ~400 000 ventes DVF passées par le pilote
        .config("spark.driver.memory", "3g")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.sql.execution.arrow.pyspark.enabled", "true")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")
    return spark
