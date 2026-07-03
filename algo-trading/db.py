import sqlite3
import os
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database schema for users, trades, and trade logs."""
    print("Initializing SQLite database tables...")
    conn = get_db_connection()
    try:
        # Create users table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                last_login TEXT
            );
        """)
        
        # Create trades table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                broker TEXT NOT NULL,
                underlying TEXT NOT NULL,
                expiry TEXT,
                call_symbol TEXT,
                put_symbol TEXT,
                entry_price REAL NOT NULL,
                exit_price REAL,
                quantity INTEGER NOT NULL,
                entry_time TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                exit_time TEXT,
                stop_loss REAL,
                target REAL,
                pnl REAL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'RUNNING',
                exit_reason TEXT,
                strategy_name TEXT,
                direction TEXT DEFAULT 'BUY',
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
        """)
        
        # Create trade_logs table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                event_type TEXT NOT NULL,
                description TEXT NOT NULL,
                price REAL,
                FOREIGN KEY(trade_id) REFERENCES trades(id) ON DELETE CASCADE
            );
        """)
        
        # Create fibonacci_levels table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fibonacci_levels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL,
                week_end TEXT NOT NULL,
                chart_type TEXT NOT NULL,
                direction TEXT NOT NULL,
                level TEXT NOT NULL,
                price REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                UNIQUE(week_start, week_end, chart_type, direction, level)
            );
        """)
        
        # Create reference_boxes table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reference_boxes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chart_type TEXT NOT NULL,
                instrument_symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                fib_direction TEXT NOT NULL,
                fib_level TEXT NOT NULL,
                candle_timestamp INTEGER NOT NULL,
                candle_open REAL NOT NULL,
                candle_high REAL NOT NULL,
                candle_low REAL NOT NULL,
                candle_close REAL NOT NULL,
                upper_boundary REAL NOT NULL,
                lower_boundary REAL NOT NULL,
                box_status TEXT NOT NULL DEFAULT 'ACTIVE',
                crossed_direction TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                invalidated_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id),
                UNIQUE(user_id, chart_type, instrument_symbol, timeframe, fib_level, candle_timestamp)
            );
        """)
        
        # Create buy_signals table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS buy_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                reference_box_id INTEGER NOT NULL,
                chart_type TEXT NOT NULL,
                instrument_symbol TEXT NOT NULL,
                signal_type TEXT NOT NULL DEFAULT 'BUY',
                signal_status TEXT NOT NULL DEFAULT 'WAITING',
                trigger_candle_timestamp INTEGER,
                trigger_open REAL,
                trigger_high REAL,
                trigger_low REAL,
                trigger_close REAL,
                breakout_price REAL,
                breakout_boundary REAL,
                confirmation_type TEXT NOT NULL DEFAULT 'BODY_CLOSE',
                rejection_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(reference_box_id) REFERENCES reference_boxes(id) ON DELETE CASCADE,
                UNIQUE(user_id, reference_box_id)
            );
        """)
        
        # Create trade_confirmations table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_confirmations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                strategy_name TEXT NOT NULL,
                confirmation_status TEXT NOT NULL DEFAULT 'WAITING',
                required_confirmations INTEGER NOT NULL DEFAULT 2,
                received_confirmations INTEGER NOT NULL DEFAULT 0,
                confirmation_window_seconds INTEGER NOT NULL DEFAULT 30,
                confirmation_start_time INTEGER NOT NULL,
                confirmation_end_time INTEGER NOT NULL,
                confirmed_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                confirmation_details TEXT,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
        """)
        
        # Create trade_confirmation_signals table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_confirmation_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                confirmation_id INTEGER NOT NULL,
                buy_signal_id INTEGER NOT NULL,
                chart_type TEXT NOT NULL,
                instrument_symbol TEXT NOT NULL,
                breakout_price REAL,
                signal_timestamp INTEGER NOT NULL,
                confirmation_order INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                FOREIGN KEY(confirmation_id) REFERENCES trade_confirmations(id) ON DELETE CASCADE,
                FOREIGN KEY(buy_signal_id) REFERENCES buy_signals(id) ON DELETE CASCADE
            );
        """)
        
        # Create trade_executions table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                confirmation_id INTEGER NOT NULL,
                trade_id INTEGER,
                broker TEXT NOT NULL,
                exchange TEXT NOT NULL,
                symbol TEXT NOT NULL,
                token TEXT NOT NULL,
                order_type TEXT NOT NULL,
                transaction_type TEXT NOT NULL,
                product_type TEXT NOT NULL,
                variety TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                requested_price REAL,
                executed_price REAL,
                order_id TEXT,
                broker_order_id TEXT,
                execution_status TEXT NOT NULL DEFAULT 'PENDING',
                rejection_reason TEXT,
                execution_time TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(confirmation_id) REFERENCES trade_confirmations(id) ON DELETE CASCADE,
                FOREIGN KEY(trade_id) REFERENCES trades(id) ON DELETE SET NULL,
                UNIQUE(user_id, confirmation_id)
            );
        """)
        
        # Create stop_loss_events table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stop_loss_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                trade_id INTEGER NOT NULL,
                execution_id INTEGER,
                reference_box_id INTEGER,
                chart_type TEXT NOT NULL,
                instrument_symbol TEXT NOT NULL,
                reference_box_upper REAL,
                reference_box_lower REAL,
                calculated_stop_loss REAL,
                trigger_candle_timestamp INTEGER,
                trigger_open REAL,
                trigger_high REAL,
                trigger_low REAL,
                trigger_close REAL,
                exit_price REAL,
                pnl REAL,
                broker_exit_order_id TEXT,
                exit_status TEXT NOT NULL DEFAULT 'MONITORING',
                exit_reason TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(trade_id) REFERENCES trades(id) ON DELETE CASCADE,
                FOREIGN KEY(execution_id) REFERENCES trade_executions(id) ON DELETE SET NULL,
                FOREIGN KEY(reference_box_id) REFERENCES reference_boxes(id) ON DELETE SET NULL,
                UNIQUE(user_id, trade_id)
            );
        """)
        
        # Create target_exit_events table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS target_exit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                trade_id INTEGER NOT NULL,
                execution_id INTEGER,
                confirmation_id INTEGER,
                reference_box_id INTEGER,
                chart_type TEXT NOT NULL,
                instrument_symbol TEXT NOT NULL,
                fib_direction TEXT NOT NULL,
                target_level TEXT NOT NULL,
                target_price REAL NOT NULL,
                trigger_candle_timestamp INTEGER,
                trigger_open REAL,
                trigger_high REAL,
                trigger_low REAL,
                trigger_close REAL,
                exit_price REAL,
                pnl REAL,
                broker_exit_order_id TEXT,
                exit_status TEXT NOT NULL DEFAULT 'MONITORING',
                exit_reason TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(trade_id) REFERENCES trades(id) ON DELETE CASCADE,
                FOREIGN KEY(execution_id) REFERENCES trade_executions(id) ON DELETE SET NULL,
                FOREIGN KEY(confirmation_id) REFERENCES trade_confirmations(id) ON DELETE SET NULL,
                FOREIGN KEY(reference_box_id) REFERENCES reference_boxes(id) ON DELETE SET NULL,
                UNIQUE(user_id, trade_id)
            );
        """)
        
        # Create strategy_sessions table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                strategy_name TEXT NOT NULL,
                session_status TEXT NOT NULL DEFAULT 'ACTIVE',
                market_status TEXT,
                active_trades INTEGER DEFAULT 0,
                running_trades INTEGER DEFAULT 0,
                completed_trades INTEGER DEFAULT 0,
                total_signals INTEGER DEFAULT 0,
                successful_signals INTEGER DEFAULT 0,
                failed_signals INTEGER DEFAULT 0,
                websocket_status TEXT,
                broker_status TEXT,
                last_market_update TEXT,
                session_started_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                session_ended_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
        """)
        
        # Create strategy_events table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                trade_id INTEGER,
                event_type TEXT NOT NULL,
                event_title TEXT NOT NULL,
                event_description TEXT NOT NULL,
                event_source TEXT,
                severity TEXT NOT NULL DEFAULT 'INFO',
                metadata_json TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                FOREIGN KEY(session_id) REFERENCES strategy_sessions(id) ON DELETE SET NULL,
                FOREIGN KEY(trade_id) REFERENCES trades(id) ON DELETE SET NULL
            );
        """)

        # Create system_health table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                websocket_status TEXT NOT NULL,
                broker_status TEXT NOT NULL,
                database_status TEXT NOT NULL,
                cache_status TEXT NOT NULL,
                api_latency REAL,
                websocket_latency REAL,
                last_market_tick TEXT,
                cpu_usage REAL,
                memory_usage REAL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            );
        """)

        # Create system_config table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                strategy_enabled INTEGER DEFAULT 1,
                execution_sizing_type TEXT DEFAULT 'FIXED',
                execution_fixed_qty INTEGER DEFAULT 10,
                execution_capital REAL DEFAULT 50000.0,
                confirmation_timeout INTEGER DEFAULT 30,
                stop_loss_offset REAL DEFAULT 5.0,
                target_level TEXT DEFAULT '1.39',
                notification_sound INTEGER DEFAULT 1,
                notification_browser INTEGER DEFAULT 1,
                dashboard_refresh_seconds INTEGER DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes')),
                UNIQUE(user_id),
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
        """)
        conn.commit()
    finally:
        conn.close()
    print("Database tables initialization complete.")

