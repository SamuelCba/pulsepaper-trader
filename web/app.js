const state = {
  refreshTimer: null,
  countdownTimer: null,
  remaining: 0,
  latestStrategy: "hybrid",
  currentPayload: null,
  selectedTradeIndex: null,
  liveResetRequested: false,
  livePayload: null,
};

const $ = (id) => document.getElementById(id);

function fmtPrice(value) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value ?? 0);
}

function fmtPct(value) {
  return `${(value ?? 0).toFixed(2)}%`;
}

function shortTs(timestamp) {
  return timestamp.replace("T", " ").split("+")[0].slice(-5);
}

function metricRow(label, value, cls = "") {
  return `<div class="metric-row"><div class="k">${label}</div><div class="v ${cls}">${value}</div></div>`;
}

function strategyExplanation(mode) {
  if (mode === "trend_breakout") {
    return [
      metricRow("Modo", "trend_breakout"),
      metricRow("Busca", "Tendencia alcista y ruptura"),
      metricRow("Usa", "EMA 20, 50, 200 + RSI + ATR"),
      metricRow("Entrada", "Cuando rompe maximo reciente con contexto"),
    ].join("");
  }
  if (mode === "ema_pullback") {
    return [
      metricRow("Modo", "ema_pullback"),
      metricRow("Busca", "Retroceso y rebote"),
      metricRow("Usa", "EMA + RSI + confirmacion"),
      metricRow("Entrada", "Cuando recupera fuerza despues del pullback"),
    ].join("");
  }
  if (mode === "range_reclaim") {
    return [
      metricRow("Modo", "range_reclaim"),
      metricRow("Busca", "Barrida y recuperacion de rango"),
      metricRow("Usa", "Breakout low + ATR + RSI"),
      metricRow("Entrada", "Cuando barre abajo y recupera"),
    ].join("");
  }
  if (mode === "momentum_scalp") {
    return [
      metricRow("Modo", "momentum_scalp"),
      metricRow("Busca", "Impulso rapido"),
      metricRow("Usa", "EMA + RSI + volumen"),
      metricRow("Entrada", "Cuando rompe la vela previa con fuerza"),
    ].join("");
  }
  return [
    metricRow("Modo", "hybrid"),
    metricRow("Primero", "Prueba scalp, pullback y breakout"),
    metricRow("Luego", "Si no hay, intenta reclaim"),
    metricRow("Idea", "Mas cobertura sin operar por ruido"),
  ].join("");
}

function tradePnlClass(value) {
  if (value > 0) return "pnl-up";
  if (value < 0) return "pnl-down";
  return "pnl-flat";
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(text || "Respuesta invalida del servidor. Reinicia la web.");
  }
  if (!response.ok) {
    throw new Error(payload.error || "Error del servidor");
  }
  return payload;
}

function setBadge(text, cls) {
  $("liveBadge").textContent = text;
  $("liveBadge").className = `badge ${cls}`;
}

function renderChart(candles) {
  $("chart").dataset.raw = JSON.stringify(candles);
  const compact = window.innerWidth < 720;
  const visibleCandles = compact ? candles.slice(-18) : candles;
  $("chart").classList.toggle("compact", compact);
  const maxHigh = Math.max(...visibleCandles.map((c) => c.high));
  const minLow = Math.min(...visibleCandles.map((c) => c.low));
  const maxVolume = Math.max(...visibleCandles.map((c) => c.volume), 1);
  const span = Math.max(maxHigh - minLow, 1);

  $("chartMeta").textContent = compact
    ? "Vista movil: ultimas 18 velas"
    : `Vista escritorio: ultimas ${visibleCandles.length} velas`;

  $("chart").innerHTML = visibleCandles.map((candle) => {
    const wickHeight = Math.max(18, ((candle.high - candle.low) / span) * 180);
    const bodyHeight = Math.max(10, (Math.abs(candle.close - candle.open) / span) * 180);
    const volumeHeight = Math.max(6, (candle.volume / maxVolume) * 48);
    const bodyClass = candle.close >= candle.open ? "up" : "down";
    return `
      <div class="candle">
        <div class="wick" style="height:${wickHeight}px"></div>
        <div class="body ${bodyClass}" style="height:${bodyHeight}px"></div>
        <div class="volume" style="height:${volumeHeight}px"></div>
        <div class="candle-time">${shortTs(candle.timestamp)}</div>
      </div>
    `;
  }).join("");
}

