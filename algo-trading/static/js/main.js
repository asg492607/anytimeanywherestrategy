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
    if (!window.IS_SIMULATION) {
        fetchData();
        initSearch();
    }
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
        const response = await fetch('/api/update_tokens', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ ce_symbol, pe_symbol })
        });
        
        const res = await response.json();
        if (res.status === 'error') {
            showToast('❌ ' + (res.message || 'Failed to find matching pair'), 'sell');
            return;
        }
        
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
        if (response.status === 401) {
            window.location.href = '/login';
            return;
        }
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();

        if (data.loading) {
            // Toast notification removed to prevent spam
            clearTimeout(_fetchRetryTimer);
            _fetchRetryTimer = setTimeout(fetchData, 4000);
            return;
        }

        renderDashboard(data);
    } catch (error) {
        if (error.name !== 'TypeError') console.error('Error fetching data:', error);
        // Retry on error too
        clearTimeout(_fetchRetryTimer);
        _fetchRetryTimer = setTimeout(fetchData, 5000);
    }
}

function renderDashboard(data) {
    if (window.activeCharts) {
        window.activeCharts.forEach(c => {
            if (c && c.chart) {
                try { c.chart.remove(); } catch(e) {}
            }
        });
        window.activeCharts = [];
    }
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
        
        // If it was a dummy initialization, set it to the first LTP
        if (lastData.open === 0 && lastData.high === 0 && lastData.low === 0 && lastData.close === 0) {
            lastData.open = ltp;
            lastData.high = ltp;
            lastData.low = ltp;
            lastData.close = ltp;
            lastData.time = currentTimestamp;
        }
        
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
            // Simulated live volume tick removed per request
            // lastData.volume += Math.floor(Math.random() * 50);
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

    // ── Always-on live candle ticking (live mode only) ────────────────────────
    if (!window.IS_SIMULATION) {
        setInterval(async () => {
            try {
                const res = await fetch('/api/live');
                if (res.status === 401) {
                    window.location.href = '/login';
                    return;
                }
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
            } catch (e) {
                if (e.name !== 'TypeError') console.error('Live tick error', e);
            }
        }, 500); // Poll every 500ms for smooth live candles
    }


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
    
    // Clear any existing chart and overlay before rendering to prevent overlaps
    container.innerHTML = '';

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

    // ─── Trade Management Integration ──────────────────────────────────────────
    let activeCeSymbol = "";
    let activePeSymbol = "";
    let pnlPollInterval = null;

    function showToastLocal(msg, type='buy') {
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

    // Intercept data load to record active symbols
    const originalRenderDashboard = window.renderDashboard;
    window.renderDashboard = function(data) {
        if (data.call && data.call.symbol) activeCeSymbol = data.call.symbol;
        if (data.put && data.put.symbol) activePeSymbol = data.put.symbol;
        if (originalRenderDashboard) originalRenderDashboard(data);
    };

    let _isPlacingTrade = false;
    async function placeTrade(direction, optionType) {
        if (_isPlacingTrade) {
            showToastLocal("⏳ Please wait, order is already processing...", "buy");
            return;
        }
        
        const symbol = optionType === 'CE' ? activeCeSymbol : activePeSymbol;
        if (!symbol) {
            showToastLocal("Error: No active option symbol selected.", "sell");
            return;
        }

        // Get current price of option from charts
        const chartIndex = optionType === 'CE' ? 1 : 2;
        const activeChart = window.activeCharts ? window.activeCharts[chartIndex] : null;
        const ltp = (activeChart && activeChart.lastData) ? activeChart.lastData.close : null;

        if (!ltp || isNaN(ltp)) {
            showToastLocal("Error: Waiting for live price stream...", "sell");
            return;
        }

        _isPlacingTrade = true;
        const payload = {
            broker: "angelone",
            underlying: "SENSEX",
            call_symbol: optionType === 'CE' ? symbol : null,
            put_symbol: optionType === 'PE' ? symbol : null,
            entry_price: ltp,
            quantity: 10, // 1 lot default
            stop_loss: direction === 'BUY' ? ltp - 10 : ltp + 10,
            target: direction === 'BUY' ? ltp + 20 : ltp - 20,
            strategy_name: "institutional",
            direction: direction
        };

        try {
            const response = await fetch('/api/trades', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const res = await response.json();
            if (res.status === 'success') {
                showToastLocal(`✅ ${direction} ${optionType} Order Filled @ ₹${ltp.toFixed(2)}`, direction === 'BUY' ? 'buy' : 'sell');
                pollLivePnL();
            } else {
                showToastLocal(`❌ Order Rejected: ${res.message}`, 'sell');
            }
        } catch(e) {
            console.error('Order error', e);
            showToastLocal('❌ Connection error placing order.', 'sell');
        } finally {
            // Unlock after a brief cooldown to completely prevent spam
            setTimeout(() => { _isPlacingTrade = false; }, 1000);
        }
    }

    // Wire up execution dock buttons
    if (!window.IS_SIMULATION) {
        const execBlocks = document.querySelectorAll('.execution-panel .exec-block');
        if (execBlocks.length >= 3) {
            // CE Block
            const buyCeBtn = execBlocks[0].querySelector('.buy-btn');
            const sellCeBtn = execBlocks[0].querySelector('.sell-btn');
            if (buyCeBtn) buyCeBtn.addEventListener('click', () => placeTrade('BUY', 'CE'));
            if (sellCeBtn) sellCeBtn.addEventListener('click', () => placeTrade('SELL', 'CE'));

            // PE Block
            const buyPeBtn = execBlocks[2].querySelector('.buy-btn');
            const sellPeBtn = execBlocks[2].querySelector('.sell-btn');
            if (buyPeBtn) buyPeBtn.addEventListener('click', () => placeTrade('BUY', 'PE'));
            if (sellPeBtn) sellPeBtn.addEventListener('click', () => placeTrade('SELL', 'PE'));
        }
    }

    // ─── Reference Box Chart Overlay Arrays ───
    let activeBoxSeries = [[], [], []];
    let activeBoxPriceLines = [[], [], []];

    // ─── Buy Signal Chart Overlay Arrays ───
    let activeSignalPriceLines = [[], [], []];

    // ─── Stop Loss Chart Overlay Arrays ───
    let activeStopLossPriceLines = [[], [], []];

    // ─── Target Chart Overlay Arrays ───
    let activeTargetPriceLines = [[], [], []];
    
    // ─── Active Trades Dock Tab State ───
    let activeTradesTab = 'pnl';
    window.switchTradesTab = function(tab) {
        activeTradesTab = tab;
        const pnlTab = document.getElementById('trades-view-tab-pnl');
        const slTab = document.getElementById('trades-view-tab-sl');
        const targetTab = document.getElementById('trades-view-tab-target');
        
        // Reset tab styles
        if (pnlTab) { pnlTab.style.color = '#787b86'; pnlTab.style.borderBottom = 'none'; }
        if (slTab) { slTab.style.color = '#787b86'; slTab.style.borderBottom = 'none'; }
        if (targetTab) { targetTab.style.color = '#787b86'; targetTab.style.borderBottom = 'none'; }

        // Apply active styles
        const activeEl = document.getElementById(`trades-view-tab-${tab}`);
        if (activeEl) {
            activeEl.style.color = '#2962ff';
            activeEl.style.borderBottom = '2px solid #2962ff';
        }
        pollLivePnL();
    };

    // Live P&L and Reference Box Poller
    async function pollLivePnL() {
        try {
            // Poll P&L and Trades
            const response = await fetch('/api/trades/pnl');
            if (response.status === 401) {
                window.location.href = '/login';
                return;
            }
            if (!response.ok) return;
            const data = await response.json();
            
            // Poll Active Reference Boxes
            const refResponse = await fetch('/api/reference-boxes');
            const refData = await refResponse.json();
            if (refData.status === 'success') {
                updateReferenceBoxesUI(refData.reference_boxes);
                drawReferenceBoxes(refData.reference_boxes);
            }

            // Poll Buy Signals
            const sigResponse = await fetch('/api/buy-signals');
            const sigData = await sigResponse.json();
            if (sigData.status === 'success') {
                updateBuySignalsUI(sigData.buy_signals);
            }

            // Poll Trade Confirmations
            const confResponse = await fetch('/api/confirmations');
            const confData = await confResponse.json();
            if (confData.status === 'success') {
                updateConfirmationTimelineUI(confData.confirmations);
            }

            // Poll Trade Executions
            const execResponse = await fetch('/api/executions');
            const execData = await execResponse.json();
            if (execData.status === 'success') {
                updateExecutionsUI(execData.executions);
            }

            // Poll Stop Loss Events (Active & History)
            const slResponse = await fetch('/api/stop-loss');
            const slData = await slResponse.json();
            const slHistResponse = await fetch('/api/stop-loss/history');
            const slHistData = await slHistResponse.json();
            
            if (data.status === 'success' && slData.status === 'success' && slHistData.status === 'success') {
                updatePnLUI(data.stats, data.running_trades, slData.stop_loss_events);
                drawVisualIndicators(sigData.buy_signals, execData.executions, slData.stop_loss_events, slHistData.stop_loss_events);
            }
        } catch(e) {
            if (e.name !== 'TypeError') console.error('Error polling live data feed:', e);
        }
    }

    function drawReferenceBoxes(boxes) {
        // 1. Clear previous reference box layers on all 3 charts
        for (let idx = 0; idx < 3; idx++) {
            const chartObj = window.activeCharts ? window.activeCharts[idx] : null;
            if (!chartObj) continue;
            
            activeBoxSeries[idx].forEach(s => {
                try { chartObj.chart.removeSeries(s); } catch(e) {}
            });
            activeBoxSeries[idx] = [];
            
            activeBoxPriceLines[idx].forEach(pl => {
                try { chartObj.candleSeries.removePriceLine(pl); } catch(e) {}
            });
            activeBoxPriceLines[idx] = [];
        }
        
        if (!boxes || !boxes.length) return;
        
        // 2. Add active boxes
        boxes.forEach(box => {
            let idx = -1;
            if (box.chart_type === 'SPOT') idx = 0;
            else if (box.chart_type === 'CALL') idx = 1;
            else if (box.chart_type === 'PUT') idx = 2;
            
            if (idx === -1) return;
            const chartObj = window.activeCharts ? window.activeCharts[idx] : null;
            if (!chartObj || !chartObj.ohlcData || !chartObj.ohlcData.length) return;
            
            const startTime = box.candle_timestamp;
            const zoneData = [];
            
            chartObj.ohlcData.forEach(d => {
                if (d.time >= startTime) {
                    zoneData.push({
                        time: d.time,
                        open: box.upper_boundary,
                        high: box.upper_boundary,
                        low: box.lower_boundary,
                        close: box.lower_boundary
                    });
                }
            });
            
            if (zoneData.length > 0) {
                const s = chartObj.chart.addCandlestickSeries({
                    upColor: 'rgba(41, 98, 255, 0.15)',
                    downColor: 'rgba(41, 98, 255, 0.15)',
                    borderVisible: false,
                    wickVisible: false,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    crosshairMarkerVisible: false,
                    autoscaleInfoProvider: () => null
                });
                s.setData(zoneData);
                activeBoxSeries[idx].push(s);
                
                const plUpper = chartObj.candleSeries.createPriceLine({
                    price: box.upper_boundary,
                    color: 'rgba(41, 98, 255, 0.60)',
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dotted,
                    axisLabelVisible: true,
                    title: `[${box.fib_level}] Upper`
                });
                
                const plLower = chartObj.candleSeries.createPriceLine({
                    price: box.lower_boundary,
                    color: 'rgba(41, 98, 255, 0.60)',
                    lineWidth: 1,
                    lineStyle: LightweightCharts.LineStyle.Dotted,
                    axisLabelVisible: true,
                    title: `[${box.fib_level}] Lower`
                });
                
                activeBoxPriceLines[idx].push(plUpper);
                activeBoxPriceLines[idx].push(plLower);
            }
        });
    }

    function updateReferenceBoxesUI(boxes) {
        const tbody = document.getElementById('reference-boxes-tbody');
        const badge = document.getElementById('ref-box-count-badge');
        
        if (badge) badge.textContent = boxes.length;
        if (!tbody) return;
        
        tbody.innerHTML = '';
        if (boxes.length === 0) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: #787b86; padding: 20px;">No active reference boxes.</td></tr>`;
            return;
        }
        
        boxes.forEach(box => {
            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid var(--border-color)';
            tr.style.height = '32px';
            
            let timeStr = '—';
            if (box.candle_timestamp) {
                const date = new Date(box.candle_timestamp * 1000);
                timeStr = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            }
            
            tr.innerHTML = `
                <td style="padding: 4px 20px;"><strong>${box.chart_type}</strong></td>
                <td style="padding: 4px 20px;"><span style="background: rgba(255,255,255,0.05); padding: 2px 6px; border-radius: 4px;">${box.fib_level}</span></td>
                <td style="padding: 4px 20px;">₹${box.upper_boundary.toFixed(2)}</td>
                <td style="padding: 4px 20px;">₹${box.lower_boundary.toFixed(2)}</td>
                <td style="padding: 4px 20px;"><span class="badge running" style="background: rgba(41, 98, 255, 0.15); color: #64b5f6; font-size:10px;">${box.box_status}</span></td>
                <td style="padding: 4px 20px; text-align: right; color: var(--text-secondary);">${timeStr}</td>
            `;
            tbody.appendChild(tr);
        });
    }

    function updateBuySignalsUI(signals) {
        const tbody = document.getElementById('buy-signals-tbody');
        const badge = document.getElementById('buy-signal-count-badge');
        
        // Count active signals (WAITING or CONFIRMED)
        const activeCount = signals.filter(s => s.signal_status === 'WAITING' || s.signal_status === 'CONFIRMED').length;
        if (badge) badge.textContent = activeCount;
        if (!tbody) return;
        
        tbody.innerHTML = '';
        if (signals.length === 0) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: #787b86; padding: 20px;">No buy signals.</td></tr>`;
            return;
        }
        
        signals.forEach(sig => {
            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid var(--border-color)';
            tr.style.height = '32px';
            
            let timeStr = '—';
            if (sig.created_at) {
                // created_at is SQLite local datetime YYYY-MM-DD HH:MM:SS
                const parts = sig.created_at.split(' ');
                timeStr = parts.length > 1 ? parts[1] : sig.created_at;
            }
            
            // Determine badge styles for status
            let badgeBg = 'rgba(120, 123, 134, 0.15)';
            let badgeColor = '#787b86';
            if (sig.signal_status === 'WAITING') {
                badgeBg = 'rgba(41, 98, 255, 0.15)';
                badgeColor = '#2962ff';
            } else if (sig.signal_status === 'CONFIRMED') {
                badgeBg = 'rgba(8, 153, 129, 0.15)';
                badgeColor = '#089981';
            } else if (sig.signal_status === 'REJECTED') {
                badgeBg = 'rgba(255, 152, 0, 0.15)';
                badgeColor = '#ff9800';
            }
            
            const breakoutLbl = sig.breakout_price ? `₹${sig.breakout_price.toFixed(2)}` : '—';
            
            tr.innerHTML = `
                <td style="padding: 4px 20px;"><strong>${sig.chart_type}</strong></td>
                <td style="padding: 4px 20px;"><span style="background: rgba(255,255,255,0.05); padding: 2px 6px; border-radius: 4px;">#${sig.reference_box_id}</span></td>
                <td style="padding: 4px 20px;">${breakoutLbl}</td>
                <td style="padding: 4px 20px;"><span class="badge" style="background: ${badgeBg}; color: ${badgeColor}; font-size:10px; padding: 2px 6px; border-radius: 4px; font-weight: 600;">${sig.signal_status}</span></td>
                <td style="padding: 4px 20px; font-weight: 600; color: ${sig.rejection_count > 0 ? '#ff9800' : 'var(--text-secondary)'};">${sig.rejection_count}</td>
                <td style="padding: 4px 20px; text-align: right; color: var(--text-secondary);">${timeStr}</td>
            `;
            tbody.appendChild(tr);
        });
    }

    function updateExecutionsUI(executions) {
        const tbody = document.getElementById('executions-tbody');
        const badge = document.getElementById('execution-count-badge');

        if (!executions) executions = [];
        if (badge) badge.textContent = executions.length;
        if (!tbody) return;

        tbody.innerHTML = '';
        if (executions.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: #787b86; padding: 20px;">No executions recorded.</td></tr>`;
            return;
        }

        executions.forEach(ex => {
            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid var(--border-color)';
            tr.style.height = '32px';
            tr.style.cursor = 'pointer';

            // Time formatting
            let timeStr = '—';
            if (ex.executed_at || ex.created_at) {
                const raw = ex.executed_at || ex.created_at;
                const parts = String(raw).split(' ');
                timeStr = parts.length > 1 ? parts[1] : raw;
            }

            // Status badge
            let badgeBg    = 'rgba(120,123,134,0.15)';
            let badgeColor = '#787b86';
            const st = (ex.execution_status || ex.status || '').toUpperCase();
            if (st === 'FILLED' || st === 'COMPLETE') {
                badgeBg = 'rgba(8,153,129,0.15)'; badgeColor = '#089981';
            } else if (st === 'PENDING' || st === 'OPEN') {
                badgeBg = 'rgba(41,98,255,0.15)'; badgeColor = '#2962ff';
            } else if (st === 'REJECTED' || st === 'CANCELLED') {
                badgeBg = 'rgba(242,54,69,0.15)'; badgeColor = '#f23645';
            }

            const orderId    = ex.broker_order_id || ex.order_id || `#${ex.id}`;
            const symbol     = ex.symbol || '—';
            const qty        = ex.quantity != null ? ex.quantity : '—';
            const reqPx      = ex.requested_price  != null ? `₹${Number(ex.requested_price).toFixed(2)}`  : '—';
            const execPx     = ex.executed_price   != null ? `₹${Number(ex.executed_price).toFixed(2)}`   : '—';

            tr.innerHTML = `
                <td style="padding:4px 10px;font-size:11px;color:#adb7c9;">${orderId}</td>
                <td style="padding:4px 10px;font-weight:600;">${symbol}</td>
                <td style="padding:4px 10px;">${qty}</td>
                <td style="padding:4px 10px;">${reqPx}</td>
                <td style="padding:4px 10px;font-weight:600;">${execPx}</td>
                <td style="padding:4px 10px;"><span style="background:${badgeBg};color:${badgeColor};font-size:10px;padding:2px 6px;border-radius:4px;font-weight:600;">${st || '—'}</span></td>
                <td style="padding:4px 10px;text-align:right;color:#787b86;">${timeStr}</td>
            `;
            tbody.appendChild(tr);
        });
    }

    function drawVisualIndicators(signals, executions, slEvents, slHistory, tgtEvents, tgtHistory) {
        let chartMarkers = [[], [], []];

        // 1. Clear previous buy signal layers and markers on all 3 charts
        for (let idx = 0; idx < 3; idx++) {
            const chartObj = window.activeCharts ? window.activeCharts[idx] : null;
            if (!chartObj) continue;
            
            activeSignalPriceLines[idx].forEach(pl => {
                try { chartObj.candleSeries.removePriceLine(pl); } catch(e) {}
            });
            activeSignalPriceLines[idx] = [];
            
            activeStopLossPriceLines[idx].forEach(pl => {
                try { chartObj.candleSeries.removePriceLine(pl); } catch(e) {}
            });
            activeStopLossPriceLines[idx] = [];
            
            activeTargetPriceLines[idx].forEach(pl => {
                try { chartObj.candleSeries.removePriceLine(pl); } catch(e) {}
            });
            activeTargetPriceLines[idx] = [];
            
            try { chartObj.candleSeries.setMarkers([]); } catch(e) {}
        }
        
        // 2. Parse signals and draw visual cues
        if (signals && signals.length) {
            signals.forEach(sig => {
                let idx = -1;
                if (sig.chart_type === 'SPOT') idx = 0;
                else if (sig.chart_type === 'CALL') idx = 1;
                else if (sig.chart_type === 'PUT') idx = 2;
                
                if (idx === -1) return;
                const chartObj = window.activeCharts ? window.activeCharts[idx] : null;
                if (!chartObj || !sig.trigger_candle_timestamp) return;
                
                // Render Breakout Line & Arrow Marker
                if (sig.signal_status === 'WAITING' || sig.signal_status === 'CONFIRMED') {
                    // Green breakout line
                    if (sig.breakout_price) {
                        const pl = chartObj.candleSeries.createPriceLine({
                            price: sig.breakout_price,
                            color: '#089981',
                            lineWidth: 2,
                            lineStyle: LightweightCharts.LineStyle.Solid,
                            axisLabelVisible: true,
                            title: `BUY Breakout: ₹${sig.breakout_price.toFixed(2)}`
                        });
                        activeSignalPriceLines[idx].push(pl);
                    }
                    
                    // Add green arrow marker above the bar
                    chartMarkers[idx].push({
                        time: sig.trigger_candle_timestamp,
                        position: 'aboveBar',
                        color: '#089981',
                        shape: 'arrowUp',
                        text: 'BUY'
                    });
                } else if (sig.signal_status === 'REJECTED') {
                    // Add orange arrow down marker indicating rejection
                    chartMarkers[idx].push({
                        time: sig.trigger_candle_timestamp,
                        position: 'aboveBar',
                        color: '#ff9800',
                        shape: 'arrowDown',
                        text: 'REJECT'
                    });
                }
            });
        }

        // 3. Parse executions and draw entry markers on CALL, SPOT, and PUT
        if (executions && executions.length) {
            executions.forEach(exec => {
                if (exec.execution_status === 'COMPLETE' && exec.execution_time) {
                    const execTs = parseLocalTimeStr(exec.execution_time);
                    const labelText = `ENTRY: ₹${exec.executed_price.toFixed(2)} (Qty: ${exec.quantity})`;
                    
                    // Place the entry marker on all three synchronized charts
                    for (let idx = 0; idx < 3; idx++) {
                        chartMarkers[idx].push({
                            time: execTs,
                            position: 'belowBar',
                            color: '#00c805',
                            shape: 'circle',
                            text: labelText
                        });
                    }
                }
            });
        }

        // 4. Parse active Stop Loss events and draw horizontal red stop-loss lines
        if (slEvents && slEvents.length) {
            slEvents.forEach(sl => {
                if (sl.exit_status === 'MONITORING' && sl.calculated_stop_loss) {
                    let idx = -1;
                    if (sl.chart_type === 'SPOT') idx = 0;
                    else if (sl.chart_type === 'CALL') idx = 1;
                    else if (sl.chart_type === 'PUT') idx = 2;
                    
                    if (idx === -1) return;
                    const chartObj = window.activeCharts ? window.activeCharts[idx] : null;
                    if (!chartObj) return;

                    const pl = chartObj.candleSeries.createPriceLine({
                        price: sl.calculated_stop_loss,
                        color: '#f23645',
                        lineWidth: 1.5,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        axisLabelVisible: true,
                        title: `SL level: ₹${sl.calculated_stop_loss.toFixed(2)}`
                    });
                    activeStopLossPriceLines[idx].push(pl);
                }
            });
        }

        // 5. Parse historical Stop Loss events and draw exit markers
        let showSlBanner = false;
        if (slHistory && slHistory.length) {
            slHistory.forEach(sl => {
                if (sl.exit_status === 'ORDER_COMPLETE') {
                    showSlBanner = true;
                    let idx = -1;
                    if (sl.chart_type === 'SPOT') idx = 0;
                    else if (sl.chart_type === 'CALL') idx = 1;
                    else if (sl.chart_type === 'PUT') idx = 2;
                    
                    if (idx === -1) return;
                    
                    const exitTs = sl.trigger_candle_timestamp || parseLocalTimeStr(sl.updated_at);
                    
                    chartMarkers[idx].push({
                        time: exitTs,
                        position: 'aboveBar',
                        color: '#f23645',
                        shape: 'arrowDown',
                        text: `SL HIT: ₹${sl.exit_price.toFixed(2)}`
                    });
                }
            });
        }
        updateStopLossBanners(showSlBanner);

        // 6. Parse active Target events and draw horizontal green target lines
        if (tgtEvents && tgtEvents.length) {
            tgtEvents.forEach(tgt => {
                if (tgt.exit_status === 'MONITORING' && tgt.target_price) {
                    let idx = -1;
                    if (tgt.chart_type === 'SPOT') idx = 0;
                    else if (tgt.chart_type === 'CALL') idx = 1;
                    else if (tgt.chart_type === 'PUT') idx = 2;
                    
                    if (idx === -1) return;
                    const chartObj = window.activeCharts ? window.activeCharts[idx] : null;
                    if (!chartObj) return;

                    const pl = chartObj.candleSeries.createPriceLine({
                        price: tgt.target_price,
                        color: '#089981',
                        lineWidth: 1.5,
                        lineStyle: LightweightCharts.LineStyle.Dashed,
                        axisLabelVisible: true,
                        title: `TGT ${tgt.target_level}: ₹${tgt.target_price.toFixed(2)}`
                    });
                    activeTargetPriceLines[idx].push(pl);
                }
            });
        }

        // 7. Parse historical Target events and draw exit markers
        let showTgtBanner = false;
        if (tgtHistory && tgtHistory.length) {
            tgtHistory.forEach(tgt => {
                if (tgt.exit_status === 'ORDER_COMPLETE') {
                    showTgtBanner = true;
                    let idx = -1;
                    if (tgt.chart_type === 'SPOT') idx = 0;
                    else if (tgt.chart_type === 'CALL') idx = 1;
                    else if (tgt.chart_type === 'PUT') idx = 2;
                    
                    if (idx === -1) return;
                    
                    const exitTs = tgt.trigger_candle_timestamp || parseLocalTimeStr(tgt.updated_at);
                    
                    chartMarkers[idx].push({
                        time: exitTs,
                        position: 'aboveBar',
                        color: '#089981',
                        shape: 'arrowDown',
                        text: `🎯 TGT HIT: ₹${tgt.exit_price.toFixed(2)}`
                    });
                }
            });
        }
        updateTargetBanners(showTgtBanner);
        
        // 8. Set combined markers chronologically on each chart
        for (let idx = 0; idx < 3; idx++) {
            const chartObj = window.activeCharts ? window.activeCharts[idx] : null;
            if (chartObj && chartMarkers[idx].length > 0) {
                chartMarkers[idx].sort((a, b) => a.time - b.time);
                chartObj.candleSeries.setMarkers(chartMarkers[idx]);
            }
        }
    }

    function parseLocalTimeStr(str) {
        if (!str) return Math.floor(Date.now() / 1000);
        const t = Date.parse(str.replace(' ', 'T'));
        if (isNaN(t)) return Math.floor(Date.now() / 1000);
        return Math.floor(t / 1000);
    }

    function updateConfirmationTimelineUI(confirmations) {
        if (!confirmations || confirmations.length === 0) {
            const bar = document.getElementById('confirmation-timeline-bar');
            if (bar) bar.style.display = 'none';
            updateChartConfirmBanners(null);
            return;
        }

        // Get the most recent session
        const session = confirmations[0];
        const bar = document.getElementById('confirmation-timeline-bar');
        if (bar) bar.style.display = 'flex';
        
        const timelineIdEl = document.getElementById('timeline-id');
        if (timelineIdEl) timelineIdEl.textContent = session.id;

        // Set status badge
        const badge = document.getElementById('timeline-status-badge');
        if (badge) {
            badge.textContent = session.confirmation_status;
            let badgeBg = 'rgba(120, 123, 134, 0.15)';
            let badgeColor = '#787b86';
            if (session.confirmation_status === 'WAITING') {
                badgeBg = 'rgba(41, 98, 255, 0.15)';
                badgeColor = '#2962ff';
            } else if (session.confirmation_status === 'CONFIRMED') {
                badgeBg = 'rgba(8, 153, 129, 0.15)';
                badgeColor = '#089981';
            } else if (session.confirmation_status === 'FAILED') {
                badgeBg = 'rgba(242, 54, 69, 0.15)';
                badgeColor = '#f23645';
            } else if (session.confirmation_status === 'EXPIRED') {
                badgeBg = 'rgba(120, 123, 134, 0.15)';
                badgeColor = '#787b86';
            }
            badge.style.background = badgeBg;
            badge.style.color = badgeColor;
        }

        // Render remaining time or status text
        const timerLbl = document.getElementById('timeline-timer');
        if (timerLbl) {
            if (session.confirmation_status === 'WAITING') {
                const now = Math.floor(Date.now() / 1000);
                const remaining = Math.max(0, session.confirmation_end_time - now);
                timerLbl.textContent = remaining + 's';
                timerLbl.style.color = remaining <= 10 ? '#f23645' : '#2962ff';
            } else if (session.confirmation_status === 'CONFIRMED') {
                timerLbl.textContent = '2/3 CONFIRMED ✓';
                timerLbl.style.color = '#089981';
            } else {
                timerLbl.textContent = session.confirmation_status;
                timerLbl.style.color = '#787b86';
            }
        }

        // Check which charts are confirmed
        const callSig = session.signals.find(s => s.chart_type === 'CALL');
        const spotSig = session.signals.find(s => s.chart_type === 'SPOT');
        const putSig = session.signals.find(s => s.chart_type === 'PUT');

        const formatLabel = (lblId, sig) => {
            const el = document.getElementById(lblId);
            if (!el) return;
            if (sig) {
                el.innerHTML = `BUY SIGNAL ✓ (₹${sig.breakout_price.toFixed(2)})`;
                el.style.color = '#089981';
            } else {
                if (session.confirmation_status === 'WAITING') {
                    el.innerHTML = 'WAITING ⏳';
                    el.style.color = '#ff9800';
                } else {
                    el.innerHTML = '—';
                    el.style.color = '#787b86';
                }
            }
        };

        formatLabel('timeline-call-lbl', callSig);
        formatLabel('timeline-spot-lbl', spotSig);
        formatLabel('timeline-put-lbl', putSig);

        // Update the absolute chart banners
        updateChartConfirmBanners(session.confirmation_status);
    }

    function updateChartConfirmBanners(status) {
        const charts = ['call-3m-chart', 'sensex-3m-chart', 'put-3m-chart'];
        charts.forEach(id => {
            const container = document.getElementById(id);
            if (!container) return;
            
            let banner = container.querySelector('.chart-confirm-banner');
            if (!banner) {
                banner = document.createElement('div');
                banner.className = 'chart-confirm-banner';
                banner.style.position = 'absolute';
                banner.style.top = '10px';
                banner.style.left = '10px';
                banner.style.zIndex = '10';
                banner.style.padding = '4px 10px';
                banner.style.borderRadius = '4px';
                banner.style.fontSize = '11px';
                banner.style.fontWeight = '700';
                banner.style.boxShadow = '0 2px 5px rgba(0,0,0,0.2)';
                container.style.position = 'relative';
                container.appendChild(banner);
            }
            
            if (!status) {
                banner.style.display = 'none';
                return;
            }
            
            banner.style.display = 'block';
            if (status === 'WAITING') {
                banner.style.background = 'rgba(255, 152, 0, 0.9)';
                banner.style.color = '#ffffff';
                banner.textContent = '⏳ WAITING CONFIRMATION';
            } else if (status === 'CONFIRMED') {
                banner.style.background = 'rgba(8, 153, 129, 0.9)';
                banner.style.color = '#ffffff';
                banner.textContent = '✅ 2/3 CONFIRMED';
            } else if (status === 'EXPIRED') {
                banner.style.background = 'rgba(120, 123, 134, 0.9)';
                banner.style.color = '#ffffff';
                banner.textContent = '⏰ EXPIRED';
            } else if (status === 'FAILED') {
                banner.style.background = 'rgba(242, 54, 69, 0.9)';
                banner.style.color = '#ffffff';
                banner.textContent = '❌ FAILED';
            }
        });
    }

    function updateStopLossBanners(hasHit) {
        const charts = ['call-3m-chart', 'sensex-3m-chart', 'put-3m-chart'];
        charts.forEach(id => {
            const container = document.getElementById(id);
            if (!container) return;
            
            let banner = container.querySelector('.sl-hit-banner');
            if (!banner) {
                banner = document.createElement('div');
                banner.className = 'sl-hit-banner';
                banner.style.position = 'absolute';
                banner.style.top = '40px';
                banner.style.right = '10px';
                banner.style.zIndex = '10';
                banner.style.padding = '4px 10px';
                banner.style.borderRadius = '4px';
                banner.style.fontSize = '11px';
                banner.style.fontWeight = '700';
                banner.style.boxShadow = '0 2px 5px rgba(0,0,0,0.2)';
                banner.style.background = 'rgba(242, 54, 69, 0.9)';
                banner.style.color = '#ffffff';
                banner.textContent = '❌ STOP LOSS HIT';
                container.appendChild(banner);
            }
            banner.style.display = hasHit ? 'block' : 'none';
        });
    }

    function updateTargetBanners(hasHit) {
        const charts = ['call-3m-chart', 'sensex-3m-chart', 'put-3m-chart'];
        charts.forEach(id => {
            const container = document.getElementById(id);
            if (!container) return;
            
            let banner = container.querySelector('.tgt-hit-banner');
            if (!banner) {
                banner = document.createElement('div');
                banner.className = 'tgt-hit-banner';
                banner.style.position = 'absolute';
                banner.style.top = '70px';
                banner.style.right = '10px';
                banner.style.zIndex = '10';
                banner.style.padding = '4px 10px';
                banner.style.borderRadius = '4px';
                banner.style.fontSize = '11px';
                banner.style.fontWeight = '700';
                banner.style.boxShadow = '0 2px 5px rgba(0,0,0,0.2)';
                banner.style.background = 'rgba(8, 153, 129, 0.9)';
                banner.style.color = '#ffffff';
                banner.textContent = '🎯 TARGET HIT';
                container.appendChild(banner);
            }
            banner.style.display = hasHit ? 'block' : 'none';
        });
    }

    function updatePnLUI(stats, runningTrades, slEvents, tgtEvents) {
        const formatPnL = (elId, val) => {
            const el = document.getElementById(elId);
            if (!el) return;
            el.textContent = `${val >= 0 ? '+' : ''}₹${val.toFixed(2)}`;
            el.style.color = val > 0 ? 'var(--call-color)' : (val < 0 ? 'var(--put-color)' : 'var(--text-primary)');
        };

        formatPnL('card-running-pnl', stats.running_pnl);
        formatPnL('card-closed-pnl', stats.closed_pnl);
        formatPnL('card-today-pnl', stats.today_pnl);
        formatPnL('card-total-pnl', stats.total_pnl);

        const spotPlLbl = document.querySelector('.spot-pl strong');
        if (spotPlLbl) {
            spotPlLbl.textContent = `${stats.running_pnl >= 0 ? '+' : ''}₹${stats.running_pnl.toFixed(2)}`;
            spotPlLbl.style.color = stats.running_pnl > 0 ? 'var(--call-color)' : (stats.running_pnl < 0 ? 'var(--put-color)' : 'var(--text-primary)');
        }

        const winRateEl = document.getElementById('card-win-rate');
        if (winRateEl) winRateEl.textContent = `${stats.win_rate.toFixed(1)}%`;
        const totalTradesEl = document.getElementById('card-total-trades');
        if (totalTradesEl) totalTradesEl.textContent = stats.total_trades;
        const wlRatioEl = document.getElementById('card-wl-ratio');
        if (wlRatioEl) wlRatioEl.textContent = `${stats.winning_trades}W / ${stats.losing_trades}L`;

        // Update Running Trades Table
        const tbody = document.getElementById('running-trades-tbody');
        const badge = document.getElementById('running-count-badge');
        
        if (badge) badge.textContent = runningTrades.length;
        if (!tbody) return;

        tbody.innerHTML = '';
        if (runningTrades.length === 0) {
            tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: #787b86; padding: 20px;">No active running trades.</td></tr>`;
            return;
        }

        // Build a stop loss map
        const slMap = {};
        if (slEvents) {
            slEvents.forEach(e => {
                slMap[e.trade_id] = e;
            });
        }

        // Build a target map
        const tgtMap = {};
        if (tgtEvents) {
            tgtEvents.forEach(e => {
                tgtMap[e.trade_id] = e;
            });
        }

        // Render table headers dynamically
        const thead = document.getElementById('running-trades-thead');
        if (thead) {
            if (activeTradesTab === 'sl') {
                thead.innerHTML = `
                    <tr style="background: #ffffff; color: #787b86; border-bottom: 1px solid #e0e3eb; height: 26px;">
                        <th style="padding: 4px 20px; font-weight: 500;">Trade ID</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Symbol</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Current Price</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Box Lower</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Calc SL</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Trigger</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Exit Status</th>
                        <th style="padding: 4px 20px; font-weight: 500; text-align: right;">Live P&L</th>
                    </tr>
                `;
            } else if (activeTradesTab === 'target') {
                thead.innerHTML = `
                    <tr style="background: #ffffff; color: #787b86; border-bottom: 1px solid #e0e3eb; height: 26px;">
                        <th style="padding: 4px 20px; font-weight: 500;">Trade ID</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Symbol</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Current Price</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Target Level</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Target Price</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Trigger</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Exit Status</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Distance</th>
                        <th style="padding: 4px 20px; font-weight: 500; text-align: right;">Live P&L</th>
                    </tr>
                `;
            } else {
                thead.innerHTML = `
                    <tr style="background: #ffffff; color: #787b86; border-bottom: 1px solid #e0e3eb; height: 26px;">
                        <th style="padding: 4px 20px; font-weight: 500;">Strategy</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Symbol</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Type</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Qty</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Entry Price</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Current Price</th>
                        <th style="padding: 4px 20px; font-weight: 500;">Live P&L</th>
                        <th style="padding: 4px 20px; font-weight: 500; text-align: right;">Action</th>
                    </tr>
                `;
            }
        }

        runningTrades.forEach(t => {
            const tr = document.createElement('tr');
            tr.style.borderBottom = '1px solid var(--border-color)';
            tr.style.height = '32px';
            tr.style.cursor = 'pointer';

            const symbol = t.call_symbol || t.put_symbol;
            const pnlClass = t.pnl > 0 ? 'up' : (t.pnl < 0 ? 'down' : '');
            const pnlSign = t.pnl > 0 ? '+' : '';
            const sl = slMap[t.id];
            const tgt = tgtMap[t.id];

            if (activeTradesTab === 'sl') {
                const boxLower = (sl && sl.reference_box_lower) ? `₹${sl.reference_box_lower.toFixed(2)}` : '—';
                const calcSL = (sl && sl.calculated_stop_loss) ? `₹${sl.calculated_stop_loss.toFixed(2)}` : '—';
                
                let triggerText = 'MONITORING 🛡️';
                let triggerColor = '#089981';
                if (sl && sl.trigger_candle_timestamp) {
                    triggerText = 'TRIGGERED ⚠️';
                    triggerColor = '#f23645';
                }

                const exitStatusText = sl ? sl.exit_status : '—';

                tr.innerHTML = `
                    <td style="padding: 4px 20px;"><strong>#${t.id}</strong></td>
                    <td style="padding: 4px 20px; font-size:11px;">${symbol}</td>
                    <td style="padding: 4px 20px;">₹${t.current_price.toFixed(2)}</td>
                    <td style="padding: 4px 20px; color:#787b86;">${boxLower}</td>
                    <td style="padding: 4px 20px; font-weight:600; color:#f23645;">${calcSL}</td>
                    <td style="padding: 4px 20px; font-weight:600; color:${triggerColor};">${triggerText}</td>
                    <td style="padding: 4px 20px; font-weight:600; color:#ff9800;">${exitStatusText}</td>
                    <td style="padding: 4px 20px; font-weight:700; text-align: right;" class="val ${pnlClass}">${pnlSign}₹${t.pnl.toFixed(2)}</td>
                `;
            } else if (activeTradesTab === 'target') {
                const tgtLvl = tgt ? tgt.target_level : '—';
                const tgtPrice = (tgt && tgt.target_price) ? `₹${tgt.target_price.toFixed(2)}` : '—';
                
                let triggerText = 'MONITORING 🛡️';
                let triggerColor = '#089981';
                if (tgt && tgt.trigger_candle_timestamp) {
                    triggerText = 'TRIGGERED 🎯';
                    triggerColor = '#089981';
                }

                const exitStatusText = tgt ? tgt.exit_status : '—';

                let distanceText = '—';
                if (tgt && tgt.target_price) {
                    const dist = tgt.target_price - t.current_price;
                    distanceText = dist <= 0 ? 'Hit' : `₹${dist.toFixed(2)}`;
                }

                tr.innerHTML = `
                    <td style="padding: 4px 20px;"><strong>#${t.id}</strong></td>
                    <td style="padding: 4px 20px; font-size:11px;">${symbol}</td>
                    <td style="padding: 4px 20px;">₹${t.current_price.toFixed(2)}</td>
                    <td style="padding: 4px 20px; color:#787b86;">${tgtLvl}</td>
                    <td style="padding: 4px 20px; font-weight:600; color:#089981;">${tgtPrice}</td>
                    <td style="padding: 4px 20px; font-weight:600; color:${triggerColor};">${triggerText}</td>
                    <td style="padding: 4px 20px; font-weight:600; color:#ff9800;">${exitStatusText}</td>
                    <td style="padding: 4px 20px; color:#787b86;">${distanceText}</td>
                    <td style="padding: 4px 20px; font-weight:700; text-align: right;" class="val ${pnlClass}">${pnlSign}₹${t.pnl.toFixed(2)}</td>
                `;
            } else {
                // Create Exit Button
                const exitTd = document.createElement('td');
                exitTd.style.padding = '4px 20px';
                exitTd.style.textAlign = 'right';
                
                const exitBtn = document.createElement('button');
                exitBtn.className = 'tv-btn';
                exitBtn.style.padding = '2px 8px';
                exitBtn.style.fontSize = '11px';
                exitBtn.style.background = 'var(--put-color)';
                exitBtn.style.color = 'white';
                exitBtn.style.border = 'none';
                exitBtn.textContent = 'Exit';
                exitBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    handleManualExit(t.id, t.current_price);
                });
                exitTd.appendChild(exitBtn);

                tr.innerHTML = `
                    <td style="padding: 4px 20px;"><strong>${t.strategy_name || 'Manual'}</strong></td>
                    <td style="padding: 4px 20px; font-size:11px;">${symbol}</td>
                    <td style="padding: 4px 20px;"><span style="font-weight:600; color: ${t.direction === 'BUY' ? 'var(--call-color)' : 'var(--put-color)'};">${t.direction}</span></td>
                    <td style="padding: 4px 20px;">${t.quantity}</td>
                    <td style="padding: 4px 20px;">₹${t.entry_price.toFixed(2)}</td>
                    <td style="padding: 4px 20px;">₹${t.current_price.toFixed(2)}</td>
                    <td style="padding: 4px 20px; font-weight:700;" class="val ${pnlClass}">${pnlSign}₹${t.pnl.toFixed(2)}</td>
                `;
                tr.appendChild(exitTd);
            }
            
            tr.addEventListener('click', () => {
                window.location.href = `/trades/${t.id}`;
            });

            tbody.appendChild(tr);
        });
    }

    async function handleManualExit(tradeId, currentLtp) {
        if (confirm("Are you sure you want to exit this trade?")) {
            try {
                const url = window.IS_SIMULATION ? `/api/simulate/trades/${tradeId}/close` : `/api/trades/${tradeId}/close`;
                const response = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ exit_price: currentLtp, exit_reason: 'Manual Exit via Dashboard' })
                });
                const res = await response.json();
                if (res.status === 'success') {
                    showToastLocal(`✅ Position Closed. Realised P&L: ₹${res.pnl.toFixed(2)}`, res.pnl >= 0 ? 'buy' : 'sell');
                    if (window.IS_SIMULATION) {
                        if (window.fetchSimData) await window.fetchSimData(false);
                    } else {
                        pollLivePnL();
                    }
                } else {
                    showToastLocal(`❌ Exit Failed: ${res.message}`, 'sell');
                }
            } catch(e) {
                console.error(e);
            }
        }
    }

    if (!window.IS_SIMULATION) {
        pollLivePnL();
        pnlPollInterval = setInterval(pollLivePnL, 1000);
    } else {
        // Expose internal rendering functions for the simulation controller.
        // These are scoped inside this IIFE so they must be explicitly surfaced.
        window.updatePnLUI            = updatePnLUI;
        window.updateReferenceBoxesUI = updateReferenceBoxesUI;
        window.updateBuySignalsUI     = updateBuySignalsUI;
        window.updateExecutionsUI     = updateExecutionsUI;
        window.drawReferenceBoxes     = drawReferenceBoxes;
        window.drawVisualIndicators   = drawVisualIndicators;
        window.updateConfirmationTimelineUI = typeof updateConfirmationTimelineUI !== 'undefined' ? updateConfirmationTimelineUI : null;
        window.handleManualExit       = typeof handleManualExit !== 'undefined' ? handleManualExit : null;
        window.pollLivePnL            = typeof pollLivePnL !== 'undefined' ? pollLivePnL : null;
    }
});