# ─── User Functions ────────────────────────────────────────────────────────────

def create_user(full_name, email, password):
    """Creates a new user. Raises ValueError if email already exists."""
    email = email.strip().lower()
    full_name = full_name.strip()
    pw_hash = generate_password_hash(password)
    
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (full_name, email, password_hash) VALUES (?, ?, ?)",
            (full_name, email, pw_hash)
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError("An account with this email address already exists.")
    finally:
        conn.close()

def get_user_by_email(email):
    """Retrieves a user by email address."""
    email = email.strip().lower()
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_user_by_id(user_id):
    """Retrieves a user by their user ID."""
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def update_last_login(user_id):
    """Updates the last_login timestamp for a user."""
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE users SET last_login = datetime('now', '+5 hours', '+30 minutes') WHERE id = ?",
            (user_id,)
        )
        conn.commit()
    finally:
        conn.close()

def verify_password(stored_hash, password):
    """Verifies a password against the stored hash."""
    return check_password_hash(stored_hash, password)

# ─── Trade Functions ───────────────────────────────────────────────────────────

def create_trade(user_id, broker, underlying, expiry, call_symbol, put_symbol, entry_price, quantity, stop_loss, target, strategy_name, direction="BUY"):
    """Creates a new trade and logs the creation event."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trades (
                user_id, broker, underlying, expiry, call_symbol, put_symbol,
                entry_price, quantity, stop_loss, target, status, strategy_name, direction
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?)
        """, (user_id, broker, underlying, expiry, call_symbol, put_symbol, 
              entry_price, quantity, stop_loss, target, strategy_name, direction))
        trade_id = cursor.lastrowid
        
        # Log the trade creation
        desc = f"Position opened: {direction} {quantity} lot(s)"
        if call_symbol and put_symbol:
            desc += f" of Call {call_symbol} and Put {put_symbol}"
        elif call_symbol:
            desc += f" of Call {call_symbol}"
        else:
            desc += f" of Put {put_symbol}"
        
        cursor.execute("""
            INSERT INTO trade_logs (trade_id, event_type, description, price)
            VALUES (?, 'Trade Created', ?, ?)
        """, (trade_id, desc, entry_price))
        
        conn.commit()
        return trade_id
    finally:
        conn.close()

def update_trade(user_id, trade_id, stop_loss=None, target=None):
    """Updates trade parameters (SL, Target) if the trade belongs to the user."""
    conn = get_db_connection()
    try:
        # Check ownership
        trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not trade:
            raise ValueError("Trade not found.")
        if trade["user_id"] != user_id:
            raise PermissionError("Access denied to this trade.")
        if trade["status"] == "CLOSED":
            raise ValueError("Cannot update a closed trade.")
            
        cursor = conn.cursor()
        if stop_loss is not None:
            cursor.execute("UPDATE trades SET stop_loss = ? WHERE id = ?", (stop_loss, trade_id))
            cursor.execute("""
                INSERT INTO trade_logs (trade_id, event_type, description)
                VALUES (?, 'Trade Updated', ?)
            """, (trade_id, f"Stop Loss updated to {stop_loss}"))
            
        if target is not None:
            cursor.execute("UPDATE trades SET target = ? WHERE id = ?", (target, trade_id))
            cursor.execute("""
                INSERT INTO trade_logs (trade_id, event_type, description)
                VALUES (?, 'Trade Updated', ?)
            """, (trade_id, f"Target updated to {target}"))
            
        conn.commit()
    finally:
        conn.close()

