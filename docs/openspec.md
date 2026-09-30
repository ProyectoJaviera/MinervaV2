# Minerva (v1.0) - Bot de Trading con IA

## 1. Visión General del Proyecto
* Nombre del Proyecto: Minerva
* Versión: 1.0 (Primera versión base de una arquitectura evolutiva e iterativa)
* Objetivo: Bot de trading algorítmico modular, asíncrono e impulsado por IA para Futuros Bitunix (BTC/USDT 4H, 10x aislados).
* Cerebro Minerva: Validación de contexto/riesgo (APROBAR/RECHAZAR) y análisis post-mortem tras pérdidas.
* Hoja de Ruta Evolutiva: v1.0 sienta las bases de infraestructura, WebSocket/REST de Bitunix, estrategias de EMAs, gestión de TP/SL/Trailing y validación por LLM.

---

## 2. Arquitectura Técnica y Stack

### 2.1 Selección de Arquitectura: Asíncrona vs. Síncrona
* **Arquitectura Elegida:** **100% Asíncrona (`asyncio`)**
* **Justificación:**
  1. **Aislamiento de Latencia del LLM:** Las peticiones de inferencia a la API del LLM tardan entre 1 y 5+ segundos. Un bucle de eventos asíncrono garantiza que la transmisión de precios, las conexiones WebSocket, el seguimiento de posiciones y el monitoreo de Stop Loss / Take Profit continúen ejecutándose sin bloqueos mientras se espera la respuesta del LLM.
  2. **Manejo Concurrente de Eventos:** Procesamiento no bloqueante de flujos WebSocket (velas K-line / ticker / posiciones), ejecución REST, alertas de Telegram e I/O en SQLite.

### 2.2 Stack Tecnológico
* **Lenguaje:** Python 3.11+
* **Motor Asíncrono:** `asyncio`
* **HTTP y WebSockets:** `httpx`, `websockets`
* **Procesamiento de Datos e Indicadores:** `pandas`, `pandas-ta`
* **Base de Datos:** `aiosqlite` (SQLite Asíncrono)
* **Integración LLM:** SDK Asíncrono (ej. `google-genai` / `openai` / `anthropic`) con validación de salida en formato JSON estructurado.
* **Notificaciones:** `python-telegram-bot` (v20+ asíncrono)
* **Contenedorización:** Docker y Docker Compose

---

## 3. Componentes Principales del Sistema

### Componente 1: Cliente API de Futuros Bitunix (`bitunix_client.py`)
* **Motor de Autenticación:** Generación de firma HMAC SHA-256 utilizando `api-key`, `nonce` (cadena aleatoria de 32 caracteres), `timestamp` (milisegundos) y resumen del payload.
* **Configuración de Apalancamiento y Margen:** Endpoints iniciales para configurar **Apalancamiento 10x** y **Modo de Margen Aislado** (`ISOLATED`).
* **Wrapper REST:** Endpoints para órdenes a mercado en contratos perpetuos (Apertura/Cierre de LONG/SHORT), saldo de cuenta, posiciones activas y velas históricas OHLCV.
* **Motor WebSocket:** Flujo en tiempo real para cierre de velas de 4H, precio marca (Mark Price) y actualizaciones de posición/cuenta para la ejecución precisa de SL/TP.
* **Tolerancia a Fallos:** Lógica de reintentos con retraso exponencial (*exponential backoff*) para manejar límites de tasa (429) y fallos de red.

### Componente 2: Motor de Estrategias (`strategy_engine.py`)
* **Arquitectura Pluggable:** Clase base `BaseStrategy` con interfaz estándar `evaluate(df) -> Signal` que retorna `LONG`, `SHORT`, `CLOSE` o `HOLD`.
* **Estrategia 1 (Cruce EMA 50):**
  * **LONG:** El cierre de la vela cruza por encima de la EMA 50 (cierra SHORT previo si existe).
  * **SHORT:** El cierre de la vela cruza por debajo de la EMA 50 (cierra LONG previo si existe).
* **Estrategia 2 (Cruce EMA 9/21):**
  * **LONG:** La EMA 9 cruza por encima de la EMA 21.
  * **SHORT:** La EMA 9 cruza por debajo de la EMA 21.
