"""
test_monitoring.py – Unit + Integration tests for Milestone 10
Strategy Monitoring Engine and Notification Engine

Run from the project root:
    python -m pytest strategy/test_monitoring.py -v
"""

import sys, os, sqlite3, types, json

# ── Path setup ───────────────────────────────────────────────────
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Stub heavy optional libs before importing project code
for _mod in ('smartapi', 'pyotp', 'websocket'):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

import pytest
import db
from strategy import strategy_monitor as sm
from strategy import notification_engine as ne


# ── Fixtures ─────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _init_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / 'test.db')
    monkeypatch.setattr(db, 'DB_PATH', db_file)
    db.init_db()
    yield db_file


def _insert_trade(pnl, status='CLOSED', exit_reason='Manual Exit'):
    """
    Insert a trade directly into the DB using create_trade + close_trade
    so all required columns are populated.
    """
    trade_id = db.create_trade(
        user_id=1, broker='TestBroker', underlying='SENSEX',
        expiry='2026-07-31', call_symbol='SYM-CE', put_symbol='SYM-PE',
        entry_price=100.0, quantity=1, stop_loss=90.0, target=120.0,
        strategy_name='TEST', direction='BUY'
    )
    if status in ('CLOSED', 'TARGET_HIT', 'STOP_LOSS_HIT'):
        exit_price = 100.0 + pnl  # qty=1, so PnL = exit-entry
        # Directly update pnl and status as close_trade recalculates from qty
        conn = db.get_db_connection()
        conn.execute(
            "UPDATE trades SET status=?, pnl=?, exit_price=?, exit_time=datetime('now'), exit_reason=? WHERE id=?",
            (status, pnl, exit_price, exit_reason, trade_id)
        )
        conn.commit()
        conn.close()
    return trade_id


def _insert_reference_box(n=1):
    conn = db.get_db_connection()
    import time as _time
    for i in range(n):
        # Use distinct timestamps to avoid UNIQUE constraint violation
        ts = f"2026-07-01 09:{i:02d}:00"
        conn.execute('''
            INSERT INTO reference_boxes
            (user_id, chart_type, instrument_symbol, timeframe,
             fib_direction, fib_level, candle_timestamp,
             candle_open, candle_high, candle_low, candle_close,
             upper_boundary, lower_boundary, box_status, crossed_direction,
             created_at, updated_at)
            VALUES (1,'CALL','SYM','3m','HIGH_TO_LOW',0.786,
                    ?,100,110,90,105,110,90,'ACTIVE','UPWARD',
                    datetime('now'),datetime('now'))
        ''', (ts,))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════