/* ================================================================
   MONITORING MODULE – Milestone 10
   Manages the Side Drawer, Health Strip, Analytics, Logs,
   Notifications, Sound Alerts, Browser Push, and Config.
   ================================================================ */

// ── Drawer state ────────────────────────────────────────────────
let _drawerOpen = false;
let _activeTab  = 'health';

function toggleMonitorDrawer(tab) {
    if (_drawerOpen && _activeTab === tab) {
        closeMonitorDrawer();
    } else {
        openMonitorDrawer(tab);
    }
}

function openMonitorDrawer(tab) {
    _drawerOpen = true;
    _activeTab  = tab || _activeTab;
    document.getElementById('monitor-drawer').classList.add('open');
    document.getElementById('drawer-overlay').style.display = 'block';
    switchDrawerTab(_activeTab);
    // Eagerly refresh the opened panel
    if (_activeTab === 'health')         refreshHealth();
    if (_activeTab === 'analytics')      refreshAnalytics();
    if (_activeTab === 'logs')           refreshLogs();
    if (_activeTab === 'notifications')  renderNotificationsList();
    if (_activeTab === 'config')         loadConfig();
}

function closeMonitorDrawer() {
    _drawerOpen = false;
    document.getElementById('monitor-drawer').classList.remove('open');
    document.getElementById('drawer-overlay').style.display = 'none';
}

