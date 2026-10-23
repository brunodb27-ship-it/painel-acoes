"""
Score de cada ação, de 0 a 100, em duas visões que podem ser combinadas.

FUNDAMENTALISTA (só números de balanço e preço):
  Qualidade: ROE médio de 10 anos, anos com lucro, margem EBIT média, dívida líquida/EBITDA, crescimento do lucro.
  Preço:     P/L e P/VP contra a mediana do setor, EV/EBITDA contra o setor e o DY de hoje contra a média de 10 anos
             da própria ação (pagadora boa rendendo mais que o normal dela costuma estar barata).
  Cada critério vira um percentil dentro do setor (ou do mercado, se o setor tiver menos de 5 empresas).

TÉCNICA (gráfico mensal: candles, médias e Fibonacci):
  Parte de 50 e soma ou tira pontos por sinal:
  tendência (preço contra a média de 12 meses), IFR de 14 meses, posição na retração de Fibonacci da última perna,
  padrões de candle do último mês fechado (martelo, martelo invertido, estrela cadente, enforcado, engolfo, doji)
  e figuras (ombro-cabeça-ombro, OCO invertido, topo duplo, fundo duplo).

Os pontos de cada sinal técnico não são tirados de livro: o backtest refaz a leitura mês a mês desde 2012
e dá a cada sinal um peso conforme o que ele entregou nos 6 meses seguintes nestas mesmas ações.

JUNÇÃO: 60% fundamentalista + 40% técnica, com destaque quando os dois apontam para o mesmo lado.
Nada aqui é recomendação: é um filtro para escolher o que estudar.
"""
from __future__ import annotations

import datetime as dt
import math

FIB = [0.236, 0.382, 0.5, 0.618, 0.763]


def _ok(v):
    return v is not None and isinstance(v, (int, float)) and math.isfinite(v)


def _pct_rank(valor, pares, maior_melhor=True):
    """Percentil de 0 a 1 do valor dentro dos pares (empates contam meio)."""
    xs = [x for x in pares if _ok(x)]
    if not _ok(valor) or len(xs) < 3:
        return None
    menores = sum(1 for x in xs if x < valor)
    iguais = sum(1 for x in xs if x == valor)
    p = (menores + 0.5 * iguais) / len(xs)
    return p if maior_melhor else 1 - p


def _fmt_pct(v, casas=1):
    return f"{v * 100:.{casas}f}%".replace(".", ",")


def _m(ym):
    return f"{ym[5:7]}/{ym[:4]}" if ym and len(ym) >= 7 else ym


def _fmt(v, casas=1):
    return f"{v:.{casas}f}".replace(".", ",")


# ------------------------------------------------------------------ fundamentalista
def _grupos(linhas):
    grupos = {}
    for r in linhas:
        if r.get("setor") in ("ETF", "Índice"):
            continue
        chave = (r.get("mercado") or "Brasil", r.get("setor") or "", bool(r.get("banco")))
        grupos.setdefault(chave, []).append(r)
    return grupos


def _pares(r, grupos, por_mercado):
    chave = (r.get("mercado") or "Brasil", r.get("setor") or "", bool(r.get("banco")))
    g = grupos.get(chave, [])
    # uma linha por empresa: PETR3 e PETR4 não podem contar duas vezes
    vistos, unicos = set(), []
    for x in g:
        if x.get("empresa") in vistos:
            continue
        vistos.add(x.get("empresa"))
        unicos.append(x)
    if len(unicos) >= 5:
        return unicos, "setor"
    return por_mercado.get((chave[0], chave[2]), []), "mercado"


