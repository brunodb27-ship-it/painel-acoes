"""
Atualização diária do Painel de Ações.

Roda sozinho no GitHub (ver .github/workflows/atualizar.yml) e grava em docs/data/:
  indicadores.json   uma linha por ação, com todos os indicadores
  hist/<TICKER>.json histórico mensal (fechamento, fechamento ajustado, dividendos)
  macro.json         CDI, IPCA e Ibovespa mensais (para o simulador comparar)
  meta.json          datas da última atualização e das fontes

Fontes:
  CVM dados abertos (balanços)  - dados.cvm.gov.br
  Yahoo Finance via yfinance    - cotações, histórico e proventos
  Banco Central (SGS)           - CDI e IPCA

Para testar localmente com zips já baixados:
  python pipeline/atualizar.py --local pasta_com_zips --sem-precos
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent))
import fundamentos as F  # noqa: E402
import historico as H  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
SAIDA = RAIZ / "docs" / "data"
CACHE = RAIZ / ".cache"
HOJE = dt.date.today()


def log(*a):
    print(f"[{dt.datetime.now():%H:%M:%S}]", *a, flush=True)


# ------------------------------------------------------------------ downloads
def baixar(url: str, destino: Path, tentativas: int = 3) -> Path | None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    for i in range(tentativas):
        try:
            r = requests.get(url, timeout=180, headers={"User-Agent": "painel-acoes/1.0"})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            destino.write_bytes(r.content)
            return destino
        except Exception as e:  # noqa: BLE001
            log("falha ao baixar", url, e)
            time.sleep(5 * (i + 1))
    return None


def zip_cvm(doc: str, ano: int, local: Path | None) -> Path | None:
    nome = f"{doc.lower()}_cia_aberta_{ano}.zip"
    if local:
        achados = list(local.glob(f"*{nome}"))
        return achados[0] if achados else None
    return baixar(f"{F.CVM}/{doc}/DADOS/{nome}", CACHE / nome)


def primeiro_disponivel(doc: str, anos: list[int], local: Path | None) -> tuple[Path, int]:
    for a in anos:
        p = zip_cvm(doc, a, local)
        if p:
            log(f"{doc} {a}: ok")
            return p, a
    raise SystemExit(f"Nenhum arquivo {doc} encontrado para {anos}")


def todos_disponiveis(doc: str, anos: list[int], local: Path | None) -> tuple[list[Path], int]:
    """Baixa todos os anos pedidos que existirem. Devolve os arquivos e o ano mais recente."""
    achados = []
    for a in anos:
        p = zip_cvm(doc, a, local)
        if p:
            log(f"{doc} {a}: ok")
            achados.append((a, p))
        else:
            log(f"{doc} {a}: não encontrado")
    if not achados:
        raise SystemExit(f"Nenhum arquivo {doc} encontrado para {anos}")
    return [p for _, p in achados], max(a for a, _ in achados)


# ------------------------------------------------------------------ cotações (Yahoo)
def cotacoes(tickers: list[str], sufixo: str = ".SA") -> tuple[dict, dict]:
    """Retorna (resumo por ticker, histórico mensal por ticker). B3 usa o sufixo .SA no Yahoo."""
    import yfinance as yf

    resumo, hist = {}, {}
    syms = [t + sufixo for t in tickers]
    for i in range(0, len(syms), 60):
        lote = syms[i : i + 60]
        log(f"cotações {i + 1}-{i + len(lote)} de {len(syms)}")
        try:
            m = yf.download(lote, period="max", interval="1mo", actions=True, auto_adjust=False,
                            group_by="ticker", progress=False, threads=True)
            d = yf.download(lote, period="7d", interval="1d", auto_adjust=False,
                            group_by="ticker", progress=False, threads=True)
        except Exception as e:  # noqa: BLE001
            log("erro no lote", e)
            continue
        for s in lote:
            t = s[: -len(sufixo)] if sufixo else s
            try:
                mm = (m[s] if isinstance(m.columns, pd.MultiIndex) else m).dropna(subset=["Close"])
                dd = (d[s] if isinstance(d.columns, pd.MultiIndex) else d).dropna(subset=["Close"])
            except KeyError:
                continue
            if mm.empty or dd.empty:
                continue
            preco = float(dd["Close"].iloc[-1])
            ant = float(dd["Close"].iloc[-2]) if len(dd) > 1 else None
            divs = mm["Dividends"] if "Dividends" in mm else pd.Series(0, index=mm.index)
            ult12 = divs[divs.index >= (pd.Timestamp(HOJE) - pd.DateOffset(months=12))].sum()
            vol = (mm["Volume"] * mm["Close"]).tail(4).iloc[:-1].mean() / 21 if "Volume" in mm else None
            c12 = mm["Close"]
            ref12 = c12[c12.index <= (pd.Timestamp(HOJE) - pd.DateOffset(months=12))]
            resumo[t] = {
                "preco": preco,
                "var_dia": (preco / ant - 1) if ant else None,
                "var_12m": (preco / float(ref12.iloc[-1]) - 1) if len(ref12) else None,
                "dpa_12m": float(ult12),
                "dy": float(ult12) / preco if preco else None,
                "liquidez": float(vol) if vol is not None and not math.isnan(vol) else None,
                "data_preco": str(dd.index[-1].date()),
            }
            adj = mm["Adj Close"] if "Adj Close" in mm else mm["Close"]
            hist[t] = {
                "d": [x.strftime("%Y-%m") for x in mm.index],
                "c": [round(float(x), 4) for x in mm["Close"]],
                "a": [round(float(x), 4) for x in adj],
                "v": [round(float(x), 6) for x in divs.reindex(mm.index).fillna(0)],
            }
            if "Stock Splits" in mm:
                sp = mm["Stock Splits"].fillna(0)
                hist[t]["sp"] = [[k.strftime("%Y-%m"), round(float(x), 4)] for k, x in sp.items() if x and float(x) != 1.0]
        time.sleep(2)
    return resumo, hist


def serie_bcb(codigo: int, desde: int = 2000) -> dict:
    out = {}
    for ini in range(desde, HOJE.year + 1, 10):
        fim = min(ini + 9, HOJE.year)
        url = (f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados?formato=json"
               f"&dataInicial=01/01/{ini}&dataFinal=31/12/{fim}")
        try:
            for x in requests.get(url, timeout=60).json():
                dd, mm, aa = x["data"].split("/")
                out[f"{aa}-{mm}"] = float(x["valor"])
        except Exception as e:  # noqa: BLE001
            log("BCB", codigo, e)
    return out


def serie_yahoo(sym: str) -> dict:
    """Fechamento mensal de um índice ou moeda no Yahoo (ex.: ^BVSP, BRL=X, ^GSPC)."""
    try:
        import yfinance as yf
        m = yf.download(sym, period="max", interval="1mo", auto_adjust=False, progress=False)
        c = m["Close"].dropna()
        if isinstance(c, pd.DataFrame):
            c = c.iloc[:, 0]
        return {k.strftime("%Y-%m"): round(float(v), 2) for k, v in c.items()}
    except Exception as e:  # noqa: BLE001
        log("yahoo", sym, e)
        return {}


# ------------------------------------------------------------------ EUA
def eua() -> tuple[list[dict], dict]:
    """Ações e ETFs dos EUA listados em pipeline/eua.txt (um código por linha; # comenta)."""
    import yfinance as yf

    lista = [l.split("#")[0].strip().upper() for l in (Path(__file__).parent / "eua.txt").read_text().splitlines()]
    lista = [t for t in lista if t]
    resumo, hist = cotacoes(lista, sufixo="")
    linhas = []
    for t in lista:
        if t not in resumo:
            continue
        try:
            info = yf.Ticker(t).info or {}
        except Exception as e:  # noqa: BLE001
            log("info", t, e); info = {}
        g = lambda k: info.get(k)
        debt, cash, ebitda = g("totalDebt"), g("totalCash"), g("ebitda")
        dl = (debt - cash) if debt is not None and cash is not None else None
        banco = (g("sector") == "Financial Services") and ("Bank" in (g("industry") or ""))
        r = {**resumo[t], "ticker": t, "empresa": g("longName") or g("shortName") or t,
             "setor": g("sector") or ("ETF" if g("quoteType") == "ETF" else ""), "segmento": g("industry") or "",
             "tipo": g("quoteType") or "", "banco": banco, "mercado": "EUA", "moeda": "USD",
             "valor_mercado": g("marketCap"), "lpa": g("trailingEps"), "vpa": g("bookValue"),
             "pl": g("trailingPE"), "pvp": g("priceToBook"), "psr": g("priceToSalesTrailing12Months"),
             "roe": g("returnOnEquity"), "m_bruta": g("grossMargins"), "m_ebit": g("operatingMargins"),
             "m_liq": g("profitMargins"), "ev_ebitda": g("enterpriseToEbitda"), "div_liq": dl,
             "div_liq_ebitda": (dl / ebitda) if dl is not None and ebitda else None,
             "liq_corrente": g("currentRatio"), "lucro_12m": g("netIncomeToCommon"),
             "receita_12m": g("totalRevenue"), "ebitda_12m": ebitda, "divida_bruta": debt, "caixa": cash,
             "acoes": g("sharesOutstanding")}
        linhas.append(r)
        time.sleep(0.4)
    return linhas, hist


# ------------------------------------------------------------------ ETFs da B3 e índices
INDICES = {"^BVSP": ("IBOV", "Índice Ibovespa", "Brasil", "BRL"),
           "^GSPC": ("SPX", "Índice S&P 500", "EUA", "USD"),
           "^NDX": ("NDX", "Índice Nasdaq 100", "EUA", "USD")}


def etfs_e_indices() -> tuple[list[dict], dict]:
    """ETFs da B3 (lista em pipeline/etfs_br.txt) e os índices, só com cotação e histórico."""
    linhas, hist = [], {}
    arq = Path(__file__).parent / "etfs_br.txt"
    nomes = {}
    for ln in arq.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            t, _, n = ln.partition(";")
            nomes[t.strip().upper()] = n.strip() or t.strip().upper()
    res, h = cotacoes(sorted(nomes), sufixo=".SA")
    for t, r in res.items():
        linhas.append({**r, "ticker": t, "empresa": nomes[t], "setor": "ETF", "segmento": "ETF B3",
                       "tipo": "ETF", "banco": False, "mercado": "Brasil", "moeda": "BRL"})
    hist.update(h)
    res, h = cotacoes(list(INDICES), sufixo="")
    for sym, r in res.items():
        t, nome, merc, moeda = INDICES[sym]
        r = {**r, "dpa_12m": None, "dy": None}
        linhas.append({**r, "ticker": t, "empresa": nome, "setor": "Índice", "segmento": "Índice",
                       "tipo": "Índice (não se compra direto; use um ETF)", "banco": False,
                       "mercado": merc, "moeda": moeda})
        if sym in h:
            hist[t] = h[sym]
    return linhas, hist


# ------------------------------------------------------------------ saída
def limpo(v):
    if v is None:
        return None
    if isinstance(v, (bool,)):
        return v
    try:
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
    except Exception:  # noqa: BLE001
        pass
    if isinstance(v, float):
        return round(v, 6) if abs(v) < 1e6 else round(v)
    return v


CAMPOS = ["ticker", "empresa", "mercado", "moeda", "setor", "segmento", "tipo", "banco", "data_balanco", "preco", "var_dia", "var_12m",
          "data_preco", "liquidez", "valor_mercado", "lpa", "vpa", "pl", "pvp", "psr", "dy", "dpa_12m", "dy_cvm",
          "roe", "m_bruta", "m_ebit", "m_liq", "ev_ebitda", "ev_ebit", "div_liq", "div_liq_ebitda", "div_liq_pl",
          "liq_corrente", "lucro_12m", "receita_12m", "ebitda_12m", "patrimonio", "divida_bruta", "caixa", "acoes"]


def _ler_hist() -> dict:
    """Usa os históricos já salvos (modo de teste sem cotações)."""
    out = {}
    for p in (SAIDA / "hist").glob("*.json"):
        try:
            out[p.stem] = json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", type=Path, help="pasta com os zips da CVM (teste)")
    ap.add_argument("--sem-precos", action="store_true", help="não busca cotações (teste)")
    ap.add_argument("--precos-json", type=Path, help="usa preços de um JSON {ticker: preco} (teste)")
    a = ap.parse_args()

    SAIDA.mkdir(parents=True, exist_ok=True)
    (SAIDA / "hist").mkdir(exist_ok=True)

    # --- CVM
    y = HOJE.year
    # O DFP do ano corrente quase não tem empresas (só as de exercício fora do calendário),
    # por isso o anual começa no ano anterior. Juntamos dois anos de cada documento:
    # cada empresa usa o seu balanço mais recente e ninguém some por atraso de entrega.
    dfp_zips, dfp_ano = todos_disponiveis("DFP", [y - 1, y - 2], a.local)
    itr_zips, itr_ano = todos_disponiveis("ITR", [y, y - 1], a.local)
    fca_zips, _ = todos_disponiveis("FCA", [y, y - 1], a.local)
    log("lendo DFP"); dfp = F.load_docs(dfp_zips)
    log("lendo ITR"); itr = F.load_docs(itr_zips)
    tk = F.load_tickers(fca_zips)
    log("cadastro:", tk["cnpj"].nunique(), "empresas,", len(tk), "códigos")
    base = F.compute(dfp, itr, tk)
    log("fundamentos calculados para", base["cnpj"].nunique(), "empresas,", len(base), "códigos")
    if len(base) < 100 and not a.local:
        raise SystemExit(f"Só {len(base)} códigos com balanço; algo veio errado da CVM. Mantendo os dados anteriores.")

    # --- preços
    resumo, hist = {}, {}
    if a.precos_json:
        resumo = {k: {"preco": v} for k, v in json.loads(a.precos_json.read_text()).items()}
    elif not a.sem_precos:
        resumo, hist = cotacoes(sorted(base["ticker"].unique()))
        if len(resumo) < 30:
            # Yahoo fora do ar ou bloqueado: não sobrescreve os dados bons da véspera
            raise SystemExit(f"Só {len(resumo)} cotações recebidas; mantendo os dados anteriores.")
    precos = {k: v["preco"] for k, v in resumo.items() if v.get("preco")}
    b = F.indicadores(base, precos)
    for campo in ["var_dia", "var_12m", "dpa_12m", "dy", "liquidez", "data_preco"]:
        b[campo] = b["ticker"].map(lambda t: resumo.get(t, {}).get(campo))

    b["mercado"] = "Brasil"; b["moeda"] = "BRL"
    linhas = [{k: limpo(r.get(k)) for k in CAMPOS} for r in b.to_dict("records")]
    if not a.sem_precos and not a.precos_json:
        try:
            us, hist_us = eua()
            linhas += [{k: limpo(r.get(k)) for k in CAMPOS} for r in us]
            hist.update(hist_us)
            log("EUA:", len(us), "ativos")
        except Exception as e:  # noqa: BLE001
            log("EUA falhou, seguindo só com a B3:", e)
        try:
            ex, hist_ex = etfs_e_indices()
            linhas += [{k: limpo(r.get(k)) for k in CAMPOS} for r in ex]
            hist.update(hist_ex)
            log("ETFs B3 e índices:", len(ex))
        except Exception as e:  # noqa: BLE001
            log("ETFs/índices falharam, seguindo sem eles:", e)
    (SAIDA / "indicadores.json").write_text(json.dumps(linhas, ensure_ascii=False, separators=(",", ":")))
    for t, h in hist.items():
        (SAIDA / "hist" / f"{t}.json").write_text(json.dumps(h, separators=(",", ":")))

    if not a.sem_precos and not a.precos_json:
        macro = {"cdi": serie_bcb(4391), "ipca": serie_bcb(433), "ibov": serie_yahoo("^BVSP"),
                 "usdbrl": serie_yahoo("BRL=X"), "sp500": serie_yahoo("^GSPC")}
        (SAIDA / "macro.json").write_text(json.dumps(macro, separators=(",", ":")))

    # --- histórico anual e ranking por período (não derruba o resto se falhar)
    try:
        anual = H.atualizar_anual(SAIDA / "anual.json", dfp_ano, lambda ano: zip_cvm("DFP", ano, a.local),
                                  tk, log, apagar_zip=not a.local)
        ult = H.ultimo_ano_completo(anual) or dfp_ano
        cnpj_de = dict(zip(b["ticker"], b["cnpj"]))
        rk = H.ranking(linhas, hist if hist else _ler_hist(), anual, ult, HOJE.strftime("%Y-%m"), cnpj_de)
        (SAIDA / "ranking.json").write_text(json.dumps(rk, ensure_ascii=False, separators=(",", ":")))
        log("ranking:", len(rk["dados"]), "ativos; balanços", rk["primeiro_balanco"], "a", ult)
    except Exception as e:  # noqa: BLE001
        log("ranking falhou, seguindo sem ele:", e)

    meta = {
        "atualizado_em": dt.datetime.now(dt.timezone(dt.timedelta(hours=-3))).strftime("%Y-%m-%d %H:%M"),
        "dfp": dfp_ano, "itr": itr_ano,
        "empresas": int(b["cnpj"].nunique()), "acoes": len(linhas),
        "com_preco": sum(1 for r in linhas if r["preco"]),
    }
    (SAIDA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    log("pronto:", meta)


if __name__ == "__main__":
    main()
