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


def load_doc(zip_path: Path) -> dict[str, pd.DataFrame]:
    """Lê do zip (DFP ou ITR) só as demonstrações consolidadas usadas no cálculo."""
    out = {}
    with zipfile.ZipFile(zip_path) as z:
        for key in ["DRE_con", "BPA_con", "BPP_con", "DVA_con", "composicao_capital"]:
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


def load_tickers(fca_zip: Path) -> pd.DataFrame:
    with zipfile.ZipFile(fca_zip) as z:
        vm = _read(z, _member(z, "valor_mobiliario"))
        geral = _read(z, _member(z, "geral"))
    vm = vm[(vm["Mercado"] == "Bolsa") & vm["Codigo_Negociacao"].notna() & vm["Data_Fim_Negociacao"].isna()]
    vm = vm[vm["Codigo_Negociacao"].str.match(r"^[A-Z]{4}\d{1,2}$")]
    vm = vm.sort_values("Versao").drop_duplicates(["CNPJ_Companhia", "Codigo_Negociacao"], keep="last")
    cols = {c: c for c in geral.columns}
    setor_col = next((c for c in geral.columns if "Setor" in c), None)
    g = geral.sort_values("Versao").drop_duplicates("CNPJ_Companhia", keep="last")
    g = g[["CNPJ_Companhia"] + ([setor_col] if setor_col else [])].rename(columns={setor_col: "setor"} if setor_col else {})
    t = vm[["CNPJ_Companhia", "Nome_Empresarial", "Codigo_Negociacao", "Valor_Mobiliario", "Segmento"]].merge(g, on="CNPJ_Companhia", how="left")
    t.columns = ["cnpj", "empresa", "ticker", "tipo", "segmento"] + (["setor"] if setor_col else [])
    return t


# ---------------------------------------------------------------- helpers de conta
LUCRO_CONTROLADORA = re.compile(r"Atribu[íi]do a S[óo]cios da Empresa Controladora", re.I)
LUCRO_TOTAL = re.compile(r"Lucro/Preju[íi]zo Consolidado do Per[íi]odo|Lucro ou Preju[íi]zo L[íi]quido", re.I)
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
    """Para DRE/DVA de ITR: só as linhas do acumulado do ano (início em 01/01)."""
    if "DT_INI_EXERC" not in x:
        return x
    return x[x["DT_INI_EXERC"].str.endswith("-01-01")]


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
    if lucro is None:
        lucro = _first(dre, LUCRO_TOTAL, prefix="3.", depth=1)
    receita = _code(dre, "3.01")
    bruto = _code(dre, "3.03")
    ebit = _first(dre, EBIT, depth=1)
    da = _first(dva, DA) if dva is not None else None
    jcp = _sum(dva, JCP, prefix="7.08") if dva is not None else None
    div = _sum(dva, DIVID, prefix="7.08") if dva is not None else None
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
    cnpjs = sorted(set(dfp["DRE_con"]["CNPJ_CIA"]) | set(itr["DRE_con"]["CNPJ_CIA"]))
    for cnpj in cnpjs:
        r = {"cnpj": cnpj}
        # --- datas
        d_dfp = S(dfp, "DRE_con", cnpj).loc[S(dfp, "DRE_con", cnpj)["CNPJ_CIA"] == cnpj, "DT_REFER"].max()
        d_itr = S(itr, "DRE_con", cnpj).loc[S(itr, "DRE_con", cnpj)["CNPJ_CIA"] == cnpj, "DT_REFER"].max()
        if not isinstance(d_dfp, str):
            d_dfp = None
        if not isinstance(d_itr, str):
            d_itr = None
        if d_itr and d_dfp and d_itr <= d_dfp:
            d_itr = None
        # --- fluxos 12m
        fa = _flows(_rows(S(dfp, "DRE_con", cnpj), cnpj, d_dfp), _rows(S(dfp, "DVA_con", cnpj), cnpj, d_dfp)) if d_dfp else {}
        if d_itr:
            fb = _flows(_ytd(_rows(S(itr, "DRE_con", cnpj), cnpj, d_itr)), _ytd(_rows(S(itr, "DVA_con", cnpj), cnpj, d_itr)))
            fc = _flows(_ytd(_rows(S(itr, "DRE_con", cnpj), cnpj, d_itr, "PENÚLTIMO")), _ytd(_rows(S(itr, "DVA_con", cnpj), cnpj, d_itr, "PENÚLTIMO")))
        else:
            fb, fc = {}, {}
        for k in ["lucro", "receita", "bruto", "ebit", "da", "proventos"]:
            r[k + "_12m"] = _ttm(fa.get(k), fb.get(k), fc.get(k)) if d_itr else fa.get(k)
        # --- estoque (balanço mais recente)
        src, dt = (itr, d_itr) if d_itr else (dfp, d_dfp)
        r["data_balanco"] = dt
        if not dt:
            continue
        bpa = _rows(S(src, "BPA_con", cnpj), cnpj, dt)
        bpp = _rows(S(src, "BPP_con", cnpj), cnpj, dt)
        pl_tot = _first(bpp, PL, prefix="2.", depth=1)
        nc = _first(bpp, NAO_CONTROL, prefix="2.", depth=2)
        r["patrimonio"] = (pl_tot - (nc or 0)) if pl_tot is not None else None
        r["ativo"] = _code(bpa, "1")
        r["banco"] = bool(_rows(S(src, "DRE_con", cnpj), cnpj, dt)["DS_CONTA"].str.contains("Intermedia", na=False).any())
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


def indicadores(base: pd.DataFrame, precos: dict[str, float]) -> pd.DataFrame:
    """Junta preço por ticker e calcula os indicadores de mercado."""
    b = base.copy()
    b["preco"] = b["ticker"].map(precos)
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
        p_any = grp["preco"].dropna()
        if p_any.empty:
            return None
        on = grp["acoes_on"].iloc[0] or 0
        pn = grp["acoes_pn"].iloc[0] or 0
        pon = p_on.iloc[0] if len(p_on) else p_any.iloc[0]
        ppn = p_pn.iloc[0] if len(p_pn) else p_any.iloc[0]
        return on * pon + pn * ppn
    vmap = b.groupby("cnpj").apply(vm, include_groups=False)
    b["valor_mercado"] = b["cnpj"].map(vmap)
    s = lambda a, c: a / c if (a is not None and c not in (None, 0)) else None
    b["lpa"] = b["lucro_12m"] / b["acoes"]
    b["vpa"] = b["patrimonio"] / b["acoes"]
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
