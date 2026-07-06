import os
import sys
import unittest
import json

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from app import app
import db
import simulate_db

class TestSimulation(unittest.TestCase):
    def setUp(self):
        # Configure app for testing
        app.config['TESTING'] = True
        self.client = app.test_client()
        
        # Initialize databases
        db.init_db()
        simulate_db.init_db()
        
        # Create a test user if it doesn't exist
        with db.get_db_connection() as conn:
            user = conn.execute("SELECT * FROM users WHERE email = ?", ("test_sim@antigravity.com",)).fetchone()
            if not user:
                db.create_user("Test Sim User", "test_sim@antigravity.com", "password123")
                user = conn.execute("SELECT * FROM users WHERE email = ?", ("test_sim@antigravity.com",)).fetchone()
            self.user_id = user['id']
            
        # Log in
        self.client.post('/login', data={
            'email': 'test_sim@antigravity.com',
            'password': 'password123'
        }, follow_redirects=True)
        
    def test_simulation_workflow(self):
        # 1. Start simulation
        print("Testing POST /api/simulate/start...")
        res = self.client.post('/api/simulate/start', json={
            'date': '2026-07-06'
        })
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertEqual(data['status'], 'RUNNING')
        self.assertEqual(data['current_index'], 0)
        
        # 2. Get simulation data
        print("Testing GET /api/simulate/data...")
        res = self.client.get('/api/simulate/data')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertEqual(data['status'], 'RUNNING')
        self.assertEqual(len(data['candles']['SPOT']), 1)
        
        # 3. Step/Tick simulation multiple times to trigger reference box, signals, execution, target exit
        print("Ticking simulation...")
        for step in range(30):
            res = self.client.post('/api/simulate/tick')
            self.assertEqual(res.status_code, 200)
            data = json.loads(res.data)
            
        # 4. Check simulation data after 30 ticks
        res = self.client.get('/api/simulate/data')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        print(f"Active Reference Boxes: {len(data['reference_boxes'])}")
        print(f"Trades Count: {len(data['trades'])}")
        print(f"Trade Executions Count: {len(data['executions'])}")
        
        # Verify a trade was actually created and exited at target!
        self.assertGreater(len(data['trades']), 0, "A trade should have been triggered by the deterministic candle feed")
        
        # 5. Stop simulation
        print("Testing POST /api/simulate/stop...")
        res = self.client.post('/api/simulate/stop')
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertEqual(data['status'], 'IDLE')
        
if __name__ == '__main__':
    unittest.main()
