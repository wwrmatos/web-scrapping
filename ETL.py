import pandas as pd
import os
import io
import csv
from sqlalchemy import create_engine
from dotenv import load_dotenv

# 1. Configurações Iniciais
load_dotenv()

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
engine = create_engine(DATABASE_URL)
SCHEMA_DESTINO = 'public'

def psql_insert_copy(table, conn, keys, data_iter):
    """
    Função de callback para o pandas.to_sql usar o comando COPY do PostgreSQL.
    Esta é a forma mais rápida e segura de fazer carga em massa.
    """
    dbapi_conn = conn.connection.dbapi_connection
    with dbapi_conn.cursor() as cur:
        s_buf = io.StringIO()
        writer = csv.writer(s_buf, delimiter='\t')
        writer.writerows(data_iter)
        s_buf.seek(0)

        columns = ', '.join('"{}"'.format(k) for k in keys)
        if table.schema:
            table_name = '"{}"."{}"'.format(table.schema, table.name)
        else:
            table_name = '"{}"'.format(table.name)

        sql = 'COPY {} ({}) FROM STDIN WITH CSV DELIMITER \'\t\''.format(
            table_name, columns)
        cur.copy_expert(sql=sql, file=s_buf)

def tratar_e_preparar_longo(caminho_csv, tipo_dado):
    print(f"\n--- Lendo Arquivo: {os.path.basename(caminho_csv)} ---")
    
    # Lendo o CSV
    df = pd.read_csv(caminho_csv, sep=';', encoding='utf-8-sig', skiprows=1, low_memory=False)
    
    # Limpa nomes de colunas (remove aspas e espaços)
    df.columns = [str(c).replace('"', '').strip() for c in df.columns]

    # 1. Identifica a coluna de período
    if 'periodo' in df.columns:
        coluna_periodo = 'periodo'
    else:
        colunas_possiveis = [c for c in df.columns if '/' in str(c) and not str(c)[0].isdigit()]
        coluna_periodo = colunas_possiveis[0] if colunas_possiveis else None

    if not coluna_periodo:
        print(f"❌ Erro: Coluna de período não encontrada!")
        return None

    # 2. Limpeza de linhas (Remove Totais e Notas)
    df = df[df['Município'].notna()]
    # Limpa aspas e espaços extras dos valores
    df['Município'] = df['Município'].astype(str).str.replace('"', '').str.strip()
    df = df[~df['Município'].str.contains('Total|FONTE|[Ii]gnorado', case=False, na=False)]

    # NOVO: Separa Código e Nome do Município (Ex: "110001 ALTA FLORESTA" -> "110001" e "ALTA FLORESTA")
    mun_split = df['Município'].str.extract(r'^(\d{6})?\s*(.*)')
    df['municipio_cod'] = mun_split[0]
    df['municipio_nome'] = mun_split[1]

    # 3. Separa Mês e Ano
    datas_split = df[coluna_periodo].str.split('/', n=1, expand=True)
    df['mes'] = datas_split[0]
    df['ano'] = datas_split[1]

    # 4. Seleciona colunas de procedimentos (ex: 0101, 0204...)
    cols_procedimentos = [c for c in df.columns if str(c)[:4].isdigit()]
    
    print(f"  ✓ Transformando {len(cols_procedimentos)} procedimentos em linhas...")

    # Wide to Long (MELT)
    # Atualizado id_vars para incluir as novas colunas de município
    df_longo = df.melt(
        id_vars=['municipio_cod', 'municipio_nome', 'mes', 'ano'],
        value_vars=cols_procedimentos,
        var_name='procedimento_completo',
        value_name=tipo_dado
    )

    # Limpeza de números e extração de código/nome
    df_longo[tipo_dado] = pd.to_numeric(df_longo[tipo_dado].astype(str).str.replace('-', '0'), errors='coerce').fillna(0)
    
    res_extract = df_longo['procedimento_completo'].str.extract(r'^(\d{4})\s+(.*)')
    df_longo['procedimento_cod'] = res_extract[0]
    df_longo['procedimento_nome'] = res_extract[1]
    
    df_longo.drop(columns=['procedimento_completo'], inplace=True)

    return df_longo

if __name__ == "__main__":
    arquivos = {
        'baixados_sia/SIA_Qtd.aprovada.csv': 'quantidade',
        'baixados_sia/SIA_Valor_aprovado.csv': 'valor'
    }
    
    for caminho, tipo in arquivos.items():
        if os.path.exists(caminho):
            df_final = tratar_e_preparar_longo(caminho, tipo)
            if df_final is not None:
                nome_tabela = f"sia_{tipo}"
                print(f"  🚀 Iniciando carga rápida na tabela '{nome_tabela}'...")
                
                # O segredo está aqui: method=psql_insert_copy
                df_final.to_sql(
                    name=nome_tabela, 
                    con=engine, 
                    schema=SCHEMA_DESTINO, 
                    if_exists='replace', 
                    index=False, 
                    method=psql_insert_copy
                )
                
                print(f"  ✓ {len(df_final)} registros inseridos com sucesso!")
        else:
            print(f"⚠️ Arquivo não encontrado: {caminho}")

    print("\n✅ ETL Finalizado com sucesso!")
