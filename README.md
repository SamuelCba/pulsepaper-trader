# Paper Trader MVP

Proyecto minimo para simular un bot de trading sin dinero real.

Incluye:

- carga de velas desde CSV
- generador de datos de ejemplo
- descarga de velas reales desde Kraken
- estrategias `trend_breakout`, `ema_pullback`, `range_reclaim`, `momentum_scalp` y `hybrid`
- backtest con `stop loss` y `take profit`
- resumen de resultados
- panel live de terminal
- app web local `PulsePaper`
- sesiones live persistidas en SQLite
- comparador live multi-mercado y multi-intervalo
- despliegue listo para Railway

## Estructura

- `main.py`: CLI principal
- `strategy.py`: indicadores y reglas
- `engine.py`: simulador
- `sample_data.py`: generador de datos de ejemplo

## Uso rapido

Generar datos de muestra:

```bash
python main.py generate-sample data/sample_btcusdt_15m.csv
```

Correr backtest:

```bash
python main.py backtest data/sample_btcusdt_15m.csv --strategy hybrid
```

Descargar velas reales desde Kraken:

```bash
python main.py fetch-kraken data/kraken_btcusdt_15m.csv --pair BTC/USDT --interval 15
```

Ver la senal mas reciente:

```bash
python main.py signal data/kraken_btcusdt_15m.csv --strategy trend_breakout
```

Abrir panel de terminal en vivo:

```bash
python main.py watch --pair BTC/USDT --interval 15 --refresh-seconds 30 --strategy hybrid
```

Levantar la web local:

```bash
python main.py serve-web --host 127.0.0.1 --port 8000
```

Luego abrir:

```text
http://127.0.0.1:8000
```

## Despliegue recomendado

### Backend

Usa `Railway`, no `Netlify`, para el backend persistente.

Motivo:

- Railway soporta servicios persistentes de larga duracion
- este proyecto mantiene sesiones live y snapshots
- Netlify encaja mejor para funciones y jobs puntuales, no para este monitor persistente

Archivos de despliegue incluidos:

- `app_server.py`
- `Procfile`
- `railway.json`

En Railway, el servicio arranca con:

```bash
python app_server.py
```

Usa las variables de entorno:

- `HOST` por defecto `0.0.0.0`
- `PORT` por defecto `8000`

Que hace `watch`:

- descarga velas reales de Kraken en cada ciclo
- muestra el mercado actual y el timeframe
- enseña las ultimas velas con mini chart y barras de volumen
- calcula si hay senal de entrada
- simula posicion y trades historicos sobre esas velas

## Estrategias

- `trend_breakout`: busca continuacion de tendencia con breakout y filtro ATR
- `ema_pullback`: espera retroceso a EMA y recuperacion
- `range_reclaim`: busca barrida de rango y recuperacion
- `momentum_scalp`: busca empuje rapido con volumen
- `hybrid`: combina scalp, pullback, breakout y reclaim

## Mercados e intervalos live

Mercados incluidos en el comparador live:

- `BTC/USDT`
- `ETH/USDT`
- `SOL/USDT`
- `XRP/USDT`
- `ADA/USDT`

Intervalos live:

- `1m`
- `5m`
- `15m`
- `30m`
- `1h`

## Opciones utiles

```bash
python main.py watch --pair ETH/USDT --interval 15 --strategy trend_breakout
python main.py watch --pair XRP/USDT --interval 5 --strategy momentum_scalp
python main.py backtest data/kraken_btcusdt_15m.csv --starting-balance 10 --risk-per-trade-pct 0.10 --strategy range_reclaim
```

## Formato CSV

Columnas requeridas:

- `timestamp`
- `open`
- `high`
- `low`
- `close`
- `volume`

`timestamp` puede ser ISO-8601 o epoch en segundos.
