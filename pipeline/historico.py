"""
Histórico anual dos balanços (DFP da CVM, desde 2010) e o ranking por período.

anual.json   um registro por empresa e ano: receita, EBIT, lucro, patrimônio, proventos, ações
ranking.json para cada ação e cada janela (5, 10, 15, 20 anos): retorno, dividendos,
             margens, ROE, crescimento e consistência

O histórico fica salvo no repositório. Cada rodada só baixa os anos que ainda faltam
e refaz os dois mais recentes (as empresas às vezes reapresentam o balanço).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

import fundamentos as F

PRIMEIRO_ANO = 2010
JANELAS = [5, 10, 15, 20]
VERSAO = 2  # aumente quando mudar o cálculo dos anos, para refazer o histórico inteiro
CAMPOS_ANO = ["receita", "ebit", "lucro", "patrimonio", "proventos", "acoes_on", "acoes_pn", "banco"]


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def ano_dfp(zip_path: Path, tickers: pd.DataFrame) -> dict:
    """Resultado de um ano (DFP) por CNPJ."""
    dfp = F.load_doc(zip_path)
    vazio = {k: v.iloc[0:0] for k, v in dfp.items()}
    base = F.compute(dfp, vazio, tickers)
    out = {}
    for r in base.drop_duplicates("cnpj").to_dict("records"):
        out[r["cnpj"]] = {
            "receita": _num(r.get("receita_12m")), "ebit": _num(r.get("ebit_12m")),
            "lucro": _num(r.get("lucro_12m")), "patrimonio": _num(r.get("patrimonio")),
            "proventos": _num(r.get("proventos_12m")), "acoes_on": _num(r.get("acoes_on")),
            "acoes_pn": _num(r.get("acoes_pn")), "banco": bool(r.get("banco")),
            "data": r.get("data_balanco"),
        }
    return out


def atualizar_anual(arquivo: Path, ultimo_ano: int, baixar_dfp, tickers: pd.DataFrame, log, apagar_zip: bool = True) -> dict:
    """Completa o anual.json. baixar_dfp(ano) devolve o caminho do zip ou None."""
    try:
        hist = json.loads(arquivo.read_text())
    except Exception:  # noqa: BLE001
        hist = {"anos": [], "dados": {}}
    if hist.get("versao") != VERSAO:  # mudou a forma de calcular: refaz todos os anos
        hist = {"anos": [], "dados": {}}
    feitos = set(hist.get("anos", []))
    for ano in range(PRIMEIRO_ANO, ultimo_ano + 1):
        if ano in feitos and ano < ultimo_ano - 1:
            continue
        z = baixar_dfp(ano)
        if not z:
            log(f"histórico: DFP {ano} não encontrado")
            continue
        try:
            res = ano_dfp(z, tickers)
        except Exception as e:  # noqa: BLE001
            log(f"histórico: falha ao ler DFP {ano}:", e)
            continue
        if len(res) < 50 and ano < ultimo_ano:
            log(f"histórico: DFP {ano} veio com só {len(res)} empresas; ignorado")
            continue
        for cnpj, v in res.items():
            hist["dados"].setdefault(cnpj, {})[str(ano)] = v
        feitos.add(ano)
        log(f"histórico: DFP {ano} ok ({len(res)} empresas)")
        try:
            if apagar_zip:
                Path(z).unlink()  # libera espaço; os anos antigos não são baixados de novo
        except Exception:  # noqa: BLE001
            pass
    hist["anos"] = sorted(feitos)
    hist["versao"] = VERSAO
    arquivo.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")))
    return hist


# ------------------------------------------------------------------ ranking
def _media(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _cagr(a, b, anos):
    if a is None or b is None or a <= 0 or b <= 0 or anos <= 0:
        return None
    return (b / a) ** (1 / anos) - 1


def _serie_precos(h):
    """{ 'AAAA-MM': (fechamento, ajustado, dividendo) }"""
    return {d: (c, a, v) for d, c, a, v in zip(h["d"], h["c"], h["a"], h["v"])}


def _preco_fim_ano(h):
    """Último fechamento de cada ano."""
    out = {}
    for d, c in zip(h["d"], h["c"]):
        if c:
            out[d[:4]] = c
    return out


def _dy_yahoo_ano(h):
    """Dividendos pagos no ano ÷ preço médio do ano (dados do Yahoo)."""
    soma, precos = {}, {}
    for d, c, v in zip(h["d"], h["c"], h["v"]):
        y = d[:4]
        soma[y] = soma.get(y, 0) + (v or 0)
        if c:
            precos.setdefault(y, []).append(c)
    return {y: soma[y] / (sum(p) / len(p)) for y, p in precos.items() if p}


def ultimo_ano_completo(anual: dict, minimo: int = 150) -> int | None:
    """Ano mais recente em que a maioria das empresas já entregou o balanço anual."""
    cont = {}
    for regs in anual.get("dados", {}).values():
        for y in regs:
            cont[int(y)] = cont.get(int(y), 0) + 1
    ok = [y for y, c in cont.items() if c >= minimo]
    return max(ok) if ok else (max(cont) if cont else None)


def ranking(linhas: list[dict], hist: dict, anual: dict, ultimo_balanco: int, mes_atual: str, cnpj_de: dict) -> dict:
    por_cnpj = anual.get("dados", {})
    # preço de fim de ano de cada classe, para o valor de mercado histórico
    precos_ano = {t: _preco_fim_ano(h) for t, h in hist.items()}
    tickers_cnpj = {}
    for t, c in cnpj_de.items():
        tickers_cnpj.setdefault(c, []).append(t)

    def valor_mercado(cnpj, ano):
        reg = por_cnpj.get(cnpj, {}).get(str(ano))
        if not reg:
            return None
        tks = tickers_cnpj.get(cnpj, [])
        p_on = next((precos_ano.get(t, {}).get(str(ano)) for t in tks if t.endswith("3") and precos_ano.get(t, {}).get(str(ano))), None)
        p_pn = next((precos_ano.get(t, {}).get(str(ano)) for t in tks if t[-1] in "456" and precos_ano.get(t, {}).get(str(ano))), None)
        p_any = p_on or p_pn
        if not p_any:
            return None
        on, pn = reg.get("acoes_on") or 0, reg.get("acoes_pn") or 0
        if on + pn <= 0:
            return None
        # mesma correção do painel: algumas empresas informam a quantidade de ações em milhares
        pl = reg.get("patrimonio")
        if pl and pl / (on + pn) / p_any > 100:
            on, pn = on * 1000, pn * 1000
        return on * (p_on or p_any) + pn * (p_pn or p_any)

    mes_fim = mes_atual
    out = {}
    for r in linhas:
        t = r["ticker"]
        h = hist.get(t)
        cnpj = cnpj_de.get(t)
        anos_reg = por_cnpj.get(cnpj, {}) if cnpj else {}
        res = {}
        dy_y = _dy_yahoo_ano(h) if h else {}
        for n in JANELAS:
            m = {}
            # ---- preço (janela termina no último mês disponível)
            if h and h.get("d"):
                serie = _serie_precos(h)
                fim = h["d"][-1]
                ini_alvo = f"{int(fim[:4]) - n}-{fim[5:7]}"
                ini = next((d for d in h["d"] if d >= ini_alvo), None)
                # dado com salto absurdo de um mês para o outro (desdobramento não ajustado,
                # erro de cotação em ação sem liquidez): fica fora do ranking de retorno
                i0 = h["d"].index(ini) if ini else None
                seg = [x for x in h["a"][i0:] if x] if i0 is not None else []
                suspeito = any(b / a > 5 or b / a < 0.2 for a, b in zip(seg, seg[1:]))
                if suspeito:
                    m["preco_suspeito"] = True
                if ini and not suspeito and serie[ini][0] and serie[fim][0]:
                    c0, a0, _ = serie[ini]
                    c1, a1, _ = serie[fim]
                    m["ret_preco"] = c1 / c0 - 1
                    m["ret_total"] = (a1 / a0 - 1) if a0 and a1 else None
                    anos_ef = (int(fim[:4]) * 12 + int(fim[5:7]) - int(ini[:4]) * 12 - int(ini[5:7])) / 12
                    m["cagr_total"] = _cagr(a0, a1, anos_ef) if a0 and a1 else None
                    m["preco_completo"] = ini == ini_alvo or h["d"][0] <= ini_alvo
            # ---- anos civis completos da janela, para dividendos e balanços
            anos = list(range(ultimo_balanco - n + 1, ultimo_balanco + 1))
            # dividend yield por ano: proventos declarados à CVM ÷ valor de mercado no fim do ano;
            # sem balanço (EUA, ETFs) usa os dividendos pagos que vêm do Yahoo
            dys, pagou = [], 0
            for y in anos:
                dy = None
                reg = anos_reg.get(str(y))
                if reg and reg.get("proventos") is not None:
                    vm = valor_mercado(cnpj, y)
                    if vm:
                        dy = reg["proventos"] / vm
                if dy is not None and dy > 0.6:  # acima de 60% ao ano quase sempre é erro de dado
                    dy = None
                if dy is None and str(y) in dy_y and dy_y[str(y)] <= 0.6:
                    dy = dy_y[str(y)]
                if dy is not None:
                    dys.append(dy)
                    if dy > 0.0005:
                        pagou += 1
            m["dy_medio"] = _media(dys) if dys else None
            m["anos_div"] = pagou if dys else None
            m["anos_div_base"] = len(dys)
            # ---- balanços (só B3)
            regs = [anos_reg.get(str(y)) for y in anos]
            regs_ok = [x for x in regs if x]
            if regs_ok:
                banco = any(x.get("banco") for x in regs_ok)
                me, ml, roe = [], [], []
                lucro_pos = 0
                for x in regs_ok:
                    rec, eb, ll, pl = x.get("receita"), x.get("ebit"), x.get("lucro"), x.get("patrimonio")
                    if not banco and rec and rec > 0:
                        # margem acima de 100% é holding ou empresa de participações:
                        # a receita não representa o negócio, então o ano fica de fora
                        if eb is not None and -1 <= eb / rec <= 1:
                            me.append(eb / rec)
                        if ll is not None and -1 <= ll / rec <= 1:
                            ml.append(ll / rec)
                    if ll is not None and pl and pl > 0:
                        roe.append(ll / pl)
                    if ll is not None and ll > 0:
                        lucro_pos += 1
                m["m_ebit_med"] = _media(me)
                m["m_liq_med"] = _media(ml)
                m["roe_med"] = _media(roe)
                m["anos_lucro"] = lucro_pos
                m["anos_balanco"] = len(regs_ok)
                prim = next((x for x in regs if x), None)
                ult = next((x for x in reversed(regs) if x), None)
                i0 = regs.index(prim)
                i1 = len(regs) - 1 - regs[::-1].index(ult)
                span = i1 - i0
                m["cagr_receita"] = None if banco else _cagr(prim.get("receita"), ult.get("receita"), span)
                m["cagr_lucro"] = _cagr(prim.get("lucro"), ult.get("lucro"), span)
            res[str(n)] = {k: (round(v, 5) if isinstance(v, float) else v) for k, v in m.items() if v is not None}
        out[t] = res
    return {"janelas": JANELAS, "fim_preco": mes_fim, "fim_balanco": ultimo_balanco,
            "primeiro_balanco": min(anual.get("anos") or [PRIMEIRO_ANO]), "dados": out}