function renderScan(rows) {
  $("scanBody").innerHTML = rows.map((row, index) => `
    <tr class="clickable-row" data-scan-index="${index}">
      <td>${row.pair}</td>
      <td>${row.interval}m</td>
      <td>${fmtPrice(row.last_close)}</td>
      <td class="${tradePnlClass(row.total_pnl)}">${row.total_pnl.toFixed(2)}</td>
      <td>${row.trades}</td>
      <td>${fmtPct(row.win_rate)}</td>
      <td>${fmtPct(row.max_drawdown_pct)}</td>
      <td>${row.signal ? row.signal.toUpperCase() : "Sin senal"}</td>
    </tr>
  `).join("");

  $("scanBody").querySelectorAll("tr[data-scan-index]").forEach((rowEl) => {
    rowEl.addEventListener("click", () => {
      const row = rows[Number(rowEl.dataset.scanIndex)];
      $("pairSelect").value = row.pair;
      $("intervalSelect").value = String(row.interval);
      state.liveResetRequested = true;
      state.selectedTradeIndex = null;
      loadStatus();
      scheduleRefresh();
    });
  });
}

function renderIndicators(indicators) {
  $("indicatorsGrid").innerHTML = [
    metricRow("EMA 20", fmtPrice(indicators.ema20)),
    metricRow("EMA 50", fmtPrice(indicators.ema50)),
    metricRow("EMA 200", fmtPrice(indicators.ema200)),
    metricRow("RSI 14", (indicators.rsi14 ?? 0).toFixed(2)),
    metricRow("ATR 14", fmtPrice(indicators.atr14)),
    metricRow("Breakout 20", fmtPrice(indicators.breakout20)),
    metricRow("Vol avg 20", (indicators.volume_avg20 ?? 0).toFixed(4)),
  ].join("");
}

function renderPosition(position) {
  if (!position) {
    $("positionPanel").innerHTML = metricRow("Estado", "No hay posicion simulada abierta", "pnl-flat");
    return;
  }
  const pnlCls = tradePnlClass(position.unrealized_pnl_pct);
  $("positionPanel").innerHTML = [
    metricRow("Estado", "Long abierta", "pnl-up"),
    metricRow("Hora entrada", position.entry_time),
    metricRow("Entrada", fmtPrice(position.entry)),
    metricRow("Precio actual", fmtPrice(position.current_price)),
    metricRow("PnL", fmtPct(position.unrealized_pnl_pct), pnlCls),
    metricRow("Stop", fmtPrice(position.stop_loss)),
    metricRow("Objetivo", fmtPrice(position.take_profit)),
    metricRow("Motivo", position.reason),
  ].join("");
}

function renderHistoricalPanel(payload) {
  $("historicalPanel").innerHTML = [
    metricRow("Modo", "Referencia historica"),
    metricRow("Que hace", "Recorre velas pasadas y reconstruye trades"),
    metricRow("Balance final", fmtPrice(payload.summary.ending_balance), tradePnlClass(payload.summary.total_pnl)),
    metricRow("PnL", payload.summary.total_pnl.toFixed(2), tradePnlClass(payload.summary.total_pnl)),
    metricRow("Trades", `${payload.summary.total_trades}`),
    metricRow("Win rate", fmtPct(payload.summary.win_rate)),
    metricRow("DD max", fmtPct(payload.summary.max_drawdown_pct)),
  ].join("");
}

function renderLiveSessionPanel(payload) {
  if (!payload) {
    $("liveSessionPanel").innerHTML = metricRow("Estado", "Cargando sesion live");
    return;
  }
  $("liveSessionPanel").innerHTML = [
    metricRow("Modo", "Paper live desde ahora"),
    metricRow("Inicio de sesion", payload.session.started_at_candle),
    metricRow("Balance final", fmtPrice(payload.summary.ending_balance), tradePnlClass(payload.summary.total_pnl)),
    metricRow("PnL live", payload.summary.total_pnl.toFixed(2), tradePnlClass(payload.summary.total_pnl)),
    metricRow("Trades live", `${payload.summary.total_trades}`),
    metricRow("Win rate live", fmtPct(payload.summary.win_rate)),
    metricRow("Senal actual", payload.signal ? payload.signal.side.toUpperCase() : "Sin senal"),
    metricRow("Posicion abierta", payload.position ? "Si" : "No"),
  ].join("");
}

