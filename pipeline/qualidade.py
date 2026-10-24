"""
Verificação automática dos dados, a cada rodada do robô.

Duas camadas:
1. Trava: se a rodada veio muito pior que a anterior (muitas empresas sumiram ou ficaram sem lucro),
   o robô NÃO grava os dados novos e a rodada falha. O site continua com os dados da véspera e o
   GitHub manda e-mail avisando da falha.
2. Alertas por ação: números impossíveis ou muito fora do normal viram alertas em
   docs/data/qualidade.json, que o site mostra na ficha da ação e no topo da página.
   Nível "erro" = quase certamente leitura errada; "atencao" = incomum, pode ser real; "info" = só aviso.
"""
from __future__ import annotations

import datetime as dt
import math


def _ok(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _pct(v):
    return f"{v * 100:.1f}%".replace(".", ",")


def _num(v):
    if not _ok(v):
        return "—"
    a = abs(v)
    if a >= 1e9:
        return f"{v / 1e9:.2f} bi".replace(".", ",")
    if a >= 1e6:
        return f"{v / 1e6:.1f} mi".replace(".", ",")
    return f"{v:.2f}".replace(".", ",")


def _empresa_b3(r):
    return (r.get("mercado") or "Brasil") == "Brasil" and r.get("setor") not in ("ETF", "Índice")


def verificar(linhas: list[dict], anteriores: list[dict] | None, hist: dict, hoje: dt.date) -> dict:
    alertas = []

    def add(t, nivel, campo, msg):
        alertas.append({"ticker": t, "nivel": nivel, "campo": campo, "msg": msg})

    ant = {r["ticker"]: r for r in (anteriores or [])}
    for r in linhas:
        t = r["ticker"]
        if r.get("setor") == "Índice":
            continue
        b3 = _empresa_b3(r)
        banco = bool(r.get("banco"))
        luc, rec, pat = r.get("lucro_12m"), r.get("receita_12m"), r.get("patrimonio")
        # ---- balanço (só B3, que vem da CVM)
        if b3:
            if luc == 0 and _ok(rec) and rec != 0:
                add(t, "erro", "lucro_12m", "Lucro de 12 meses exatamente zero com receita diferente de zero: provável erro de leitura do balanço.")
            if luc is None:
                add(t, "erro", "lucro_12m", "Sem lucro de 12 meses: o robô não encontrou a linha de lucro no balanço.")
            if not banco and (rec is None or rec == 0):
                add(t, "atencao", "receita_12m", "Receita zerada ou ausente. É normal em holdings; nas demais é erro de leitura.")
            if pat is None or pat == 0:
                add(t, "erro", "patrimonio", "Patrimônio líquido zerado ou ausente.")
            elif pat < 0:
                add(t, "info", "patrimonio", f"Patrimônio líquido negativo ({_num(pat)}): P/VP e ROE perdem o sentido.")
            db = r.get("data_balanco")
            if db:
                idade = (hoje - dt.date.fromisoformat(db)).days
                if idade > 270:
                    add(t, "atencao", "data_balanco", f"Último balanço entregue à CVM é de {db[8:10]}/{db[5:7]}/{db[:4]}: números desatualizados.")
            # mudança brusca contra a rodada anterior
            a = ant.get(t)
            if a and _ok(luc) and _ok(a.get("lucro_12m")):
                la = a["lucro_12m"]
                mesmo_balanco = a.get("data_balanco") == r.get("data_balanco")
                if mesmo_balanco and la and abs(luc - la) > abs(la) * 0.02:
                    add(t, "atencao", "lucro_12m", f"Lucro de 12 meses mudou de {_num(la)} para {_num(luc)} sem balanço novo (reapresentação da empresa ou mudança de cálculo).")
                if not mesmo_balanco and la and luc and (la > 0) != (luc > 0) and abs(luc) > 3 * abs(la):
                    add(t, "atencao", "lucro_12m", f"Com o balanço novo, o lucro de 12 meses foi de {_num(la)} para {_num(luc)}. Confira se é real.")
        # ---- indicadores de mercado (B3 e EUA)
        if r.get("setor") != "ETF":
            pl, pvp, dy, roe, ml = r.get("pl"), r.get("pvp"), r.get("dy"), r.get("roe"), r.get("m_liq")
            if _ok(pl) and pl > 0 and (pl < 1 or pl > 300):
                add(t, "atencao", "pl", f"P/L de {_num(pl)}: muito fora do normal, confira lucro e quantidade de ações.")
            if _ok(pvp) and pvp > 0 and (pvp < 0.05 or pvp > 40):
                add(t, "atencao", "pvp", f"P/VP de {_num(pvp)}: muito fora do normal, confira patrimônio e quantidade de ações.")
            if _ok(dy) and dy > 0.25:
                add(t, "atencao", "dy", f"Dividend yield de {_pct(dy)} em 12 meses: pode ser provento extraordinário ou erro do Yahoo.")
            if _ok(roe) and _ok(pat) and pat > 0 and abs(roe) > 1.5:
                add(t, "atencao", "roe", f"ROE de {_pct(roe)}: muito fora do normal.")
            if b3 and not banco and _ok(ml) and abs(ml) > 1:
                add(t, "info", "m_liq", f"Margem líquida de {_pct(ml)}: normal em holdings (a receita não é o negócio), estranho nas demais.")
        # ---- preço
        if r.get("preco") is None:
            if b3:
                add(t, "info", "preco", "Sem cotação no Yahoo (código mudou, saiu da bolsa ou não negocia).")
        else:
            vd = r.get("var_dia")
            if _ok(vd) and abs(vd) > 0.35:
                add(t, "atencao", "preco", f"Variação de {_pct(vd)} no dia: confira se houve evento (desdobramento, grupamento, fato relevante).")
        h = hist.get(t)
        if h and h.get("a"):
            a_ = [x for x in h["a"][-25:] if x]
            for x0, x1 in zip(a_, a_[1:]):
                if x1 / x0 > 4 or x1 / x0 < 0.25:
                    add(t, "atencao", "historico", "Salto de preço impossível no histórico dos últimos 2 anos: provável desdobramento ou grupamento não ajustado no Yahoo.")
                    break

    # ---- trava geral: rodada muito pior que a anterior
    bloquear = None
    novas = [r for r in linhas if _empresa_b3(r)]
    velhas = [r for r in (anteriores or []) if _empresa_b3(r)]
    if velhas:
        if len(novas) < 0.9 * len(velhas):
            bloquear = f"Só {len(novas)} empresas da B3 nesta rodada, contra {len(velhas)} na anterior."
        sem_lucro = lambda xs: sum(1 for r in xs if r.get("lucro_12m") in (None, 0))  # noqa: E731
        if sem_lucro(novas) > max(15, 2 * sem_lucro(velhas)):
            bloquear = f"{sem_lucro(novas)} empresas sem lucro nesta rodada, contra {sem_lucro(velhas)} na anterior."
        com_preco = lambda xs: sum(1 for r in xs if r.get("preco"))  # noqa: E731
        if com_preco(novas) < 0.85 * com_preco(velhas):
            bloquear = f"Só {com_preco(novas)} ações com cotação, contra {com_preco(velhas)} na anterior."

    ordem = {"erro": 0, "atencao": 1, "info": 2}
    alertas.sort(key=lambda a: (ordem[a["nivel"]], a["ticker"]))
    resumo = {k: sum(1 for a in alertas if a["nivel"] == k) for k in ordem}
    return {"gerado": hoje.isoformat(), "resumo": resumo, "bloqueio": bloquear, "alertas": alertas,
            "empresas_b3": len(novas), "com_preco": sum(1 for r in novas if r.get("preco"))}