function switchDrawerTab(tab) {
    _activeTab = tab;
    const tabs   = ['health', 'analytics', 'logs', 'notifications', 'config'];
    tabs.forEach(t => {
        const btn   = document.getElementById(`dtab-${t}`);
        const panel = document.getElementById(`dpanel-${t}`);
        if (!btn || !panel) return;
        const isActive = t === tab;
        btn.style.color       = isActive ? '#2962ff' : '#787b86';
        btn.style.borderBottom= isActive ? '2px solid #2962ff' : '2px solid transparent';
        panel.style.display   = isActive ? 'block' : 'none';
    });
    if (tab === 'health')        refreshHealth();
    if (tab === 'analytics')     refreshAnalytics();
    if (tab === 'logs')          refreshLogs();
    if (tab === 'notifications') renderNotificationsList();
    if (tab === 'config')        loadConfig();
}

// ── Health polling ───────────────────────────────────────────────
async function refreshHealth() {
    try {
        const res  = await fetch('/api/system/health');
        const data = await res.json();
        if (!data) return;

        const statusColor = (s) => s === 'HEALTHY' || s === 'CONNECTED' || s === 'LIVE' ? '#089981' : '#f23645';
        const statusDot   = (s) => (s === 'HEALTHY' || s === 'CONNECTED' || s === 'LIVE') ? '●' : '✖';

        // Health strip (always visible)
        setEl('hs-broker',  data.broker_status   || '--');
        setEl('hs-ws',      data.websocket_status || '--');
        setEl('hs-db',      data.database_status  || '--');
        setEl('hs-cache',   data.cache_status     || '--');
        setEl('hs-cpu',     data.cpu_percent != null ? `${data.cpu_percent.toFixed(1)}%` : '--');
        setEl('hs-mem',     data.memory_percent != null ? `${data.memory_percent.toFixed(1)}%` : '--');
        setEl('hs-api-lat', data.api_latency_ms  != null ? `${data.api_latency_ms}ms` : '--');
        setEl('hs-tick',    data.last_tick_time   ? fmtTime(data.last_tick_time) : '--');

        // Nav health indicator
        const allOk = ['broker_status','websocket_status','database_status'].every(
            k => data[k] && (data[k].toUpperCase().includes('HEALTH') || data[k].toUpperCase().includes('CONNECT') || data[k].toUpperCase().includes('LIVE') || data[k].toUpperCase().includes('OK'))
        );
        const dot = document.getElementById('nav-health-dot');
        const lbl = document.getElementById('nav-health-lbl');
        if (dot) dot.style.background = allOk ? '#089981' : '#f23645';
        if (lbl) lbl.textContent = allOk ? 'SYSTEM OK' : 'ALERT';

        // Drawer panels
        const dpBroker = document.getElementById('dp-broker');
        if (dpBroker) { dpBroker.textContent = `${statusDot(data.broker_status)} ${data.broker_status || '--'}`; dpBroker.style.color = statusColor(data.broker_status || ''); }
        const dpWs = document.getElementById('dp-ws');
        if (dpWs) { dpWs.textContent = `${statusDot(data.websocket_status)} ${data.websocket_status || '--'}`; dpWs.style.color = statusColor(data.websocket_status || ''); }
        const dpDb = document.getElementById('dp-db');
        if (dpDb) { dpDb.textContent = `${statusDot(data.database_status)} ${data.database_status || '--'}`; dpDb.style.color = statusColor(data.database_status || ''); }
        const dpCache = document.getElementById('dp-cache');
        if (dpCache) { dpCache.textContent = `${statusDot(data.cache_status)} ${data.cache_status || '--'}`; dpCache.style.color = statusColor(data.cache_status || ''); }

        const cpu = data.cpu_percent || 0;
        const mem = data.memory_percent || 0;
        setEl('dp-cpu',     `${cpu.toFixed(1)}%`);
        setEl('dp-mem',     `${mem.toFixed(1)}%`);
        setEl('dp-api-lat', data.api_latency_ms != null ? `${data.api_latency_ms} ms` : '--');
        setEl('dp-tick',    data.last_tick_time ? fmtTime(data.last_tick_time) : '--');

        const cpuBar = document.getElementById('dp-cpu-bar');
        const memBar = document.getElementById('dp-mem-bar');
        if (cpuBar) cpuBar.style.width = `${Math.min(cpu, 100)}%`;
        if (memBar) memBar.style.width = `${Math.min(mem, 100)}%`;
        if (cpuBar) cpuBar.style.background = cpu > 80 ? '#f23645' : cpu > 60 ? '#ff9800' : '#2962ff';
        if (memBar) memBar.style.background = mem > 85 ? '#f23645' : mem > 70 ? '#ff9800' : '#089981';

    } catch(e) { console.warn('Health refresh error:', e); }
}

