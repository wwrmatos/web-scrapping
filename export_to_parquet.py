import os
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import polars as pl

load_dotenv()

engine = create_engine(
    f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST')}:{os.getenv('DB_PORT', '5432')}"
    f"/{os.getenv('DB_NAME')}",
    pool_pre_ping=True,
)

output_dir = Path("dashboard/data")
output_dir.mkdir(exist_ok=True)

for table in ("sia_qtd", "sia_valor"):
    print(f"Exportando {table}...")
    with engine.connect() as conn:
        result = conn.execute(text(f'SELECT * FROM "{table}"'))
        keys = list(result.keys())
        rows = result.fetchall()

    df = pl.from_dicts([dict(zip(keys, row)) for row in rows])
    out = output_dir / f"{table}.parquet"
    df.write_parquet(out)
    print(f"  {len(df)} linhas, {len(df.columns)} colunas -> {out}")

print("\nPronto! Arquivos em dashboard/data/")
