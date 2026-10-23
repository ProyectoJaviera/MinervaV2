---
name: Error (algo no funciona)
about: Algo se rompe o se comporta distinto de lo que dice la documentación
title: "[error] "
labels: bug
---

> **Antes de escribir:** no pegues nunca tu `.env`, claves, tokens, `chat_id`, hashes de contraseña ni capturas que
> los muestren. Tampoco pegues resultados de tu medición (PnL, saldo, motivos de cierre): sesgan el veredicto. Si lo
> que encontraste es una **vulnerabilidad**, no abras un issue público: lee `docs/SEGURIDAD.md`.

## Qué pasó

<!-- Describe el problema en pocas líneas. -->

## Qué esperabas que pasara

## Cómo reproducirlo

1.
2.
3.

## Entorno

- Cómo lo corres: <!-- Docker (docker compose) / nativo en Windows con start_bot.ps1 / otro -->
- Sistema operativo y versión:
- Versión de Docker (`docker --version`) o de Python (`python --version`):
- Commit del repositorio (`git log -1 --oneline`):
- Resultado de `GET http://127.0.0.1:8000/health` (si el bot responde; ese JSON no lleva credenciales ni resultados):

## Registros relevantes

<!-- Pega solo las líneas pertinentes de `docker compose logs --tail 100 minerva` o de logs/minerva.log, revisadas
     para quitar cualquier dato personal. -->

```text

```

## ¿Qué ya probaste?

<!-- Por ejemplo, lo que dice docs/SOLUCION_PROBLEMAS.md. -->