// ── Analytics ────────────────────────────────────────────────────
async function refreshAnalytics() {
    try {
        const res  = await fetch('/api/strategy/analytics');
        const data = await res.json();
        if (!data) return;

        const fmt  = (v) => `₹${(v || 0).toFixed(2)}`;
        const pct  = (v) => `${(v || 0).toFixed(1)}%`;

        setEl('an-net',       fmt(data.net_profit));
        setEl('an-winrate',   pct(data.win_rate));
        setEl('an-pf',        data.profit_factor != null ? data.profit_factor.toFixed(2) : '—');
        setEl('an-dd',        fmt(data.max_drawdown));
        setEl('an-avg-profit',fmt(data.avg_profit));
        setEl('an-avg-loss',  fmt(data.avg_loss));
        setEl('an-lwin',      fmt(data.largest_win));
        setEl('an-lloss',     fmt(data.largest_loss));
        setEl('an-boxes',     data.total_boxes    || 0);
        setEl('an-signals',   data.total_signals  || 0);

        const confRate = data.confirmation_rate != null ? `${data.confirmation_rate.toFixed(1)}%` : '0%';
        const tgtPct   = data.target_exit_pct   != null ? `${data.target_exit_pct.toFixed(1)}%`  : '0%';
        const slPct    = data.sl_exit_pct        != null ? `${data.sl_exit_pct.toFixed(1)}%`      : '0%';
        setEl('an-conf-rate', confRate);
        setEl('an-tgt-pct',   tgtPct);
        setEl('an-sl-pct',    slPct);

        // Colour net profit
        const netEl = document.getElementById('an-net');
        if (netEl) netEl.style.color = (data.net_profit || 0) >= 0 ? '#089981' : '#f23645';

    } catch(e) { console.warn('Analytics refresh error:', e); }
}