* **Modo de Ejecución:** Evalúa al cierre de la vela de 4H por defecto, con bandera configurable para evaluación intra-vela en tiempo real.

### Componente 3: Cerebro LLM y Motor de Decisión (`llm_brain.py`)
* **Rol 1 (Validación de Señal / Pre-Trade):**
  * Evalúa la señal técnica (LONG/SHORT) junto con el contexto del mercado (perfil de volumen, volatilidad reciente, tendencia de 24h, tasa de financiación / funding rate).
  * Retorna un objeto JSON: `{"decision": "APPROVE" | "REJECT", "confidence": float, "reasoning": str}`.
* **Rol 2 (Análisis Post-Mortem / Circuit Breaker):**
  * Se activa tras acumular 3 operaciones perdedoras consecutivas.
  * Recibe el historial de operaciones, precios de entrada/salida, apalancamiento, estado de los indicadores y condiciones del mercado.
  * Genera un reporte de diagnóstico explicando las causas probables del fallo y sugiere ajustes antes de pausar la ejecución del bot.

### Componente 4: Gestor de Riesgo y Ejecución (`risk_execution.py`)
* **Apalancamiento y Tamaño de Posición:** **$10 USDT de Margen Fijo** con **Apalancamiento 10x** (Valor Nocional de la Posición = $100 USDT equivalente en BTC).
* **Modo de Margen:** Margen Aislado (`ISOLATED`).
* **Stop Loss (SL):** Fijo en **-20% de ROI sobre el Margen** (equivalente a un movimiento del -2% en el precio en contra de la posición a 10x).
* **Take Profit Escalonado (TP):**
  * **TP 1 (+20% ROI / +2% mov. precio):** Cierra el 30% de la posición + Activa el Trailing Stop.
  * **TP 2 (+40% ROI / +4% mov. precio):** Cierra el 30% de la posición.
  * **TP 3 (+60% ROI / +6% mov. precio):** Cierra el 40% restante de la posición.
* **Trailing Stop:** Se activa al alcanzar el TP1 (+20% ROI). La distancia de retroceso (*callback*) se fija en un 20% de tolerancia respecto al punto de mayor beneficio alcanzado.
* **Interruptor de Emergencia (Circuit Breaker):** Pausa automática del bot si ocurren 3 pérdidas consecutivas; desencadena el análisis post-mortem del LLM y envía notificación a Telegram.

### Componente 5: Capa de Persistencia (`database.py`)
* **Base de Datos:** SQLite a través de `aiosqlite`.
* **Tablas:**
  * `trades`: ID, símbolo, estrategia, lado (LONG/SHORT), apalancamiento (10x), precio de entrada, margen USDT, tamaño de posición, estado, PnL, fecha de creación, fecha de cierre.
  * `llm_logs`: ID de trade, prompt, respuesta cruda, decisión, razonamiento, marca de tiempo.
  * `system_state`: Estado del bot (RUNNING, PAUSED), pérdidas consecutivas, configuración activa.

### Componente 6: Bot de Telegram y Alertas (`telegram_bot.py`)
* **Notificaciones en Tiempo Real:**
  * Propuesta de Operación (Señal generada + LONG/SHORT + Decisión del LLM).
  * Orden Ejecutada (Precio de entrada, apalancamiento 10x, ejecuciones parciales de TP, activación de SL/Trailing).
  * Activación de Circuit Breaker + Reporte de Diagnóstico Post-Mortem del LLM.
* **Comandos Interactivos:** `/status`, `/balance`, `/positions`, `/pause`, `/resume`, `/metrics`, `/llm_report`.

---

## 4. Hoja de Ruta y Desglose de Tareas (OpenSpec Tasks)

### Fase 1: Configuración del Proyecto y Conexión con Bitunix Futuros
- [ ] **Tarea 1.1:** Crear la estructura del proyecto, analizador de configuración (`config.py` con `pydantic-settings`) y plantilla `.env`.
- [ ] **Tarea 1.2:** Implementar el generador de firmas HMAC SHA-256 y la fábrica de encabezados para la API de Futuros.
- [ ] **Tarea 1.3:** Construir el cliente API REST asíncrono para endpoints de Futuros (Saldo de Contrato, fijar Margen Aislado, fijar Apalancamiento 10x, Órdenes a Mercado de Apertura/Cierre).
- [ ] **Tarea 1.4:** Construir el cliente WebSocket asíncrono para flujos de velas 4H, precio marca y actualizaciones de posición con reconexión automática.

