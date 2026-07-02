// Premium Toast Notification System
function showToast(message, type = 'buy') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = message;
    
    container.appendChild(toast);
    
    // Trigger reflow to enable animation
    void toast.offsetWidth;
    toast.classList.add('show');
    
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

document.addEventListener('DOMContentLoaded', () => {
    fetchData();
    initSearch();
});

let debounceTimer;
function initSearch() {
    const searchInput = document.getElementById('contract-search');
    const resultsContainer = document.getElementById('autocomplete-results');
    
    if (!searchInput) return;
    
    searchInput.addEventListener('input', (e) => {
        clearTimeout(debounceTimer);
        const query = e.target.value.trim();
        
        if (query.length < 3) {
            resultsContainer.style.display = 'none';
            return;
        }
        
        debounceTimer = setTimeout(async () => {
            try {
                const res = await fetch(`/api/search?q=${query}`);
                const results = await res.json();
                
                resultsContainer.innerHTML = '';
                if (results.length > 0) {
                    results.forEach(contract => {
                        const div = document.createElement('div');
                        div.style.padding = '8px 12px';
                        div.style.cursor = 'pointer';
                        div.style.borderBottom = '1px solid var(--border-color)';
                        div.style.fontSize = '12px';
                        div.innerHTML = `<strong>${contract.symbol}</strong> <span style="color:#888; float:right;">${contract.instrumenttype}</span>`;
                        
                        div.addEventListener('mouseover', () => div.style.background = 'rgba(255,255,255,0.1)');
                        div.addEventListener('mouseout', () => div.style.background = 'transparent');
                        
                        div.addEventListener('click', () => selectContract(contract));
                        resultsContainer.appendChild(div);
                    });
                    resultsContainer.style.display = 'block';
                } else {
                    resultsContainer.style.display = 'none';
                }
            } catch (err) {
                console.error('Search error', err);
            }
        }, 300);
    });
    
    // Hide when clicking outside
    document.addEventListener('click', (e) => {
        if (e.target !== searchInput && e.target !== resultsContainer) {
            resultsContainer.style.display = 'none';
        }
    });
}

// Store active chart instances
let activeCharts = [];

async function selectContract(contract) {
    document.getElementById('contract-search').value = contract.symbol;
    document.getElementById('autocomplete-results').style.display = 'none';
    
    // Auto-detect the matching pair
    const baseSymbol = contract.symbol.slice(0, -2);
    const ce_symbol = baseSymbol + "CE";
    const pe_symbol = baseSymbol + "PE";
    
    showToast(`🔄 Switching contracts to:<br>${ce_symbol}<br>${pe_symbol}`, 'buy');
    
    try {
        await fetch('/api/update_tokens', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ ce_symbol, pe_symbol })
        });
        
        // Remove old charts
        activeCharts.forEach(c => {
            if (c && c.chart) {
                c.chart.remove();
            }
        });
        activeCharts = [];
        document.getElementById('call-3m-chart').innerHTML = '';
        document.getElementById('sensex-3m-chart').innerHTML = '';
        document.getElementById('put-3m-chart').innerHTML = '';
        
        fetchData(); // Reload charts
    } catch(e) {
        console.error(e);
        showToast('❌ Failed to swap contracts', 'sell');
    }
}

let _fetchRetryTimer = null;

async function fetchData() {
    try {
        const response = await fetch('/api/data');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();

        if (data.loading) {
            // Server cache is still warming up — show spinner and retry in 4s
            showToast('⏳ Loading data, please wait...', 'buy');
            clearTimeout(_fetchRetryTimer);
            _fetchRetryTimer = setTimeout(fetchData, 4000);
            return;
        }

        renderDashboard(data);
    } catch (error) {
        console.error('Error fetching data:', error);
        // Retry on error too
        clearTimeout(_fetchRetryTimer);
        _fetchRetryTimer = setTimeout(fetchData, 5000);
    }
}

