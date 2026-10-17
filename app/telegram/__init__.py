"""Bot de Telegram por long-polling (subfase 3.7, docs/FASE3_PLAN.md punto 5).

Telegram solo INFORMA y CONTROLA el proceso (pausa/reanudar/kill switch): nunca
decide una entrada o salida, y nunca muestra PnL ni resultados de las sombras
mientras dure la medicion (ver `commands.py`). Un fallo de este paquete (red,
token, excepcion) no puede tumbar ni frenar el scheduler, el etiquetado ni el
monitor de posiciones: ver `bot.py`."""