// ── Logs ─────────────────────────────────────────────────────────
async function refreshLogs() {
    const search   = (document.getElementById('log-search')   || {}).value || '';
    const severity = (document.getElementById('log-severity') || {}).value || '';
    const list     = document.getElementById('logs-list');
    if (!list) return;

    try {
        const params = new URLSearchParams();
        if (severity) params.set('severity', severity);
        if (search)   params.set('search',   search);
        params.set('limit', '80');
        const url = window.IS_SIMULATION ? `/api/simulate/system/logs?${params}` : `/api/system/logs?${params}`;
        const res  = await fetch(url);
        const data = await res.json();
        const events = data.events || data.logs || data || [];

        if (!events.length) {
            list.innerHTML = '<div style="color:#787b86;font-size:12px;text-align:center;padding:20px;">No events logged yet.</div>';
            return;
        }
        list.innerHTML = events.map(e => {
            const sev   = e.severity || 'INFO';
            const color = sev === 'CRITICAL' ? '#f23645' : sev === 'WARNING' ? '#ff9800' : '#adb7c9';
            const badge = sev === 'CRITICAL' ? '#f23645' : sev === 'WARNING' ? '#ff9800' : '#2a2e39';
            return `<div style="background:#131722;border:1px solid #2a2e39;border-radius:6px;padding:8px 10px;font-size:11px;">
                <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
                    <span style="background:${badge};color:white;padding:1px 5px;border-radius:3px;font-size:10px;font-weight:700;">${sev}</span>
                    <span style="color:#787b86;">${fmtTime(e.timestamp || e.created_at)}</span>
                </div>
                <div style="color:${color};font-weight:600;">${e.event_type || e.category || ''}</div>
                <div style="color:#787b86;margin-top:2px;">${e.message || e.details || ''}</div>
            </div>`;
        }).join('');
    } catch(e) { console.warn('Logs refresh error:', e); }
}

