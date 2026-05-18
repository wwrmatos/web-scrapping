from functools import lru_cache
from pathlib import Path

import polars as pl

_DATA_DIR = Path(__file__).parent / "data"

_qtd = pl.read_parquet(_DATA_DIR / "sia_qtd.parquet").with_columns(
    pl.col("ano").cast(pl.Int64),
    pl.col("municipio_cod").cast(pl.Utf8),
)
_val = pl.read_parquet(_DATA_DIR / "sia_valor.parquet").with_columns(
    pl.col("ano").cast(pl.Int64),
    pl.col("municipio_cod").cast(pl.Utf8),
)

REGIOES: dict[str, list[str]] = {
    "Norte":        ["AC", "AM", "AP", "PA", "RO", "RR", "TO"],
    "Nordeste":     ["AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"],
    "Sudeste":      ["ES", "MG", "RJ", "SP"],
    "Sul":          ["PR", "RS", "SC"],
    "Centro-Oeste": ["DF", "GO", "MS", "MT"],
}

UF_COD: dict[str, str] = {
    "RO": "11", "AC": "12", "AM": "13", "RR": "14", "PA": "15", "AP": "16", "TO": "17",
    "MA": "21", "PI": "22", "CE": "23", "RN": "24", "PB": "25", "PE": "26",
    "AL": "27", "SE": "28", "BA": "29",
    "MG": "31", "ES": "32", "RJ": "33", "SP": "35",
    "PR": "41", "SC": "42", "RS": "43",
    "MS": "50", "MT": "51", "GO": "52", "DF": "53",
}


_MES_NUM: dict[str, int] = {
    "Jan": 1, "Fev": 2, "Mar": 3, "Abr": 4,
    "Mai": 5, "Jun": 6, "Jul": 7, "Ago": 8,
    "Set": 9, "Out": 10, "Nov": 11, "Dez": 12,
}


def _periodo_idx(periodo: str) -> int:
    mes, ano = periodo.split("/")
    return int(ano) * 100 + _MES_NUM.get(mes, 0)


def _proc_cols(df: pl.DataFrame) -> list[str]:
    return [c for c in df.columns if c[:1].isdigit()]


def _row_sum_expr(df: pl.DataFrame) -> pl.Expr:
    cols = _proc_cols(df)
    if not cols:
        return pl.lit(0.0).alias("_total")
    return pl.sum_horizontal([pl.col(c).cast(pl.Float64) for c in cols]).alias("_total")


def _filter(
    df: pl.DataFrame,
    periodo_ini: str,
    periodo_fim: str,
    municipios: list[str] | None = None,
    uf_prefixes: list[str] | None = None,
) -> pl.DataFrame:
    idx_ini = _periodo_idx(periodo_ini)
    idx_fim = _periodo_idx(periodo_fim)
    df = df.with_columns(
        (pl.col("ano").cast(pl.Int64) * 100 +
         pl.col("mes").map_elements(lambda m: _MES_NUM.get(m, 0), return_dtype=pl.Int64)
         ).alias("_idx")
    ).filter(
        (pl.col("_idx") >= idx_ini) & (pl.col("_idx") <= idx_fim)
    ).drop("_idx")
    if municipios:
        df = df.filter(pl.col("municipio_cod").is_in([str(m) for m in municipios]))
    elif uf_prefixes:
        mask = pl.lit(False)
        for p in uf_prefixes:
            mask = mask | pl.col("municipio_cod").str.starts_with(str(p))
        df = df.filter(mask)
    return df


@lru_cache(maxsize=1)
def get_periodos() -> list[str]:
    rows = _qtd.select(["mes", "ano"]).unique().iter_rows(named=True)
    periodos = [f"{r['mes']}/{r['ano']}" for r in rows]
    return sorted(periodos, key=_periodo_idx)


@lru_cache(maxsize=30)
def get_municipios(uf: str) -> tuple[dict, ...]:
    cod = UF_COD.get(uf, "")
    df = (
        _qtd.filter(pl.col("municipio_cod").str.starts_with(cod))
        .select(["municipio_cod", "municipio_nome"])
        .unique()
        .sort("municipio_nome")
    )
    return tuple(df.iter_rows(named=True))