function renderRuntime(payload) {
  $("runtimePanel").innerHTML = [
    metricRow("Mercado activo", `${payload.market.requested_pair} en ${payload.market.interval}m`),
    metricRow("Opera ahora", "Solo el mercado seleccionado en pantalla"),
    metricRow("Mercados disponibles", "BTC, ETH, SOL, XRP y ADA"),
    metricRow("PnL guardado", "No persiste en base de datos; se recalcula con las velas descargadas"),
    metricRow("Balance inicial", fmtPrice(payload.summary.starting_balance)),
    metricRow("Riesgo por trade", `${payload.summary.risk_per_trade_pct.toFixed(2)}%`),
    metricRow("Riesgo aprox", fmtPrice(payload.summary.risk_amount_estimate)),
    metricRow("Trades cerrados", `${payload.summary.total_trades}`),
  ].join("");
}

function renderTradeFocus(payload, trade) {
  if (!trade) {
    $("tradeFocusInfo").innerHTML = metricRow("Estado", "No hay trade seleccionado");
    $("tradeFocusChart").innerHTML = "";
    return;
  }

  const candles = payload.all_candles || [];
  const entryIndex = candles.findIndex((c) => c.timestamp === trade.entry_time);
  const exitIndex = candles.findIndex((c) => c.timestamp === trade.exit_time);
  const start = Math.max(0, (entryIndex === -1 ? 0 : entryIndex) - 6);
  const end = Math.min(candles.length, (exitIndex === -1 ? candles.length : exitIndex + 7));
  const window = candles.slice(start, end);

  $("tradeFocusInfo").innerHTML = [
    metricRow("Entrada", trade.entry_time),
    metricRow("Salida", trade.exit_time),
    metricRow("Precio entrada", fmtPrice(trade.entry)),
    metricRow("Precio salida", fmtPrice(trade.exit)),
    metricRow("PnL", trade.pnl.toFixed(2), tradePnlClass(trade.pnl)),
    metricRow("Motivo", trade.reason),
    metricRow("Que paso", "Entro en la vela de entrada y se mantuvo abierta hasta que otra vela toco el stop o el objetivo"),
  ].join("");

  if (!window.length) {
    $("tradeFocusChart").innerHTML = "";
    return;
  }

  const maxHigh = Math.max(...window.map((c) => c.high));
  const minLow = Math.min(...window.map((c) => c.low));
  const maxVolume = Math.max(...window.map((c) => c.volume), 1);
  const span = Math.max(maxHigh - minLow, 1);
  const lineTop = (price) => `${100 - (((price - minLow) / span) * 100)}%`;

  $("tradeFocusChart").innerHTML = window.map((candle) => {
    const wickHeight = Math.max(18, ((candle.high - candle.low) / span) * 150);
    const bodyHeight = Math.max(10, (Math.abs(candle.close - candle.open) / span) * 150);
    const volumeHeight = Math.max(6, (candle.volume / maxVolume) * 42);
    const bodyClass = candle.close >= candle.open ? "up" : "down";
    const marker =
      candle.timestamp === trade.entry_time
        ? '<div class="marker entry">ENT</div>'
        : candle.timestamp === trade.exit_time
          ? '<div class="marker exit">SAL</div>'
          : '<div class="marker empty">-</div>';
    return `
      <div class="focus-candle">
        ${marker}
        <div class="focus-area">
          <div class="price-line entry-line" style="top:${lineTop(trade.entry)}"></div>
          <div class="price-line stop-line" style="top:${lineTop(trade.stop_loss)}"></div>
          <div class="price-line target-line" style="top:${lineTop(trade.take_profit)}"></div>
          <div class="wick" style="height:${wickHeight}px"></div>
          <div class="body ${bodyClass}" style="height:${bodyHeight}px"></div>
        </div>
        <div class="volume" style="height:${volumeHeight}px"></div>
        <div class="candle-time">${shortTs(candle.timestamp)}</div>
      </div>
    `;
  }).join("");
}

