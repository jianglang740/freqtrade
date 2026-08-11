import pandas as pd
from unittest.mock import MagicMock
from freqtrade.data.dataprovider import DataProvider
from freqtrade.misc import remove_entry_exit_signals

# 模拟：缓存存的是对象引用，remove_entry_exit_signals 原地修改
cached = pd.DataFrame({'date':[1,2,3],'close':[10,11,12],'enter_long':[0,0,1],'enter_short':[0,0,0]})
print("缓存对象 id:", id(cached))

# 模拟 ohlcv(copy=True) 返回副本
import copy
new_df = cached.copy()
print("新副本 id:", id(new_df), " 共享吗:", new_df is cached)

# else 分支：对副本做 remove_entry_exit_signals
new_df = remove_entry_exit_signals(new_df)
print("副本 enter_long 处理后:", new_df['enter_long'].tolist())
print("缓存 enter_long 受影响吗:", cached['enter_long'].tolist())

# 关键测试：如果 remove_entry_exit_signals 直接作用于缓存对象
cached2 = pd.DataFrame({'date':[1,2,3],'close':[10,11,12],'enter_long':[0,0,1],'enter_short':[0,0,0]})
remove_entry_exit_signals(cached2)
print("直接作用于缓存对象 enter_long:", cached2['enter_long'].tolist())