def close_trade(user_id, trade_id, exit_price, exit_reason="Manual Exit"):
    """Closes an active trade, computes the final P&L, and logs the closure."""
    conn = get_db_connection()
    try:
        trade = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not trade:
            raise ValueError("Trade not found.")
        if trade["user_id"] != user_id:
            raise PermissionError("Access denied to this trade.")
        if trade["status"] == "CLOSED":
            raise ValueError("Trade is already closed.")
            
        # Calculate final P&L
        quantity = trade["quantity"]
        entry_price = trade["entry_price"]
        direction = trade["direction"] or "BUY"
        
        if direction == "BUY":
            pnl = (exit_price - entry_price) * quantity
        else:
            pnl = (entry_price - exit_price) * quantity
            
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trades 
            SET exit_price = ?, exit_time = datetime('now', '+5 hours', '+30 minutes'), pnl = ?, status = 'CLOSED', exit_reason = ?
            WHERE id = ?
        """, (exit_price, pnl, exit_reason, trade_id))
        
        cursor.execute("""
            INSERT INTO trade_logs (trade_id, event_type, description, price)
            VALUES (?, 'Trade Closed', ?, ?)
        """, (trade_id, f"Position closed: {exit_reason}", exit_price))
        
        conn.commit()
        return pnl
    finally:
        conn.close()

def get_trade_by_id(user_id, trade_id):
    """Retrieves a single trade by ID and verifies owner permissions."""
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not row:
            return None
        trade = dict(row)
        if trade["user_id"] != user_id:
            raise PermissionError("Access denied to this trade.")
        return trade
    finally:
        conn.close()

def get_running_trades(user_id):
    """Retrieves all active running trades for the specified user."""
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT * FROM trades WHERE user_id = ? AND status = 'RUNNING' ORDER BY entry_time DESC", (user_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_closed_trades(user_id):
    """Retrieves all closed trades for the specified user."""
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT * FROM trades WHERE user_id = ? AND status = 'CLOSED' ORDER BY exit_time DESC", (user_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_all_trades(user_id, status=None, search=None, start_date=None, end_date=None, sort_by=None, page=1, per_page=10):
    """Retrieves a filtered, sorted, paginated list of trades along with total count."""
    query = "SELECT * FROM trades WHERE user_id = ?"
    params = [user_id]
    
    if status:
        query += " AND status = ?"
        params.append(status)
        
    if search:
        query += " AND (underlying LIKE ? OR call_symbol LIKE ? OR put_symbol LIKE ? OR strategy_name LIKE ? OR exit_reason LIKE ?)"
        search_val = f"%{search}%"
        params.extend([search_val, search_val, search_val, search_val, search_val])
        
    if start_date:
        query += " AND entry_time >= ?"
        params.append(start_date)
        
    if end_date:
        query += " AND entry_time <= ?"
        params.append(end_date)
        
    # Get total count first
    count_query = f"SELECT COUNT(*) as count FROM ({query})"
    
    # Apply sorting
    allowed_sorts = {
        "entry_time_desc": "entry_time DESC",
        "entry_time_asc": "entry_time ASC",
        "exit_time_desc": "exit_time DESC",
        "exit_time_asc": "exit_time ASC",
        "pnl_desc": "pnl DESC",
        "pnl_asc": "pnl ASC",
        "qty_desc": "quantity DESC",
        "qty_asc": "quantity ASC"
    }
    sort_clause = allowed_sorts.get(sort_by, "entry_time DESC")
    query += f" ORDER BY {sort_clause}"
    
    # Apply pagination
    limit = per_page
    offset = (page - 1) * per_page
    query += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    conn = get_db_connection()
    try:
        total_count = conn.execute(count_query, params[:-2]).fetchone()["count"]
        rows = conn.execute(query, params).fetchall()
        trades_list = [dict(row) for row in rows]
        return trades_list, total_count
    finally:
        conn.close()

# ─── Trade Log Functions ───────────────────────────────────────────────────────

def add_trade_log(trade_id, event_type, description, price=None):
    """Inserts a new log entry for a trade."""
    conn = get_db_connection()
    try:
        conn.execute("""
            INSERT INTO trade_logs (trade_id, event_type, description, price)
            VALUES (?, ?, ?, ?)
        """, (trade_id, event_type, description, price))
        conn.commit()
    finally:
        conn.close()

def get_trade_logs(user_id, trade_id):
    """Retrieves all log events for a trade if the user is the owner."""
    conn = get_db_connection()
    try:
        # Check ownership
        trade = conn.execute("SELECT user_id FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if not trade:
            return []
        if trade["user_id"] != user_id:
            raise PermissionError("Access denied to these trade logs.")
            
        rows = conn.execute("SELECT * FROM trade_logs WHERE trade_id = ? ORDER BY timestamp ASC", (trade_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_all_logs(user_id):
    """Retrieves all logs for all trades owned by the user."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT tl.* FROM trade_logs tl
            JOIN trades t ON tl.trade_id = t.id
            WHERE t.user_id = ?
            ORDER BY tl.timestamp DESC
        """, (user_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

# ─── Fibonacci Levels Cache Functions ──────────────────────────────────────────

def save_fib_levels(week_start, week_end, chart_type, direction, levels_dict):
    """Saves a dict of {level_name: price} for a week range, chart type, and direction."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        for lvl, prc in levels_dict.items():
            cursor.execute("""
                INSERT OR REPLACE INTO fibonacci_levels (
                    week_start, week_end, chart_type, direction, level, price
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (week_start, week_end, chart_type, direction, lvl, prc))
        conn.commit()
    finally:
        conn.close()

def get_fib_levels(week_start, week_end, chart_type, direction):
    """Retrieves levels from the database. Returns dict of {level: price} if found, else None."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT level, price FROM fibonacci_levels
            WHERE week_start = ? AND week_end = ? AND chart_type = ? AND direction = ?
        """, (week_start, week_end, chart_type, direction)).fetchall()
        if not rows:
            return None
        return {r["level"]: r["price"] for r in rows}
    finally:
        conn.close()

# ─── Reference Box Functions ───────────────────────────────────────────────────

def save_reference_box(user_id, chart_type, instrument_symbol, timeframe, fib_direction, fib_level, 
                       candle_timestamp, candle_open, candle_high, candle_low, candle_close, 
                       upper_boundary, lower_boundary, box_status="ACTIVE", crossed_direction="UPWARD"):
    """Inserts a new reference box into the database. Returns the box ID. Handles duplicates by UNIQUE constraint."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO reference_boxes (
                user_id, chart_type, instrument_symbol, timeframe, fib_direction, fib_level,
                candle_timestamp, candle_open, candle_high, candle_low, candle_close,
                upper_boundary, lower_boundary, box_status, crossed_direction
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (user_id, chart_type, instrument_symbol, timeframe, fib_direction, fib_level,
              candle_timestamp, candle_open, candle_high, candle_low, candle_close,
              upper_boundary, lower_boundary, box_status, crossed_direction))
        
        # If successfully inserted, return lastrowid
        box_id = cursor.lastrowid
        conn.commit()
        
        if box_id == 0 or box_id is None:
            # Row already exists due to UNIQUE constraint, fetch it
            row = conn.execute("""
                SELECT id FROM reference_boxes
                WHERE user_id = ? AND chart_type = ? AND instrument_symbol = ? AND timeframe = ? AND fib_level = ? AND candle_timestamp = ?
            """, (user_id, chart_type, instrument_symbol, timeframe, fib_level, candle_timestamp)).fetchone()
            if row:
                return row["id"]
        else:
            try:
                from strategy.notification_engine import notify_box_created
                notify_box_created(user_id, chart_type, fib_level, upper_boundary)
            except Exception:
                pass
        return box_id
    finally:
        conn.close()

