## Qué cambia y por qué

<!-- El porqué importa más que el qué. Enlaza el issue si existe (Fixes #123). -->

## Cómo lo probaste

<!-- Qué ejecutaste y qué viste. Di también lo que NO pudiste probar. -->

## Lista de comprobación

- [ ] `pytest` en verde
- [ ] `ruff check .` en verde
- [ ] `npm --prefix frontend run test` y `npm --prefix frontend run build` en verde (si tocaste el frontend)
- [ ] Commits pequeños, de una línea, con el estilo de Conventional Commits (`feat:`, `fix:`, `docs:`…)
- [ ] La documentación afectada está actualizada (variables en `docs/CONFIGURACION.md`, comandos, enlaces)
- [ ] No hay claves, tokens, `chat_id`, hashes ni datos de una base real en el código, las pruebas ni los documentos

## Zonas sensibles (marca las que toca el cambio y explica)

- [ ] **No** envía órdenes reales ni activa `BitunixBackend`
- [ ] **No** modifica el prompt congelado (`app/llm/prompts.py`, `app/llm/market_features.py`,
      `FROZEN_PROMPT_SHA256`) ni el modelo por defecto
- [ ] **No** muestra resultados (PnL, saldo, precios de salida, motivos de cierre) sin pasar por
      `is_outcome_reveal_allowed`
- [ ] Si añade una pieza opcional: interruptor propio, apagado por defecto, tarea aislada y pruebas de aislamiento
- [ ] Si toca riesgo o simulación: pruebas con casos límite