def fundamentalista(r, rk10, grupos, por_mercado):
    pares, base = _pares(r, grupos, por_mercado)
    banco = bool(r.get("banco"))
    itens = []

    def crit(nome, valor, campo_pares, maior_melhor, peso, pilar, texto):
        vals = [p.get(campo_pares) if not callable(campo_pares) else campo_pares(p) for p in pares]
        pr = _pct_rank(valor, vals, maior_melhor)
        if pr is None:
            return
        itens.append({"nome": nome, "pilar": pilar, "peso": peso, "nota": round(pr, 3), "txt": texto})

    m10 = lambda k: (lambda p: (RK.get(p["ticker"], {}).get("10", {}) or {}).get(k))  # noqa: E731

    # --- qualidade
    roe10 = rk10.get("roe_med")
    if _ok(roe10):
        crit("ROE médio 10 anos", roe10, m10("roe_med"), True, 3, "Qualidade", f"{_fmt_pct(roe10)} ao ano")
    elif _ok(r.get("roe")):
        crit("ROE", r["roe"], "roe", True, 2, "Qualidade", _fmt_pct(r["roe"]))
    if _ok(rk10.get("anos_lucro")) and rk10.get("anos_balanco"):
        n = rk10["anos_lucro"]
        itens.append({"nome": "Anos com lucro", "pilar": "Qualidade", "peso": 2,
                      "nota": round(n / max(10, rk10["anos_balanco"]), 3), "txt": f"{n} de {rk10['anos_balanco']}"})
    if not banco:
        mebit = rk10.get("m_ebit_med")
        if _ok(mebit):
            crit("Margem EBIT média", mebit, m10("m_ebit_med"), True, 2, "Qualidade", _fmt_pct(mebit))
        elif _ok(r.get("m_liq")):
            crit("Margem líquida", r["m_liq"], "m_liq", True, 2, "Qualidade", _fmt_pct(r["m_liq"]))
        dl = r.get("div_liq_ebitda")
        if _ok(dl):
            crit("Dív. líq./EBITDA", dl, "div_liq_ebitda", False, 2, "Qualidade", f"{_fmt(dl, 2)}x")
    cl = rk10.get("cagr_lucro")
    if _ok(cl):
        crit("Crescimento do lucro", cl, m10("cagr_lucro"), True, 1, "Qualidade", f"{_fmt_pct(cl)} ao ano")

    # --- preço
    pl = r.get("pl")
    if _ok(pl):
        if pl <= 0:
            itens.append({"nome": "P/L", "pilar": "Preço", "peso": 2, "nota": 0.0, "txt": "prejuízo nos últimos 12 meses"})
        else:
            crit("P/L contra o setor", pl, lambda p: p.get("pl") if _ok(p.get("pl")) and p["pl"] > 0 else None,
                 False, 2, "Preço", _fmt(pl, 1))
    pvp = r.get("pvp")
    if _ok(pvp) and pvp > 0:
        crit("P/VP contra o setor", pvp, lambda p: p.get("pvp") if _ok(p.get("pvp")) and p["pvp"] > 0 else None,
             False, 2, "Preço", _fmt(pvp, 2))
    if not banco and _ok(r.get("ev_ebitda")) and r["ev_ebitda"] > 0:
        crit("EV/EBITDA contra o setor", r["ev_ebitda"],
             lambda p: p.get("ev_ebitda") if _ok(p.get("ev_ebitda")) and p["ev_ebitda"] > 0 else None,
             False, 1, "Preço", _fmt(r["ev_ebitda"], 1))
    dy, dym = r.get("dy"), rk10.get("dy_medio")
    if _ok(r.get("dy_cvm")) and (not _ok(dy) or dy < r["dy_cvm"] * 0.3) and r.get("mercado", "Brasil") == "Brasil":
        dy = r["dy_cvm"]
    if _ok(dy) and _ok(dym) and dym > 0.02:
        razao = dy / dym
        nota = max(0.0, min(1.0, (razao - 0.6) / 0.9))  # 0,6x a média = 0 · 1,5x a média = 1
        itens.append({"nome": "DY de hoje contra a média de 10 anos", "pilar": "Preço", "peso": 2, "nota": round(nota, 3),
                      "txt": f"{_fmt_pct(dy)} hoje · média {_fmt_pct(dym)}"})

    if len(itens) < 3:
        return None
    def media(pilar):
        xs = [i for i in itens if i["pilar"] == pilar]
        w = sum(i["peso"] for i in xs)
        return (sum(i["nota"] * i["peso"] for i in xs) / w) if w else None
    q, pr = media("Qualidade"), media("Preço")
    partes = [(q, 0.5), (pr, 0.5)]
    w = sum(p for v, p in partes if v is not None)
    score = round(100 * sum(v * p for v, p in partes if v is not None) / w)
    return {"score": score, "qualidade": None if q is None else round(100 * q), "preco": None if pr is None else round(100 * pr),
            "base": base, "itens": itens}


