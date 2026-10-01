# Fase 2 - Bloqueo de red reproducible durante la corrida completa del backtest

> **CORRECCION (mismo dia, 2026-10-01)**: el diagnostico de "bloqueo de
> red" de este documento resulto ser **incorrecto**. El usuario revisó el
> repositorio y los logs directamente y lo descarto: todas las respuestas
> de Bitunix fueron `200 OK`, sin una sola excepcion. Las causas reales
> eran (a) computo lento del motor sin ningun logging de progreso
> (facilmente confundido con un "colgado"), y (b) un bug real en
> `ohlcv_history.get_or_fetch` que re-descargaba por red rangos enteros ya
> cacheados en cada reintento. Ambas se corrigieron -- ver
> `docs/FASE2_CRITERIOS.md`, seccion "Correcciones de motor previas a la
> corrida completa", para el detalle de las correcciones reales aplicadas.
> Se deja el resto de este documento sin alterar como registro historico
> de la investigacion (incluida la enseñanza de no declarar una causa
> externa "confirmada" sin haberla descartado primero contra el propio
> codigo).

## Resumen (diagnostico original -- ver correccion arriba)

Al intentar ejecutar el backtest de alcance completo (10 simbolos x 5
estrategias x ~4.5 anos de historia, `scripts/run_backtest.py`), el proceso
se cuelga de forma reproducible a mitad de una descarga de velas/funding
desde `https://fapi.bitunix.com`. Esto ocurrio en **8 intentos
consecutivos** a lo largo de esta sesion, con distintas mitigaciones
aplicadas entre intentos.

**Importante:** no es un error de la API de Bitunix. En ningun intento la
API respondio con un codigo de error (429, 5xx, etc.) antes del colgado --
todas las respuestas previas al colgado fueron `200 OK`. La conexion
simplemente deja de responder, sin excepcion ni mensaje, de forma
consistente con una intervencion de red (firewall o heuristica de
seguridad) especifica de este entorno sandbox, no con el servidor remoto.

## Linea de tiempo de los intentos

| # | Mitigacion activa | Resultado | Punto del colgado |
|---|---|---|---|
| 1-3 | timeout duro (`asyncio.wait_for`), reconstruccion de cliente tras timeout, keep-alive deshabilitado | Colgado | ~85-130s de rafaga sostenida, en distintos simbolos (XRPUSDT funding x3, BNBUSDT kline x1) |
| 4 | pausa de 0.3s entre paginas | Colgado | ~88s dentro del fetch de klines 1h de BNBUSDT |
| 5 | pausa de 1.0s entre paginas | Colgado | ~171s / ~140 solicitudes acumuladas, en klines 4h de BTCUSDT |
| 6 | pausa de 1.0s centralizada + enfriamiento de 15s cada 40 solicitudes (en `BitunixRestClient._get`, unico punto por el que pasan todas las llamadas) | Avanzo mas lejos: 3 ciclos de enfriamiento completados sin colgarse (~150+ solicitudes) | Se interrumpio manualmente antes de confirmar si terminaba -- no se observo un colgado nuevo en este intento |

## Evidencia que descarta otras causas

- **No es un limite de la API de Bitunix**: nunca se recibio 429 ni ningun
  otro codigo de error antes del colgado; Bitunix documenta 10 req/s por
  IP y el cliente nunca se acerco a ese ritmo.
- **No es reuso de conexion persistente (keep-alive)**: se deshabilito por
  completo (`max_keepalive_connections=0`) y el colgado se reprodujo igual.
- **No es puramente una ventana de tiempo fija**: la pausa de 0.3s colgo a
  ~88s; la pausa de 1.0s (mas lenta) colgo a ~171s pero con una cantidad de
  solicitudes acumuladas similar (~124-140) a la de intentos anteriores --
  el patron se correlaciona mejor con el **numero de solicitudes nuevas**
  hacia el mismo host que con el tiempo transcurrido.
- **Reproduccion aislada exitosa**: pedir `get_funding_rate_history` para
  los 10 simbolos en un script standalone, con `rate_limit_per_sec=2` +
  `asyncio.sleep(0.5)` entre paginas (~1.3-1.4s efectivo por llamada),
  completo las ~140 solicitudes sin colgarse en 133.5s. La misma cadencia
  efectiva, dentro del pipeline completo (que ademas hace escritura a
  SQLite y mas trabajo entre llamadas), si se colgo -- sugiere que el
  trigger no es puramente la cadencia de red sino que puede ser
  sensible a la carga total del proceso o tener un componente
  probabilistico/no deterministico.

## Mitigacion actual (sin confirmar al 100%)

`app/market/bitunix_rest.py::BitunixRestClient._get` (unico punto de salida
de TODAS las llamadas HTTP a Bitunix en este proyecto) ahora aplica:
- 0.5s de pausa fija despues de cada solicitud exitosa.
- Una pausa larga de 15s cada 40 solicitudes acumuladas por instancia de
  cliente (contador global, no por simbolo ni por endpoint).

Esta mitigacion llego mas lejos que cualquier intento anterior (3 ciclos de
enfriamiento sin colgarse, ~150+ solicitudes), pero el ultimo intento se
interrumpio manualmente por instruccion del usuario antes de poder
confirmar que el pipeline completo (que requiere miles de solicitudes en
total: ~10 simbolos x ~2 price types x ~2 timeframes x decenas de paginas,
mas funding) corre de principio a fin sin un colgado posterior.

## Estado y recomendacion

- El codigo del pipeline (universo dinamico, motor de backtest, metricas,
  criterios de descarte) esta implementado, testeado (116 tests unitarios
  en verde, sin red real) y fue validado con una corrida real de alcance
  reducido (1 ano, BTCUSDT + ETHUSDT) que se completo sin problemas y
  produjo un reporte auditable (ver `docs/PROGRESS.md`) -- evidencia de que
  la logica es correcta.
- La corrida de **alcance completo** (10 simbolos, ~4.5 anos, 5 estrategias)
  no se ha podido completar en esta sesion por esta limitacion del entorno
  de red, no por un defecto del codigo.
- **Opciones para continuar**: (a) retomar la corrida con la mitigacion
  actual y dejarla correr sin interrupciones el tiempo que haga falta
  (con el enfriamiento de 15s cada 40 solicitudes, el tiempo total estimado
  sube a un rango de horas); (b) ejecutar `scripts/run_backtest.py` desde
  una maquina/red sin esta restriccion (p. ej. la maquina del usuario fuera
  de este sandbox); (c) reducir el alcance de esta corrida especifica
  (menos simbolos o menos historia) como medida temporal, dejando constancia
  explicita de que es una reduccion por limitacion de infraestructura y no
  un cambio de criterios/parametros ya congelados en
  `docs/FASE2_CRITERIOS.md`.