### Fase 2: Pipeline de Datos y Estrategias Técnicas (Futuros)
- [ ] **Tarea 2.1:** Desarrollar `DataEngine` para obtener velas de 4H, convertirlas a DataFrame de Pandas y calcular indicadores técnicos (`pandas-ta`).
- [ ] **Tarea 2.2:** Definir la interfaz abstracta `BaseStrategy` con soporte para señales LONG, SHORT, CLOSE y HOLD.
- [ ] **Tarea 2.3:** Implementar el módulo `EMA50CrossStrategy` (LONG al cruzar hacia arriba, SHORT al cruzar hacia abajo).
- [ ] **Tarea 2.4:** Implementar el módulo `EMA9_21CrossStrategy` (LONG en cruce alcista, SHORT en cruce bajista).
- [ ] **Tarea 2.5:** Agregar bandera de configuración para alternar entre evaluación al cierre de vela vs. evaluación en tiempo real por tick.

### Fase 3: Gestión de Riesgo y Lógica de Ejecución en Futuros
- [ ] **Tarea 3.1:** Crear `PositionManager` para realizar seguimiento del precio de entrada, lado (LONG/SHORT), apalancamiento (10x), margen ($10 USDT base) y estados de ejecución parcial.
- [ ] **Tarea 3.2:** Implementar lógica de ejecución para Stop Loss duro (-20% ROI / -2% de movimiento de precio).
- [ ] **Tarea 3.3:** Implementar el motor de Take Profit Parcial (TP1 30% @ +20% ROI, TP2 30% @ +40% ROI, TP3 40% @ +60% ROI).
- [ ] **Tarea 3.4:** Construir el módulo de Trailing Stop activado automáticamente al alcanzar el TP1 (+20% ROI).
- [ ] **Tarea 3.5:** Implementar la lógica del Circuit Breaker (contador de 3 pérdidas consecutivas y bloqueo de ejecución).

### Fase 4: Integración del Cerebro LLM
- [ ] **Tarea 4.1:** Implementar el cliente asíncrono para la API del LLM forzando esquemas de respuesta JSON estructurados.
- [ ] **Tarea 4.2:** Diseñar la plantilla de prompt Pre-Trade pasando dirección de señal (LONG/SHORT), contexto de apalancamiento 10x, indicadores técnicos y estado del mercado para la validación APROBAR/RECHAZAR.
- [ ] **Tarea 4.3:** Diseñar la plantilla de prompt Post-Mortem para analizar el evento de 3 pérdidas consecutivas en Futuros y generar diagnósticos.
- [ ] **Tarea 4.4:** Conectar el filtro de validación del LLM en el flujo de ejecución (`Señal Técnica -> Validación LLM -> Ejecución de Orden`).

### Fase 5: Persistencia y Bot de Telegram
- [ ] **Tarea 5.1:** Diseñar el esquema de base de datos SQLite adaptado para contratos de Futuros y crear las funciones CRUD asíncronas en `aiosqlite`.
- [ ] **Tarea 5.2:** Construir el servicio de notificaciones asíncronas de Telegram para aperturas (LONG/SHORT), apalancamiento, ejecuciones de TP parciales y alertas de SL.
- [ ] **Tarea 5.3:** Implementar los manejadores de comandos del Bot de Telegram (`/status`, `/balance`, `/positions`, `/pause`, `/resume`, `/llm_report`).

### Fase 6: Dockerización, Pruebas y Despliegue
- [ ] **Tarea 6.1:** Crear `Dockerfile` multi-etapa optimizado y `docker-compose.yml` con mapeo de volumen persistente para la base de datos SQLite.
- [ ] **Tarea 6.2:** Crear simulador de modo Paper Trading / Dry-Run para Futuros utilizando precios reales de Bitunix sin enviar órdenes reales a la API.
- [ ] **Tarea 6.3:** Pruebas de extremo a extremo (E2E) de la ejecución de velas 4H (LONG/SHORT), cierres parciales de TP, activación de Trailing SL y diagnóstico post-mortem del Circuit Breaker por el LLM.
