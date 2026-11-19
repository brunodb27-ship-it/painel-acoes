"""
Balanços anuais das empresas americanas, pela SEC (a "CVM dos EUA"), para o ranking e o score.

Usa a API pública "frames" da SEC: cada chamada traz um número (ex.: lucro líquido de 2019)
de todas as empresas de uma vez. Com ~10 chamadas por ano dá para montar a receita, o lucro
operacional (EBIT), o lucro líquido e o patrimônio de cada empresa desde 2009.

Grava docs/data/anual_eua.json:
  {"anos": [...], "atualizado": "AAAA-MM-DD",
   "ausentes": {"2019": [cik, ...]},        # empresas que a SEC não tem naquele ano (não busca de novo)
   "dados": {"<cik>": {"2019": {"receita":..., "ebit":..., "lucro":..., "patrimonio":..., "fim": "2019-09-28"}}}}

A SEC pede um User-Agent com contato; usamos o endereço "noreply" do GitHub do projeto.
Empresas estrangeiras que reportam em IFRS (ASML, TSMC, Novo Nordisk) não aparecem aqui:
para elas o painel continua só com os números atuais do Yahoo.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import requests

UA = {"User-Agent": "painel-acoes brunodb27-ship-it@users.noreply.github.com",
      "Accept-Encoding": "gzip, deflate"}
PRIMEIRO_ANO = 2009
URL_FRAME = "https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/USD/{periodo}.json"
URL_TICKERS = "https://www.sec.gov/files/company_tickers.json"

# em ordem de preferência: a primeira etiqueta que a empresa usar vale
RECEITA = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
           "RevenuesNetOfInterestExpense"]
EBIT = ["OperatingIncomeLoss"]
LUCRO = ["NetIncomeLoss", "ProfitLoss"]
PATRIMONIO = ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
REFAZER_ULTIMOS = 2   # os 2 anos mais recentes são buscados de novo toda rodada (balanço novo ou refeito)


def _get(url: str, log, tentativas: int = 3):
    for i in range(tentativas):
        try:
            r = requests.get(url, headers=UA, timeout=60)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            time.sleep(0.15)          # a SEC aceita até 10 chamadas por segundo
            return r.json()
        except Exception as e:  # noqa: BLE001
            log("SEC", url.rsplit("/", 2)[-2:], "tentativa", i + 1, e)
            time.sleep(3 * (i + 1))
    raise RuntimeError("SEC fora do ar: " + url)


def ciks(tickers: list[str], log, buscar=None) -> dict:
    """ticker -> CIK (10 dígitos), pela lista oficial da SEC."""
    buscar = buscar or (lambda u: _get(u, log))
    js = buscar(URL_TICKERS) or {}
    por_tk = {str(v["ticker"]).upper().replace(".", "-"): str(v["cik_str"]).zfill(10) for v in js.values()}
    return {t: por_tk[t] for t in tickers if t in por_tk}


def _duracao(buscar, tags: list[str], ano: int) -> dict:
    """{cik: (valor, fim)} do número anual; primeira etiqueta da lista que a empresa usar."""
    out = {}
    for tag in tags:
        js = buscar(URL_FRAME.format(tag=tag, periodo=f"CY{ano}"))
        for x in (js or {}).get("data", []):
            cik = str(x["cik"]).zfill(10)
            if cik not in out and x.get("val") is not None:
                out[cik] = (float(x["val"]), x.get("end"))
    return out


def _instante(buscar, tags: list[str], ano: int) -> dict:
    """{cik: {data: valor}} do patrimônio nas 4 datas de fechamento de trimestre do ano
    (empresas com ano fiscal que não termina em dezembro, como a Apple, fecham em setembro)."""
    out = {}
    for tag in tags:
        for q in (4, 3, 2, 1):
            js = buscar(URL_FRAME.format(tag=tag, periodo=f"CY{ano}Q{q}I"))
            for x in (js or {}).get("data", []):
                cik = str(x["cik"]).zfill(10)
                if x.get("val") is None or not x.get("end"):
                    continue
                d = out.setdefault(cik, {})
                d.setdefault(x["end"], float(x["val"]))
    return out


def _ano(buscar, ano: int, alvo: set[str]) -> tuple[dict, list]:
    rec = _duracao(buscar, RECEITA, ano)
    eb = _duracao(buscar, EBIT, ano)
    ll = _duracao(buscar, LUCRO, ano)
    pl = _instante(buscar, PATRIMONIO, ano)
    dados, ausentes = {}, []
    for cik in alvo:
        if cik not in ll:
            ausentes.append(cik)
            continue
        lucro, fim = ll[cik]
        reg = {"lucro": lucro, "fim": fim}
        if cik in rec:
            reg["receita"] = rec[cik][0]
        if cik in eb:
            reg["ebit"] = eb[cik][0]
        # patrimônio na data de fechamento do ano fiscal (ou a mais próxima, até 45 dias)
        cand = pl.get(cik, {})
        if cand and fim:
            f = dt.date.fromisoformat(fim)
            melhor = min(cand, key=lambda d: abs((dt.date.fromisoformat(d) - f).days))
            if abs((dt.date.fromisoformat(melhor) - f).days) <= 45:
                reg["patrimonio"] = cand[melhor]
        dados[cik] = reg
    return dados, sorted(ausentes)


def atualizar(arquivo: Path, tickers: list[str], ultimo_ano: int, log, buscar=None, max_anos: int = 20) -> tuple[dict, dict]:
    """Atualiza o arquivo e devolve ({cik: {ano: registro}}, {ticker: cik})."""
    buscar = buscar or (lambda u: _get(u, log))
    try:
        base = json.loads(arquivo.read_text())
    except Exception:  # noqa: BLE001
        base = {}
    dados = base.get("dados", {})
    ausentes = base.get("ausentes", {})
    cik_de = ciks(tickers, log, buscar)
    alvo = set(cik_de.values())
    feitos = 0
    for ano in range(ultimo_ano, PRIMEIRO_ANO - 1, -1):
        y = str(ano)
        tem = {c for c, regs in dados.items() if y in regs} | set(ausentes.get(y, []))
        recente = ano > ultimo_ano - REFAZER_ULTIMOS
        if not recente and alvo <= tem:
            continue
        if feitos >= max_anos:
            break
        try:
            novos, aus = _ano(buscar, ano, alvo)
        except Exception as e:  # noqa: BLE001
            log("SEC: ano", ano, "falhou, fica para a próxima rodada:", e)
            continue
        for c in alvo:   # o ano é refeito por inteiro para as empresas da lista
            dados.get(c, {}).pop(y, None)
        for c, reg in novos.items():
            dados.setdefault(c, {})[y] = reg
        ausentes[y] = aus
        feitos += 1
        log(f"SEC {ano}: {len(novos)} empresas com balanço, {len(aus)} sem")
    # guarda só as empresas da lista atual
    dados = {c: regs for c, regs in dados.items() if c in alvo and regs}
    anos = sorted({int(y) for regs in dados.values() for y in regs})
    arquivo.write_text(json.dumps({"anos": anos, "atualizado": dt.date.today().isoformat(),
                                   "ausentes": ausentes, "dados": dados}, separators=(",", ":")))
    return dados, cik_de