function renderDashboard(data) {
    const charts = [];
    
    // Check for Strategy Signals
    if (data.signal && data.signal.active) {
        document.getElementById('strategy-banner').classList.remove('hidden');
        document.getElementById('banner-symbol').textContent = data.signal.symbol;
        document.getElementById('banner-entry').textContent = data.signal.entry;
        document.getElementById('banner-sl').textContent = data.signal.sl;
        document.getElementById('banner-tp').textContent = data.signal.tp;
        
        // Make the banner background red if it's a SELL
        if (data.signal.action === 'SELL') {
            document.getElementById('strategy-banner').style.background = '#f23645';
        }
    }
    
    // SENSEX
    const commonTimeScale = data.sensex.intraday;
    const sensexChart = createChartWithZones('sensex-3m-chart', data.sensex, 'SENSEX', data.signal, commonTimeScale);
    
    // CALL
    const callChart = createChartWithZones('call-3m-chart', data.call, data.call.symbol, data.signal, commonTimeScale);
    
    // PUT
    const putChart = createChartWithZones('put-3m-chart', data.put, data.put.symbol, data.signal, commonTimeScale);
    
    activeCharts = [sensexChart, callChart, putChart];
    
    // Attach draw listeners
    if (window.attachDrawListener) {
        window.attachDrawListener(sensexChart, 'SENSEX');
        window.attachDrawListener(callChart, data.call.symbol);
        window.attachDrawListener(putChart, data.put.symbol);
    }
        
    window.activeCharts = [sensexChart, callChart, putChart];
    
    // Dynamic Recalibration UI Hook
    const applyBtn = document.getElementById('calib-apply');
    if (applyBtn) {
        applyBtn.addEventListener('click', () => {
            if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
            const h = parseFloat(document.getElementById('calib-high').value);
            const l = parseFloat(document.getElementById('calib-low').value);
            if (!isNaN(h) && !isNaN(l) && h > l) {
                // Instantly re-render SENSEX zones!
                if (window.activeCharts && window.activeCharts[0]) {
                    window.activeCharts[0].chart.redrawZones(h, l);
                }
            }
        });
    }

    // Synchronize crosshairs across all charts
    function attachCrosshairSync(mainChart, childCharts) {
        if (!mainChart || !mainChart.chart) return;
        mainChart.chart.subscribeCrosshairMove(param => {
            if (param.time === undefined || param.point === undefined) {
                childCharts.forEach(c => { if (c && c.chart) c.chart.clearCrosshairPosition(); });
                return;
            }
            childCharts.forEach(c => {
                if (c && c.chart && c.candleSeries && c.hasData) {
                    // Try to find the actual price at this time, fallback to lastData.close
                    const dp = c.ohlcData ? c.ohlcData.find(d => d.time === param.time) : null;
                    const price = dp ? dp.close : (c.lastData ? c.lastData.close : null);
                    
                    if (price !== null && price !== undefined && !isNaN(price) && price !== 0) {
                        try {
                            c.chart.setCrosshairPosition(price, param.time, c.candleSeries);
                        } catch(e) {}
                    } else {
                        c.chart.clearCrosshairPosition();
                    }
                }
            });
        });
    }

    // Crosshair Sync Disabled per user request (so touching one screen only updates that screen)
    // attachCrosshairSync(sensexChart, [callChart, putChart]);
    // attachCrosshairSync(callChart, [sensexChart, putChart]);
    // attachCrosshairSync(putChart, [sensexChart, callChart]);
    // Synchronize Time Scales (Zoom/Scroll) across all charts using TIME (not logical index)
    let syncTimeout = null;
    let isSyncing = false;
    
    function attachTimeScaleSync(mainChart, childCharts) {
        if (!mainChart || !mainChart.chart) return;
        mainChart.chart.timeScale().subscribeVisibleTimeRangeChange(range => {
            if (isSyncing || !range) return;
            
            isSyncing = true;
            childCharts.forEach(c => {
                if (c && c.chart) {
                    c.chart.timeScale().setVisibleRange(range);
                }
            });
            
            // Release the lock after a micro-delay to prevent echo
            if (syncTimeout) clearTimeout(syncTimeout);
            syncTimeout = setTimeout(() => { isSyncing = false; }, 50);
        });
    }

    // attachTimeScaleSync(sensexChart, [callChart, putChart]);
    // attachTimeScaleSync(callChart, [sensexChart, putChart]);
    // attachTimeScaleSync(putChart, [sensexChart, callChart]);
    
    // Master Zoom: Focus exactly on the last 150 candles (one full day) based on the SENSEX timeline
    if (data.sensex && data.sensex.intraday && data.sensex.intraday.length > 0) {
        const sx = data.sensex.intraday;
        const total = sx.length;
        if (total > 150) {
            // By setting the logical range on Sensex, our timeScaleSync will propagate the exact TIME range to CE and PE
            sensexChart.chart.timeScale().setVisibleLogicalRange({
                from: total - 150,
                to: total - 1
            });
        } else {
            sensexChart.chart.timeScale().fitContent();
        }
    }
    
    // Audio Engine for Danger Alerts
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    let lastChimeTime = 0;

    function playAlertChime() {
        if (Date.now() - lastChimeTime < 5000) return; // Debounce 5 seconds
        lastChimeTime = Date.now();
        
        if (audioCtx.state === 'suspended') audioCtx.resume();
        
        const oscillator = audioCtx.createOscillator();
        const gainNode = audioCtx.createGain();
        
        // Sleek "ding" sound
        oscillator.type = 'sine';
        oscillator.frequency.setValueAtTime(880, audioCtx.currentTime); // A5
        oscillator.frequency.exponentialRampToValueAtTime(1760, audioCtx.currentTime + 0.1);
        
        gainNode.gain.setValueAtTime(0.5, audioCtx.currentTime);
        gainNode.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.3);
        
        oscillator.connect(gainNode);
        gainNode.connect(audioCtx.destination);
        
        oscillator.start();
        oscillator.stop(audioCtx.currentTime + 0.3);
    }

    // Live Polling and Dynamic Overlay Ticking
    function processLiveTick(chartObj, containerId, ltp, currentTimestamp) {
        if (!chartObj.lastData) return;
        
        let lastData = chartObj.lastData;
        const candleDuration = 3 * 60; // 3 minutes
        
        // Check if we need to spawn a new candle (time rollover)
        if (currentTimestamp >= lastData.time + candleDuration) {
            // Create new candle inheriting the last close
            const newCandle = {
                time: lastData.time + candleDuration,
                open: lastData.close,
                high: lastData.close,
                low: lastData.close,
                close: ltp,
                volume: 0 // Reset volume for new candle
            };
            chartObj.lastData = newCandle;
            lastData = newCandle;
        } else {
            // Update current candle
            lastData.close = ltp;
            lastData.high = Math.max(lastData.high, ltp);
            lastData.low = Math.min(lastData.low, ltp);
            // Simulate live volume tick
            lastData.volume += Math.floor(Math.random() * 50); 
        }
        
        chartObj.candleSeries.update(lastData);
        
        // Audio Danger Alert Check
        if (chartObj.boundaryLevels && chartObj.boxHeight) {
            chartObj.boundaryLevels.forEach(lvl => {
                if (!lvl.price) return;
                const distance = Math.abs(ltp - lvl.price);
                // Touch threshold from auto-marking (20% of box height)
                if (distance <= (chartObj.boxHeight * 0.2)) {
                    playAlertChime();
                }
            });
        }
        
        // Optional: Update volume series if we stored it (we need to pass it into chartObj)
        if (chartObj.volumeSeries) {
            chartObj.volumeSeries.update({
                time: lastData.time,
                value: lastData.volume,
                color: lastData.close >= lastData.open ? 'rgba(8, 153, 129, 0.5)' : 'rgba(242, 54, 69, 0.5)'
            });
        }
        
        // Dynamically update the overlay UI so it ticks live without mouse movement
        const upClass = lastData.close >= lastData.open ? 'up' : 'down';
        const els = ['o', 'h', 'l', 'c'].map(id => document.getElementById(`${containerId}-${id}`));
        if (els[0]) {
            els[0].textContent = lastData.open.toFixed(2);
            els[1].textContent = lastData.high.toFixed(2);
            els[2].textContent = lastData.low.toFixed(2);
            
            // Add a subtle flash effect to the close price
            els[3].textContent = lastData.close.toFixed(2);
            els[3].style.textShadow = lastData.close >= lastData.open ? '0 0 5px #089981' : '0 0 5px #f23645';
            setTimeout(() => { els[3].style.textShadow = 'none'; }, 200);
            
            els.forEach(el => el.className = `val ${upClass}`);
            
            const container = document.getElementById(containerId);
            const buyBtn = container.querySelector('.tv-btn.buy');
            const sellBtn = container.querySelector('.tv-btn.sell');
            if(buyBtn) buyBtn.textContent = `BUY @ ${lastData.close.toFixed(2)}`;
            if(sellBtn) sellBtn.textContent = `SELL @ ${(lastData.close - 0.05).toFixed(2)}`;
            
            // Update Timer
            const timerEl = document.getElementById(`${containerId}-t`);
            if (timerEl) {
                const timeLeft = Math.max(0, (lastData.time + candleDuration) - currentTimestamp);
                const mins = Math.floor(timeLeft / 60);
                const secs = timeLeft % 60;
                timerEl.textContent = `⏳ ${mins}:${secs.toString().padStart(2, '0')}`;
            }
        }
    }

    // ── Always-on live candle ticking ─────────────────────────────────────────
    setInterval(async () => {
        try {
            const res = await fetch('/api/live');
            if (!res.ok) return;
            const liveData = await res.json();
            const now = Math.floor(Date.now() / 1000);

            if (Object.keys(liveData).length > 0) {
                if (liveData.SENSEX && liveData.SENSEX.ltp !== undefined)
                    processLiveTick(sensexChart, 'sensex-3m-chart', liveData.SENSEX.ltp, now);
                if (liveData.CALL && liveData.CALL.ltp !== undefined)
                    processLiveTick(callChart, 'call-3m-chart', liveData.CALL.ltp, now);
                if (liveData.PUT && liveData.PUT.ltp !== undefined)
                    processLiveTick(putChart, 'put-3m-chart', liveData.PUT.ltp, now);
            }
        } catch (e) { console.error('Live tick error', e); }
    }, 500); // Poll every 500ms for smooth live candles


    // --- Backtrace functionality removed per user request, system runs purely live now ---

    // Connect Execution Panel Buttons for Premium Feel
    const themeBtn = document.querySelector('.exec-btn.theme-toggle');
    const scannerBtn = document.querySelector('.exec-btn.scanner');
    const markingBtn = document.querySelector('.exec-btn.toggle-marking');
    
    if (themeBtn) {
        themeBtn.addEventListener('click', (e) => {
            const currentTheme = document.documentElement.getAttribute('data-theme') || 'dark';
            const newTheme = currentTheme === 'light' ? 'dark' : 'light';
            document.documentElement.setAttribute('data-theme', newTheme);
            e.target.innerText = newTheme === 'light' ? '☀️ Light Mode' : '🌙 Dark Mode';
            
            // Update charts dynamically
            if (window.activeCharts) {
                const isLight = newTheme === 'light';
                window.activeCharts.forEach(c => {
                    c.chart.applyOptions({
                        layout: { textColor: isLight ? '#131722' : '#d1d4dc' },
                        grid: { vertLines: { color: isLight ? '#e0e3eb' : '#2a2e39' }, horzLines: { color: isLight ? '#e0e3eb' : '#2a2e39' } },
                        rightPriceScale: { borderColor: isLight ? '#e0e3eb' : '#2a2e39' },
                        timeScale: { borderColor: isLight ? '#e0e3eb' : '#2a2e39' }
                    });
                    if (c.priceLines) {
                        c.priceLines.forEach(pl => pl.applyOptions({ color: isLight ? '#000000' : '#d1d4dc' }));
                    }
                });
            }
            showToast(`Switched to ${newTheme.toUpperCase()} mode.`, 'buy');
        });
    }

    if (scannerBtn) {
        scannerBtn.addEventListener('click', (e) => {
            document.querySelectorAll('.exec-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            showToast("System switched to Real-Time Scanner Mode. Polling 500ms.", 'buy');
        });
    }
    // backtraceBtn removed

    // Manual Fib Drawing Tool
    const btnManualDraw = document.getElementById('btn-manual-draw');
    let drawState = 0; // 0: off, 1: waiting for high, 2: waiting for low
    let customHigh = null;
    let customLow = null;
    let customSymbol = null;

    if (btnManualDraw) {
        btnManualDraw.addEventListener('click', () => {
            if (drawState === 0) {
                drawState = 1;
                btnManualDraw.classList.add('active');
                btnManualDraw.innerText = '❌ Cancel Draw';
                showToast("Manual Fib Mode: Click the HIGH point on any chart", "buy");
            } else {
                drawState = 0;
                btnManualDraw.classList.remove('active');
                btnManualDraw.innerText = '✏️ Draw Fib';
                showToast("Manual Fib Drawing Cancelled.", "sell");
            }
        });
    }

    // This will be attached to each chart when it's created
    window.attachDrawListener = function(chartObj, symbol) {
        chartObj.chart.subscribeClick((param) => {
            if (drawState === 0) return;
            if (!param.point) return;
            
            const price = chartObj.candleSeries.coordinateToPrice(param.point.y);
            if (!price) return;
            
            if (drawState === 1) {
                customHigh = price;
                customSymbol = symbol;
                drawState = 2;
                showToast(`High point recorded at ${price.toFixed(2)}. Now click the LOW point on the same chart.`, "buy");
            } else if (drawState === 2) {
                if (symbol !== customSymbol) {
                    showToast("Please click the low point on the same chart!", "sell");
                    return;
                }
                customLow = price;
                
                // Ensure high is actually > low
                const finalHigh = Math.max(customHigh, customLow);
                const finalLow = Math.min(customHigh, customLow);
                
                showToast(`Low point recorded at ${finalLow.toFixed(2)}. Sending precise layout to engine...`, "buy");
                
                // Reset UI state
                drawState = 0;
                if(btnManualDraw) {
                    btnManualDraw.classList.remove('active');
                    btnManualDraw.innerText = '✏️ Draw Fib';
                }
                
                // Send to backend
                fetch('/api/set_fib', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ symbol: symbol, high: finalHigh, low: finalLow })
                }).then(() => {
                    showToast(`✅ Precise points synced for ${symbol}! Refreshing lines...`, "buy");
                    // Refresh data to instantly draw the lines
                    fetchData();
                });
            }
        });
    };
    
    // Timeframe toggle buttons
    const tfBtns = document.querySelectorAll('.timeframe-btn');
    tfBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            tfBtns.forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            const tf = e.target.getAttribute('data-tf');
            
            if (window.activeCharts) {
                // 1. Update data on all charts first
                window.activeCharts.forEach(c => {
                    if (c.datasets && c.datasets[tf]) {
                        c.candleSeries.setData(c.datasets[tf]);
                        if (c.chart.updateTimeline) {
                            c.chart.updateTimeline(c.datasets[tf]);
                        }
                    }
                });
                
                // 2. Safely apply zoom levels
                const sensexChart = window.activeCharts[0];
                if (tf === '3m' && sensexChart.datasets['3m']) {
                    const total = sensexChart.datasets['3m'].length;
                    if (total > 150) {
                        sensexChart.chart.timeScale().setVisibleLogicalRange({
                            from: total - 150,
                            to: total - 1
                        });
                    } else {
                        sensexChart.chart.timeScale().fitContent();
                    }
                } else {
                    window.activeCharts.forEach(c => c.chart.timeScale().fitContent());
                }
            }
            showToast(`Switched to ${tf.toUpperCase()} timeframe.`, 'buy');
        });
    });
}



