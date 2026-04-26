import polars as pl
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dash import Dash, dcc, html, Input, Output
import dash_bootstrap_components as dbc

from data import (
    REGIOES, UF_COD,
    get_anos, get_municipios,
    query_kpis, query_serie_temporal, query_ranking, query_grupos,
)

app = Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP, dbc.icons.BOOTSTRAP],
    title="SIA/SUS — Produção Ambulatorial",
)
server = app.server  # expõe para gunicorn / HuggingFace

anos = get_anos()
ANO_MIN = anos[0] if anos else 2008
ANO_MAX = anos[-1] if anos else 2024

# ── Helpers ──────────────────────────────────────────────────────────────────

_BLUE  = "#0d6efd"
_GREEN = "#198754"
_GRAY  = "#6c757d"
_CARD  = {"borderRadius": "12px", "boxShadow": "0 2px 10px rgba(0,0,0,.08)"}

_MES_PT = {
    "Jan": "01", "Fev": "02", "Mar": "03", "Abr": "04",
    "Mai": "05", "Jun": "06", "Jul": "07", "Ago": "08",
    "Set": "09", "Out": "10", "Nov": "11", "Dez": "12",
}


def _fmt_num(n: float) -> str:
    return f"{int(n):,}".replace(",", ".")


def _fmt_brl(n: float) -> str:
    s = f"{float(n):,.2f}"           # "1,234,567.89"
    inteiro, decimal = s.split(".")
    return "R$ " + inteiro.replace(",", ".") + "," + decimal


# ── Charts ───────────────────────────────────────────────────────────────────

def _empty_fig(msg: str, height: int = 300) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=msg, xref="paper", yref="paper", x=0.5, y=0.5,
        showarrow=False, font=dict(size=14, color=_GRAY),
    )
    fig.update_layout(
        height=height,
        plot_bgcolor="white", paper_bgcolor="white",
        xaxis=dict(visible=False), yaxis=dict(visible=False),
    )
    return fig


def fig_serie_temporal(df: pl.DataFrame) -> go.Figure:
    if df.is_empty():
        return _empty_fig("Sem dados para o período selecionado", 480)

    df = (
        df.with_columns(
            pl.col("mes")
            .map_elements(lambda m: _MES_PT.get(str(m), "00"), return_dtype=pl.Utf8)
            .alias("mes_num")
        )
        .with_columns(
            pl.concat_str([pl.col("ano").cast(pl.Utf8), pl.lit("-"), pl.col("mes_num"), pl.lit("-01")])
            .str.to_date("%Y-%m-%d", strict=False)
            .alias("data")
        )
        .filter(pl.col("data").is_not_null())
        .sort("data")
    )

    datas = df["data"].to_list()
    qtds  = df["quantidade"].cast(pl.Float64).to_list()
    vals  = df["valor"].cast(pl.Float64).to_list()

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=("Quantidade de Procedimentos", "Valor Aprovado (R$)"),
    )

    fig.add_trace(
        go.Scatter(
            x=datas, y=qtds, name="Procedimentos",
            line=dict(color=_BLUE, width=2.5),
            mode="lines+markers", marker=dict(size=4),
            hovertemplate="%{x|%b %Y}<br>Qtd: <b>%{y:,.0f}</b><extra></extra>",
            fill="tozeroy", fillcolor=f"rgba(13,110,253,0.08)",
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=datas, y=vals, name="Valor aprovado",
            line=dict(color=_GREEN, width=2.5),
            mode="lines+markers", marker=dict(size=4),
            hovertemplate="%{x|%b %Y}<br>Valor: <b>R$ %{y:,.2f}</b><extra></extra>",
            fill="tozeroy", fillcolor=f"rgba(25,135,84,0.08)",
        ),
        row=2, col=1,
    )

    fig.update_layout(
        height=480,
        margin=dict(l=0, r=0, t=40, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="right", x=1),
        plot_bgcolor="white", paper_bgcolor="white",
        hovermode="x unified",
        showlegend=False,
    )
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="#f0f0f0")

    return fig


def fig_donut_grupos(df: pl.DataFrame, tipo: str = "qtd") -> go.Figure:
    if df.is_empty():
        return _empty_fig("Sem dados para o período selecionado", 350)

    col_valor = "quantidade" if tipo == "qtd" else "valor"
    # Filtrar e ordenar para pegar os maiores
    df = df.filter(pl.col(col_valor) > 0).sort(col_valor, descending=True)
    
    if df.is_empty():
        return _empty_fig("Sem dados para exibir", 350)

    # Lógica para agrupar Top 5 + Outros
    if len(df) > 5:
        top_5 = df.head(5)
        outros_val = df.slice(5).select(pl.col(col_valor).sum()).item()
        
        outros_df = pl.DataFrame({
            "grupo": ["Outros"],
            "quantidade": [outros_val if tipo == "qtd" else 0.0],
            "valor": [outros_val if tipo == "valor" else 0.0]
        })
        df = pl.concat([top_5, outros_df])

    labels = df["grupo"].to_list()
    valores = df[col_valor].cast(pl.Float64).to_list()
    textos = [(_fmt_num(v) if tipo == "qtd" else _fmt_brl(v)) for v in valores]
    
    fig = go.Figure(
        go.Pie(
            labels=labels,
            values=valores,
            hole=0.6,
            textinfo="percent",
            text=textos,
            hovertemplate="<b>%{label}</b><br>" + 
                          ("Quantidade" if tipo == "qtd" else "Valor") + ": %{text}<br>" +
                          "Percentual: %{percent}<extra></extra>",
            marker=dict(line=dict(color="white", width=2)),
        )
    )
    
    fig.update_layout(
        height=420,
        margin=dict(l=10, r=10, t=20, b=100),
        legend=dict(
            orientation="h",
            yanchor="top", y=-0.2,
            xanchor="center", x=0.5,
            font=dict(size=10)
        ),
        plot_bgcolor="white", paper_bgcolor="white",
    )
    return fig


