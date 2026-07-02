from flask import Flask, render_template, jsonify, send_from_directory, request
from data_engine import get_dashboard_data, search_contracts, set_active_tokens
import os

app = Flask(__name__)

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/')
def index():
    from flask import redirect
    return redirect('/dashboard?broker=angelone&underlying=SENSEX&ce_symbol=SENSEX2670276900CE&pe_symbol=SENSEX2670276900PE&strategy=institutional')

@app.route('/dashboard')
def dashboard():
    ce_symbol = request.args.get('ce_symbol')
    pe_symbol = request.args.get('pe_symbol')
    if ce_symbol and pe_symbol:
        set_active_tokens(ce_symbol, pe_symbol)
    return render_template('dashboard.html')

@app.route('/api/data')
def get_data():
    """Returns the JSON data for the dashboard."""
    data = get_dashboard_data()
    return jsonify(data)

@app.route('/api/search')
def search():
    query = request.args.get('q', '').upper()
    if not query:
        return jsonify([])
    results = search_contracts(query)
    return jsonify(results)

@app.route('/api/update_tokens', methods=['POST'])
def update_tokens():
    payload = request.json
    ce_symbol = payload.get('ce_symbol')
    pe_symbol = payload.get('pe_symbol')
    if ce_symbol and pe_symbol:
        set_active_tokens(ce_symbol, pe_symbol)
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error', 'message': 'Invalid symbols'})

@app.route('/api/set_fib', methods=['POST'])
def set_fib():
    payload = request.json
    symbol = payload.get('symbol')
    high = payload.get('high')
    low = payload.get('low')
    if symbol and high and low:
        from data_engine import MANUAL_FIBS
        MANUAL_FIBS[symbol] = {'high': float(high), 'low': float(low)}
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error'})

@app.route('/api/live')
def get_live():
    from data_engine import LIVE_PRICES
    return jsonify(LIVE_PRICES)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