def replace_active_boxes(user_id, chart_type, fib_level, exclude_box_id):
    """Sets all other active boxes for the same chart and level to REPLACED status."""
    conn = get_db_connection()
    try:
        conn.execute("""
            UPDATE reference_boxes 
            SET box_status = 'REPLACED', updated_at = datetime('now', '+5 hours', '+30 minutes')
            WHERE user_id = ? AND chart_type = ? AND fib_level = ? AND box_status = 'ACTIVE' AND id != ?
        """, (user_id, chart_type, fib_level, exclude_box_id))
        conn.commit()
    finally:
        conn.close()

def get_active_boxes(user_id, chart_type=None):
    """Retrieves all active reference boxes. Filters by chart type if provided."""
    conn = get_db_connection()
    try:
        if chart_type:
            rows = conn.execute("""
                SELECT * FROM reference_boxes
                WHERE user_id = ? AND chart_type = ? AND box_status = 'ACTIVE'
                ORDER BY candle_timestamp DESC
            """, (user_id, chart_type)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM reference_boxes
                WHERE user_id = ? AND box_status = 'ACTIVE'
                ORDER BY candle_timestamp DESC
            """, (user_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_reference_box_by_id(user_id, box_id):
    """Retrieves a single reference box by ID, validating user ownership."""
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT * FROM reference_boxes WHERE id = ? AND user_id = ?
        """, (box_id, user_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def update_box_status(user_id, box_id, status):
    """Updates the status of a reference box (REPLACED, INVALIDATED, EXPIRED). Sets invalidated_at if needed."""
    conn = get_db_connection()
    try:
        invalidated_time = None
        if status == 'INVALIDATED':
            # Generate local timestamp
            cursor = conn.cursor()
            cursor.execute("SELECT datetime('now', '+5 hours', '+30 minutes')")
            invalidated_time = cursor.fetchone()[0]

        if invalidated_time:
            conn.execute("""
                UPDATE reference_boxes
                SET box_status = ?, updated_at = datetime('now', '+5 hours', '+30 minutes'), invalidated_at = ?
                WHERE id = ? AND user_id = ?
            """, (status, invalidated_time, box_id, user_id))
        else:
            conn.execute("""
                UPDATE reference_boxes
                SET box_status = ?, updated_at = datetime('now', '+5 hours', '+30 minutes')
                WHERE id = ? AND user_id = ?
            """, (status, box_id, user_id))
        conn.commit()
    finally:
        conn.close()

def load_all_boxes(user_id):
    """Retrieves all historical and active reference boxes for the user."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM reference_boxes WHERE user_id = ?
            ORDER BY candle_timestamp DESC
        """, (user_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

# ─── Buy Signal Functions ──────────────────────────────────────────────────────

def save_buy_signal(user_id, reference_box_id, chart_type, instrument_symbol, signal_status, 
                    trigger_candle_timestamp=None, trigger_open=None, trigger_high=None, 
                    trigger_low=None, trigger_close=None, breakout_price=None, breakout_boundary=None, 
                    rejection_count=0):
    """Saves or updates a Buy Signal. UNIQUE on (user_id, reference_box_id) ensures updates work."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Check if record already exists
        row = conn.execute("""
            SELECT id, rejection_count FROM buy_signals
            WHERE user_id = ? AND reference_box_id = ?
        """, (user_id, reference_box_id)).fetchone()
        
        if row:
            # Update existing record, preserve/increment rejection count as required
            sig_id = row["id"]
            final_rejections = max(row["rejection_count"], rejection_count)
            cursor.execute("""
                UPDATE buy_signals
                SET signal_status = ?, trigger_candle_timestamp = ?, trigger_open = ?,
                    trigger_high = ?, trigger_low = ?, trigger_close = ?,
                    breakout_price = ?, breakout_boundary = ?, rejection_count = ?,
                    updated_at = datetime('now', '+5 hours', '+30 minutes')
                WHERE id = ? AND user_id = ?
            """, (signal_status, trigger_candle_timestamp, trigger_open, trigger_high,
                  trigger_low, trigger_close, breakout_price, breakout_boundary,
                  final_rejections, sig_id, user_id))
            conn.commit()
            if signal_status in ['WAITING', 'CONFIRMED']:
                try:
                    from strategy.notification_engine import notify_buy_signal
                    notify_buy_signal(user_id, chart_type, breakout_boundary, breakout_price)
                except Exception:
                    pass
            return sig_id
        else:
            # Insert brand new record
            cursor.execute("""
                INSERT INTO buy_signals (
                    user_id, reference_box_id, chart_type, instrument_symbol, signal_status,
                    trigger_candle_timestamp, trigger_open, trigger_high, trigger_low, trigger_close,
                    breakout_price, breakout_boundary, rejection_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, reference_box_id, chart_type, instrument_symbol, signal_status,
                  trigger_candle_timestamp, trigger_open, trigger_high, trigger_low, trigger_close,
                  breakout_price, breakout_boundary, rejection_count))
            sig_id = cursor.lastrowid
            conn.commit()
            if signal_status in ['WAITING', 'CONFIRMED']:
                try:
                    from strategy.notification_engine import notify_buy_signal
                    notify_buy_signal(user_id, chart_type, breakout_boundary, breakout_price)
                except Exception:
                    pass
            return sig_id
    finally:
        conn.close()

def get_buy_signal_by_box(user_id, reference_box_id):
    """Retrieves buy signal for a specific reference box."""
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT * FROM buy_signals WHERE user_id = ? AND reference_box_id = ?
        """, (user_id, reference_box_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_buy_signal_by_id(user_id, signal_id):
    """Retrieves buy signal by its ID."""
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT * FROM buy_signals WHERE user_id = ? AND id = ?
        """, (user_id, signal_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_active_signals(user_id, chart_type=None):
    """Retrieves active (WAITING or CONFIRMED) buy signals. Filters by chart type if provided."""
    conn = get_db_connection()
    try:
        if chart_type:
            rows = conn.execute("""
                SELECT * FROM buy_signals
                WHERE user_id = ? AND chart_type = ? AND signal_status IN ('WAITING', 'CONFIRMED')
                ORDER BY created_at DESC
            """, (user_id, chart_type)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM buy_signals
                WHERE user_id = ? AND signal_status IN ('WAITING', 'CONFIRMED')
                ORDER BY created_at DESC
            """, (user_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def update_signal_status(user_id, signal_id, status):
    """Updates status for a specific buy signal."""
    conn = get_db_connection()
    try:
        conn.execute("""
            UPDATE buy_signals
            SET signal_status = ?, updated_at = datetime('now', '+5 hours', '+30 minutes')
            WHERE id = ? AND user_id = ?
        """, (status, signal_id, user_id))
        conn.commit()
    finally:
        conn.close()

def increment_signal_rejection(user_id, signal_id):
    """Increments the rejection count by 1 for a specific signal."""
    conn = get_db_connection()
    try:
        conn.execute("""
            UPDATE buy_signals
            SET rejection_count = rejection_count + 1, updated_at = datetime('now', '+5 hours', '+30 minutes')
            WHERE id = ? AND user_id = ?
        """, (signal_id, user_id))
        conn.commit()
    finally:
        conn.close()

def load_all_signals(user_id):
    """Loads all signals for user review and dashboard audits."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM buy_signals WHERE user_id = ?
            ORDER BY created_at DESC
        """, (user_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

# ─── Trade Confirmation Functions ──────────────────────────────────────────────

def create_confirmation_session(user_id, strategy_name, window_seconds, start_time):
    """Creates a new trade confirmation session inside trade_confirmations."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        end_time = start_time + window_seconds
        cursor.execute("""
            INSERT INTO trade_confirmations (
                user_id, strategy_name, confirmation_status, required_confirmations,
                received_confirmations, confirmation_window_seconds, confirmation_start_time,
                confirmation_end_time
            ) VALUES (?, ?, 'WAITING', 2, 0, ?, ?, ?)
        """, (user_id, strategy_name, window_seconds, start_time, end_time))
        conf_id = cursor.lastrowid
        conn.commit()
        return conf_id
    finally:
        conn.close()

def add_signal_to_confirmation(confirmation_id, buy_signal_id, chart_type, instrument_symbol, 
                               breakout_price, signal_timestamp, order_num):
    """Links a Buy Signal to a Confirmation session and updates confirmations count."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # 1. Insert into trade_confirmation_signals mapping table
        cursor.execute("""
            INSERT INTO trade_confirmation_signals (
                confirmation_id, buy_signal_id, chart_type, instrument_symbol,
                breakout_price, signal_timestamp, confirmation_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (confirmation_id, buy_signal_id, chart_type, instrument_symbol,
              breakout_price, signal_timestamp, order_num))
        
        # 2. Update received_confirmations in main session table
        cursor.execute("""
            UPDATE trade_confirmations
            SET received_confirmations = received_confirmations + 1,
                updated_at = datetime('now', '+5 hours', '+30 minutes')
            WHERE id = ?
        """, (confirmation_id,))
        
        conn.commit()
    finally:
        conn.close()

def update_confirmation_status(user_id, confirmation_id, status, confirmed_time=None, details=None):
    """Updates status for a specific trade confirmation session."""
    conn = get_db_connection()
    try:
        if confirmed_time:
            conn.execute("""
                UPDATE trade_confirmations
                SET confirmation_status = ?, confirmed_at = ?, confirmation_details = ?,
                    updated_at = datetime('now', '+5 hours', '+30 minutes')
                WHERE id = ? AND user_id = ?
            """, (status, confirmed_time, details, confirmation_id, user_id))
        else:
            conn.execute("""
                UPDATE trade_confirmations
                SET confirmation_status = ?, confirmation_details = ?,
                    updated_at = datetime('now', '+5 hours', '+30 minutes')
                WHERE id = ? AND user_id = ?
            """, (status, details, confirmation_id, user_id))
        conn.commit()
        if status == 'CONFIRMED':
            try:
                from strategy.notification_engine import notify_confirmation_complete
                notify_confirmation_complete(user_id, confirmation_id, ['CALL', 'SPOT', 'PUT'])
            except Exception:
                pass
    finally:
        conn.close()

def check_signal_in_any_confirmation(user_id, buy_signal_id):
    """Returns True if the Buy Signal is already part of any confirmation session (active or completed)."""
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT tcs.id FROM trade_confirmation_signals tcs
            JOIN trade_confirmations tc ON tcs.confirmation_id = tc.id
            WHERE tc.user_id = ? AND tcs.buy_signal_id = ?
        """, (user_id, buy_signal_id)).fetchone()
        return row is not None
    finally:
        conn.close()

def get_confirmation_by_id(user_id, conf_id):
    """Retrieves a single confirmation session by ID, including its associated signals."""
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT * FROM trade_confirmations WHERE id = ? AND user_id = ?
        """, (conf_id, user_id)).fetchone()
        if not row:
            return None
        
        session = dict(row)
        
        # Load related buy signals
        rows_sigs = conn.execute("""
            SELECT * FROM trade_confirmation_signals
            WHERE confirmation_id = ?
            ORDER BY confirmation_order ASC
        """, (conf_id,)).fetchall()
        
        session["signals"] = [dict(r) for r in rows_sigs]
        return session
    finally:
        conn.close()

def get_active_confirmations(user_id):
    """Retrieves all active (WAITING) trade confirmation sessions with their signals."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM trade_confirmations
            WHERE user_id = ? AND confirmation_status = 'WAITING'
            ORDER BY created_at DESC
        """, (user_id,)).fetchall()
        
        sessions = []
        for r in rows:
            sess = dict(r)
            rows_sigs = conn.execute("""
                SELECT * FROM trade_confirmation_signals
                WHERE confirmation_id = ?
                ORDER BY confirmation_order ASC
            """, (sess["id"],)).fetchall()
            sess["signals"] = [dict(rs) for rs in rows_sigs]
            sessions.append(sess)
        return sessions
    finally:
        conn.close()

def get_confirmation_history(user_id):
    """Retrieves completed confirmation sessions (CONFIRMED, FAILED, EXPIRED)."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM trade_confirmations
            WHERE user_id = ? AND confirmation_status IN ('CONFIRMED', 'FAILED', 'EXPIRED')
            ORDER BY created_at DESC
        """, (user_id,)).fetchall()
        
        sessions = []
        for r in rows:
            sess = dict(r)
            rows_sigs = conn.execute("""
                SELECT * FROM trade_confirmation_signals
                WHERE confirmation_id = ?
                ORDER BY confirmation_order ASC
            """, (sess["id"],)).fetchall()
            sess["signals"] = [dict(rs) for rs in rows_sigs]
            sessions.append(sess)
        return sessions
    finally:
        conn.close()

def get_all_confirmations(user_id):
    """Retrieves all confirmations (active and historical) with their signals."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM trade_confirmations WHERE user_id = ?
            ORDER BY created_at DESC
        """, (user_id,)).fetchall()
        
        sessions = []
        for r in rows:
            sess = dict(r)
            rows_sigs = conn.execute("""
                SELECT * FROM trade_confirmation_signals
                WHERE confirmation_id = ?
                ORDER BY confirmation_order ASC
            """, (sess["id"],)).fetchall()
            sess["signals"] = [dict(rs) for rs in rows_sigs]
            sessions.append(sess)
        return sessions
    finally:
        conn.close()