# strategy_monitor tests
# ══════════════════════════════════════════════════════════════════
class TestStrategyMonitor:

    def test_calculate_live_statistics_no_trades(self):
        result = sm.calculate_live_statistics(user_id=1)
        assert result['total_completed'] == 0
        assert result['win_rate']        == 0.0
        assert result['net_profit']      == 0.0

    def test_calculate_live_statistics_all_wins(self):
        for pnl in [100.0, 200.0, 50.0]:
            _insert_trade(pnl)
        result = sm.calculate_live_statistics(user_id=1)
        assert result['total_completed'] == 3
        assert result['winning_trades']  == 3
        assert result['losing_trades']   == 0
        assert result['win_rate']        == 100.0
        assert result['net_profit']      == pytest.approx(350.0)

    def test_calculate_live_statistics_mixed(self):
        _insert_trade(200.0)
        _insert_trade(-100.0)
        result = sm.calculate_live_statistics(user_id=1)
        assert result['total_completed'] == 2
        assert result['win_rate']        == 50.0
        assert result['net_profit']      == pytest.approx(100.0)
        assert result['largest_win']     == pytest.approx(200.0)
        assert result['largest_loss']    == pytest.approx(-100.0)

    def test_profit_factor_calculated(self):
        _insert_trade(300.0)
        _insert_trade(-100.0)
        result = sm.calculate_live_statistics(user_id=1)
        # gross_profit=300, gross_loss=100 → factor=3.0
        assert result['profit_factor']   == pytest.approx(3.0)

    def test_max_drawdown_is_non_negative(self):
        _insert_trade(100.0)
        _insert_trade(-200.0)
        _insert_trade(50.0)
        result = sm.calculate_live_statistics(user_id=1)
        # max_drawdown is stored as absolute drawdown value
        assert result['max_drawdown']    >= 0

    def test_avg_profit_and_loss(self):
        _insert_trade(100.0)
        _insert_trade(200.0)
        _insert_trade(-50.0)
        _insert_trade(-150.0)
        result = sm.calculate_live_statistics(user_id=1)
        assert result['avg_profit']      == pytest.approx(150.0)
        # avg_loss = gross_loss / losing_trades = 200/2 = 100
        assert result['avg_loss']        == pytest.approx(100.0)

    def test_running_trades_excluded(self):
        # RUNNING trades should not be counted in completed stats
        db.create_trade(
            user_id=1, broker='B', underlying='SENSEX',
            expiry='2026-07-31', call_symbol='SYM-CE', put_symbol='SYM-PE',
            entry_price=100.0, quantity=1, stop_loss=90.0, target=120.0,
            strategy_name='TEST', direction='BUY'
        )
        result = sm.calculate_live_statistics(user_id=1)
        assert result['total_completed'] == 0

    def test_boxes_created_funnel_count(self):
        _insert_reference_box(n=4)
        result = sm.calculate_live_statistics(user_id=1)
        assert result['boxes_created']   == 4

    def test_collect_system_metrics_returns_dict(self):
        metrics = sm.collect_system_metrics()
        assert isinstance(metrics, dict)
        # Either cpu_percent or cpu_usage key is acceptable
        has_cpu = 'cpu_percent' in metrics or 'cpu_usage' in metrics
        has_mem = 'memory_percent' in metrics or 'memory_usage' in metrics
        assert has_cpu, f"No CPU key found in metrics: {list(metrics.keys())}"
        assert has_mem, f"No Memory key found in metrics: {list(metrics.keys())}"

    def test_collect_system_metrics_cpu_in_range(self):
        metrics = sm.collect_system_metrics()
        cpu = metrics.get('cpu_percent', metrics.get('cpu_usage', 0))
        assert 0 <= cpu <= 100

    def test_monitor_strategy_returns_result(self):
        result = sm.monitor_strategy(user_id=1)
        # monitor_strategy may return session_id (int), dict, or None — just ensure no exception
        assert result is not None  # returns at least a truthy session id

    def test_win_rate_accuracy(self):
        wins, losses = 7, 3
        for _ in range(wins):   _insert_trade(100.0)
        for _ in range(losses): _insert_trade(-100.0)
        result   = sm.calculate_live_statistics(user_id=1)
        expected = (wins / (wins + losses)) * 100
        assert result['win_rate'] == pytest.approx(expected, abs=0.01)