function renderTrades(trades) {
  if (!trades.length) {
    $("tradesBody").innerHTML = `<tr><td colspan="6">Todavia no hay trades simulados cerrados.</td></tr>`;
    $("tradeFocusInfo").innerHTML = metricRow("Estado", "No hay trade seleccionado");
    $("tradeFocusChart").innerHTML = "";
    return;
  }
  const ordered = trades.slice().reverse();
  if (state.selectedTradeIndex === null || state.selectedTradeIndex >= ordered.length) {
    state.selectedTradeIndex = 0;
  }
  $("tradesBody").innerHTML = ordered.map((trade, index) => `
    <tr data-trade-index="${index}" class="${state.selectedTradeIndex === index ? "selected-row" : ""}">
      <td>${trade.entry_time}</td>
      <td>${trade.exit_time}</td>
      <td>${fmtPrice(trade.entry)}</td>
      <td>${fmtPrice(trade.exit)}</td>
      <td class="${tradePnlClass(trade.pnl)}">${trade.pnl.toFixed(2)}</td>
      <td>${trade.reason}</td>
    </tr>
  `).join("");

  $("tradesBody").querySelectorAll("tr[data-trade-index]").forEach((row) => {
    row.addEventListener("click", () => {
      state.selectedTradeIndex = Number(row.dataset.tradeIndex);
      renderTrades(state.currentPayload.recent_trades);
    });
  });
  renderTradeFocus(state.currentPayload, ordered[state.selectedTradeIndex] || ordered[0]);
}

function updateCountdown() {
  $("refreshText").textContent = `Siguiente refresh en ${state.remaining}s`;
  state.remaining = Math.max(0, state.remaining - 1);
}

function readControls() {
  return {
    pair: $("pairSelect").value,
    interval: Number($("intervalSelect").value),
    strategy: $("strategySelect").value,
    refresh: Number($("refreshSelect").value),
    startingBalance: Number($("balanceInput").value || 10),
    riskPct: Number($("riskInput").value || 10) / 100,
  };
}

async function loadScan() {
  try {
    const controls = readControls();
    const resetFlag = state.liveResetRequested ? "&reset=1" : "";
    const url = `/api/live-scan?strategy=${encodeURIComponent(state.latestStrategy)}&starting_balance=${controls.startingBalance}&risk_per_trade_pct=${controls.riskPct}${resetFlag}`;
    const payload = await fetchJson(url);
    renderScan(payload.rows);
  } catch (error) {
    $("scanBody").innerHTML = `<tr><td colspan="8">${error.message}</td></tr>`;
  }
}

async function loadLiveStatus() {
  const controls = readControls();
  const resetFlag = state.liveResetRequested ? "&reset=1" : "";
  const url = `/api/live-status?pair=${encodeURIComponent(controls.pair)}&interval=${controls.interval}&strategy=${controls.strategy}&starting_balance=${controls.startingBalance}&risk_per_trade_pct=${controls.riskPct}${resetFlag}`;
  const payload = await fetchJson(url);
  state.liveResetRequested = false;
  state.livePayload = payload;
  renderLiveSessionPanel(payload);
}