RK = {}


# ------------------------------------------------------------------ técnica
def _zigzag(xs, limiar=0.15):
    """Topos e fundos alternados, ignorando oscilações menores que o limiar.
    O último pivô é o extremo da perna em andamento (ainda não confirmado)."""
    piv = []
    if len(xs) < 3:
        return piv
    tend, hi, lo, ext = 0, 0, 0, 0
    for i in range(1, len(xs)):
        x = xs[i]
        if not _ok(x) or x <= 0:
            continue
        if tend == 0:
            if x > xs[hi]:
                hi = i
            if x < xs[lo]:
                lo = i
            if xs[hi] >= xs[lo] * (1 + limiar):
                if hi > lo:
                    piv.append(("fundo", lo)); tend, ext = 1, hi
                else:
                    piv.append(("topo", hi)); tend, ext = -1, lo
        elif tend == 1:
            if x > xs[ext]:
                ext = i
            elif x <= xs[ext] * (1 - limiar):
                piv.append(("topo", ext)); tend, ext = -1, i
        else:
            if x < xs[ext]:
                ext = i
            elif x >= xs[ext] * (1 + limiar):
                piv.append(("fundo", ext)); tend, ext = 1, i
    if tend == 1:
        piv.append(("topo", ext))
    elif tend == -1:
        piv.append(("fundo", ext))
    return piv


def _candle(o, h, l, c):
    corpo, faixa = abs(c - o), max(h - l, 1e-9)
    sup, inf = h - max(o, c), min(o, c) - l
    return corpo, faixa, sup, inf


def _padroes_candle(O, H, L, C, i):
    """Padrões do mês i olhando a tendência dos 6 meses anteriores."""
    out = []
    if i < 6 or not all(_ok(v) and v > 0 for v in (O[i], H[i], L[i], C[i], O[i - 1], C[i - 1])):
        return out
    if L[i] > min(O[i], C[i]) * 1.001 or H[i] < max(O[i], C[i]) * 0.999:
        return out  # máxima/mínima incoerentes (dado ruim do Yahoo)
    caiu = C[i - 1] < C[i - 6] * 0.92
    subiu = C[i - 1] > C[i - 6] * 1.08
    corpo, faixa, sup, inf = _candle(O[i], H[i], L[i], C[i])
    if corpo <= faixa * 0.35 and inf >= 2 * max(corpo, faixa * 0.05) and sup <= faixa * 0.15:
        if caiu:
            out.append(("Martelo", "alta", "pavio longo para baixo depois de queda: compradores rejeitaram preços menores"))
        elif subiu:
            out.append(("Enforcado", "baixa", "mesmo formato do martelo, mas depois de alta: sinal de cansaço"))
    if corpo <= faixa * 0.35 and sup >= 2 * max(corpo, faixa * 0.05) and inf <= faixa * 0.15:
        if subiu:
            out.append(("Estrela cadente", "baixa", "pavio longo para cima depois de alta: vendedores rejeitaram preços maiores"))
        elif caiu:
            out.append(("Martelo invertido", "alta", "pavio longo para cima depois de queda: primeira tentativa de reação"))
    c0, o0 = C[i - 1], O[i - 1]
    if o0 > c0 and C[i] > O[i] and C[i] >= o0 and O[i] <= c0 and caiu:
        out.append(("Engolfo de alta", "alta", "candle de alta cobre todo o corpo de queda do mês anterior"))
    if c0 > o0 and O[i] > C[i] and O[i] >= c0 and C[i] <= o0 and subiu:
        out.append(("Engolfo de baixa", "baixa", "candle de queda cobre todo o corpo de alta do mês anterior"))
    if corpo <= faixa * 0.08 and faixa > 0:
        out.append(("Doji", "neutro", "abertura e fechamento quase iguais: indecisão"))
    return out


