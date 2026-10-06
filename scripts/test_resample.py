import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from strategies.swing_momentum import resample_1m_to_15m, _ema, _adx

def test_resample_1m_to_15m():
    # 60 1m candles -> 4 15m candles
    closes = [100.0 + i for i in range(60)]
    highs = [101.0 + i for i in range(60)]
    lows = [99.0 + i for i in range(60)]
    volumes = [10.0 for _ in range(60)]

    c15, h15, l15, v15 = resample_1m_to_15m(closes, highs, lows, volumes, bar_size=15)
    assert len(c15) == 4
    assert len(h15) == 4
    assert len(l15) == 4
    assert len(v15) == 4
    assert c15[0] == closes[14]
    assert h15[0] == max(highs[:15])
    assert l15[0] == min(lows[:15])
    assert v15[0] == 150.0

print("Test passed successfully!")