def fig_ranking(df: pl.DataFrame) -> go.Figure:
    if df.is_empty():
        return _empty_fig("Sem dados para o período selecionado", 500)

    df = df.sort("quantidade")  # ascending → maior fica no topo no plotly horizontal
    nomes = df["municipio_nome"].to_list()
    qtds  = df["quantidade"].cast(pl.Float64).to_list()

    fig = go.Figure(
        go.Bar(
            x=qtds, y=nomes, orientation="h",
            marker=dict(color=qtds, colorscale="Blues", showscale=False),
            text=[_fmt_num(v) for v in qtds],
            textposition="auto",
            hovertemplate="<b>%{y}</b><br>Procedimentos: %{text}<extra></extra>",
        )
    )
    fig.update_layout(
        height=max(420, len(nomes) * 30),
        margin=dict(l=0, r=60, t=10, b=0),
        plot_bgcolor="white", paper_bgcolor="white",
        xaxis=dict(showgrid=True, gridcolor="#f0f0f0", title="Total de procedimentos"),
        yaxis=dict(tickfont=dict(size=11)),
    )
    return fig


# ── Layout ───────────────────────────────────────────────────────────────────

header = dbc.Navbar(
    dbc.Container(
        dbc.Row([
            dbc.Col(html.I(className="bi bi-hospital fs-4 text-white"), width="auto"),
            dbc.Col([
                html.Span("SIA/SUS", className="fw-bold fs-5 text-white ms-2"),
                html.Span(" — Produção Ambulatorial", className="text-white-50 small"),
            ]),
        ], align="center"),
        fluid=True,
    ),
    color="primary", dark=True, className="mb-3",
)

filtros = dbc.Card(
    dbc.CardBody(
        dbc.Row([
            dbc.Col([
                html.Label("Região", className="small fw-semibold text-muted"),
                dcc.Dropdown(
                    id="dd-regiao",
                    options=[{"label": r, "value": r} for r in REGIOES],
                    placeholder="Todas",
                    clearable=True,
                    className="mt-1",
                ),
            ], sm=6, md=3, lg=2, className="mb-2"),

            dbc.Col([
                html.Label("UF", className="small fw-semibold text-muted"),
                dcc.Dropdown(
                    id="dd-uf",
                    placeholder="Todas",
                    clearable=True,
                    className="mt-1",
                ),
            ], sm=6, md=3, lg=2, className="mb-2"),

            dbc.Col([
                html.Label("Município", className="small fw-semibold text-muted"),
                dcc.Dropdown(
                    id="dd-mun",
                    placeholder="Selecione uma UF primeiro",
                    multi=True,
                    clearable=True,
                    disabled=True,
                    className="mt-1",
                ),
            ], sm=12, md=6, lg=4, className="mb-2"),

            dbc.Col([
                html.Label("Período (ano)", className="small fw-semibold text-muted"),
                dcc.RangeSlider(
                    id="sl-anos",
                    min=ANO_MIN, max=ANO_MAX,
                    value=[ANO_MIN, ANO_MAX],
                    marks={
                        y: {"label": str(y), "style": {"fontSize": "11px"}}
                        for y in range(ANO_MIN, ANO_MAX + 1, 2)
                    },
                    step=1,
                    tooltip={"placement": "bottom", "always_visible": True},
                    className="mt-3",
                ),
            ], sm=12, lg=4, className="mb-2"),
        ], align="end"),
    ),
    style=_CARD, className="mb-3",
)

kpi_row = dbc.Row([
    dbc.Col(
        dbc.Card(dbc.CardBody([
            html.P(
                [html.I(className="bi bi-clipboard2-pulse me-2"), "Total de Procedimentos"],
                className="text-muted small mb-1",
            ),
            html.H2(id="kpi-qtd", className="fw-bold text-primary mb-0"),
        ]), style=_CARD),
        sm=12, md=6, className="mb-3",
    ),
    dbc.Col(
        dbc.Card(dbc.CardBody([
            html.P(
                [html.I(className="bi bi-cash-coin me-2"), "Valor Aprovado"],
                className="text-muted small mb-1",
            ),
            html.H2(id="kpi-valor", className="fw-bold text-success mb-0"),
        ]), style=_CARD),
        sm=12, md=6, className="mb-3",
    ),
], className="mb-1")