// ── In-app notifications list ────────────────────────────────────
const _notifications = [];
let _unreadCount = 0;

function addNotification(icon, title, body, type = 'info') {
    _notifications.unshift({ icon, title, body, type, time: new Date().toISOString() });
    if (_notifications.length > 50) _notifications.pop();
    _unreadCount++;
    const badge = document.getElementById('notif-badge');
    if (badge) { badge.textContent = _unreadCount; badge.style.display = 'inline'; }
    renderNotificationsList();
    // Browser & sound alerts
    playSoundAlert(type);
    sendBrowserNotification(title, body);
}

function renderNotificationsList() {
    const list = document.getElementById('notifications-list');
    if (!list) return;
    if (!_notifications.length) {
        list.innerHTML = '<div style="color:#787b86;font-size:12px;text-align:center;padding:20px;">No alerts yet.</div>';
        return;
    }
    const colorMap = { buy: '#089981', sell: '#f23645', warning: '#ff9800', info: '#2962ff' };
    list.innerHTML = _notifications.map(n => {
        const col = colorMap[n.type] || '#2962ff';
        return `<div style="background:#131722;border:1px solid #2a2e39;border-left:3px solid ${col};border-radius:6px;padding:10px 12px;">
            <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
                <strong style="font-size:12px;color:#d1d4dc;">${n.icon} ${n.title}</strong>
                <span style="font-size:10px;color:#787b86;">${fmtTime(n.time)}</span>
            </div>
            <div style="font-size:11px;color:#adb7c9;">${n.body}</div>
        </div>`;
    }).join('');
}

