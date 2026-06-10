"""
Бектест стратегии ASA (SMC-style) на исторических данных.

Логика повторяет идею TradingView-стратегии, сигналы которой принимает бот:
  - Старший таймфрейм (H4) задаёт направление (bias) по EMA;
  - Вход на H1 на откате в зону дисконта/премиума после подтверждающей свечи;
  - SL за свингом с буфером ATR, TP = RR * риск;
  - Перевод в безубыток после прохождения 1R (как алерт BE у бота);
  - Фильтр торговых сессий (Лондон + Нью-Йорк).

Запуск:
    pip install -r backtest/requirements.txt
    python backtest/asa_backtest.py                # бектест с дефолтами
    python backtest/asa_backtest.py --optimize     # подбор параметров
    python backtest/asa_backtest.py --symbol GBPUSD --source fxdata
"""
import argparse

import numpy as np
import pandas as pd
from backtesting import Backtest, Strategy

from data_loader import load


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hl = df["High"] - df["Low"]
    hc = (df["High"] - df["Close"].shift()).abs()
    lc = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()


class ASAStrategy(Strategy):
    # Параметры по умолчанию = рекомендованные по результатам оптимизации
    rr = 2.0              # минимальный риск/прибыль
    htf_ema = 50          # EMA на H4 для bias (50 * 4h = 200h тренд)
    pullback_ema = 20     # зона отката на H1
    atr_buffer = 0.5      # буфер SL в долях ATR за свингом
    swing_lookback = 10   # глубина поиска свинга для SL
    use_sessions = True   # торговать только Лондон/НЙ (7-16 UTC)
    move_to_be = True     # безубыток после +1R

    def init(self):
        close = pd.Series(self.data.Close, index=self.data.index)
        df = pd.DataFrame({
            "Open": self.data.Open, "High": self.data.High,
            "Low": self.data.Low, "Close": self.data.Close,
        }, index=self.data.index)

        # H4 bias: EMA на ресемпле в 4 часа, переносим на H1
        h4_close = close.resample("4h").last().dropna()
        h4_ema = ema(h4_close, self.htf_ema).reindex(close.index, method="ffill")
        self.htf_bias = self.I(lambda: h4_ema.values, name="H4_EMA")

        self.pb_ema = self.I(lambda: ema(close, self.pullback_ema).values, name="PB_EMA")
        self.atr14 = self.I(lambda: atr(df).values, name="ATR")
        self._entry_risk = None

    def in_session(self) -> bool:
        if not self.use_sessions:
            return True
        return 7 <= self.data.index[-1].hour < 16

    def next(self):
        price = self.data.Close[-1]

        # Перевод открытой позиции в безубыток после +1R
        if self.position and self.move_to_be and self._entry_risk:
            for trade in self.trades:
                if trade.is_long and price >= trade.entry_price + self._entry_risk:
                    trade.sl = max(trade.sl, trade.entry_price)
                elif trade.is_short and price <= trade.entry_price - self._entry_risk:
                    trade.sl = min(trade.sl, trade.entry_price)
            return

        if self.position or not self.in_session():
            return
        if len(self.data) < self.swing_lookback + 2:
            return

        bias_up = price > self.htf_bias[-1]
        bias_dn = price < self.htf_bias[-1]
        a = self.atr14[-1]
        if not np.isfinite(a) or a <= 0:
            return

        prev_o, prev_c = self.data.Open[-2], self.data.Close[-2]
        cur_o, cur_c = self.data.Open[-1], self.data.Close[-1]

        # Лонг: H4 bias вверх, откат под EMA20 (дисконт), бычье поглощение
        pulled_back_long = self.data.Low[-2] <= self.pb_ema[-2]
        engulf_long = (prev_c < prev_o) and (cur_c > cur_o) and (cur_c > prev_o)

        # Шорт: зеркально
        pulled_back_short = self.data.High[-2] >= self.pb_ema[-2]
        engulf_short = (prev_c > prev_o) and (cur_c < cur_o) and (cur_c < prev_o)

        if bias_up and pulled_back_long and engulf_long:
            swing_low = min(self.data.Low[-self.swing_lookback:])
            sl = swing_low - self.atr_buffer * a
            risk = price - sl
            if risk <= 0:
                return
            tp = price + self.rr * risk
            self._entry_risk = risk
            self.buy(sl=sl, tp=tp, size=0.02)

        elif bias_dn and pulled_back_short and engulf_short:
            swing_high = max(self.data.High[-self.swing_lookback:])
            sl = swing_high + self.atr_buffer * a
            risk = sl - price
            if risk <= 0:
                return
            tp = price - self.rr * risk
            self._entry_risk = risk
            self.sell(sl=sl, tp=tp, size=0.02)