def _figuras(C, d):
    """Ombro-cabeça-ombro, OCO invertido, topo duplo e fundo duplo nos últimos 48 meses."""
    out = []
    ini = max(0, len(C) - 48)
    xs = C[ini:]
    piv = [(t, i + ini) for t, i in _zigzag(xs, 0.10)]
    topos = [i for t, i in piv if t == "topo"]
    fundos = [i for t, i in piv if t == "fundo"]
    atual = C[-1]

    def entre(a, b, lista):
        return [i for i in lista if a < i < b]

    # OCO: três topos, o do meio mais alto, ombros parecidos (até 12%)
    if len(topos) >= 3:
        a, b, c = topos[-3:]
        if C[b] > C[a] * 1.05 and C[b] > C[c] * 1.05 and abs(C[a] - C[c]) / max(C[a], C[c]) < 0.12:
            vales = entre(a, b, fundos) + entre(b, c, fundos)
            if vales:
                pescoco = min(C[v] for v in vales)
                if atual < pescoco * 0.98:
                    out.append(("Ombro-cabeça-ombro confirmado", "baixa",
                                f"preço rompeu a linha de pescoço ({_fmt(pescoco, 2)}) depois dos topos de {_m(d[a])}, {_m(d[b])} e {_m(d[c])}", 20))
                elif c >= len(C) - 6:
                    out.append(("Ombro-cabeça-ombro em formação", "baixa",
                                f"topos de {_m(d[a])}, {_m(d[b])} e {_m(d[c])}; confirma se perder {_fmt(pescoco, 2)}", 6))
    if len(fundos) >= 3:
        a, b, c = fundos[-3:]
        if C[b] < C[a] * 0.95 and C[b] < C[c] * 0.95 and abs(C[a] - C[c]) / max(C[a], C[c]) < 0.12:
            picos = entre(a, b, topos) + entre(b, c, topos)
            if picos:
                pescoco = max(C[v] for v in picos)
                if atual > pescoco * 1.02:
                    out.append(("OCO invertido confirmado", "alta",
                                f"preço rompeu a linha de pescoço ({_fmt(pescoco, 2)}) depois dos fundos de {_m(d[a])}, {_m(d[b])} e {_m(d[c])}", 20))
                elif c >= len(C) - 6:
                    out.append(("OCO invertido em formação", "alta",
                                f"fundos de {_m(d[a])}, {_m(d[b])} e {_m(d[c])}; confirma se passar de {_fmt(pescoco, 2)}", 6))
    # topo e fundo duplo: dois extremos parecidos (até 4%) com vale/pico entre eles
    if len(topos) >= 2 and not any("cabeça" in x[0] for x in out):
        a, b = topos[-2:]
        meio = entre(a, b, fundos)
        if abs(C[a] - C[b]) / max(C[a], C[b]) < 0.04 and meio and b >= len(C) - 12:
            vale = min(C[v] for v in meio)
            if atual < vale:
                out.append(("Topo duplo confirmado", "baixa", f"dois topos ({_m(d[a])} e {_m(d[b])}) e perda do vale de {_fmt(vale, 2)}", 14))
    if len(fundos) >= 2 and not any("OCO" in x[0] for x in out):
        a, b = fundos[-2:]
        meio = entre(a, b, topos)
        if abs(C[a] - C[b]) / max(C[a], C[b]) < 0.04 and meio and b >= len(C) - 12:
            pico = max(C[v] for v in meio)
            if atual > pico:
                out.append(("Fundo duplo confirmado", "alta", f"dois fundos ({_m(d[a])} e {_m(d[b])}) e rompimento de {_fmt(pico, 2)}", 14))
    return out, piv


def _ifr(C, n=14):
    if len(C) < n + 1:
        return None
    g = p = 0.0
    for i in range(len(C) - n, len(C)):
        dlt = C[i] - C[i - 1]
        if dlt > 0:
            g += dlt
        else:
            p -= dlt
    if p == 0:
        return 100.0
    rs = (g / n) / (p / n)
    return 100 - 100 / (1 + rs)


