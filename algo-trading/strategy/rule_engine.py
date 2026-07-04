import json
import os
import glob
import logging
from datetime import datetime

logger = logging.getLogger('rule_engine')

RULES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'rules')
LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)
RULE_LOG_FILE = os.path.join(LOGS_DIR, 'rule_evaluations.log')

class RuleEngine:
    def __init__(self):
        self.rules = []
        self.load_rules()

    def load_rules(self):
        """Loads all JSON rules from the rules directory."""
        if not os.path.exists(RULES_DIR):
            logger.warning(f"Rules directory not found: {RULES_DIR}")
            return

        rule_files = glob.glob(os.path.join(RULES_DIR, '*.json'))
        for file_path in rule_files:
            try:
                with open(file_path, 'r') as f:
                    rule = json.load(f)
                    self.rules.append(rule)
                    logger.info(f"Loaded rule: {rule.get('id')}")
            except Exception as e:
                logger.error(f"Error loading rule {file_path}: {e}")

    def evaluate_rule(self, rule, facts_by_chart):
        """
        Evaluates a single rule against the provided facts.
        """
        matched_conditions = 0
        failed_reasons = []
        
        for condition in rule.get('conditions', []):
            chart = condition.get('chart')
            req_cond = condition.get('condition')
            
            chart_facts = facts_by_chart.get(chart, {})
            
            if chart_facts.get(req_cond, False):
                 matched_conditions += 1
            else:
                 failed_reasons.append(f"{chart} missing {req_cond}")
                 
        match_logic = rule.get('match', 'ALL')
        is_match = False
        
        if match_logic.startswith('ANY_'):
            min_required = int(match_logic.split('_')[1])
            is_match = matched_conditions >= min_required
        elif match_logic == 'ALL':
            is_match = matched_conditions == len(rule.get('conditions', []))
            
        return is_match, failed_reasons

    def evaluate_all(self, facts_by_chart):
        """
        Evaluates all loaded rules and returns a list of matched rule IDs.
        Logs the detailed evaluation process to rule_evaluations.log.
        """
        signals = []
        now_str = datetime.now().strftime('%H:%M:%S')
        
        with open(RULE_LOG_FILE, 'a') as log_file:
            # We don't want to log every single tick if facts are empty,
            # only when we have active boxes and facts to evaluate against.
            if not facts_by_chart:
                return signals

            for rule in self.rules:
                is_match, failed_reasons = self.evaluate_rule(rule, facts_by_chart)
                rule_id = rule.get('id')
                
                if is_match:
                    log_msg = f"[{now_str}] {rule_id}: TRUE (Action: {rule.get('type')})\n"
                    log_file.write(log_msg)
                    signals.append({
                        'rule': rule,
                        'rule_id': rule_id,
                        'action': rule.get('type'),
                        'priority': rule.get('priority', 5),
                        'cooldown': rule.get('cooldown', 60),
                        'allow_reentry': rule.get('allow_reentry', False)
                    })
                else:
                    reason_str = ", ".join(failed_reasons)
                    log_msg = f"[{now_str}] {rule_id}: FALSE (Reason: {reason_str})\n"
                    log_file.write(log_msg)
                
        return signals

# Singleton instance
engine = RuleEngine()

def get_engine():
    return engine