# ─── Trade Execution Functions ─────────────────────────────────────────────────

def save_trade_execution(user_id, confirmation_id, trade_id, broker, exchange, symbol, token, 
                         order_type, transaction_type, product_type, variety, quantity, 
                         requested_price, executed_price, order_id, broker_order_id, 
                         execution_status, rejection_reason, execution_time):
    """Saves or updates a Trade Execution record. UNIQUE on (user_id, confirmation_id) enables updates."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Check if record exists
        row = conn.execute("""
            SELECT id FROM trade_executions
            WHERE user_id = ? AND confirmation_id = ?
        """, (user_id, confirmation_id)).fetchone()
        
        if row:
            exec_id = row["id"]
            cursor.execute("""
                UPDATE trade_executions
                SET trade_id = ?, broker = ?, exchange = ?, symbol = ?, token = ?,
                    order_type = ?, transaction_type = ?, product_type = ?, variety = ?,
                    quantity = ?, requested_price = ?, executed_price = ?, order_id = ?,
                    broker_order_id = ?, execution_status = ?, rejection_reason = ?,
                    execution_time = ?, updated_at = datetime('now', '+5 hours', '+30 minutes')
                WHERE id = ? AND user_id = ?
            """, (trade_id, broker, exchange, symbol, token, order_type, transaction_type,
                  product_type, variety, quantity, requested_price, executed_price, order_id,
                  broker_order_id, execution_status, rejection_reason, execution_time, exec_id, user_id))
            conn.commit()
            return exec_id
        else:
            cursor.execute("""
                INSERT INTO trade_executions (
                    user_id, confirmation_id, trade_id, broker, exchange, symbol, token,
                    order_type, transaction_type, product_type, variety, quantity,
                    requested_price, executed_price, order_id, broker_order_id,
                    execution_status, rejection_reason, execution_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, confirmation_id, trade_id, broker, exchange, symbol, token,
                  order_type, transaction_type, product_type, variety, quantity,
                  requested_price, executed_price, order_id, broker_order_id,
                  execution_status, rejection_reason, execution_time))
            exec_id = cursor.lastrowid
            conn.commit()
            if execution_status == 'COMPLETE':
                try:
                    from strategy.notification_engine import notify_trade_executed
                    notify_trade_executed(user_id, trade_id, symbol, quantity, executed_price)
                except Exception:
                    pass
            return exec_id
    finally:
        conn.close()