function createChartWithZones(containerId, instrumentData, symbol, signal, globalTimelineData) {
    symbol = symbol || 'SENSEX';
    let ohlcData  = instrumentData.intraday  || [];
    let fibData   = instrumentData.fibonacci || [];
    const anchorHigh = instrumentData.anchor_high || null;
    const anchorLow  = instrumentData.anchor_low  || null;
    const container  = document.getElementById(containerId);
    container.style.position = 'relative';

    // ── Overlay ──────────────────────────────────────────────────────────────
    const overlay = document.createElement('div');
    overlay.className = 'tv-overlay';
    const lastData    = ohlcData.length > 0 ? ohlcData[ohlcData.length - 1]
                      : { open: 0, high: 0, low: 0, close: 0, volume: 0, time: Math.floor(Date.now()/1000) };
    const upDownClass = lastData.close >= lastData.open ? 'up' : 'down';
    let titleClass = 'main-header';
    if (containerId.includes('call')) titleClass = 'call-header';
    if (containerId.includes('put'))  titleClass = 'put-header';

    const anchorTxt = (anchorHigh && anchorLow)
        ? `<span style="font-size:10px;color:#787b86;margin-left:8px;">W.H:${anchorHigh.toFixed(2)}  W.L:${anchorLow.toFixed(2)}</span>`
        : '';

    overlay.innerHTML = `
        <div class="tv-title ${titleClass}">${symbol} • BSE FO${anchorTxt}</div>
        <div class="tv-ohlc">
            <span>O <span class="val ${upDownClass}" id="${containerId}-o">${lastData.open.toFixed(2)}</span></span>
            <span>H <span class="val ${upDownClass}" id="${containerId}-h">${lastData.high.toFixed(2)}</span></span>
            <span>L <span class="val ${upDownClass}" id="${containerId}-l">${lastData.low.toFixed(2)}</span></span>
            <span>C <span class="val ${upDownClass}" id="${containerId}-c">${lastData.close.toFixed(2)}</span></span>
            <span style="margin-left:10px;">Vol <span id="${containerId}-v">${(lastData.volume/1000).toFixed(3)}K</span></span>
            <span style="margin-left:10px; color: #ff9800;" id="${containerId}-t"></span>
        </div>
    `;
    container.appendChild(overlay);

    // ── Chart Init ────────────────────────────────────────────────────────────
    const chart = LightweightCharts.createChart(container, {
        autoSize: true,
        layout: { background: { type: 'solid', color: 'transparent' }, textColor: '#d1d4dc', fontSize: 11 },
        grid: { vertLines: { color: '#e8eaed' }, horzLines: { color: '#e8eaed' } },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        rightPriceScale: { borderColor: '#2a2e39', scaleMargins: { top: 0.35, bottom: 0.25 } },
        timeScale: {
            borderColor: '#2a2e39', timeVisible: true, secondsVisible: false,
            shiftVisibleRangeOnNewBar: false,
            tickMarkFormatter: (time, tickMarkType) => {
                const date = new Date(time * 1000);
                const opts = { timeZone: 'Asia/Kolkata' };
                if      (tickMarkType === 0) { opts.year = 'numeric'; }
                else if (tickMarkType === 1) { opts.month = 'short'; opts.year = 'numeric'; } // Use full year to avoid Jul 26 confusion
                else if (tickMarkType === 2) { opts.day = 'numeric'; opts.month = 'short'; }
                else { opts.hour = '2-digit'; opts.minute = '2-digit'; opts.hour12 = false; }
                return new Intl.DateTimeFormat('en-IN', opts).format(date);
            }
        },
        localization: {
            timeFormatter: (t) => new Intl.DateTimeFormat('en-IN', {
                timeZone: 'Asia/Kolkata', day: 'numeric', month: 'short',
                hour: '2-digit', minute: '2-digit', hour12: false
            }).format(new Date(t * 1000)) + ' IST'
        }
    });

    // ── Candlestick ───────────────────────────────────────────────────────────
    const candleSeries = chart.addCandlestickSeries({
        upColor: '#089981', downColor: '#f23645',
        borderDownColor: '#f23645', borderUpColor: '#089981',
        wickDownColor: '#f23645', wickUpColor: '#089981',
    });
    candleSeries.setData(ohlcData);

    // ── Volume ────────────────────────────────────────────────────────────────
    const volumeSeries = chart.addHistogramSeries({
        color: '#26a69a', priceFormat: { type: 'volume' },
        priceScaleId: 'volume_scale', lastValueVisible: false, priceLineVisible: false,
    });
    chart.priceScale('volume_scale').applyOptions({ scaleMargins: { top: 0.9, bottom: 0 }, visible: false });
    volumeSeries.setData(ohlcData.map(d => ({
        time: d.time, value: d.volume,
        color: d.close >= d.open ? 'rgba(8,153,129,0.5)' : 'rgba(242,54,69,0.5)'
    })));

    // ── Dummy series for PriceLines ───────────────────────────────────────────
    const plSeries = chart.addLineSeries({
        autoscaleInfoProvider: () => null, lineWidth: 0,
        priceLineVisible: false, crosshairMarkerVisible: false, lastValueVisible: false,
    });

    // ── Crosshair overlay update ──────────────────────────────────────────────
    chart.subscribeCrosshairMove(param => {
        if (!param.point || !param.time) return;
        const d = param.seriesData.get(candleSeries);
        const v = param.seriesData.get(volumeSeries);
        if (d) {
            const cls = d.close >= d.open ? 'up' : 'down';
            const vals = [d.open, d.high, d.low, d.close];
            ['o','h','l','c'].forEach((id, i) => {
                const el = document.getElementById(`${containerId}-${id}`);
                if (el) { el.textContent = vals[i].toFixed(2); el.className = `val ${cls}`; }
            });
        }
        if (v) {
            const vEl = document.getElementById(`${containerId}-v`);
            if (vEl) vEl.textContent = (v.value/1000).toFixed(3) + 'K';
        }
    });

    const LINE_DEFS = [
        // ── Fib 1 (Above High) ──
        { key: 'f1_4_618', label: '4.618', title: '4.618', color: '#ff9800', style: LightweightCharts.LineStyle.Solid },
        { key: 'f1_4_414', label: '4.414', title: '4.414', color: '#e91e63', style: LightweightCharts.LineStyle.Solid },
        { key: 'f1_4_272', label: '4.272', title: '4.272', color: '#9c27b0', style: LightweightCharts.LineStyle.Solid },
        { key: 'f1_4_000', label: '4.00',  title: '4.00',  color: '#0d47a1', style: LightweightCharts.LineStyle.Solid, lineWidth: 2 },
        { key: 'f1_3_618', label: '3.618', title: '3.618', color: '#9c27b0', style: LightweightCharts.LineStyle.Solid },
        { key: 'f1_3_414', label: '3.414', title: '3.414', color: '#2196f3', style: LightweightCharts.LineStyle.Solid },
        { key: 'f1_3_272', label: '3.272', title: '3.272', color: '#9e9e9e', style: LightweightCharts.LineStyle.Solid },
        { key: 'f1_3_000', label: '3.00',  title: '3.00',  color: '#0d47a1', style: LightweightCharts.LineStyle.Solid, lineWidth: 2 },
        { key: 'f1_2_618', label: '2.618', title: '2.618', color: '#f44336', style: LightweightCharts.LineStyle.Solid },
        { key: 'f1_2_414', label: '2.414', title: '2.414', color: '#4caf50', style: LightweightCharts.LineStyle.Solid },
        { key: 'f1_2_272', label: '2.272', title: '2.272', color: '#ff9800', style: LightweightCharts.LineStyle.Solid },
        { key: 'f1_2_000', label: '2.00',  title: '2.00',  color: '#0d47a1', style: LightweightCharts.LineStyle.Solid, lineWidth: 2 },
        { key: 'f1_1_618', label: '1.618', title: '1.618', color: '#2196f3', style: LightweightCharts.LineStyle.Solid },
        { key: 'f1_1_414', label: '1.41',  title: '1.41',  color: '#f44336', style: LightweightCharts.LineStyle.Solid },
        { key: 'f1_1_390', label: '1.39',  title: '1.39',  color: '#f44336', style: LightweightCharts.LineStyle.Solid },
        { key: 'f1_1_272', label: '1.272', title: '1.272', color: '#ff9800', style: LightweightCharts.LineStyle.Solid },
        { key: 'f1_1_000', label: '1.00',  title: '1.00',  color: '#0d47a1', style: LightweightCharts.LineStyle.Solid, lineWidth: 2 },
        { key: 'f1_0_786', label: '0.786', title: '0.786', color: '#131722', style: LightweightCharts.LineStyle.Solid },
        { key: 'f1_0_236', label: '0.236', title: '0.236', color: '#131722', style: LightweightCharts.LineStyle.Solid },
        { key: 'f1_0_000', label: '0.00',  title: '0.00',  color: '#0d47a1', style: LightweightCharts.LineStyle.Solid, lineWidth: 2 },
        
        // ── Fib 2 (Below Low) ──
        { key: 'f2_0_236', label: '0.236', title: '0.236', color: '#131722', style: LightweightCharts.LineStyle.Solid },
        { key: 'f2_0_786', label: '0.786', title: '0.786', color: '#131722', style: LightweightCharts.LineStyle.Solid },
        { key: 'f2_1_272', label: '1.272', title: '1.272', color: '#ff9800', style: LightweightCharts.LineStyle.Solid },
        { key: 'f2_1_390', label: '1.39',  title: '1.39',  color: '#f44336', style: LightweightCharts.LineStyle.Solid },
        { key: 'f2_1_414', label: '1.41',  title: '1.41',  color: '#f44336', style: LightweightCharts.LineStyle.Solid },
        { key: 'f2_1_618', label: '1.618', title: '1.618', color: '#2196f3', style: LightweightCharts.LineStyle.Solid },
        { key: 'f2_2_000', label: '2.00',  title: '2.00',  color: '#0d47a1', style: LightweightCharts.LineStyle.Solid, lineWidth: 2 },
        { key: 'f2_2_272', label: '2.272', title: '2.272', color: '#ff9800', style: LightweightCharts.LineStyle.Solid },
        { key: 'f2_2_414', label: '2.414', title: '2.414', color: '#4caf50', style: LightweightCharts.LineStyle.Solid },
        { key: 'f2_2_618', label: '2.618', title: '2.618', color: '#f44336', style: LightweightCharts.LineStyle.Solid },
        { key: 'f2_3_000', label: '3.00',  title: '3.00',  color: '#0d47a1', style: LightweightCharts.LineStyle.Solid, lineWidth: 2 },
        { key: 'f2_3_272', label: '3.272', title: '3.272', color: '#9e9e9e', style: LightweightCharts.LineStyle.Solid },
        { key: 'f2_3_414', label: '3.414', title: '3.414', color: '#2196f3', style: LightweightCharts.LineStyle.Solid },
        { key: 'f2_3_618', label: '3.618', title: '3.618', color: '#9c27b0', style: LightweightCharts.LineStyle.Solid },
        { key: 'f2_4_000', label: '4.00',  title: '4.00',  color: '#0d47a1', style: LightweightCharts.LineStyle.Solid, lineWidth: 2 },
        { key: 'f2_4_272', label: '4.272', title: '4.272', color: '#9c27b0', style: LightweightCharts.LineStyle.Solid },
        { key: 'f2_4_414', label: '4.414', title: '4.414', color: '#e91e63', style: LightweightCharts.LineStyle.Solid },
        { key: 'f2_4_618', label: '4.618', title: '4.618', color: '#ff9800', style: LightweightCharts.LineStyle.Solid },

        // ── Standard Mid Lines (Dark Blue overlap lines) ──
        { key: 'level_0_618', label: '0.618', title: '0.618', color: '#0d47a1', style: LightweightCharts.LineStyle.Solid },
        { key: 'level_0_500', label: '0.50',  title: '0.50',  color: '#0d47a1', style: LightweightCharts.LineStyle.Solid },
        { key: 'level_0_382', label: '0.382', title: '0.382', color: '#0d47a1', style: LightweightCharts.LineStyle.Solid },
    ];

    const fibLineSeries    = [];
    const activePriceLines = [];
    const zoneSeries       = [];
    const boundaryLevels   = [];

    function drawAllZones(zones) {
        if (!zones || !zones.length) return;

        fibLineSeries.forEach(s => chart.removeSeries(s));     fibLineSeries.length    = 0;
        zoneSeries.forEach(s => chart.removeSeries(s));        zoneSeries.length       = 0;
        activePriceLines.forEach(pl => plSeries.removePriceLine(pl)); activePriceLines.length = 0;
        boundaryLevels.length = 0;

        // ── Historical step lines ─────────────────────────────────────────────
        const linesData = {};
        LINE_DEFS.forEach(ld => linesData[ld.key] = []);

        ohlcData.forEach(d => {
            let zone = null;
            for (let i = zones.length - 1; i >= 0; i--) {
                if (d.time >= zones[i].start_time) { zone = zones[i]; break; }
            }
            if (zone) {
                LINE_DEFS.forEach(ld => {
                    const v = zone.fibs[ld.key];
                    if (v != null) linesData[ld.key].push({ time: d.time, value: v });
                });
            }
        });

        // ── Highlight Specific Reversal Zones ─────────────────────────────────
        const REVERSAL_ZONES = [
            ['f1_1_414', 'f1_1_390', 'rgba(242, 54, 69, 0.15)'],  // Top Extension Zone: 1.41 to 1.39
            ['f1_0_786', 'f2_0_236', 'rgba(242, 54, 69, 0.20)'],  // Top Confluence Zone: f1 0.786 to f2 0.236
            ['f1_0_236', 'f2_0_786', 'rgba(0, 188, 212, 0.20)'],  // Bottom Confluence Zone: f1 0.236 to f2 0.786
            ['f2_1_390', 'f2_1_414', 'rgba(0, 188, 212, 0.15)'],  // Bottom Extension Zone: 1.39 to 1.41
        ];

        REVERSAL_ZONES.forEach(([topKey, botKey, fillColor]) => {
            const topData = linesData[topKey];
            const botData = linesData[botKey];
            if (!topData || !botData || !topData.length || !botData.length) return;

            const topMap = {}; topData.forEach(d => topMap[d.time] = d.value);
            const botMap = {}; botData.forEach(d => botMap[d.time] = d.value);

            const zoneData = [];
            ohlcData.forEach(d => {
                if (topMap[d.time] != null && botMap[d.time] != null) {
                    const topV = topMap[d.time];
                    const botV = botMap[d.time];
                    zoneData.push({
                        time: d.time,
                        open: topV,
                        high: Math.max(topV, botV),
                        low: Math.min(topV, botV),
                        close: botV
                    });
                }
            });

            if (zoneData.length > 0) {
                const s = chart.addCandlestickSeries({
                    upColor: fillColor, downColor: fillColor,
                    borderVisible: false, wickVisible: false,
                    priceLineVisible: false, lastValueVisible: false,
                    crosshairMarkerVisible: false, autoscaleInfoProvider: () => null,
                });
                s.setData(zoneData);
                zoneSeries.push(s);
            }
        });

        // Historical step lines removed for a cleaner look

        // ── Current-week projected price lines with full label ─────────────────
        if (zones.length > 0) {
            const lz = zones[zones.length - 1];
            // Midpoint of the zone for the dummy anchor
            const midKey = 'level_0_500';
            const midVal = lz.fibs[midKey] || ohlcData[0].close;
            plSeries.setData(ohlcData.map(d => ({ time: d.time, value: midVal })));

            LINE_DEFS.forEach(ld => {
                const price = lz.fibs[ld.key];
                if (price == null) return;
                const pl = plSeries.createPriceLine({
                    price,
                    color          : ld.color,
                    lineWidth      : ld.style === LightweightCharts.LineStyle.Solid ? 2 : 1,
                    lineStyle      : ld.style,
                    axisLabelVisible: true,
                    title          : ld.title,
                });
                activePriceLines.push(pl);
                boundaryLevels.push({ price, label: ld.label, color: ld.color });
            });
        }
    }

    if (fibData && fibData.length > 0) drawAllZones(fibData);

        // Touch markers removed to prevent clutter on higher timeframes

    chart.updateTimeline = function(newData) {
        ohlcData = newData;
        if (fibData && fibData.length > 0) drawAllZones(fibData);
    };

    chart.priceScale('right').applyOptions({ autoScale: true });

    new ResizeObserver(entries => {
        if (!entries.length || entries[0].target !== container) return;
        const r = entries[0].contentRect;
        chart.applyOptions({ height: r.height, width: r.width });
    }).observe(container);

    return {
        chart, candleSeries, volumeSeries, lastData,
        datasets: {
            '3m': instrumentData.intraday || [],
            '1d': instrumentData.daily    || [],
            '1w': instrumentData.weekly   || [],
        },
        hasData: ohlcData.length > 0, ohlcData, boundaryLevels, priceLines: activePriceLines,
    };
}