function clearNotifications() {
    _notifications.length = 0;
    _unreadCount = 0;
    const badge = document.getElementById('notif-badge');
    if (badge) badge.style.display = 'none';
    renderNotificationsList();
}

// ── Sound Alerts ─────────────────────────────────────────────────
const _audioCtx = (typeof AudioContext !== 'undefined') ? new AudioContext() : null;

function playSoundAlert(type) {
    if (!_audioCtx) return;
    const soundEl = document.getElementById('cfg-sound');
    if (soundEl && !soundEl.checked) return;
    try {
        const osc  = _audioCtx.createOscillator();
        const gain = _audioCtx.createGain();
        osc.connect(gain);
        gain.connect(_audioCtx.destination);
        osc.type = 'sine';
        if (type === 'buy')     osc.frequency.setValueAtTime(880, _audioCtx.currentTime);
        else if (type === 'sell') osc.frequency.setValueAtTime(440, _audioCtx.currentTime);
        else                    osc.frequency.setValueAtTime(660, _audioCtx.currentTime);
        gain.gain.setValueAtTime(0.2, _audioCtx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.0001, _audioCtx.currentTime + 0.3);
        osc.start(_audioCtx.currentTime);
        osc.stop(_audioCtx.currentTime + 0.3);
    } catch(e) {}
}