def get_execution_by_id(user_id, exec_id):
    """Retrieves a single execution by ID, validating user ownership."""
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT * FROM trade_executions WHERE id = ? AND user_id = ?
        """, (exec_id, user_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_running_executions(user_id):
    """Retrieves running executions (status is PENDING, SUBMITTED, or COMPLETE with running trade)."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT te.* FROM trade_executions te
            LEFT JOIN trades t ON te.trade_id = t.id
            WHERE te.user_id = ? AND (te.execution_status IN ('PENDING', 'SUBMITTED') OR (te.execution_status = 'COMPLETE' AND t.status = 'RUNNING'))
            ORDER BY te.created_at DESC
        """, (user_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_completed_executions(user_id):
    """Retrieves completed executions (status is COMPLETE, FAILED, REJECTED, CANCELLED)."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM trade_executions
            WHERE user_id = ? AND execution_status IN ('COMPLETE', 'FAILED', 'REJECTED', 'CANCELLED')
            ORDER BY created_at DESC
        """, (user_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_all_executions(user_id):
    """Retrieves all historical and active executions."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM trade_executions WHERE user_id = ?
            ORDER BY created_at DESC
        """, (user_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def check_confirmation_executed(user_id, confirmation_id):
    """Returns True if the confirmation has already triggered a trade execution."""
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT id FROM trade_executions
            WHERE user_id = ? AND confirmation_id = ?
        """, (user_id, confirmation_id)).fetchone()
        return row is not None
    finally:
        conn.close()

def update_execution_status(user_id, exec_id, status, executed_price=None, broker_order_id=None, 
                            rejection_reason=None, trade_id=None):
    """Updates status and results for a specific execution session."""
    conn = get_db_connection()
    try:
        conn.execute("""
            UPDATE trade_executions
            SET execution_status = ?, executed_price = COALESCE(?, executed_price), 
                broker_order_id = COALESCE(?, broker_order_id), rejection_reason = COALESCE(?, rejection_reason),
                trade_id = COALESCE(?, trade_id), updated_at = datetime('now', '+5 hours', '+30 minutes')
            WHERE id = ? AND user_id = ?
        """, (status, executed_price, broker_order_id, rejection_reason, trade_id, exec_id, user_id))
        conn.commit()
        if status == 'COMPLETE' and trade_id:
            try:
                row_exec = conn.execute("SELECT symbol, quantity FROM trade_executions WHERE id = ?", (exec_id,)).fetchone()
                if row_exec:
                    from strategy.notification_engine import notify_trade_executed
                    notify_trade_executed(user_id, trade_id, row_exec['symbol'], row_exec['quantity'], executed_price or 0.0)
            except Exception:
                pass
    finally:
        conn.close()

# ─── Stop Loss Functions ────────────────────────────────────────────────────────

def save_stop_loss_event(user_id, trade_id, execution_id, reference_box_id, chart_type, 
                         instrument_symbol, reference_box_upper, reference_box_lower, 
                         calculated_stop_loss, trigger_candle_timestamp, trigger_open, 
                         trigger_high, trigger_low, trigger_close, exit_price, pnl, 
                         broker_exit_order_id, exit_status, exit_reason):
    """Saves or updates a Stop Loss Event. UNIQUE on (user_id, trade_id) enables updates."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        row = conn.execute("""
            SELECT id FROM stop_loss_events
            WHERE user_id = ? AND trade_id = ?
        """, (user_id, trade_id)).fetchone()
        
        if row:
            sl_id = row["id"]
            cursor.execute("""
                UPDATE stop_loss_events
                SET execution_id = ?, reference_box_id = ?, chart_type = ?, instrument_symbol = ?,
                    reference_box_upper = ?, reference_box_lower = ?, calculated_stop_loss = ?,
                    trigger_candle_timestamp = ?, trigger_open = ?, trigger_high = ?, trigger_low = ?,
                    trigger_close = ?, exit_price = ?, pnl = ?, broker_exit_order_id = ?,
                    exit_status = ?, exit_reason = ?, updated_at = datetime('now', '+5 hours', '+30 minutes')
                WHERE id = ? AND user_id = ?
            """, (execution_id, reference_box_id, chart_type, instrument_symbol, reference_box_upper,
                  reference_box_lower, calculated_stop_loss, trigger_candle_timestamp, trigger_open,
                  trigger_high, trigger_low, trigger_close, exit_price, pnl, broker_exit_order_id,
                  exit_status, exit_reason, sl_id, user_id))
            conn.commit()
            return sl_id
        else:
            cursor.execute("""
                INSERT INTO stop_loss_events (
                    user_id, trade_id, execution_id, reference_box_id, chart_type,
                    instrument_symbol, reference_box_upper, reference_box_lower,
                    calculated_stop_loss, trigger_candle_timestamp, trigger_open,
                    trigger_high, trigger_low, trigger_close, exit_price, pnl,
                    broker_exit_order_id, exit_status, exit_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, trade_id, execution_id, reference_box_id, chart_type,
                  instrument_symbol, reference_box_upper, reference_box_lower,
                  calculated_stop_loss, trigger_candle_timestamp, trigger_open,
                  trigger_high, trigger_low, trigger_close, exit_price, pnl,
                  broker_exit_order_id, exit_status, exit_reason))
            sl_id = cursor.lastrowid
            conn.commit()
            return sl_id
    finally:
        conn.close()