# ══════════════════════════════════════════════════════════════════
# notification_engine tests
# ══════════════════════════════════════════════════════════════════
class TestNotificationEngine:

    def test_trigger_notification_inserts_row(self):
        ne.trigger_notification(
            user_id=1,
            event_type='TEST_EVENT',
            title='Test Title',
            description='Test description',
            severity='INFO'
        )
        conn  = db.get_db_connection()
        cur   = conn.cursor()
        cur.execute("SELECT COUNT(*) as c FROM strategy_events WHERE event_type='TEST_EVENT'")
        count = cur.fetchone()['c']
        conn.close()
        assert count == 1

    def test_trigger_notification_severity_levels(self):
        for sev in ('INFO', 'WARNING', 'CRITICAL'):
            ne.trigger_notification(user_id=1, event_type=f'EVT_{sev}', title='T', description='D', severity=sev)
        conn = db.get_db_connection()
        rows = conn.execute("SELECT severity FROM strategy_events ORDER BY rowid DESC LIMIT 3").fetchall()
        conn.close()
        severities = [r['severity'] for r in rows]
        assert 'CRITICAL' in severities
        assert 'WARNING'  in severities
        assert 'INFO'     in severities

    def test_notify_box_created(self):
        ne.notify_box_created(user_id=1, chart_type='CALL', level=0.786, price=105.0)
        conn  = db.get_db_connection()
        count = conn.execute("SELECT COUNT(*) as c FROM strategy_events WHERE event_type='BOX_CREATED'").fetchone()['c']
        conn.close()
        assert count >= 1

    def test_notify_buy_signal(self):
        ne.notify_buy_signal(user_id=1, chart_type='SPOT', boundary=110.0, price=110.5)
        conn  = db.get_db_connection()
        count = conn.execute("SELECT COUNT(*) as c FROM strategy_events WHERE event_type='BUY_SIGNAL'").fetchone()['c']
        conn.close()
        assert count >= 1

    def test_notify_trade_executed(self):
        trade_id = _insert_trade(0, status='RUNNING')
        ne.notify_trade_executed(user_id=1, trade_id=trade_id, symbol='SYM', qty=10, price=105.0)
        conn  = db.get_db_connection()
        count = conn.execute("SELECT COUNT(*) as c FROM strategy_events WHERE event_type='TRADE_EXECUTED'").fetchone()['c']
        conn.close()
        assert count >= 1

    def test_notify_stop_loss_hit(self):
        trade_id = _insert_trade(-100.0, status='STOP_LOSS_HIT')
        ne.notify_stop_loss_hit(user_id=1, trade_id=trade_id, symbol='SYM', exit_price=90.0, loss_val=100.0)
        conn  = db.get_db_connection()
        count = conn.execute("SELECT COUNT(*) as c FROM strategy_events WHERE event_type='STOP_LOSS_HIT'").fetchone()['c']
        conn.close()
        assert count >= 1

    def test_notify_target_hit(self):
        trade_id = _insert_trade(200.0, status='TARGET_HIT')
        ne.notify_target_hit(user_id=1, trade_id=trade_id, symbol='SYM', exit_price=120.0, profit_val=200.0)
        conn  = db.get_db_connection()
        count = conn.execute("SELECT COUNT(*) as c FROM strategy_events WHERE event_type='TARGET_HIT'").fetchone()['c']
        conn.close()
        assert count >= 1

    def test_notify_broker_disconnected(self):
        ne.notify_broker_disconnected(user_id=1, error_msg='Connection refused')
        conn  = db.get_db_connection()
        count = conn.execute("SELECT COUNT(*) as c FROM strategy_events WHERE event_type='BROKER_DISCONNECTED'").fetchone()['c']
        conn.close()
        assert count >= 1

    def test_get_strategy_logs_limit(self):
        for i in range(10):
            ne.trigger_notification(user_id=1, event_type='LOOP', title='T', description=f'msg {i}')
        events = db.get_strategy_logs(user_id=1, limit=5)
        assert len(events) <= 5

    def test_get_strategy_logs_severity_filter(self):
        ne.trigger_notification(user_id=1, event_type='A', title='T', description='d', severity='INFO')
        ne.trigger_notification(user_id=1, event_type='B', title='T', description='d', severity='CRITICAL')
        events = db.get_strategy_logs(user_id=1, severity='CRITICAL', limit=10)
        for e in events:
            assert e['severity'] == 'CRITICAL'

    def test_get_strategy_logs_search_filter(self):
        ne.trigger_notification(user_id=1, event_type='X', title='T', description='unique_abc_xyz')
        ne.trigger_notification(user_id=1, event_type='Y', title='T', description='something else')
        events = db.get_strategy_logs(user_id=1, search='unique_abc_xyz', limit=10)
        assert len(events) >= 1


# ══════════════════════════════════════════════════════════════════
# Integration: monitor + notifications full lifecycle
# ══════════════════════════════════════════════════════════════════
class TestIntegration:

    def test_full_lifecycle_logging(self):
        """
        Simulate a complete strategy session with boxes, signals,
        execution, and target hit. Verify event count and analytics.
        """
        ne.trigger_notification(user_id=1, event_type='SESSION_START', title='S', description='Session started', severity='INFO')
        _insert_reference_box(n=3)
        for _ in range(3):
            ne.notify_box_created(user_id=1, chart_type='CALL', level=0.786, price=105.0)
        ne.notify_buy_signal(user_id=1, chart_type='CALL', boundary=110.0, price=110.5)

        trade_id = _insert_trade(0, status='RUNNING')
        ne.notify_trade_executed(user_id=1, trade_id=trade_id, symbol='SYM', qty=10, price=105.0)

        # Close as target hit
        conn = db.get_db_connection()
        conn.execute("UPDATE trades SET status='TARGET_HIT', pnl=500, exit_price=120, exit_time=datetime('now') WHERE id=?", (trade_id,))
        conn.commit()
        conn.close()
        ne.notify_target_hit(user_id=1, trade_id=trade_id, symbol='SYM', exit_price=120.0, profit_val=500.0)
        ne.trigger_notification(user_id=1, event_type='SESSION_END', title='S', description='Session ended', severity='INFO')

        events = db.get_strategy_logs(user_id=1, limit=50)
        assert len(events) >= 7

        result = sm.calculate_live_statistics(user_id=1)
        assert result['total_completed'] >= 1
        assert result['boxes_created']   == 3

    def test_broker_disconnect_logs_critical(self):
        ne.notify_broker_disconnected(user_id=1, error_msg='Timeout')
        events = db.get_strategy_logs(user_id=1, severity='CRITICAL', limit=10)
        assert len(events) >= 1
        assert any(e['event_type'] == 'BROKER_DISCONNECTED' for e in events)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