def tecnica(h, hoje: dt.date | None):
    if not h or not h.get("d") or len(h["d"]) < 24:
        return None
    d, C = h["d"], h["c"]
    O, H, L = h.get("o"), h.get("h"), h.get("l")
    n = len(C)
    sinais = []

    def add(nome, pontos, txt, tipo):
        sinais.append({"nome": nome, "pontos": pontos, "txt": txt, "tipo": tipo})

    # tendência: média de 12 meses
    mm = sum(C[-12:]) / 12
    mm_ant = sum(C[-15:-3]) / 12
    if C[-1] > mm and mm > mm_ant:
        add("Tendência de alta", 12, f"preço acima da média de 12 meses ({_fmt(mm, 2)}), que está subindo", "alta")
    elif C[-1] < mm and mm < mm_ant:
        add("Tendência de baixa", -12, f"preço abaixo da média de 12 meses ({_fmt(mm, 2)}), que está caindo", "baixa")
    else:
        add("Sem tendência definida", 0, f"preço e média de 12 meses ({_fmt(mm, 2)}) em direções diferentes", "neutro")

    # IFR mensal
    ifr = _ifr(C)
    if ifr is not None:
        if ifr <= 30:
            add("IFR sobrevendido", 10, f"IFR de 14 meses em {_fmt(ifr, 0)}: queda esticada", "alta")
        elif ifr >= 70:
            add("IFR sobrecomprado", -10, f"IFR de 14 meses em {_fmt(ifr, 0)}: alta esticada", "baixa")

    # longo prazo: média de 36 meses e distância do topo de 5 anos (com dividendos, para não punir boa pagadora)
    aviso_lp = None
    A = h.get("a") or C
    if n >= 36 and all(_ok(x) and x > 0 for x in A[-36:]):
        m36 = sum(A[-36:]) / 36
        m36_ant = sum(A[-42:-6]) / 36 if n >= 42 else None
        if A[-1] > m36 and (m36_ant is None or m36 >= m36_ant):
            add("Acima da média de 36 meses", 6, "longo prazo: retorno com dividendos acima da média de 3 anos, que está subindo", "alta")
        elif A[-1] < m36 and (m36_ant is None or m36 < m36_ant):
            add("Abaixo da média de 36 meses", -6, "longo prazo: retorno com dividendos abaixo da média de 3 anos, que está caindo", "baixa")
        jan5 = [x for x in A[-60:] if _ok(x) and x > 0]
        topo = max(jan5)
        i_topo = n - len(A[-60:]) + A[-60:].index(topo)
        queda = 1 - A[-1] / topo
        quando = _m(d[i_topo])
        if queda <= 0.10:
            add("Perto do topo de 5 anos", 4, f"a até 10% do maior valor dos últimos 5 anos ({quando}), contando dividendos", "alta")
        elif queda <= 0.30:
            add("10–30% abaixo do topo de 5 anos", 0, f"{_fmt_pct(queda, 0)} abaixo do maior valor dos últimos 5 anos ({quando}), contando dividendos", "neutro")
        elif queda <= 0.50:
            add("30–50% abaixo do topo de 5 anos", -4, f"{_fmt_pct(queda, 0)} abaixo do maior valor dos últimos 5 anos ({quando}), contando dividendos", "baixa")
        else:
            add("Mais de 50% abaixo do topo de 5 anos", -6, f"{_fmt_pct(queda, 0)} abaixo do maior valor dos últimos 5 anos ({quando}), contando dividendos", "baixa")
        if queda > 0.40:
            aviso_lp = (f"Queda forte no longo prazo: mesmo com dividendos, está {_fmt_pct(queda, 0)} abaixo do topo de {quando}. "
                        "Uma reação recente pode ser só um repique. Descubra o motivo da queda antes de tudo.")

    # Fibonacci da última perna completa (dois pivôs confirmados de 20% ou mais)
    jan = C[-72:]
    off = n - len(jan)
    piv = [(t, i + off) for t, i in _zigzag(jan, 0.20)]
    fib = None
    if len(piv) >= 3:
        (t1, i1), (t2, i2) = piv[-3], piv[-2]
        a_, b_ = C[i1], C[i2]
        if t1 == "fundo":   # perna de alta: mede quanto da alta o preço devolveu
            ret = (b_ - C[-1]) / (b_ - a_)
            fib = {"tipo": "alta", "de": d[i1], "ate": d[i2], "a": a_, "b": b_, "ret": round(ret, 4)}
            txt = f"alta de {_m(d[i1])} a {_m(d[i2])} ({_fmt(a_, 2)} → {_fmt(b_, 2)}); preço devolveu {_fmt_pct(ret)} dela"
            if ret < 0:
                add("Acima do topo da última alta", 6, f"alta de {_m(d[i1])} a {_m(d[i2])} ({_fmt(a_, 2)} → {_fmt(b_, 2)}) já foi superada", "alta")
            elif ret < 0.236:
                add("Perto do topo da última alta", 0, txt, "neutro")
            elif ret < 0.382:
                add("Correção rasa (23,6–38,2%)", 3, txt, "neutro")
            elif ret <= 0.618:
                verde = bool(O) and _ok(O[-1]) and C[-1] >= O[-1]
                add("Correção na zona de Fibonacci (38,2–61,8%)", 12 if verde else 8,
                    txt + (": e o mês está reagindo" if verde else ": zona onde costuma achar suporte"), "alta")
            elif ret <= 0.763:
                add("Correção profunda (61,8–76,3%)", 2, txt + ": último suporte antes de anular a alta", "neutro")
            elif ret <= 1:
                add("Devolveu quase toda a alta", -6, txt, "baixa")
            else:
                add("Perdeu o fundo da última alta", -10, txt, "baixa")
        else:               # perna de baixa: mede quanto da queda o preço recuperou
            ret = (C[-1] - b_) / (a_ - b_)
            fib = {"tipo": "baixa", "de": d[i1], "ate": d[i2], "a": a_, "b": b_, "ret": round(ret, 4)}
            txt = f"queda de {_m(d[i1])} a {_m(d[i2])} ({_fmt(a_, 2)} → {_fmt(b_, 2)}); preço recuperou {_fmt_pct(ret)} dela"
            if ret < 0:
                add("Abaixo do fundo da última queda", -8, txt.split(";")[0] + ": fundo perdido", "baixa")
            elif 0.5 <= ret <= 0.68:
                add("Repique na resistência de Fibonacci (50–61,8%)", -8, txt + ": zona onde repiques costumam travar", "baixa")
            elif ret > 1:
                add("Superou o topo da última queda", 10,
                    f"queda de {_m(d[i1])} a {_m(d[i2])} ({_fmt(a_, 2)} → {_fmt(b_, 2)}) já foi toda recuperada; preço {_fmt_pct(C[-1] / a_ - 1, 0)} acima daquele topo", "alta")
            elif ret > 0.763:
                add("Recuperou mais de 76,3% da queda", 5, txt, "alta")
            else:
                add("Recuperando a queda", 0, txt, "neutro")

    # candles: último mês fechado (o mês corrente ainda está em aberto)
    padroes = []
    if O and H and L:
        i = n - 1
        if hoje and d[-1] == hoje.strftime("%Y-%m") and n >= 2:
            i = n - 2
        for nome, tipo, txt in _padroes_candle(O, H, L, C, i):
            pts = 8 if tipo == "alta" else -8 if tipo == "baixa" else 0
            add(nome, pts, f"{txt} ({_m(d[i])})", tipo)
            padroes.append({"nome": nome, "tipo": tipo, "mes": d[i]})

    # figuras
    figs, _ = _figuras(C, d)
    for nome, tipo, txt, pts in figs:
        add(nome, pts if tipo == "alta" else -pts, txt, tipo)
        padroes.append({"nome": nome, "tipo": tipo, "mes": d[-1]})

    score = max(0, min(100, 50 + sum(s["pontos"] for s in sinais)))
    return {"score": score, "sinais": sinais, "padroes": padroes, "fib": fib, "ifr": None if ifr is None else round(ifr, 1),
            "aviso_lp": aviso_lp}