// Execution Panel Wiring
document.addEventListener('DOMContentLoaded', () => {
    const btnTheme = document.getElementById('btn-theme');
    if (btnTheme) {
        btnTheme.addEventListener('click', (e) => {
            const html = document.documentElement;
            const isDark = html.getAttribute('data-theme') === 'dark';
            const newTheme = isDark ? 'light' : 'dark';
            html.setAttribute('data-theme', newTheme);
            e.target.innerText = newTheme === 'light' ? '🌙 Dark Mode' : '🌓 Light Mode';
            
            // Update charts dynamically without reloading
            if (window.activeCharts) {
                const isLight = newTheme === 'light';
                window.activeCharts.forEach(c => {
                    c.chart.applyOptions({
                        layout: { textColor: isLight ? '#131722' : '#d1d4dc' },
                        grid: { vertLines: { color: isLight ? '#e0e3eb' : '#2a2e39' }, horzLines: { color: isLight ? '#e0e3eb' : '#2a2e39' } },
                        rightPriceScale: { borderColor: isLight ? '#e0e3eb' : '#2a2e39' },
                        timeScale: { borderColor: isLight ? '#e0e3eb' : '#2a2e39' }
                    });
                });
            }
        });
    }

    const btnManualHigh = document.getElementById('btn-manual-high');
    const btnManualLow = document.getElementById('btn-manual-low');
    const btnApplyZones = document.getElementById('btn-apply-zones');
    const btnScanner = document.getElementById('btn-scanner');

    function showToast(msg, type='buy') {
        const toastContainer = document.getElementById('toast-container');
        if (!toastContainer) return;
        const toast = document.createElement('div');
        toast.className = `toast ${type} show`;
        toast.textContent = msg;
        toastContainer.appendChild(toast);
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    if (btnManualHigh) {
        btnManualHigh.addEventListener('click', () => {
            btnManualHigh.classList.toggle('active');
            if(btnManualHigh.classList.contains('active')) {
                showToast("Manual High Anchor enabled. Click on chart to set.", 'buy');
            }
        });
    }

    if (btnManualLow) {
        btnManualLow.addEventListener('click', () => {
            btnManualLow.classList.toggle('active');
            if(btnManualLow.classList.contains('active')) {
                showToast("Manual Low Anchor enabled. Click on chart to set.", 'sell');
            }
        });
    }

    if (btnApplyZones) {
        btnApplyZones.addEventListener('click', () => {
            showToast("Fibonacci Zones applied!", 'buy');
            if(btnManualHigh) btnManualHigh.classList.remove('active');
            if(btnManualLow) btnManualLow.classList.remove('active');
        });
    }

    if (btnScanner) {
        btnScanner.addEventListener('click', () => {
            btnScanner.classList.toggle('active');
            if(btnScanner.classList.contains('active')) {
                showToast("Real-Time Scanner Activating...", 'buy');
            } else {
                showToast("Real-Time Scanner Paused.", 'sell');
            }
        });
    }
});