def get_stop_loss_by_trade_id(user_id, trade_id):
    """Retrieves the stop loss event for a trade."""
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT * FROM stop_loss_events WHERE trade_id = ? AND user_id = ?
        """, (trade_id, user_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_active_stop_loss_events(user_id):
    """Retrieves all stop loss events currently in MONITORING status."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM stop_loss_events
            WHERE user_id = ? AND exit_status = 'MONITORING'
            ORDER BY created_at DESC
        """, (user_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_completed_stop_loss_events(user_id):
    """Retrieves completed stop loss events."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM stop_loss_events
            WHERE user_id = ? AND exit_status IN ('ORDER_COMPLETE', 'FAILED', 'REJECTED', 'CANCELLED')
            ORDER BY created_at DESC
        """, (user_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_reference_box_for_trade(user_id, trade_id):
    """Relational query mapping trade to its trigger Reference Box details."""
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT rb.* FROM reference_boxes rb
            JOIN buy_signals bs ON rb.id = bs.reference_box_id
            JOIN trade_confirmation_signals tcs ON bs.id = tcs.buy_signal_id
            JOIN trade_executions te ON tcs.confirmation_id = te.confirmation_id
            WHERE te.trade_id = ? AND te.user_id = ?
        """, (trade_id, user_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_execution_for_trade(user_id, trade_id):
    """Retrieves the entry execution details for a specific trade."""
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT * FROM trade_executions WHERE trade_id = ? AND user_id = ?
        """, (trade_id, user_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def update_stop_loss_status(user_id, sl_id, status, exit_price=None, pnl=None, 
                            broker_exit_order_id=None, exit_reason=None):
    """Updates stop loss event progress status."""
    conn = get_db_connection()
    try:
        conn.execute("""
            UPDATE stop_loss_events
            SET exit_status = ?, exit_price = COALESCE(?, exit_price), pnl = COALESCE(?, pnl),
                broker_exit_order_id = COALESCE(?, broker_exit_order_id), exit_reason = COALESCE(?, exit_reason),
                updated_at = datetime('now', '+5 hours', '+30 minutes')
            WHERE id = ? AND user_id = ?
        """, (status, exit_price, pnl, broker_exit_order_id, exit_reason, sl_id, user_id))
        conn.commit()
        if status == 'ORDER_COMPLETE':
            try:
                row_sl = conn.execute("SELECT trade_id, instrument_symbol FROM stop_loss_events WHERE id = ?", (sl_id,)).fetchone()
                if row_sl:
                    from strategy.notification_engine import notify_stop_loss_hit
                    notify_stop_loss_hit(user_id, row_sl['trade_id'], row_sl['instrument_symbol'], exit_price or 0.0, pnl or 0.0)
            except Exception:
                pass
    finally:
        conn.close()

# ─── Target Exit Functions ──────────────────────────────────────────────────────

def save_target_exit_event(user_id, trade_id, execution_id, confirmation_id, reference_box_id,
                           chart_type, instrument_symbol, fib_direction, target_level, target_price,
                           trigger_candle_timestamp, trigger_open, trigger_high, trigger_low,
                           trigger_close, exit_price, pnl, broker_exit_order_id, exit_status, exit_reason):
    """Saves or updates a Fibonacci Target Exit Event. UNIQUE on (user_id, trade_id)."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        row = conn.execute("""
            SELECT id FROM target_exit_events
            WHERE user_id = ? AND trade_id = ?
        """, (user_id, trade_id)).fetchone()
        
        if row:
            t_id = row["id"]
            cursor.execute("""
                UPDATE target_exit_events
                SET execution_id = ?, confirmation_id = ?, reference_box_id = ?, chart_type = ?,
                    instrument_symbol = ?, fib_direction = ?, target_level = ?, target_price = ?,
                    trigger_candle_timestamp = ?, trigger_open = ?, trigger_high = ?, trigger_low = ?,
                    trigger_close = ?, exit_price = ?, pnl = ?, broker_exit_order_id = ?,
                    exit_status = ?, exit_reason = ?, updated_at = datetime('now', '+5 hours', '+30 minutes')
                WHERE id = ? AND user_id = ?
            """, (execution_id, confirmation_id, reference_box_id, chart_type, instrument_symbol,
                  fib_direction, target_level, target_price, trigger_candle_timestamp, trigger_open,
                  trigger_high, trigger_low, trigger_close, exit_price, pnl, broker_exit_order_id,
                  exit_status, exit_reason, t_id, user_id))
            conn.commit()
            return t_id
        else:
            cursor.execute("""
                INSERT INTO target_exit_events (
                    user_id, trade_id, execution_id, confirmation_id, reference_box_id,
                    chart_type, instrument_symbol, fib_direction, target_level, target_price,
                    trigger_candle_timestamp, trigger_open, trigger_high, trigger_low,
                    trigger_close, exit_price, pnl, broker_exit_order_id, exit_status, exit_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, trade_id, execution_id, confirmation_id, reference_box_id,
                  chart_type, instrument_symbol, fib_direction, target_level, target_price,
                  trigger_candle_timestamp, trigger_open, trigger_high, trigger_low,
                  trigger_close, exit_price, pnl, broker_exit_order_id, exit_status, exit_reason))
            t_id = cursor.lastrowid
            conn.commit()
            return t_id
    finally:
        conn.close()

