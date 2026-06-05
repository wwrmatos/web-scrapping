import asyncio
import pandas as pd
import os
from playwright.async_api import async_playwright

# Configurações
TIMEOUT = 60000  # 60 segundos
MESES = 25  # Jan/2024 a Jan/2026
URL = "http://tabnet.datasus.gov.br/cgi/deftohtm.exe?sia/cnv/qabr.def"


async def wait_and_click(page, selector, timeout=TIMEOUT):
    await page.wait_for_selector(selector, state="visible", timeout=timeout)
    await page.click(selector)


async def get_current_text(page, selector):
    element = await page.wait_for_selector(selector, timeout=TIMEOUT)
    return await element.text_content()


async def click_xpath(page, xpath, timeout=TIMEOUT):
    selector = f'xpath={xpath}'
    await page.wait_for_selector(selector, timeout=timeout)
    await page.click(selector)


async def select_option_by_label(page, selector, label, timeout=TIMEOUT):
    await page.wait_for_selector(selector, timeout=timeout)
    selected = await page.select_option(selector, label=label)
    if selected:
        return selected

    normalized = label.strip().lower()
    if not normalized:
        normalized = ''

    success = await page.evaluate(
        """
        (sel, label) => {
            const element = document.querySelector(sel);
            if (!element) return false;
            const options = Array.from(element.options);
            const exactMatch = options.find(o => (o.label || o.text || '').trim().toLowerCase() === label);
            const partialMatch = options.find(o => (o.label || o.text || '').trim().toLowerCase().includes(label));
            const valueMatch = options.find(o => (o.value || '').trim().toLowerCase().includes(label));
            const option = exactMatch || partialMatch || valueMatch;
            if (!option) return false;
            element.value = option.value;
            element.dispatchEvent(new Event('input', { bubbles: true }));
            element.dispatchEvent(new Event('change', { bubbles: true }));
            return true;
        }
        """,
        selector,
        normalized,
    )
    if not success:
        raise ValueError(
            f"Option with label '{label}' not found in '{selector}'")
    return success


async def get_data():
    os.makedirs("data/raw", exist_ok=True)

    async with async_playwright() as p:
        # Lança navegador (headless=False para ver a automação)
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(URL, timeout=TIMEOUT)
        await page.wait_for_selector('xpath=//*[@id="I"]', timeout=TIMEOUT)

        # Conteúdo: vazio
        await wait_and_click(page, 'xpath=//*[@id="I"]/option[1]')
        # Linha: vazio
        await wait_and_click(page, 'xpath=//*[@id="L"]/option[1]')
        # Coluna: vazio
        await wait_and_click(page, 'xpath=//*[@id="C"]/option[1]')
        # Período: vazio
        await wait_and_click(page, 'xpath=//*[@id="A"]/option[1]')

        # Exibir linhas zeradas e Separador ponto e vírgula
        await page.check('xpath=//*[@id="Z"]')
        await click_xpath(page, '/html/body/div/div/center/div/form/div[4]/div[2]/div[1]/div[2]/input[3]')

        # 3. Configurar Linha (Município) e Coluna (Subgrupo proc.)
        await select_option_by_label(page, '#L', 'Município')
        await select_option_by_label(page, '#C', 'Subgrupo proced.')

        # 4. Loop sobre Conteúdo (Qtd. Aprovada e Valor Aprovado)
        conteudo_labels = ['Qtd.aprovada', 'Valor aprovado']
        meses = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun',
                 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']

        for conteudo_label in conteudo_labels:
            await select_option_by_label(page, '#I', conteudo_label)
            conteudo_nome = conteudo_label.strip()

            print(f"\n--- Processando Conteúdo: {conteudo_nome} ---")
            df_consolidado = pd.DataFrame()

            for mes_idx in range(MESES):
                year = 2024 + mes_idx // 12
                month_idx = mes_idx % 12
                month_text = meses[month_idx]

                periodo_label = f"{month_text}/{year}"

                print(f"  Tentando período {periodo_label}...")
                try:
                    await select_option_by_label(page, '#A', periodo_label)
                    ano_mes_text = periodo_label
                except ValueError as ex:
                    print(
                        f"    Período {periodo_label} não disponível, pulando...")
                    continue

                botao_mostrar = '/html/body/div/div/center/div/form/div[4]/div[2]/div[2]/input[1]'
                await page.wait_for_selector(f'xpath={botao_mostrar}', timeout=TIMEOUT)

                new_page = None
                pagina_resultado = page
                try:
                    async with context.expect_page(timeout=10000) as new_page_info:
                        await page.click(f'xpath={botao_mostrar}')
                    new_page = await new_page_info.value
                    await new_page.wait_for_load_state('load', timeout=TIMEOUT)
                    pagina_resultado = new_page
                except Exception as e:
                    print(
                        f"    Nenhuma nova aba detectada; usando página atual. Detalhe: {e}")
                    await page.wait_for_load_state('load', timeout=TIMEOUT)

                try:
                    await pagina_resultado.wait_for_selector('pre', timeout=120000)

                    pre_element = await pagina_resultado.query_selector('pre')
                    texto = await pre_element.inner_text()
                    linhas_raw = texto.strip().split('\n')

                    if len(linhas_raw) < 2:
                        raise ValueError("Dados insuficientes no pre")

                    # DATASUS inclui o período no final da linha de cabeçalho (ex: "Jan/2024")
                    # Removemos para que o nome das colunas seja consistente entre meses
                    cabecalho = [c.strip().strip('"') for c in linhas_raw[0].split(';')]
                    if cabecalho and '/' in cabecalho[-1]:
                        cabecalho = cabecalho[:-1]
                    n_colunas = len(cabecalho)

                    dados = []
                    for linha in linhas_raw[1:]:
                        if linha.strip():
                            valores = [v.strip().strip('"') for v in linha.split(';')]
                            valores = (valores + [''] * n_colunas)[:n_colunas]
                            dados.append(valores)

                    df_mes = pd.DataFrame(dados, columns=cabecalho)
                    df_mes['periodo'] = ano_mes_text

                    print(
                        f"    ✓ Capturado {len(df_mes)} linhas para {ano_mes_text}")

                    if df_consolidado.empty:
                        df_consolidado = df_mes
                    else:
                        df_consolidado = pd.concat(
                            [df_consolidado, df_mes], ignore_index=True)

                except Exception as e:
                    safe_period = ano_mes_text.replace('/', '_')
                    print(
                        f"    ✗ ERRO no período {ano_mes_text}: {type(e).__name__}: {e}")
                    try:
                        content = await pagina_resultado.content()
                    except Exception:
                        content = ''
                    with open(f"debug_sia_{safe_period}.html", "w", encoding="utf-8") as f:
                        f.write(content)

                if new_page is not None:
                    try:
                        await pagina_resultado.close()
                    except Exception:
                        pass
                    await page.bring_to_front()
                else:
                    try:
                        await page.go_back()
                    except Exception:
                        pass
                await asyncio.sleep(1)

            nome_clean = conteudo_nome.replace('/', '_').replace(' ', '_').replace('.', '_').lower()
            nome_arquivo = f"data/raw/sia_{nome_clean}.csv"

            if df_consolidado.empty:
                print(
                    f"⚠️  Nenhum dado foi coletado para {conteudo_nome}. Arquivo NÃO será salvo.")
            else:
                df_consolidado.to_csv(
                    nome_arquivo, index=False, sep=';', encoding='utf-8-sig')
                print(
                    f"✓ Arquivo salvo: {nome_arquivo} ({len(df_consolidado)} linhas)")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(get_data())