grafico_ts = dbc.Card([
    dbc.CardHeader(
        [html.I(className="bi bi-graph-up me-2"), "Evolução Temporal da Produção Ambulatorial"],
        className="fw-semibold bg-white border-bottom-0 pt-3",
    ),
    dbc.CardBody(
        dcc.Loading(
            dcc.Graph(id="chart-ts", config={"displayModeBar": False}),
            type="dot", color=_BLUE,
        ),
    ),
], style=_CARD, className="mb-3")

graficos_donuts = dbc.Row([
    dbc.Col(
        dbc.Card([
            dbc.CardHeader(
                [html.I(className="bi bi-pie-chart-fill me-2"), "Distribuição por Grupo (Qtd)"],
                className="fw-semibold bg-white border-bottom-0 pt-3",
            ),
            dbc.CardBody(
                dcc.Loading(
                    dcc.Graph(id="chart-donut-qtd", config={"displayModeBar": False}),
                    type="dot", color=_BLUE,
                ),
            ),
        ], style=_CARD),
        sm=12, lg=6, className="mb-3",
    ),
    dbc.Col(
        dbc.Card([
            dbc.CardHeader(
                [html.I(className="bi bi-pie-chart-fill me-2"), "Distribuição por Grupo (Valor)"],
                className="fw-semibold bg-white border-bottom-0 pt-3",
            ),
            dbc.CardBody(
                dcc.Loading(
                    dcc.Graph(id="chart-donut-valor", config={"displayModeBar": False}),
                    type="dot", color=_GREEN,
                ),
            ),
        ], style=_CARD),
        sm=12, lg=6, className="mb-3",
    ),
])

grafico_rank = dbc.Card([
    dbc.CardHeader(
        [html.I(className="bi bi-bar-chart-horizontal me-2"), "Top 20 Municípios — Produção Ambulatorial"],
        className="fw-semibold bg-white border-bottom-0 pt-3",
    ),
    dbc.CardBody(
        dcc.Loading(
            dcc.Graph(id="chart-rank", config={"displayModeBar": False}),
            type="dot", color=_BLUE,
        ),
    ),
], style=_CARD, className="mb-3")

app.layout = dbc.Container(
    [header, filtros, kpi_row, grafico_ts, graficos_donuts, grafico_rank],
    fluid=True, className="px-3 py-2",
)

# ── Callbacks ────────────────────────────────────────────────────────────────

@app.callback(
    Output("dd-uf", "options"),
    Output("dd-uf", "value"),
    Input("dd-regiao", "value"),
)
def cb_uf(regiao: str | None):
    ufs = REGIOES.get(regiao, list(UF_COD.keys())) if regiao else list(UF_COD.keys())
    return [{"label": u, "value": u} for u in sorted(ufs)], None


@app.callback(
    Output("dd-mun", "options"),
    Output("dd-mun", "value"),
    Output("dd-mun", "disabled"),
    Output("dd-mun", "placeholder"),
    Input("dd-uf", "value"),
)
def cb_mun(uf: str | None):
    if not uf:
        return [], None, True, "Selecione uma UF primeiro"
    rows = get_municipios(uf)
    options = [{"label": r["municipio_nome"], "value": r["municipio_cod"]} for r in rows]
    return options, None, False, "Todos"


@app.callback(
    Output("kpi-qtd",   "children"),
    Output("kpi-valor", "children"),
    Output("chart-ts",  "figure"),
    Output("chart-donut-qtd", "figure"),
    Output("chart-donut-valor", "figure"),
    Output("chart-rank","figure"),
    Input("sl-anos",   "value"),
    Input("dd-regiao", "value"),
    Input("dd-uf",     "value"),
    Input("dd-mun",    "value"),
)
def cb_dashboard(anos_range: list, regiao: str | None, uf: str | None, municipios: list | None):
    a1, a2 = anos_range
    muns   = municipios if municipios else None

    if muns:
        uf_prefixes = None
    elif uf:
        uf_prefixes = [UF_COD[uf]] if uf in UF_COD else None
    elif regiao:
        uf_prefixes = [UF_COD[u] for u in REGIOES.get(regiao, []) if u in UF_COD]
    else:
        uf_prefixes = None

    k     = query_kpis(a1, a2, muns, uf_prefixes)
    df_ts = query_serie_temporal(a1, a2, muns, uf_prefixes)
    df_rk = query_ranking(a1, a2, muns, uf_prefixes)
    df_gp = query_grupos(a1, a2, muns, uf_prefixes)

    return (
        _fmt_num(k["qtd"]),
        _fmt_brl(k["valor"]),
        fig_serie_temporal(df_ts),
        fig_donut_grupos(df_gp, "qtd"),
        fig_donut_grupos(df_gp, "valor"),
        fig_ranking(df_rk),
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7860, debug=False)
