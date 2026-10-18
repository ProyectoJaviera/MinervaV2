"""Decision del LLM sobre las senales agrupadas (subfase 3.6).

Ver docs/FASE3_6_LLM.md. `ShadowLiveLabeler` (`app/trading/shadow_live_labeler.py`) llama a
`LlmDecisionService` desde el ciclo del scheduler para etiquetar cada sombra `APROBADA` o
`RECHAZADA` con el prompt congelado (`prompts.py`); el interruptor es `LLM_LIVE_LABELING_ENABLED`.
`prompts_v5.py` esta preparado y SIN usar.
"""