async function loadStatus() {
  const controls = readControls();
  setBadge("Cargando", "neutral");
  try {
    const url = `/api/status?pair=${encodeURIComponent(controls.pair)}&interval=${controls.interval}&strategy=${controls.strategy}&starting_balance=${controls.startingBalance}&risk_per_trade_pct=${controls.riskPct}`;
    const payload = await fetchJson(url);

    const livePayload = await (async () => {
      const resetFlag = state.liveResetRequested ? "&reset=1" : "";
      const liveUrl = `/api/live-status?pair=${encodeURIComponent(controls.pair)}&interval=${controls.interval}&strategy=${controls.strategy}&starting_balance=${controls.startingBalance}&risk_per_trade_pct=${controls.riskPct}${resetFlag}`;
      return fetchJson(liveUrl);
    })();
    state.liveResetRequested = false;
    state.livePayload = livePayload;

    setBadge("En vivo", "live");
    state.latestStrategy = payload.strategy.mode;
    state.currentPayload = livePayload;
    $("marketLabel").textContent = `${payload.app_name} · ${livePayload.market.requested_pair}`;
    $("lastPrice").textContent = fmtPrice(livePayload.last_candle.close);

    const priceDelta = livePayload.last_candle.close - livePayload.last_candle.open;
    const deltaCls = priceDelta > 0 ? "up" : priceDelta < 0 ? "down" : "neutral";
    $("priceDelta").className = `delta ${deltaCls}`;
    $("priceDelta").textContent = `${priceDelta >= 0 ? "+" : ""}${priceDelta.toFixed(2)}`;

    $("marketMeta").innerHTML = [
      `<div>Mercado pedido: ${livePayload.market.requested_pair}</div>`,
      `<div>Mercado real Kraken: ${livePayload.market.pair}</div>`,
      `<div>Intervalo: ${livePayload.market.interval}m</div>`,
      `<div>Fuente: ${livePayload.market.source}</div>`,
      `<div>Velas live desde: ${livePayload.session.started_at_candle}</div>`,
      `<div>Ultima vela: ${livePayload.last_candle.timestamp}</div>`,
      `<div>Volumen: ${livePayload.last_candle.volume.toFixed(6)}</div>`,
    ].join("");

    if (!livePayload.signal) {
      $("signalBadge").className = "signal-badge neutral";
      $("signalBadge").textContent = "Sin senal";
      $("signalDetails").innerHTML = metricRow("Modo", payload.strategy.mode);
    } else {
      $("signalBadge").className = "signal-badge long";
      $("signalBadge").textContent = livePayload.signal.side.toUpperCase();
      $("signalDetails").innerHTML = [
        metricRow("Modo", payload.strategy.mode),
        metricRow("Entrada", fmtPrice(livePayload.signal.entry)),
        metricRow("Stop", fmtPrice(livePayload.signal.stop_loss)),
        metricRow("Objetivo", fmtPrice(livePayload.signal.take_profit)),
        metricRow("Motivo", livePayload.signal.reason),
      ].join("");
    }

    $("balanceValue").textContent = fmtPrice(livePayload.summary.ending_balance);
    $("pnlValue").textContent = livePayload.summary.total_pnl.toFixed(2);
    $("pnlValue").className = `value ${tradePnlClass(livePayload.summary.total_pnl)}`;
    $("winRateValue").textContent = fmtPct(livePayload.summary.win_rate);
    $("drawdownValue").textContent = fmtPct(livePayload.summary.max_drawdown_pct);

    renderIndicators(livePayload.indicators);
    renderPosition(livePayload.position);
    renderRuntime(livePayload);
    renderHistoricalPanel(payload);
    $("explainPanel").innerHTML = strategyExplanation(payload.strategy.mode);
    renderTrades(livePayload.recent_trades);
    renderChart(livePayload.candles);
    renderLiveSessionPanel(livePayload);

    await loadScan();

    state.remaining = controls.refresh;
    clearInterval(state.countdownTimer);
    updateCountdown();
    state.countdownTimer = setInterval(updateCountdown, 1000);
  } catch (error) {
    setBadge("Error", "error");
    $("refreshText").textContent = error.message;
  }
}

function scheduleRefresh() {
  clearInterval(state.refreshTimer);
  const { refresh } = readControls();
  state.refreshTimer = setInterval(loadStatus, refresh * 1000);
}

function bindEvents() {
  ["pairSelect", "intervalSelect", "strategySelect", "refreshSelect"].forEach((id) => {
    $(id).addEventListener("change", () => {
      state.liveResetRequested = true;
      state.selectedTradeIndex = null;
      loadStatus();
      scheduleRefresh();
    });
  });
  ["balanceInput", "riskInput"].forEach((id) => {
    $(id).addEventListener("change", () => {
      state.liveResetRequested = true;
      loadStatus();
      scheduleRefresh();
    });
  });
  $("refreshNowBtn").addEventListener("click", loadStatus);
  $("resetLiveBtn").addEventListener("click", () => {
    state.liveResetRequested = true;
    loadStatus();
  });
}

bindEvents();
loadStatus();
scheduleRefresh();
window.addEventListener("resize", () => {
  const currentCandles = document.getElementById("chart").dataset.raw;
  if (currentCandles) {
    renderChart(JSON.parse(currentCandles));
  }
});
