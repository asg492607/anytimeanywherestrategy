import logging
import time
from datetime import datetime
import db

logger = logging.getLogger('decision_engine')

class DecisionEngine:
    def __init__(self):
        # In-memory track of last trade times per rule ID to handle cooldowns
        self.last_trade_times = {}

    def evaluate_decisions(self, user_id, signals):
        """
        Takes raw matched signals from the Rule Engine and filters them based on:
        - Opposite Trades running
        - Cooldown periods
        - Priority conflict resolution
        - Duplicate / Re-entry allowance
        Returns a list of approved signals to be executed.
        """
        if not signals:
            return []

        approved_signals = []
        running_trades = db.get_running_trades(user_id)
        current_time = time.time()

        # Group running trades by direction (CE vs PE) to block opposite trades
        running_ce = False
        running_pe = False
        for trade in running_trades:
            if trade.get('call_symbol'):
                running_ce = True
            if trade.get('put_symbol'):
                running_pe = True

        for signal in signals:
            rule_id = signal['rule_id']
            action = signal['action'] # e.g. BUY_CE
            cooldown = signal['cooldown']
            allow_reentry = signal['allow_reentry']
            
            # 1. Opposite Trade Check
            if action == 'BUY_CE' and running_pe:
                logger.info(f"Decision Engine Rejected {rule_id}: Opposite trade (PE) is already running.")
                continue
            if action == 'BUY_PE' and running_ce:
                logger.info(f"Decision Engine Rejected {rule_id}: Opposite trade (CE) is already running.")
                continue

            # 2. Duplicate / Re-entry Check
            if action == 'BUY_CE' and running_ce and not allow_reentry:
                logger.info(f"Decision Engine Rejected {rule_id}: Duplicate trade (CE already running and allow_reentry is false).")
                continue
            if action == 'BUY_PE' and running_pe and not allow_reentry:
                logger.info(f"Decision Engine Rejected {rule_id}: Duplicate trade (PE already running and allow_reentry is false).")
                continue
                
            # 3. Cooldown Check
            last_time = self.last_trade_times.get(rule_id, 0)
            if current_time - last_time < cooldown:
                logger.info(f"Decision Engine Rejected {rule_id}: Cooldown active ({(current_time - last_time):.1f}s / {cooldown}s).")
                continue
                
            approved_signals.append(signal)

        # 4. Conflict Resolution (Priority)
        if len(approved_signals) > 1:
            # Sort by priority ascending (1 is highest priority). If priorities are equal, it keeps both.
            # Usually only one trade is allowed per action, but if multiple rules trigger the same action, pick the highest priority.
            approved_signals.sort(key=lambda x: x['priority'])
            
            # For this MVP, we only execute the absolute highest priority signal if there's a conflict
            # to avoid buying the same option 5 times in 1 minute.
            best_signal = approved_signals[0]
            logger.info(f"Decision Engine: Multiple rules matched. Selected highest priority rule: {best_signal['rule_id']}")
            approved_signals = [best_signal]

        # Register trade times for approved signals
        for sig in approved_signals:
            self.last_trade_times[sig['rule_id']] = current_time

        return approved_signals

# Singleton instance
engine = DecisionEngine()

def get_decision_engine():
    return engine