class AsianSweepStrategy(Strategy):
    """
    Лучшая из протестированных моделей: ICT-style Judas Swing.

    Лондон (7-11 UTC) снимает ликвидность за границей азиатского
    диапазона (0-7 UTC) и закрывается обратно внутрь сильной свечой
    (displacement). Вход в сторону возврата, SL за хвостом свипа,
    TP = RR * риск.

    На 2016-2019 (EURUSD H1): PF 1.2-1.47, WR 33-40% при RR 2.
    ВНИМАНИЕ: на out-of-sample 2020-2021 преимущество не подтвердилось
    (PF ~0.97) — обязательно прогоняйте walk-forward на свежих данных
    перед использованием на реальном счёте.
    """
    rr = 2.0
    atr_buffer = 0.25
    disp_mult = 0.6        # тело свечи-подтверждения > 0.6 * ATR
    max_asia_range = 1.5   # пропускать дни с диапазоном Азии > 1.5 * ATR

    def init(self):
        df = pd.DataFrame({
            "Open": self.data.Open, "High": self.data.High,
            "Low": self.data.Low, "Close": self.data.Close,
        }, index=self.data.index)
        self.atr14 = self.I(lambda: atr(df).values, name="ATR")
        self._day = None
        self._asia_hi = self._asia_lo = None
        self._traded = None

    def next(self):
        ts = self.data.index[-1]
        if self.position:
            return
        d = ts.date()
        if d != self._day:
            self._day = d
            self._asia_hi = self._asia_lo = None
        h = ts.hour  # UTC
        if 0 <= h < 7:  # копим диапазон Азии
            lo, hi = self.data.Low[-1], self.data.High[-1]
            self._asia_lo = lo if self._asia_lo is None else min(self._asia_lo, lo)
            self._asia_hi = hi if self._asia_hi is None else max(self._asia_hi, hi)
            return
        if not (7 <= h < 11):  # торгуем только лондонский killzone
            return
        if self._asia_lo is None or self._traded == d:
            return
        a = self.atr14[-1]
        if not np.isfinite(a) or a <= 0:
            return
        if self.max_asia_range > 0 and \
                (self._asia_hi - self._asia_lo) > self.max_asia_range * a:
            return

        lo, hi = self.data.Low[-1], self.data.High[-1]
        c, o = self.data.Close[-1], self.data.Open[-1]
        if abs(c - o) <= self.disp_mult * a:  # нужна свеча-displacement
            return

        if lo < self._asia_lo and c > self._asia_lo and c > o:
            sl = lo - self.atr_buffer * a
            risk = c - sl
            if risk > 0:
                self._traded = d
                self.buy(sl=sl, tp=c + self.rr * risk, size=0.02)
        elif hi > self._asia_hi and c < self._asia_hi and c < o:
            sl = hi + self.atr_buffer * a
            risk = sl - c
            if risk > 0:
                self._traded = d
                self.sell(sl=sl, tp=c - self.rr * risk, size=0.02)


STRATEGIES = {"pullback": ASAStrategy, "sweep": AsianSweepStrategy}


def run(symbol: str, optimize: bool = False, plot: bool = False,
        source: str = "auto", strategy: str = "sweep", split: str = ""):
    print(f"Загрузка {symbol} (H1)...")
    data = load(symbol, source=source)
    print(f"{len(data)} баров: {data.index[0]} — {data.index[-1]}")

    # Walk-forward: --split 2020-01-01 оптимизирует ДО даты,
    # проверяет ПОСЛЕ. Без out-of-sample проверки результат
    # оптимизации — это почти всегда подгонка под историю.
    train = data[data.index < split] if split else data
    test = data[data.index >= split] if split else None

    strat = STRATEGIES[strategy]
    bt = Backtest(train, strat, cash=10_000, margin=0.02,
                  commission=0.00002, finalize_trades=True)

    if optimize:
        if strategy == "sweep":
            stats = bt.optimize(
                rr=[2.0, 2.5, 3.0, 4.0],
                disp_mult=[0.4, 0.5, 0.6],
                atr_buffer=[0.25, 0.5],
                max_asia_range=[0.0, 1.5],
                maximize="SQN",
            )
        else:
            stats = bt.optimize(
                rr=[2.0, 2.5, 3.0, 4.0],
                htf_ema=[30, 50, 100],
                pullback_ema=[10, 20, 34],
                atr_buffer=[0.25, 0.5, 1.0],
                use_sessions=[True, False],
                maximize="SQN",
            )
        best = stats._strategy._params
        print("\nЛучшие параметры (in-sample!):")
        for k, v in best.items():
            print(f"  {k} = {v}")
    else:
        best = {}
        stats = bt.run()

    def report(title, s):
        print(f"\n{'='*46}\n{title}")
        for key in ["# Trades", "Win Rate [%]", "Return [%]",
                    "Max. Drawdown [%]", "Profit Factor", "Expectancy [%]", "SQN"]:
            if key in s:
                print(f"  {key:26s} {s[key]}")

    report(f"{symbol} [{strategy}] train", stats)

    if test is not None and len(test):
        bt2 = Backtest(test, strat, cash=10_000, margin=0.02,
                       commission=0.00002, finalize_trades=True)
        report(f"{symbol} [{strategy}] OUT-OF-SAMPLE (после {split})",
               bt2.run(**best))

    if plot:
        bt.plot()
    return stats


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="EURUSD")
    p.add_argument("--source", default="auto", choices=["auto", "yfinance", "fxdata"])
    p.add_argument("--strategy", default="sweep", choices=list(STRATEGIES))
    p.add_argument("--split", default="", help="дата walk-forward, напр. 2020-01-01")
    p.add_argument("--optimize", action="store_true")
    p.add_argument("--plot", action="store_true")
    args = p.parse_args()
    run(args.symbol, optimize=args.optimize, plot=args.plot,
        source=args.source, strategy=args.strategy, split=args.split)
