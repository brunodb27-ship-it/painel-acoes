"""
Calcula indicadores fundamentalistas de todas as companhias abertas a partir dos
dados abertos da CVM (DFP anual + ITR trimestral + FCA cadastral).

Metodologia (12 meses = "TTM"):
  Fluxo (lucro, receita, EBIT, D&A, proventos declarados):
      anual do último DFP  +  acumulado do ano no último ITR  -  mesmo acumulado do ano anterior
  Estoque (patrimônio, dívida, caixa, ativos): último ITR (ou DFP, se for mais recente)
  Ações: composição do capital do último documento, descontando as ações em tesouraria
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import pandas as pd

CVM = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC"


# ---------------------------------------------------------------- leitura
def _read(z: zipfile.ZipFile, name: str) -> pd.DataFrame:
    with z.open(name) as f:
        return pd.read_csv(io.TextIOWrapper(f, encoding="latin1"), sep=";", dtype=str)


def _member(z: zipfile.ZipFile, part: str) -> str | None:
    for n in z.namelist():
        if part in n:
            return n
    return None


def load_docs(zips: list[Path]) -> dict[str, pd.DataFrame]:
    """Junta vários anos do mesmo documento (ex.: DFP 2025 + DFP 2024).
    Cada empresa usa depois o seu documento mais recente, então quem ainda não
    entregou o balanço do ano fica com o do ano anterior em vez de sumir."""
    partes = [load_doc(z) for z in zips]
    out = {}
    for key in {k for p in partes for k in p}:
        dfs = [p[key] for p in partes if key in p]
        out[key] = pd.concat(dfs, ignore_index=True).drop_duplicates() if len(dfs) > 1 else dfs[0]
    return out


def load_doc(zip_path: Path) -> dict[str, pd.DataFrame]:
    """Lê do zip (DFP ou ITR) só as demonstrações consolidadas usadas no cálculo."""
    out = {}
    with zipfile.ZipFile(zip_path) as z:
        # _ind = demonstrações individuais: usadas quando a empresa não tem controladas e o consolidado vem vazio ou zerado
        for key in ["DRE_con", "BPA_con", "BPP_con", "DVA_con", "DRE_ind", "BPA_ind", "BPP_ind", "DVA_ind", "composicao_capital"]:
            n = _member(z, key)
            if n:
                df = _read(z, n)
                if "VL_CONTA" in df:
                    df["VL"] = pd.to_numeric(df["VL_CONTA"], errors="coerce")
                    # escala: valores em MIL na maioria das empresas
                    esc = df.get("ESCALA_MOEDA", "MIL")
                    df["VL"] = df["VL"] * (esc.map({"MIL": 1000, "UNIDADE": 1}).fillna(1000) if isinstance(esc, pd.Series) else 1000)
                out[key] = df
    return out


def load_tickers(fca_zips) -> pd.DataFrame:
    """Códigos de negociação ativos, juntando o cadastro (FCA) de mais de um ano.
    O FCA do ano corrente vem incompleto até as empresas reapresentarem o formulário;
    o do ano anterior cobre as que faltam. Para cada código vale o registro mais novo."""
    if isinstance(fca_zips, (str, Path)):
        fca_zips = [fca_zips]
    vms, gerais = [], []
    ano_de = lambda zp: int((re.findall(r"(\d{4})\.zip$", str(zp)) or ["0"])[0])
    for zp in sorted(fca_zips, key=ano_de):  # do ano mais antigo para o mais novo
        with zipfile.ZipFile(zp) as z:
            vm = _read(z, _member(z, "valor_mobiliario"))
            geral = _read(z, _member(z, "geral"))
        vm["_ano"] = ano_de(zp)
        geral["_ano"] = ano_de(zp)
        vms.append(vm)
        gerais.append(geral)
    vm = pd.concat(vms, ignore_index=True)
    geral = pd.concat(gerais, ignore_index=True)
    vm["_v"] = pd.to_numeric(vm["Versao"], errors="coerce").fillna(0)
    geral["_v"] = pd.to_numeric(geral["Versao"], errors="coerce").fillna(0)
    vm = vm[(vm["Mercado"] == "Bolsa") & vm["Codigo_Negociacao"].notna()]
    vm = vm[vm["Codigo_Negociacao"].str.match(r"^[A-Z]{4}\d{1,2}$")]
    # registro mais novo de cada código; só depois filtra os que deixaram de negociar
    vm = vm.sort_values(["_ano", "_v"]).drop_duplicates(["CNPJ_Companhia", "Codigo_Negociacao"], keep="last")
    vm = vm[vm["Data_Fim_Negociacao"].isna()]
    vm = vm.drop_duplicates("Codigo_Negociacao", keep="last")
    setor_col = next((c for c in geral.columns if "Setor" in c), None)
    g = geral.sort_values(["_ano", "_v"]).drop_duplicates("CNPJ_Companhia", keep="last")
    g = g[["CNPJ_Companhia"] + ([setor_col] if setor_col else [])].rename(columns={setor_col: "setor"} if setor_col else {})
    t = vm[["CNPJ_Companhia", "Nome_Empresarial", "Codigo_Negociacao", "Valor_Mobiliario", "Segmento"]].merge(g, on="CNPJ_Companhia", how="left")
    t.columns = ["cnpj", "empresa", "ticker", "tipo", "segmento"] + (["setor"] if setor_col else [])
    return t.reset_index(drop=True)


# ---------------------------------------------------------------- helpers de conta
LUCRO_CONTROLADORA = re.compile(r"Atribu[íi]do a S[óo]cios da Empresa Controladora", re.I)
LUCRO_TOTAL = re.compile(r"Lucro/Preju[íi]zo Consolidado do Per[íi]odo|Lucro/Preju[íi]zo do Per[íi]odo|Lucro ou Preju[íi]zo L[íi]quido", re.I)
EBIT = re.compile(r"Resultado Antes do Resultado Financeiro", re.I)
DA = re.compile(r"Deprecia[çc][ãa]o, Amortiza[çc][ãa]o", re.I)
PL = re.compile(r"^Patrim[ôo]nio L[íi]quido", re.I)
NAO_CONTROL = re.compile(r"N[ãa]o Controladores", re.I)
EMPREST = re.compile(r"^Empr[ée]stimos e Financiamentos$", re.I)
JCP = re.compile(r"Juros sobre o Capital Pr[óo]prio", re.I)
DIVID = re.compile(r"^Dividendos$", re.I)


def _rows(df, cnpj, dt_refer, ordem="ÚLTIMO"):
    x = df[(df["CNPJ_CIA"] == cnpj) & (df["DT_REFER"] == dt_refer) & (df["ORDEM_EXERC"] == ordem)]
    return x


def _ytd(x):
    """Para DRE/DVA de ITR: só as linhas do acumulado do exercício (o período que começa mais cedo).
    Funciona também para quem tem ano fiscal diferente do calendário (ex.: BrasilAgro, de julho a junho)."""
    if "DT_INI_EXERC" not in x or not len(x):
        return x
    ini = x["DT_INI_EXERC"].dropna()
    return x[x["DT_INI_EXERC"] == ini.min()] if len(ini) else x


def _first(x, pattern, prefix=None, depth=None):
    m = x[x["DS_CONTA"].str.contains(pattern, na=False)]
    if prefix:
        m = m[m["CD_CONTA"].str.startswith(prefix)]
    if depth is not None:
        m = m[m["CD_CONTA"].str.count(r"\.") == depth]
    m = m.sort_values("CD_CONTA")
    return float(m["VL"].iloc[0]) if len(m) else None


def _sum(x, pattern, prefix=None, depth=None):
    m = x[x["DS_CONTA"].str.contains(pattern, na=False)]
    if prefix:
        m = m[m["CD_CONTA"].str.startswith(prefix)]
    if depth is not None:
        m = m[m["CD_CONTA"].str.count(r"\.") == depth]
    return float(m["VL"].sum()) if len(m) else None


def _code(x, code):
    m = x[x["CD_CONTA"] == code]
    return float(m["VL"].iloc[0]) if len(m) else None


def _flows(dre, dva):
    lucro = _first(dre, LUCRO_CONTROLADORA)
    total = _first(dre, LUCRO_TOTAL, prefix="3.", depth=1)
    # muitas empresas preenchem a linha "atribuído aos controladores" com zero e põem o lucro só no total
    if lucro is None or (lucro == 0 and total):
        lucro = total
    receita = _code(dre, "3.01")
    bruto = _code(dre, "3.03")
    ebit = _first(dre, EBIT, depth=1)
    da = _first(dva, DA) if dva is not None else None
    jcp = _sum(dva, JCP, prefix="7.08") if dva is not None else None
    div = _sum(dva, DIVID, prefix="7.08") if dva is not None else None
    for pref in ("7.09", "7.10", "7.11"):
        # bancos e seguradoras usam outro modelo de DVA: a distribuição fica mais abaixo
        if jcp is not None or div is not None or dva is None:
            break
        jcp = _sum(dva, JCP, prefix=pref)
        div = _sum(dva, DIVID, prefix=pref)
    prov = (jcp or 0) + (div or 0) if (jcp is not None or div is not None) else None
    return dict(lucro=lucro, receita=receita, bruto=bruto, ebit=ebit, da=abs(da) if da is not None else None, proventos=prov)


def _ttm(a, b, c):
    if a is None:
        return None
    return a + (b or 0) - (c or 0)


def compute(dfp: dict, itr: dict, tickers: pd.DataFrame) -> pd.DataFrame:
    rows = []
    cache = {}
    empty = {}
    def S(doc, key, cnpj):
        k = (id(doc), key)
        if k not in cache:
            df = doc.get(key)
            cache[k] = {c: g for c, g in df.groupby("CNPJ_CIA")} if df is not None else {}
            empty[k] = df.iloc[0:0] if df is not None else pd.DataFrame(columns=["CNPJ_CIA", "DT_REFER", "ORDEM_EXERC", "DS_CONTA", "CD_CONTA", "VL"])
        return cache[k].get(cnpj, empty[k])
    def tipo(doc, cnpj):
        """'con' quando o consolidado tem números; senão 'ind' (empresa sem controladas)."""
        x = S(doc, "DRE_con", cnpj)
        if len(x) and x["VL"].abs().sum() > 0:
            return "con"
        y = S(doc, "DRE_ind", cnpj) if doc.get("DRE_ind") is not None else x
        return "ind" if len(y) and y["VL"].abs().sum() > 0 else "con"
    cnpjs = sorted(set(dfp["DRE_con"]["CNPJ_CIA"]) | set(itr["DRE_con"]["CNPJ_CIA"])
                   | set(dfp.get("DRE_ind", dfp["DRE_con"])["CNPJ_CIA"]) | set(itr.get("DRE_ind", itr["DRE_con"])["CNPJ_CIA"]))
    for cnpj in cnpjs:
        r = {"cnpj": cnpj}
        td, ti = tipo(dfp, cnpj), tipo(itr, cnpj)
        # --- datas
        d_dfp = S(dfp, "DRE_" + td, cnpj)["DT_REFER"].max()
        d_itr = S(itr, "DRE_" + ti, cnpj)["DT_REFER"].max()
        if not isinstance(d_dfp, str):
            d_dfp = None
        if not isinstance(d_itr, str):
            d_itr = None
        if d_itr and d_dfp and d_itr <= d_dfp:
            d_itr = None
        # 12 meses só fecham se o acumulado do ITR começar no dia seguinte ao fim do último balanço anual
        # (vale para ano fiscal de janeiro a dezembro e para quem fecha o ano em outro mês)
        ttm_ok = False
        if d_itr and d_dfp:
            ini = _ytd(_rows(S(itr, "DRE_" + ti, cnpj), cnpj, d_itr)).get("DT_INI_EXERC")
            ini = ini.dropna().min() if ini is not None and len(ini) else None
            prox = (pd.Timestamp(d_dfp) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            ttm_ok = ini == prox
        # --- fluxos 12m
        fa = _flows(_rows(S(dfp, "DRE_" + td, cnpj), cnpj, d_dfp), _rows(S(dfp, "DVA_" + td, cnpj), cnpj, d_dfp)) if d_dfp else {}
        if d_itr:
            fb = _flows(_ytd(_rows(S(itr, "DRE_" + ti, cnpj), cnpj, d_itr)), _ytd(_rows(S(itr, "DVA_" + ti, cnpj), cnpj, d_itr)))
            fc = _flows(_ytd(_rows(S(itr, "DRE_" + ti, cnpj), cnpj, d_itr, "PENÚLTIMO")), _ytd(_rows(S(itr, "DVA_" + ti, cnpj), cnpj, d_itr, "PENÚLTIMO")))
        else:
            fb, fc = {}, {}
        for k in ["lucro", "receita", "bruto", "ebit", "da", "proventos"]:
            r[k + "_12m"] = _ttm(fa.get(k), fb.get(k), fc.get(k)) if ttm_ok else fa.get(k)
        # --- estoque (balanço mais recente)
        src, dt, tp = (itr, d_itr, ti) if d_itr else (dfp, d_dfp, td)
        r["data_balanco"] = dt
        if not dt:
            continue
        bpa = _rows(S(src, "BPA_" + tp, cnpj), cnpj, dt)
        bpp = _rows(S(src, "BPP_" + tp, cnpj), cnpj, dt)
        pl_tot = _first(bpp, PL, prefix="2.", depth=1)
        nc = _first(bpp, NAO_CONTROL, prefix="2.", depth=2)
        r["patrimonio"] = (pl_tot - (nc or 0)) if pl_tot is not None else None
        r["ativo"] = _code(bpa, "1")
        r["banco"] = bool(_rows(S(src, "DRE_" + tp, cnpj), cnpj, dt)["DS_CONTA"].str.contains("Intermedia", na=False).any())
        if not r["banco"]:
            r["ativo_circ"] = _code(bpa, "1.01")
            r["passivo_circ"] = _code(bpp, "2.01")
            r["caixa"] = (_code(bpa, "1.01.01") or 0) + (_code(bpa, "1.01.02") or 0)
            r["divida_bruta"] = _sum(bpp[bpp["CD_CONTA"].str.match(r"^2\.0[12]\.\d\d$")], EMPREST)
        # --- ações
        cc = S(src, "composicao_capital", cnpj)
        c = cc[(cc["CNPJ_CIA"] == cnpj)].sort_values("DT_REFER")
        if len(c):
            c = c.iloc[-1]
            n = lambda k: float(c.get(k) or 0)
            r["acoes_on"] = n("QT_ACAO_ORDIN_CAP_INTEGR") - n("QT_ACAO_ORDIN_TESOURO")
            r["acoes_pn"] = n("QT_ACAO_PREF_CAP_INTEGR") - n("QT_ACAO_PREF_TESOURO")
            r["acoes"] = r["acoes_on"] + r["acoes_pn"]
        rows.append(r)
    f = pd.DataFrame(rows)
    return tickers.merge(f, on="cnpj", how="inner")


# composição das units mais negociadas (ações por unit). As outras são deduzidas pelo preço.
UNIT_K = {"TAEE11": 3, "KLBN11": 5, "SANB11": 2, "ENGI11": 5, "ALUP11": 3, "SAPR11": 5, "BPAC11": 3,
          "CPLE11": 5, "IGTI11": 3, "BRBI11": 3, "RNEW11": 3}


def indicadores(base: pd.DataFrame, precos: dict[str, float]) -> pd.DataFrame:
    """Junta preço por ticker e calcula os indicadores de mercado."""
    b = base.copy()
    b["preco"] = pd.to_numeric(b["ticker"].map(precos), errors="coerce").astype(float)
    # algumas empresas informam a quantidade de ações em milhares (ex.: Itaú).
    # Se o VPA ficar mais de 100x acima do preço, a quantidade está em milhares.
    vpa0 = b["patrimonio"] / b["acoes"]
    pmed = b.groupby("cnpj")["preco"].transform("max")
    mil = (pmed.notna() & (vpa0 / pmed > 100)) | (pmed.isna() & (vpa0 > 2000))
    for c in ["acoes", "acoes_on", "acoes_pn"]:
        b.loc[mil, c] = b.loc[mil, c] * 1000
    # valor de mercado: cada classe ao seu preço; se faltar o preço de uma classe, usa o do próprio ticker
    def vm(grp):
        p_on = grp.loc[grp["ticker"].str.endswith("3"), "preco"].dropna()
        p_pn = grp.loc[grp["ticker"].str.match(r".*[456]$"), "preco"].dropna()
        p_any = grp.loc[~grp["ticker"].str.endswith("11"), "preco"].dropna()   # units valem várias ações: não servem de preço de uma ação
        if p_any.empty:   # só a unit tem preço (ex.: KLBN11): divide pelo número de ações do pacote
            u = grp[grp["ticker"].isin(UNIT_K.keys())].dropna(subset=["preco"])
            p_any = (u["preco"] / u["ticker"].map(UNIT_K)) if len(u) else grp["preco"].dropna()
        if p_any.empty:
            return None
        on = grp["acoes_on"].iloc[0] or 0
        pn = grp["acoes_pn"].iloc[0] or 0
        pon = p_on.iloc[0] if len(p_on) else p_any.iloc[0]
        ppn = p_pn.iloc[0] if len(p_pn) else p_any.iloc[0]
        return on * pon + pn * ppn
    vmap = {c: vm(g) for c, g in b.groupby("cnpj")}
    b["valor_mercado"] = pd.to_numeric(b["cnpj"].map(vmap), errors="coerce").astype(float)
    s = lambda a, c: a / c if (a is not None and c not in (None, 0)) else None
    # units (ex.: TAEE11 = 1 ON + 2 PN, KLBN11 = 1 ON + 4 PN): o preço é de um pacote de ações.
    # Descobre quantas ações há no pacote comparando o preço da unit com o preço médio por ação da empresa,
    # e calcula LPA e VPA por unit; sem isso o P/L e o P/VP da unit saem multiplicados (TAEE11 aparecia com P/L 28 em vez de 9).
    pm = b["valor_mercado"] / b["acoes"]
    k = b["preco"] / pm
    kr = k.round()
    unit = b["ticker"].str.endswith("11") & kr.ge(2) & ((k / kr - 1).abs() < 0.12)
    b["acoes_por_ticker"] = 1.0
    b.loc[unit, "acoes_por_ticker"] = kr[unit]
    conhecida = b["ticker"].isin(UNIT_K.keys())
    b.loc[conhecida, "acoes_por_ticker"] = b.loc[conhecida, "ticker"].map(UNIT_K)
    b["lpa"] = b["lucro_12m"] / b["acoes"] * b["acoes_por_ticker"]
    b["vpa"] = b["patrimonio"] / b["acoes"] * b["acoes_por_ticker"]
    b["pl"] = b["preco"] / b["lpa"]
    b["pvp"] = b["preco"] / b["vpa"]
    b["roe"] = b["lucro_12m"] / b["patrimonio"]
    b["dy_cvm"] = b["proventos_12m"] / b["valor_mercado"]
    nb = ~b["banco"].fillna(False)
    b.loc[nb, "m_bruta"] = b["bruto_12m"] / b["receita_12m"]
    b.loc[nb, "m_ebit"] = b["ebit_12m"] / b["receita_12m"]
    b.loc[nb, "m_liq"] = b["lucro_12m"] / b["receita_12m"]
    b.loc[nb, "ebitda_12m"] = b["ebit_12m"] + b["da_12m"].fillna(0)
    b.loc[nb, "div_liq"] = b["divida_bruta"].fillna(0) - b["caixa"].fillna(0)
    b.loc[nb, "div_liq_ebitda"] = b["div_liq"] / b["ebitda_12m"]
    b.loc[nb, "div_liq_pl"] = b["div_liq"] / b["patrimonio"]
    b.loc[nb, "liq_corrente"] = b["ativo_circ"] / b["passivo_circ"]
    b.loc[nb, "ev_ebitda"] = (b["valor_mercado"] + b["div_liq"]) / b["ebitda_12m"]
    b.loc[nb, "ev_ebit"] = (b["valor_mercado"] + b["div_liq"]) / b["ebit_12m"]
    b["psr"] = b["valor_mercado"] / b["receita_12m"]
    return b