def get_target_exit_by_trade_id(user_id, trade_id):
    """Retrieves the target exit event for a trade."""
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT * FROM target_exit_events WHERE trade_id = ? AND user_id = ?
        """, (trade_id, user_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def get_active_target_events(user_id):
    """Retrieves all active target exit events currently in MONITORING status."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM target_exit_events
            WHERE user_id = ? AND exit_status = 'MONITORING'
            ORDER BY created_at DESC
        """, (user_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def get_completed_target_events(user_id):
    """Retrieves completed target exit events."""
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT * FROM target_exit_events
            WHERE user_id = ? AND exit_status IN ('ORDER_COMPLETE', 'FAILED', 'REJECTED', 'CANCELLED')
            ORDER BY created_at DESC
        """, (user_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def update_target_exit_status(user_id, target_id, status, exit_price=None, pnl=None, 
                              broker_exit_order_id=None, exit_reason=None):
    """Updates target exit event status and metrics."""
    conn = get_db_connection()
    try:
        conn.execute("""
            UPDATE target_exit_events
            SET exit_status = ?, exit_price = COALESCE(?, exit_price), pnl = COALESCE(?, pnl),
                broker_exit_order_id = COALESCE(?, broker_exit_order_id), exit_reason = COALESCE(?, exit_reason),
                updated_at = datetime('now', '+5 hours', '+30 minutes')
            WHERE id = ? AND user_id = ?
        """, (status, exit_price, pnl, broker_exit_order_id, exit_reason, target_id, user_id))
        conn.commit()
        if status == 'ORDER_COMPLETE':
            try:
                row_tgt = conn.execute("SELECT trade_id, instrument_symbol FROM target_exit_events WHERE id = ?", (target_id,)).fetchone()
                if row_tgt:
                    from strategy.notification_engine import notify_target_hit
                    notify_target_hit(user_id, row_tgt['trade_id'], row_tgt['instrument_symbol'], exit_price or 0.0, pnl or 0.0)
            except Exception:
                pass
    finally:
        conn.close()

# ─── System Configuration, Health, Logs, and Session Functions ────────────────

def get_system_config(user_id):
    """Retrieves config row for a user. Seeds defaults if row doesn't exist."""
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM system_config WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            return dict(row)
        
        # Seed defaults
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO system_config (user_id) VALUES (?)
        """, (user_id,))
        conn.commit()
        
        row_new = conn.execute("SELECT * FROM system_config WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row_new)
    finally:
        conn.close()

def save_system_config(user_id, config_dict):
    """Updates system config settings."""
    conn = get_db_connection()
    try:
        conn.execute("""
            UPDATE system_config
            SET strategy_enabled = ?, execution_sizing_type = ?, execution_fixed_qty = ?,
                execution_capital = ?, confirmation_timeout = ?, stop_loss_offset = ?,
                target_level = ?, notification_sound = ?, notification_browser = ?,
                dashboard_refresh_seconds = ?, updated_at = datetime('now', '+5 hours', '+30 minutes')
            WHERE user_id = ?
        """, (
            config_dict.get('strategy_enabled', 1),
            config_dict.get('execution_sizing_type', 'FIXED'),
            config_dict.get('execution_fixed_qty', 10),
            config_dict.get('execution_capital', 50000.0),
            config_dict.get('confirmation_timeout', 30),
            config_dict.get('stop_loss_offset', 5.0),
            config_dict.get('target_level', '1.39'),
            config_dict.get('notification_sound', 1),
            config_dict.get('notification_browser', 1),
            config_dict.get('dashboard_refresh_seconds', 1),
            user_id
        ))
        conn.commit()
    finally:
        conn.close()

def create_strategy_session(user_id, strategy_name):
    """Closes any active sessions of the strategy and opens a new active session."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # Set previous active sessions to INACTIVE
        cursor.execute("""
            UPDATE strategy_sessions
            SET session_status = 'INACTIVE', session_ended_at = datetime('now', '+5 hours', '+30 minutes'),
                updated_at = datetime('now', '+5 hours', '+30 minutes')
            WHERE user_id = ? AND strategy_name = ? AND session_status = 'ACTIVE'
        """, (user_id, strategy_name))
        
        # Insert new session
        cursor.execute("""
            INSERT INTO strategy_sessions (user_id, strategy_name, session_status)
            VALUES (?, ?, 'ACTIVE')
        """, (user_id, strategy_name))
        session_id = cursor.lastrowid
        conn.commit()
        return session_id
    finally:
        conn.close()

def get_active_strategy_session(user_id, strategy_name):
    """Retrieves active session details for a strategy."""
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT * FROM strategy_sessions
            WHERE user_id = ? AND strategy_name = ? AND session_status = 'ACTIVE'
            ORDER BY created_at DESC LIMIT 1
        """, (user_id, strategy_name)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def update_strategy_session_stats(user_id, session_id, stats_dict):
    """Updates dynamic counts on a strategy session."""
    conn = get_db_connection()
    try:
        conn.execute("""
            UPDATE strategy_sessions
            SET active_trades = ?, running_trades = ?, completed_trades = ?, total_signals = ?,
                successful_signals = ?, failed_signals = ?, websocket_status = ?, broker_status = ?,
                last_market_update = ?, market_status = ?, updated_at = datetime('now', '+5 hours', '+30 minutes')
            WHERE id = ? AND user_id = ?
        """, (
            stats_dict.get('active_trades', 0),
            stats_dict.get('running_trades', 0),
            stats_dict.get('completed_trades', 0),
            stats_dict.get('total_signals', 0),
            stats_dict.get('successful_signals', 0),
            stats_dict.get('failed_signals', 0),
            stats_dict.get('websocket_status', 'Connected'),
            stats_dict.get('broker_status', 'Connected'),
            stats_dict.get('last_market_update', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            stats_dict.get('market_status', 'Open'),
            session_id,
            user_id
        ))
        conn.commit()
    finally:
        conn.close()

def log_strategy_event(user_id, session_id, trade_id, event_type, event_title, 
                       event_description, event_source, severity='INFO', metadata_json=None):
    """Appends an event log entry."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO strategy_events (
                session_id, trade_id, event_type, event_title, event_description, event_source, severity, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (session_id, trade_id, event_type, event_title, event_description, event_source, severity, metadata_json))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()

def save_system_health(websocket_status, broker_status, database_status, cache_status, 
                       cpu_usage, memory_usage, api_latency=None, websocket_latency=None, last_market_tick=None):
    """Logs health vitals."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO system_health (
                websocket_status, broker_status, database_status, cache_status,
                cpu_usage, memory_usage, api_latency, websocket_latency, last_market_tick
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (websocket_status, broker_status, database_status, cache_status,
              cpu_usage, memory_usage, api_latency, websocket_latency, last_market_tick))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()

def get_system_health():
    """Gets the latest health metrics row."""
    conn = get_db_connection()
    try:
        row = conn.execute("""
            SELECT * FROM system_health ORDER BY created_at DESC LIMIT 1
        """, ()).fetchone()
        return dict(row) if row else {
            'websocket_status': 'Connected', 'broker_status': 'Connected',
            'database_status': 'Healthy', 'cache_status': 'Healthy',
            'api_latency': 45.0, 'websocket_latency': 10.0,
            'last_market_tick': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'cpu_usage': 12.0, 'memory_usage': 44.0
        }
    finally:
        conn.close()

def get_strategy_logs(user_id, search=None, severity=None, event_type=None, limit=100):
    """Searches and filters system event logs."""
    conn = get_db_connection()
    try:
        query = """
            SELECT se.*, ss.strategy_name FROM strategy_events se
            LEFT JOIN strategy_sessions ss ON se.session_id = ss.id
            WHERE 1=1
        """
        params = []
        
        if user_id:
            query += " AND (ss.user_id = ? OR se.session_id IS NULL)"
            params.append(user_id)
            
        if severity:
            query += " AND se.severity = ?"
            params.append(severity)
            
        if event_type:
            query += " AND se.event_type = ?"
            params.append(event_type)
            
        if search:
            query += " AND (se.event_title LIKE ? OR se.event_description LIKE ?)"
            search_val = f"%{search}%"
            params.extend([search_val, search_val])
            
        query += " ORDER BY se.created_at DESC LIMIT ?"
        params.append(limit)
        
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
