import os
from functools import lru_cache

import polars as pl
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

_engine = create_engine(
    f"postgresql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
    f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5432')}"
    f"/{os.getenv('DB_NAME')}",
    pool_pre_ping=True,
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


def _run(sql: str, params: dict | None = None) -> pl.DataFrame:
    with _engine.connect() as conn:
        r = conn.execute(text(sql), params or {})
        keys = list(r.keys())
        rows = r.fetchall()
    if not rows:
        return pl.DataFrame()
    return pl.from_dicts([dict(zip(keys, row)) for row in rows])


@lru_cache(maxsize=2)
def _proc_cols(table: str) -> tuple[str, ...]:
    """Retorna as colunas de procedimento (começam com dígito) de uma tabela wide."""
    df = _run(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = :t AND table_schema = 'public' "
        "AND column_name ~ '^[0-9]' ORDER BY column_name",
        {"t": table},
    )
    return tuple(df["column_name"].to_list()) if not df.is_empty() else ()


def _sum_expr(table: str) -> str:
    """Expressão SQL que soma todas as colunas de procedimento de uma tabela wide."""
    cols = _proc_cols(table)
    if not cols:
        return "0"
    return " + ".join(f'COALESCE("{c}", 0)' for c in cols)


def _where(
    ano_ini: int,
    ano_fim: int,
    municipios: list[str] | None = None,
    uf_prefixes: list[str] | None = None,
) -> tuple[str, dict]:
    """
    Prioridade: municipios > uf_prefixes (uma UF ou lista de UFs de uma região).
    Parâmetros de ano são injetados via SQLAlchemy; códigos passam por isdigit().
    """
    clause = "ano BETWEEN :a1 AND :a2"
    params: dict = {"a1": str(ano_ini), "a2": str(ano_fim)}

    if municipios:
        safe = [c for c in municipios if str(c).isdigit()]
        if safe:
            in_list = ", ".join(f"'{c}'" for c in safe)
            clause += f" AND municipio_cod IN ({in_list})"
    elif uf_prefixes:
        safe = [p for p in uf_prefixes if str(p).isdigit()]
        if safe:
            likes = " OR ".join(f"municipio_cod LIKE '{p}%'" for p in safe)
            clause += f" AND ({likes})"

    return clause, params


# ── Public API ────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_anos() -> list[int]:
    df = _run("SELECT DISTINCT ano FROM sia_qtd WHERE ano IS NOT NULL ORDER BY ano")
    return [int(a) for a in df["ano"].to_list()] if not df.is_empty() else list(range(2008, 2025))


@lru_cache(maxsize=30)
def get_municipios(uf: str) -> tuple[dict, ...]:
    cod = UF_COD.get(uf, "")
    df = _run(
        "SELECT DISTINCT municipio_cod, municipio_nome "
        "FROM sia_qtd WHERE municipio_cod LIKE :p ORDER BY municipio_nome",
        {"p": f"{cod}%"},
    )
    return tuple(df.iter_rows(named=True)) if not df.is_empty() else ()


def query_kpis(
    ano_ini: int,
    ano_fim: int,
    municipios: list[str] | None = None,
    uf_prefixes: list[str] | None = None,
) -> dict:
    w, p = _where(ano_ini, ano_fim, municipios, uf_prefixes)
    sq = _sum_expr("sia_qtd")
    sv = _sum_expr("sia_valor")
    df = _run(
        f"SELECT"
        f"  (SELECT COALESCE(SUM({sq}), 0) FROM sia_qtd WHERE {w}) AS qtd,"
        f"  (SELECT COALESCE(SUM({sv}), 0) FROM sia_valor WHERE {w}) AS valor",
        p,
    )
    if df.is_empty():
        return {"qtd": 0, "valor": 0.0}
    return {"qtd": float(df["qtd"][0] or 0), "valor": float(df["valor"][0] or 0)}


def query_serie_temporal(
    ano_ini: int,
    ano_fim: int,
    municipios: list[str] | None = None,
    uf_prefixes: list[str] | None = None,
) -> pl.DataFrame:
    w, p = _where(ano_ini, ano_fim, municipios, uf_prefixes)
    sq = _sum_expr("sia_qtd")
    sv = _sum_expr("sia_valor")
    return _run(
        f"""
        SELECT q.ano, q.mes, q.quantidade, COALESCE(v.valor, 0) AS valor
        FROM (
            SELECT ano, mes, SUM({sq}) AS quantidade
            FROM sia_qtd
            WHERE {w}
            GROUP BY ano, mes
        ) q
        LEFT JOIN (
            SELECT ano, mes, SUM({sv}) AS valor
            FROM sia_valor
            WHERE {w}
            GROUP BY ano, mes
        ) v ON q.ano = v.ano AND q.mes = v.mes
        ORDER BY q.ano, q.mes
        """,
        p,
    )


def query_grupos(
    ano_ini: int,
    ano_fim: int,
    municipios: list[str] | None = None,
    uf_prefixes: list[str] | None = None,
) -> pl.DataFrame:
    """Retorna (grupo, quantidade, valor) somados por grupo de procedimento."""
    w, p = _where(ano_ini, ano_fim, municipios, uf_prefixes)
    cols_qtd = _proc_cols("sia_qtd")
    cols_val = _proc_cols("sia_valor")
    if not cols_qtd and not cols_val:
        return pl.DataFrame()

    qtd_map: dict[str, float] = {}
    if cols_qtd:
        sel = ", ".join(f'SUM(COALESCE("{c}", 0)) AS "g{i}"' for i, c in enumerate(cols_qtd))
        df = _run(f"SELECT {sel} FROM sia_qtd WHERE {w}", p)
        if not df.is_empty():
            qtd_map = {c: float(df[f"g{i}"][0] or 0) for i, c in enumerate(cols_qtd)}

    val_map: dict[str, float] = {}
    if cols_val:
        sel = ", ".join(f'SUM(COALESCE("{c}", 0)) AS "g{i}"' for i, c in enumerate(cols_val))
        df = _run(f"SELECT {sel} FROM sia_valor WHERE {w}", p)
        if not df.is_empty():
            val_map = {c: float(df[f"g{i}"][0] or 0) for i, c in enumerate(cols_val)}

    all_groups = sorted(set(cols_qtd) | set(cols_val))
    rows = [
        {"grupo": g, "quantidade": qtd_map.get(g, 0.0), "valor": val_map.get(g, 0.0)}
        for g in all_groups
    ]
    return pl.from_dicts(rows)


def query_ranking(
    ano_ini: int,
    ano_fim: int,
    municipios: list[str] | None = None,
    uf_prefixes: list[str] | None = None,
    top: int = 20,
) -> pl.DataFrame:
    w, p = _where(ano_ini, ano_fim, municipios, uf_prefixes)
    sq = _sum_expr("sia_qtd")
    sv = _sum_expr("sia_valor")
    return _run(
        f"""
        SELECT q.municipio_nome, q.quantidade, COALESCE(v.valor, 0) AS valor
        FROM (
            SELECT municipio_cod, municipio_nome, SUM({sq}) AS quantidade
            FROM sia_qtd
            WHERE {w}
            GROUP BY municipio_cod, municipio_nome
            ORDER BY quantidade DESC
            LIMIT {top}
        ) q
        LEFT JOIN (
            SELECT municipio_cod, SUM({sv}) AS valor
            FROM sia_valor
            WHERE {w}
            GROUP BY municipio_cod
        ) v ON q.municipio_cod = v.municipio_cod
        ORDER BY q.quantidade DESC
        """,
        p,
    )