def query_kpis(
    periodo_ini: str,
    periodo_fim: str,
    municipios: list[str] | None = None,
    uf_prefixes: list[str] | None = None,
) -> dict:
    qtd_df = _filter(_qtd, periodo_ini, periodo_fim, municipios, uf_prefixes)
    val_df = _filter(_val, periodo_ini, periodo_fim, municipios, uf_prefixes)

    qtd = float(qtd_df.select(_row_sum_expr(qtd_df))["_total"].sum()) if not qtd_df.is_empty() else 0.0
    val = float(val_df.select(_row_sum_expr(val_df))["_total"].sum()) if not val_df.is_empty() else 0.0

    return {"qtd": qtd, "valor": val}


def query_serie_temporal(
    periodo_ini: str,
    periodo_fim: str,
    municipios: list[str] | None = None,
    uf_prefixes: list[str] | None = None,
) -> pl.DataFrame:
    qtd_df = _filter(_qtd, periodo_ini, periodo_fim, municipios, uf_prefixes)
    val_df = _filter(_val, periodo_ini, periodo_fim, municipios, uf_prefixes)

    if qtd_df.is_empty():
        return pl.DataFrame()

    ts_qtd = (
        qtd_df
        .with_columns(_row_sum_expr(qtd_df))
        .group_by(["ano", "mes"])
        .agg(pl.col("_total").sum().alias("quantidade"))
    )

    if not val_df.is_empty():
        ts_val = (
            val_df
            .with_columns(_row_sum_expr(val_df))
            .group_by(["ano", "mes"])
            .agg(pl.col("_total").sum().alias("valor"))
        )
        result = ts_qtd.join(ts_val, on=["ano", "mes"], how="left").with_columns(
            pl.col("valor").fill_null(0.0)
        )
    else:
        result = ts_qtd.with_columns(pl.lit(0.0).alias("valor"))

    return result.sort(["ano", "mes"])


def query_grupos(
    periodo_ini: str,
    periodo_fim: str,
    municipios: list[str] | None = None,
    uf_prefixes: list[str] | None = None,
) -> pl.DataFrame:
    qtd_df = _filter(_qtd, periodo_ini, periodo_fim, municipios, uf_prefixes)
    val_df = _filter(_val, periodo_ini, periodo_fim, municipios, uf_prefixes)

    cols_qtd = _proc_cols(qtd_df)
    cols_val = _proc_cols(val_df)

    if not cols_qtd and not cols_val:
        return pl.DataFrame()

    qtd_map: dict[str, float] = {}
    if cols_qtd and not qtd_df.is_empty():
        sums = qtd_df.select([pl.col(c).cast(pl.Float64).sum() for c in cols_qtd])
        qtd_map = {c: float(sums[c][0] or 0) for c in cols_qtd}

    val_map: dict[str, float] = {}
    if cols_val and not val_df.is_empty():
        sums = val_df.select([pl.col(c).cast(pl.Float64).sum() for c in cols_val])
        val_map = {c: float(sums[c][0] or 0) for c in cols_val}

    all_groups = sorted(set(cols_qtd) | set(cols_val))
    rows = [
        {"grupo": g, "quantidade": qtd_map.get(g, 0.0), "valor": val_map.get(g, 0.0)}
        for g in all_groups
    ]
    return pl.from_dicts(rows)


def query_ranking(
    periodo_ini: str,
    periodo_fim: str,
    municipios: list[str] | None = None,
    uf_prefixes: list[str] | None = None,
    top: int = 20,
) -> pl.DataFrame:
    qtd_df = _filter(_qtd, periodo_ini, periodo_fim, municipios, uf_prefixes)
    val_df = _filter(_val, periodo_ini, periodo_fim, municipios, uf_prefixes)

    if qtd_df.is_empty():
        return pl.DataFrame()

    rank_qtd = (
        qtd_df
        .with_columns(_row_sum_expr(qtd_df))
        .group_by(["municipio_cod", "municipio_nome"])
        .agg(pl.col("_total").sum().alias("quantidade"))
        .sort("quantidade", descending=True)
        .head(top)
    )

    if not val_df.is_empty():
        rank_val = (
            val_df
            .with_columns(_row_sum_expr(val_df))
            .group_by("municipio_cod")
            .agg(pl.col("_total").sum().alias("valor"))
        )
        result = rank_qtd.join(rank_val, on="municipio_cod", how="left").with_columns(
            pl.col("valor").fill_null(0.0)
        )
    else:
        result = rank_qtd.with_columns(pl.lit(0.0).alias("valor"))

    return result.sort("quantidade", descending=True)
