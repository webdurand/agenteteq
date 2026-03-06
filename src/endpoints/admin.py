from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List
from src.auth.deps import require_admin
from src.memory.identity import _use_postgres, _get_pg_engine, _get_sqlite_conn, get_user

router = APIRouter(prefix="/admin", tags=["admin"])

class AdminCreateRequest(BaseModel):
    phone_number: str

@router.get("/business/summary")
def get_business_summary(user: dict = Depends(require_admin)):
    try:
        if _use_postgres():
            engine = _get_pg_engine()
            with engine.connect() as conn:
                total_users = conn.execute(__import__("sqlalchemy").text("SELECT COUNT(*) FROM users")).scalar()
                verified_users = conn.execute(__import__("sqlalchemy").text("SELECT COUNT(*) FROM users WHERE whatsapp_verified = TRUE")).scalar()
                total_msgs = conn.execute(__import__("sqlalchemy").text("SELECT COUNT(*) FROM usage_events WHERE event_type = 'message_received'")).scalar()
        else:
            conn = _get_sqlite_conn()
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            verified_users = conn.execute("SELECT COUNT(*) FROM users WHERE whatsapp_verified = 1").fetchone()[0]
            total_msgs = conn.execute("SELECT COUNT(*) FROM usage_events WHERE event_type = 'message_received'").fetchone()[0]
            conn.close()

        return {
            "total_users": total_users,
            "verified_users": verified_users,
            "total_messages": total_msgs
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/business/users")
def list_users(user: dict = Depends(require_admin)):
    try:
        users = []
        query = """
            SELECT 
                u.phone_number, 
                u.name, 
                u.email, 
                u.role, 
                u.last_seen_at,
                u.trial_started_at,
                u.trial_ends_at,
                s.status,
                s.plan_code,
                s.current_period_end
            FROM users u
            LEFT JOIN subscriptions s ON u.phone_number = s.user_id 
                 AND s.status IN ('active', 'trialing', 'past_due')
            ORDER BY u.trial_started_at DESC
        """
        if _use_postgres():
            engine = _get_pg_engine()
            with engine.connect() as conn:
                rows = conn.execute(__import__("sqlalchemy").text(query)).fetchall()
        else:
            conn = _get_sqlite_conn()
            rows = conn.execute(query).fetchall()
            conn.close()
            
        import datetime as dt_module
        now_utc = dt_module.datetime.now(dt_module.timezone.utc)

        for row in rows:
            stripe_status = row[7]  # status da assinatura Stripe (pode ser None)
            trial_ends_at = row[6]

            if stripe_status:
                eff_status = stripe_status
            elif trial_ends_at:
                # Normaliza para datetime aware para comparação
                if isinstance(trial_ends_at, str):
                    try:
                        trial_dt = dt_module.datetime.fromisoformat(trial_ends_at)
                        if trial_dt.tzinfo is None:
                            trial_dt = trial_dt.replace(tzinfo=dt_module.timezone.utc)
                    except Exception:
                        trial_dt = None
                else:
                    trial_dt = trial_ends_at
                    if trial_dt and trial_dt.tzinfo is None:
                        trial_dt = trial_dt.replace(tzinfo=dt_module.timezone.utc)
                eff_status = "trialing" if trial_dt and now_utc < trial_dt else "expired"
            else:
                eff_status = "none"

            users.append({
                "phone_number": row[0],
                "name": row[1],
                "email": row[2],
                "role": row[3],
                "last_seen_at": str(row[4]) if row[4] else None,
                "created_at": str(row[5]) if row[5] else None,
                "trial_ends_at": str(row[6]) if row[6] else None,
                "subscription_status": eff_status,
                "plan_code": row[8],
                "current_period_end": str(row[9]) if row[9] else None,
            })
        return users
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/business/tools")
def get_tools_summary(user: dict = Depends(require_admin)):
    try:
        tools = []
        if _use_postgres():
            engine = _get_pg_engine()
            with engine.connect() as conn:
                rows = conn.execute(__import__("sqlalchemy").text("SELECT tool_name, COUNT(*) as count FROM usage_events WHERE event_type = 'tool_called' GROUP BY tool_name ORDER BY count DESC")).fetchall()
        else:
            conn = _get_sqlite_conn()
            rows = conn.execute("SELECT tool_name, COUNT(*) as count FROM usage_events WHERE event_type = 'tool_called' GROUP BY tool_name ORDER BY count DESC").fetchall()
            conn.close()
            
        for row in rows:
            tools.append({"name": row[0], "calls": row[1]})
        return tools
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/health/summary")
def get_health_summary(user: dict = Depends(require_admin)):
    db_status = "ok"
    try:
        if _use_postgres():
            engine = _get_pg_engine()
            with engine.connect() as conn:
                conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        else:
            conn = _get_sqlite_conn()
            conn.execute("SELECT 1")
            conn.close()
    except Exception as e:
        db_status = f"error: {str(e)}"
        
    return {
        "status": "online" if db_status == "ok" else "degraded",
        "database": db_status
    }

@router.post("/admins")
def add_admin(req: AdminCreateRequest, current_user: dict = Depends(require_admin)):
    from src.memory.identity import promote_user_to_admin, get_user
    target = get_user(req.phone_number)
    if not target:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
        
    promote_user_to_admin(req.phone_number)
    return {"message": f"Usuário {req.phone_number} promovido a admin com sucesso"}

@router.delete("/admins/{phone_number}")
def remove_admin(phone_number: str, current_user: dict = Depends(require_admin)):
    from src.memory.identity import demote_admin, get_user
    target = get_user(phone_number)
    if not target:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
        
    if current_user.get("phone_number") == phone_number:
        raise HTTPException(status_code=400, detail="Você não pode remover seu próprio acesso de admin")
        
    demote_admin(phone_number)
    return {"message": f"Usuário {phone_number} rebaixado para user com sucesso"}