// ── Browser Push Notifications ───────────────────────────────────
function sendBrowserNotification(title, body) {
    const el = document.getElementById('cfg-browser-notif');
    if (el && !el.checked) return;
    if (!('Notification' in window)) return;
    if (Notification.permission === 'granted') {
        new Notification(title, { body });
    } else if (Notification.permission !== 'denied') {
        Notification.requestPermission().then(perm => {
            if (perm === 'granted') new Notification(title, { body });
        });
    }
}

// ── Configuration save / load ────────────────────────────────────
async function loadConfig() {
    try {
        const res  = await fetch('/api/config');
        const data = await res.json();
        if (!data || !data.config) return;
        const cfg = data.config;
        setCheckbox('cfg-strategy-enabled', cfg.strategy_enabled !== false);
        setSelectVal('cfg-sizing-type',     cfg.sizing_type     || 'FIXED');
        setInputVal('cfg-fixed-qty',        cfg.fixed_quantity  != null ? cfg.fixed_quantity : 10);
        setInputVal('cfg-conf-timeout',     cfg.confirmation_timeout_seconds != null ? cfg.confirmation_timeout_seconds : 30);
        setInputVal('cfg-sl-offset',        cfg.stop_loss_offset_points != null ? cfg.stop_loss_offset_points : 5.0);
        setSelectVal('cfg-target-level',    cfg.target_fib_level || '1.39');
    } catch(e) { console.warn('Config load error:', e); }
}

async function saveConfig() {
    const payload = {
        strategy_enabled:             document.getElementById('cfg-strategy-enabled')?.checked ?? true,
        sizing_type:                  document.getElementById('cfg-sizing-type')?.value || 'FIXED',
        fixed_quantity:               parseInt(document.getElementById('cfg-fixed-qty')?.value || 10),
        confirmation_timeout_seconds: parseInt(document.getElementById('cfg-conf-timeout')?.value || 30),
        stop_loss_offset_points:      parseFloat(document.getElementById('cfg-sl-offset')?.value || 5.0),
        target_fib_level:             document.getElementById('cfg-target-level')?.value || '1.39',
    };
    try {
        const res = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        const msg = document.getElementById('cfg-save-msg');
        if (msg) {
            msg.style.display = 'block';
            msg.textContent   = result.status === 'success' ? '✅ Configuration saved!' : `❌ ${result.message}`;
            msg.style.color   = result.status === 'success' ? '#089981' : '#f23645';
            setTimeout(() => { msg.style.display = 'none'; }, 3000);
        }
        if (result.status === 'success') {
            addNotification('⚙️', 'Config Updated', 'Strategy configuration has been saved.', 'info');
        }
    } catch(e) { console.warn('Config save error:', e); }
}

// ── Periodic auto-refresh ────────────────────────────────────────
setInterval(refreshHealth, 15000);  // health strip always refreshes
setInterval(() => {
    if (_drawerOpen) {
        if (_activeTab === 'health')    refreshHealth();
        if (_activeTab === 'analytics') refreshAnalytics();
        if (_activeTab === 'logs')      refreshLogs();
    }
}, 10000);

// Initial health strip load
refreshHealth();

// ── Helper utilities ─────────────────────────────────────────────
function setEl(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}
function setCheckbox(id, val) {
    const el = document.getElementById(id);
    if (el) el.checked = val;
}
function setSelectVal(id, val) {
    const el = document.getElementById(id);
    if (el) el.value = val;
}
function setInputVal(id, val) {
    const el = document.getElementById(id);
    if (el) el.value = val;
}
function fmtTime(ts) {
    if (!ts) return '--';
    try {
        const d = new Date(ts);
        if (isNaN(d)) return ts;
        return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch { return ts; }
}

/* ── Cleanup of invalid exports ── */
window.syncActiveSymbols = function(data) {
    if (data.call && data.call.symbol) activeCeSymbol = data.call.symbol;
    if (data.put && data.put.symbol) activePeSymbol = data.put.symbol;
};

/* ── Hook into existing trade events for in-app notifications ── */
// Override showToast to also push to notification list
const _origShowToast = window.showToast || showToast;
window.showToast = function(message, type) {
    if (_origShowToast) _origShowToast(message, type);
    const clean = message.replace(/<[^>]+>/g, '');
    const icon  = type === 'buy'  ? '📈' : type === 'sell' ? '📉' : 'ℹ️';
    addNotification(icon, type === 'buy' ? 'BUY Signal' : type === 'sell' ? 'SELL / SL Alert' : 'Alert', clean, type);
};
