"""Backtest historico de la Fase 2: motor, metricas, veredicto por estrategia y analisis de riesgo.

Es investigacion offline: lee velas y funding del cache local, no usa la red ni escribe
la cuenta paper ni las sombras. Sus resultados (`backtest_*`) no alimentan la medicion
del LLM; solo justifican con que estrategias se parte. Se corre con `scripts/run_backtest.py`.
"""

