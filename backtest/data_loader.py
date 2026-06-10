"""
Загрузка исторических данных для бектеста.

Два источника:
  1. GitHub-репозиторий FX-Data (https://github.com/FX-Data) — бесплатные
     минутные/часовые данные MT4 (.hst) по основным парам, 2011-2021;
  2. yfinance — последние ~2 года H1 (нужен доступ к Yahoo Finance).
"""
import gzip
import io
import struct
import urllib.request

import pandas as pd

FX_DATA_URL = ("https://github.com/FX-Data/FX-Data-{symbol}-DS/"
               "releases/download/{year}/{symbol}{period}.hst.gz")

HST_HEADER_SIZE = 148
HST_RECORD = struct.Struct("<q4dqiq")  # time, OHLC, volume, spread, real_vol


def parse_hst(raw: bytes) -> pd.DataFrame:
    """Парсит MT4 .hst (version 401) в DataFrame OHLCV."""
    version = struct.unpack("<i", raw[:4])[0]
    if version != 401:
        raise ValueError(f"Unsupported HST version: {version}")
    rows = []
    size = HST_RECORD.size
    for off in range(HST_HEADER_SIZE, len(raw) - size + 1, size):
        t, o, h, l, c, v, _, _ = HST_RECORD.unpack_from(raw, off)
        rows.append((t, o, h, l, c, v))
    df = pd.DataFrame(rows, columns=["time", "Open", "High", "Low", "Close", "Volume"])
    df.index = pd.to_datetime(df.pop("time"), unit="s")
    return df


def load_fxdata(symbol: str = "EURUSD", years=range(2016, 2022),
                period: int = 60) -> pd.DataFrame:
    """Качает H1 (.hst) с GitHub FX-Data и склеивает указанные годы."""
    frames = []
    for year in years:
        url = FX_DATA_URL.format(symbol=symbol, year=year, period=period)
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                raw = gzip.decompress(resp.read())
            frames.append(parse_hst(raw))
            print(f"  {symbol} {year}: ok")
        except Exception as e:
            print(f"  {symbol} {year}: пропущен ({e})")
    if not frames:
        raise RuntimeError("Не удалось загрузить ни одного года данных")
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="last")]


def load_yfinance(symbol: str = "EURUSD=X", period: str = "730d",
                  interval: str = "1h") -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(symbol, period=period, interval=interval,
                     auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    if df.index.tz is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)
    return df


def load(symbol: str = "EURUSD", source: str = "auto", **kwargs) -> pd.DataFrame:
    if source == "fxdata":
        return load_fxdata(symbol.replace("=X", ""), **kwargs)
    if source == "yfinance":
        return load_yfinance(symbol if "=" in symbol else symbol + "=X", **kwargs)
    try:
        return load_yfinance(symbol if "=" in symbol else symbol + "=X")
    except Exception as e:
        print(f"yfinance недоступен ({e}), переключаюсь на FX-Data GitHub...")
        return load_fxdata(symbol.replace("=X", ""))