# ------------------------------------------------------------------ junção
def sinal(f, t):
    if f is not None and t is not None:
        if f >= 65 and t >= 60:
            return "convergencia_compra"
        if f <= 35 and t <= 40:
            return "convergencia_venda"
        if t <= 35 and f >= 65:
            return "boa_empresa_grafico_fraco"
        if f >= 65:
            return "fundamentos_bons"
    if t is not None and t >= 65:
        return "grafico_compra"
    if t is not None and t <= 35:
        return "grafico_venda"
    return "neutro"


def _fatia(h, i):
    return {k: h[k][: i + 1] for k in ("d", "c", "a", "o", "h", "l") if h.get(k)}


GRUPOS_EXCLUSIVOS = [
    ["Tendência de alta", "Tendência de baixa", "Sem tendência definida"],
    ["Perto do topo de 5 anos", "10–30% abaixo do topo de 5 anos", "30–50% abaixo do topo de 5 anos",
     "Mais de 50% abaixo do topo de 5 anos"],
]


def _pesos_conjuntos(meses, nomes, l2=30.0, iters=400):
    """Regressão logística (subiu ou não em 6 meses) com todos os sinais juntos.
    Assim sinais que dizem a mesma coisa (tendência de 12 meses, média de 36, topo de 5 anos) não somam
    pontos em dobro: cada um ganha só o que acrescenta além dos outros."""
    import numpy as np
    idx = {k: i for i, k in enumerate(nomes)}
    X = np.zeros((len(meses), len(nomes)), dtype=np.float32)
    y = np.zeros(len(meses), dtype=np.float32)
    for j, (r, ns) in enumerate(meses):
        y[j] = 1.0 if r > 0 else 0.0
        for nm in ns:
            if nm in idx:
                X[j, idx[nm]] = 1.0
    w = np.zeros(len(nomes)); b = 0.0
    n = len(meses)
    for _ in range(iters):  # descida de gradiente simples (Newton seria mais rápido, mas isto basta)
        z = X @ w + b
        p = 1 / (1 + np.exp(-z))
        g = p - y
        w -= 1.5 * ((X.T @ g) / n + l2 * w / n)
        b -= 1.5 * float(g.mean())
    # grupos em que sempre aparece exatamente um sinal: o peso só tem sentido comparado com os irmãos,
    # então cada grupo é centrado na média (ponderada pela frequência)
    freq = X.sum(axis=0)
    for grupo in GRUPOS_EXCLUSIVOS:
        ii = [idx[k] for k in grupo if k in idx]
        if len(ii) > 1 and freq[ii].sum() > 0:
            w[ii] -= float((w[ii] * freq[ii]).sum() / freq[ii].sum())
    # 0,1 de log-odds ≈ 2,5 pontos percentuais de chance de subir; vira ~5 pontos de score
    return {k: int(round(max(-15, min(15, w[i] * 50)))) for k, i in idx.items()}


