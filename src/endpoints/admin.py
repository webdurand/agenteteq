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
        if _use_postgres():
            engine = _get_pg_engine()
            with engine.connect() as conn:
                rows = conn.execute(__import__("sqlalchemy").text("SELECT phone_number, name, email, role, last_seen_at FROM users")).fetchall()
        else:
            conn = _get_sqlite_conn()
            rows = conn.execute("SELECT phone_number, name, email, role, last_seen_at FROM users").fetchall()
            conn.close()
            
        for row in rows:
            users.append({
                "phone_number": row[0],
                "name": row[1],
                "email": row[2],
                "role": row[3],
                "last_seen_at": str(row[4]) if row[4] else None
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
    from src.memory.identity import promote_user_to_admin
    target = get_user(req.phone_number)
    if not target:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
        
    promote_user_to_admin(req.phone_number)
    return {"message": f"Usuário {req.phone_number} promovido a admin com sucesso"}
