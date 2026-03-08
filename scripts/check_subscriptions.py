"""Lista todas as subscriptions do banco para verificar se são fantasmas."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from src.db.session import get_db
from sqlalchemy import text

with get_db() as session:
    rows = session.execute(text("""
        SELECT s.id, s.user_id, s.plan_code, s.status, s.provider,
               s.provider_customer_id, s.provider_subscription_id,
               s.trial_start, s.trial_end, s.current_period_start, s.current_period_end,
               s.created_at, s.updated_at,
               u.name
        FROM subscriptions s
        LEFT JOIN users u ON s.user_id = u.phone_number
        ORDER BY s.id
    """)).fetchall()

    if not rows:
        print("Nenhuma subscription encontrada no banco.")
    else:
        print(f"Total de subscriptions: {len(rows)}\n")
        for r in rows:
            print(f"--- Subscription #{r[0]} ---")
            print(f"  user_id:                  {r[1]}")
            print(f"  user_name:                {r[13]}")
            print(f"  plan_code:                {r[2]}")
            print(f"  status:                   {r[3]}")
            print(f"  provider:                 {r[4]}")
            print(f"  provider_customer_id:     {r[5]}")
            print(f"  provider_subscription_id: {r[6]}")
            print(f"  trial_start:              {r[7]}")
            print(f"  trial_end:                {r[8]}")
            print(f"  current_period_start:     {r[9]}")
            print(f"  current_period_end:       {r[10]}")
            print(f"  created_at:               {r[11]}")
            print(f"  updated_at:               {r[12]}")
            print()
