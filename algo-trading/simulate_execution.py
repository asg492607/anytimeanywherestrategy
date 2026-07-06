import uuid
import logging

logger = logging.getLogger('simulate_execution')

def mock_execute(params):
    """
    Simulates placing an order. Returns a mock broker order ID starting with SIM_.
    """
    logger.info(f"[SIMULATION EXECUTION] Placing order: {params}")
    order_id = f"SIM_{uuid.uuid4().hex[:12].upper()}"
    return {
        'status': True,
        'broker_order_id': order_id,
        'message': 'SUCCESS'
    }
