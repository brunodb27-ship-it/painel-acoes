"""
Candles diários e semanais para o gráfico da ficha (o score continua no mensal).

Um arquivo por ativo em docs/data/d/<TICKER>.json:
  {"d": {"t": [datas], "o": [...], "h": [...], "l": [...], "c": [...]},   ~1 ano de pregões
   "w": {...}}                                                            ~5 anos de semanas
Os preços são os do Yahoo, ajustados por desdobramento (sem ajuste de dividendos).
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import pandas as pd

DIAS_ARQ, SEMANAS_ARQ = 260, 262      # o que vai para o site
PERIODO_D, PERIODO_W = "2y", "10y"    # o que se baixa (o excedente serve só para o teste dos padrões)


def _r(x):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    x = float(x)
    return round(x, 2) if abs(x) >= 1 else round(x, 4)


def _serie(df: pd.DataFrame, fmt: str) -> dict | None:
    df = df.dropna(subset=["Close"])
    df = df[df["Close"] > 0]
    if df.empty:
        return None
    o, h, l, c = (df[k].astype(float) for k in ("Open", "High", "Low", "Close"))
    # o Yahoo às vezes manda máxima/mínima zeradas ou fora do candle: corrige para não desenhar pavio absurdo
    o = o.where(o > 0, c)
    h = pd.concat([h.where(h > 0, 0), o, c], axis=1).max(axis=1)
    l = pd.concat([l.where(l > 0, float("inf")), o, c], axis=1).min(axis=1)
    return {"t": [x.strftime(fmt) for x in df.index], "o": [_r(x) for x in o], "h": [_r(x) for x in h],
            "l": [_r(x) for x in l], "c": [_r(x) for x in c]}


def _corta(s: dict | None, n: int) -> dict | None:
    return None if not s else {k: v[-n:] for k, v in s.items()}


def baixar(simbolos: dict[str, str], pasta: Path, log) -> tuple[dict, dict]:
    """simbolos = {ticker do painel: símbolo no Yahoo}. Grava os arquivos e devolve as séries
    completas (diárias e semanais) para o teste dos padrões."""
    import yfinance as yf

    pasta.mkdir(parents=True, exist_ok=True)
    inv = {v: k for k, v in simbolos.items()}
    syms = sorted(inv)
    dia, sem = {}, {}
    for i in range(0, len(syms), 80):
        lote = syms[i : i + 80]
        log(f"candles diários {i + 1}-{i + len(lote)} de {len(syms)}")
        try:
            d = yf.download(lote, period=PERIODO_D, interval="1d", auto_adjust=False,
                            group_by="ticker", progress=False, threads=True)
            w = yf.download(lote, period=PERIODO_W, interval="1wk", auto_adjust=False,
                            group_by="ticker", progress=False, threads=True)
        except Exception as e:  # noqa: BLE001
            log("erro no lote diário", e)
            continue
        for s in lote:
            t = inv[s]
            try:
                dd = d[s] if isinstance(d.columns, pd.MultiIndex) else d
                ww = w[s] if isinstance(w.columns, pd.MultiIndex) else w
                sd, sw = _serie(dd, "%Y-%m-%d"), _serie(ww, "%Y-%m-%d")
            except Exception:  # noqa: BLE001
                continue
            if not sd and not sw:
                continue
            dia[t], sem[t] = sd, sw
            arq = {"d": _corta(sd, DIAS_ARQ), "w": _corta(sw, SEMANAS_ARQ)}
            (pasta / f"{t}.json").write_text(json.dumps(arq, separators=(",", ":")))
        time.sleep(2)
    return {t: s for t, s in dia.items() if s}, {t: s for t, s in sem.items() if s}
