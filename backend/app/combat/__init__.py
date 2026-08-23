"""战斗核心（W2）：balance 系数 → stats/hit/damage/skills/potion 五模块（todo 9–12）。

数值纪律：本包内任何模块禁止本地定义数值系数，一律从 ``app.combat.balance``
导入（tests/test_balance.py 有 grep 防回归断言）。
"""