def backtest(hist, horizonte=6, desde="2012-01"):
    """Para cada mês do passado, refaz a leitura técnica só com o que se sabia naquele mês e mede o
    retorno total (com dividendos) dos 6 meses seguintes. Assim cada sinal ganha um peso de acordo com o
    que ele realmente entregou nestas ações, em vez de um peso tirado de livro."""
    meses = []  # (retorno, [nomes dos sinais])
    for t, h in hist.items():
        if not h or not h.get("o") or len(h.get("d", [])) < 36:
            continue
        A, d = h["a"], h["d"]
        for i in range(24, len(d) - horizonte):
            if d[i] < desde or not A[i] or not A[i + horizonte]:
                continue
            r = A[i + horizonte] / A[i] - 1
            if r > 4 or r < -0.95:   # salto de dado (desdobramento sem ajuste etc.)
                continue
            te = tecnica(_fatia(h, i), None)
            if te:
                meses.append((r, [x["nome"] for x in te["sinais"]]))
    if len(meses) < 1000:
        return None

    def resumo(rs):
        rs = sorted(rs)
        return {"n": len(rs), "media": round(sum(rs) / len(rs), 4), "mediana": round(rs[len(rs) // 2], 4),
                "positivos": round(sum(1 for x in rs if x > 0) / len(rs), 4)}
    base = resumo([r for r, _ in meses])
    por_sinal = {}
    for r, nomes in meses:
        for nm in nomes:
            por_sinal.setdefault(nm, []).append(r)
    nomes_ok = sorted(k for k, rs in por_sinal.items() if len(rs) >= 30)
    pesos = _pesos_conjuntos(meses, nomes_ok)
    sinais = {}
    for k in nomes_ok:
        res = resumo(por_sinal[k])
        res["peso"] = pesos.get(k, 0)
        sinais[k] = res
    faixas = {}
    for r, nomes in meses:
        sc = 50 + sum(sinais.get(nm, {}).get("peso", 0) for nm in nomes)
        f = "compra" if sc >= 65 else "venda" if sc <= 35 else "neutro"
        faixas.setdefault(f, []).append(r)
    return {"horizonte": horizonte, "desde": desde, "base": base, "sinais": sinais,
            "faixas": {k: resumo(v) for k, v in faixas.items() if len(v) >= 30}}


def aplicar_pesos(te, bt):
    if not te or not bt:
        return te
    for x in te["sinais"]:
        st = bt["sinais"].get(x["nome"])
        x["livro"] = x["tipo"]               # o que a teoria diz
        if st:
            x["pontos"] = st["peso"]
            x["hist"] = {"n": st["n"], "positivos": st["positivos"], "media": st["media"]}
            x["tipo"] = "alta" if st["peso"] >= 3 else "baixa" if st["peso"] <= -3 else "neutro"
    te["score"] = max(0, min(100, 50 + sum(x["pontos"] for x in te["sinais"])))
    return te


def concordancia(f, t):
    """Se fundamentos e gráfico apontam para o mesmo lado. Não é probabilidade de acerto."""
    if f is None or t is None:
        return None
    lado = lambda s: 1 if s >= 55 else -1 if s <= 45 else 0  # noqa: E731
    lf, lt = lado(f), lado(t)
    if lf and lt and lf != lt:
        return "baixa"
    if lf and lf == lt:
        forte = (f >= 65 or f <= 35) and (t >= 65 or t <= 35)
        return "alta" if forte else "media"
    if lf or lt:
        return "media"
    return "sem_direcao"


def calcular(linhas, hist, ranking, hoje: dt.date, bt=None):
    global RK
    RK = (ranking or {}).get("dados", {})
    grupos = _grupos(linhas)
    por_mercado = {}
    for (merc, _setor, banco), rows in grupos.items():
        por_mercado.setdefault((merc, banco), []).extend(rows)
    for k, rows in por_mercado.items():
        vistos, unicos = set(), []
        for x in rows:
            if x.get("empresa") in vistos:
                continue
            vistos.add(x.get("empresa"))
            unicos.append(x)
        por_mercado[k] = unicos
    out = {}
    for r in linhas:
        t = r["ticker"]
        if r.get("setor") in ("Índice",):
            continue
        rk10 = (RK.get(t, {}) or {}).get("10", {}) or {}
        f = None if r.get("setor") == "ETF" else fundamentalista(r, rk10, grupos, por_mercado)
        te = aplicar_pesos(tecnica(hist.get(t), hoje), bt)
        if not f and not te:
            continue
        fs, ts = (f or {}).get("score"), (te or {}).get("score")
        total = round(0.6 * fs + 0.4 * ts) if fs is not None and ts is not None else (fs if fs is not None else ts)
        out[t] = {"f": f, "t": te, "total": total, "sinal": sinal(fs, ts), "conc": concordancia(fs, ts)}
    bt_resumo = None
    if bt:
        bt_resumo = {k: v for k, v in bt.items()}
    return {"gerado": hoje.isoformat(), "backtest": bt_resumo, "dados": out}
