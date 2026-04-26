import pandas as pd
import os
import io
import csv
from sqlalchemy import create_engine
from dotenv import load_dotenv

load_dotenv()

# Configurações de Banco de Dados
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
engine = create_engine(DATABASE_URL)
SCHEMA_DESTINO = 'public'

def psql_insert_copy(table, conn, keys, data_iter):
    dbapi_conn = conn.connection.dbapi_connection
    with dbapi_conn.cursor() as cur:
        s_buf = io.StringIO()
        writer = csv.writer(s_buf, delimiter='\t')
        writer.writerows(data_iter)
        s_buf.seek(0)

        columns = ', '.join('"{}"'.format(k) for k in keys)
        table_name = '"{}"."{}"'.format(table.schema, table.name) if table.schema else '"{}"'.format(table.name)

        sql = 'COPY {} ({}) FROM STDIN WITH CSV DELIMITER \'\t\''.format(table_name, columns)
        cur.copy_expert(sql=sql, file=s_buf)

def tratar_e_preparar_wide(caminho_csv):
    print(f"\n--- Lendo Arquivo: {os.path.basename(caminho_csv)} ---")
    
    # Leitura inicial pulando a primeira linha (skiprows=1)
    df = pd.read_csv(caminho_csv, sep=';', encoding='utf-8-sig', skiprows=1, low_memory=False)
    
    # 1. Limpeza de nomes de colunas
    df.columns = [str(c).replace('"', '').strip() for c in df.columns]
    df = df.loc[:, ~df.columns.str.startswith('Unnamed')]

    # 2. Identificação da coluna de período
    if 'periodo' in df.columns:
        coluna_periodo = 'periodo'
    else:
        colunas_possiveis = [c for c in df.columns if '/' in str(c) and not str(c)[0].isdigit()]
        coluna_periodo = colunas_possiveis[0] if colunas_possiveis else None

    if not coluna_periodo:
        print(f"❌ Erro: Coluna de período não encontrada!")
        return None

    # 3. Limpeza de linhas de Município (Remove totais e notas de rodapé)
    df = df[df['Município'].notna()]
    df['Município'] = df['Município'].astype(str).str.replace('"', '').str.strip()
    df = df[~df['Município'].str.contains('Total|FONTE|[Ii]gnorado', case=False, na=False)]

    # 4. Extração de Código e Nome do Município
    mun_split = df['Município'].str.extract(r'^(\d{6})?\s*(.*)')
    df['municipio_cod'] = mun_split[0]
    df['municipio_nome'] = mun_split[1]

    # 5. Extração de Mês e Ano
    datas_split = df[coluna_periodo].str.split('/', n=1, expand=True)
    df['mes'] = datas_split[0]
    df['ano'] = datas_split[1]

    # 6. Limpeza dos valores numéricos em todas as colunas de procedimentos (que começam com números)
    cols_procedimentos = [c for c in df.columns if str(c)[:4].isdigit()]
    
    for col in cols_procedimentos:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace('-', '0').str.replace('.', '').str.replace(',', '.'), errors='coerce').fillna(0)

    # 7. Organização final: Remove colunas originais tratadas para evitar duplicidade
    # Mantemos as colunas de procedimentos em formato 'Wide'
    df.drop(columns=['Município', coluna_periodo], inplace=True)

    return df

if __name__ == "__main__":
    pasta = os.path.join(os.path.dirname(__file__), 'baixados_sia')

    for arquivo in os.listdir(pasta):
        caminho = os.path.join(pasta, arquivo)
        if os.path.exists(caminho):
            df_final = tratar_e_preparar_wide(caminho)
            if df_final is not None:
                # Define o nome da tabela com base no arquivo
                prefixo = "sia_qtd" if "Qtd" in caminho else "sia_valor"

                print(f"  🚀 Iniciando carga na tabela '{prefixo}' (Formato Wide)...")

                df_final.to_sql(
                    name=prefixo,
                    con=engine,
                    schema=SCHEMA_DESTINO,
                    if_exists='replace',
                    index=False,
                    method=psql_insert_copy
                )

                print(f"  ✓ {len(df_final)} linhas e {len(df_final.columns)} colunas inseridas!")
        else:
            print(f"⚠️ Arquivo não encontrado: {caminho}")

    print("\n✅ ETL Finalizado!")