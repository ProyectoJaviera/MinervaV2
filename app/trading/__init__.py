"""Nucleo operativo: senales, riesgo, sombras, monitor de posiciones y medicion del LLM.

Es el paquete mas grande y el que contiene casi todas las zonas protegidas por la
medicion (ver `docs/dev/ZONAS_PROTEGIDAS.md`). Flujo: `signal_generator` evalua las
estrategias y agrupa senales; `shadow_book` abre la sombra de cada senal;
`shadow_live_labeler` pide al LLM su etiqueta; `paper_execution` abre en la cuenta paper
las APROBADA a tiempo a traves de `risk_engine`; `position_monitor` cierra todo por SL,
TP, trailing o liquidacion; `measurement_status`, `ai_value*` y `verdict_record`
miden y deciden el veredicto sin mirar resultados antes de tiempo.
"""

