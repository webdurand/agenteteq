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

        # Pega a assinatura mais recente de cada usuário (qualquer status)
        pg_query = """
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
            LEFT JOIN LATERAL (
                SELECT status, plan_code, current_period_end
                FROM subscriptions
                WHERE user_id = u.phone_number
                ORDER BY updated_at DESC NULLS LAST
                LIMIT 1
            ) s ON true
            ORDER BY u.trial_started_at DESC NULLS LAST
        """

        sqlite_query = """
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
            LEFT JOIN (
                SELECT s1.user_id, s1.status, s1.plan_code, s1.current_period_end
                FROM subscriptions s1
                INNER JOIN (
                    SELECT user_id, MAX(updated_at) AS max_updated
                    FROM subscriptions
                    GROUP BY user_id
                ) latest ON s1.user_id = latest.user_id AND s1.updated_at = latest.max_updated
            ) s ON u.phone_number = s.user_id
            ORDER BY u.trial_started_at DESC
        """

        if _use_postgres():
            engine = _get_pg_engine()
            with engine.connect() as conn:
                rows = conn.execute(__import__("sqlalchemy").text(pg_query)).fetchall()
        else:
            conn = _get_sqlite_conn()
            rows = conn.execute(sqlite_query).fetchall()
            conn.close()
            
        import datetime as dt_module
        now_utc = dt_module.datetime.now(dt_module.timezone.utc)

        for row in rows:
            stripe_status = row[7]  # status da assinatura mais recente (pode ser None)
            trial_ends_at = row[6]

            if stripe_status:
                # Tem assinatura Stripe: usa o status real dela, incluindo 'canceled'
                # 'trialing' via Stripe = trial pago (Pro), diferente do trial gratuito
                eff_status = "pro_trial" if stripe_status == "trialing" else stripe_status
            elif trial_ends_at:
                # Sem assinatura Stripe: verifica se o trial gratuito ainda é válido
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

# ============================================================================
# SYSTEM & QUEUE ENDPOINTS
# ============================================================================

from src.config.system_config import get_all_configs, set_config

@router.get("/system/queue")
def get_queue_status(user: dict = Depends(require_admin)):
    engine = _get_pg_engine()
    status_counts = {"pending": 0, "processing": 0, "done_today": 0, "failed_today": 0, "avg_wait": 0}
    if engine:
        from sqlalchemy import text
        with engine.connect() as conn:
            counts = conn.execute(text("""
                SELECT status, COUNT(*) FROM background_tasks 
                WHERE created_at >= NOW() - INTERVAL '24 hours' OR status IN ('pending', 'processing')
                GROUP BY status
            """)).fetchall()
            for row in counts:
                if row[0] == 'pending': status_counts['pending'] = row[1]
                elif row[0] == 'processing': status_counts['processing'] = row[1]
                elif row[0] == 'done': status_counts['done_today'] = row[1]
                elif row[0] == 'failed': status_counts['failed_today'] = row[1]
                
            from src.queue.task_queue import _get_avg_processing_time
            status_counts["avg_wait"] = round(_get_avg_processing_time(), 1)
    else:
        with _get_sqlite_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT status, COUNT(*) FROM background_tasks 
                WHERE created_at >= datetime('now', '-24 hours') OR status IN ('pending', 'processing')
                GROUP BY status
            """)
            for row in cursor.fetchall():
                if row[0] == 'pending': status_counts['pending'] = row[1]
                elif row[0] == 'processing': status_counts['processing'] = row[1]
                elif row[0] == 'done': status_counts['done_today'] = row[1]
                elif row[0] == 'failed': status_counts['failed_today'] = row[1]
            from src.queue.task_queue import _get_avg_processing_time
            status_counts["avg_wait"] = round(_get_avg_processing_time(), 1)
            
    return status_counts

@router.get("/system/config")
def list_system_configs(user: dict = Depends(require_admin)):
    return get_all_configs()

class ConfigUpdateRequest(BaseModel):
    key: str
    value: str

@router.put("/system/config")
def update_system_config(req: ConfigUpdateRequest, user: dict = Depends(require_admin)):
    set_config(req.key, req.value)
    return {"message": "Configuração atualizada com sucesso"}

@router.get("/system/tasks")
def list_system_tasks(status: str = None, limit: int = 50, user: dict = Depends(require_admin)):
    engine = _get_pg_engine()
    tasks = []
    if engine:
        from sqlalchemy import text
        query = "SELECT id, user_id, task_type, channel, status, created_at, started_at, completed_at, attempts, result FROM background_tasks"
        params = {"limit": limit}
        if status:
            query += " WHERE status = :status"
            params["status"] = status
        query += " ORDER BY created_at DESC LIMIT :limit"
        
        with engine.connect() as conn:
            rows = conn.execute(text(query), params).fetchall()
            for r in rows:
                tasks.append({
                    "id": str(r[0]), "user_id": r[1], "task_type": r[2], "channel": r[3],
                    "status": r[4], "created_at": r[5].isoformat() if r[5] else None,
                    "started_at": r[6].isoformat() if r[6] else None,
                    "completed_at": r[7].isoformat() if r[7] else None,
                    "attempts": r[8], "result": r[9]
                })
    return {"tasks": tasks}

@router.post("/system/tasks/{task_id}/retry")
def retry_task(task_id: str, user: dict = Depends(require_admin)):
    engine = _get_pg_engine()
    if engine:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("UPDATE background_tasks SET status = 'pending', attempts = 0 WHERE id = :id"), {"id": task_id})
            conn.commit()
    return {"message": "Task enviada para retry"}

@router.post("/system/tasks/{task_id}/cancel")
def cancel_task(task_id: str, user: dict = Depends(require_admin)):
    engine = _get_pg_engine()
    if engine:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("UPDATE background_tasks SET status = 'failed', result = '{\"error\": \"cancelled by admin\"}' WHERE id = :id AND status IN ('pending', 'processing')"), {"id": task_id})
            conn.commit()
    return {"message": "Task cancelada"}

@router.get("/system/metrics")
def get_system_metrics(days: int = 7, user: dict = Depends(require_admin)):
    engine = _get_pg_engine()
    if not engine:
        return {"error": "Not supported on SQLite"}
        
    metrics = {
        "tools_usage": [],
        "user_usage": [],
        "plan_avg": []
    }
    
    from sqlalchemy import text
    with engine.connect() as conn:
        # Tools usage
        rows = conn.execute(text("""
            SELECT tool_name, COUNT(*) as count 
            FROM usage_events 
            WHERE event_type = 'tool_called' AND created_at >= NOW() - make_interval(days => :d)
            GROUP BY tool_name ORDER BY count DESC LIMIT 10
        """), {"d": days}).fetchall()
        metrics["tools_usage"] = [{"name": r[0], "count": r[1]} for r in rows]
        
        rows = conn.execute(text("""
            SELECT user_id, COUNT(*) as count 
            FROM background_tasks 
            WHERE task_type = 'carousel' AND status = 'done' AND created_at >= NOW() - make_interval(days => :d)
            GROUP BY user_id ORDER BY count DESC LIMIT 10
        """), {"d": days}).fetchall()
        metrics["user_usage"] = [{"user_id": r[0], "generates": r[1]} for r in rows]
        
    return metrics

@router.get("/business/analytics")
def get_business_analytics(days: int = 30, user: dict = Depends(require_admin)):
    import datetime as dt_module
    from sqlalchemy import text
    from src.memory.identity import _use_postgres, _get_pg_engine, _get_sqlite_conn
    from src.config.system_config import _get_sqlite_conn as _get_sqlite_sys_conn
    
    analytics = {
        "financial": {"mrr_cents": 0, "active_subs": 0, "status_distribution": [], "conversion_rate": 0, "churn_rate": 0},
        "engagement": {"dau": [], "new_users_by_day": [], "messages_by_day": [], "channel_distribution": []},
        "features": {"tools_ranking": [], "tools_trend_by_day": [], "tasks_stats": {}, "reminders_active": 0, "carousel_stats": {}, "image_edit_stats": {}},
        "operational": {"error_rate": 0, "latency_by_tool": []}
    }

    try:
        engine = _get_pg_engine()
        if engine and _use_postgres():
            with engine.connect() as conn:
                # 1. Financial
                mrr = conn.execute(text("SELECT COALESCE(SUM(amount_cents), 0) FROM subscriptions s JOIN billing_plans p ON s.plan_code = p.code WHERE s.status IN ('active', 'trialing')")).scalar()
                analytics["financial"]["mrr_cents"] = int(mrr or 0)
                
                active_subs = conn.execute(text("SELECT COUNT(*) FROM subscriptions WHERE status IN ('active', 'trialing')")).scalar()
                analytics["financial"]["active_subs"] = int(active_subs or 0)
                
                rows = conn.execute(text("SELECT status, COUNT(*) FROM subscriptions GROUP BY status")).fetchall()
                analytics["financial"]["status_distribution"] = [{"name": r[0] or 'unknown', "value": r[1]} for r in rows]
                
                # 2. Engagement
                rows = conn.execute(text("""
                    SELECT DATE(created_at) as d, COUNT(DISTINCT user_id) 
                    FROM usage_events 
                    WHERE created_at >= NOW() - make_interval(days => :d)
                    GROUP BY d ORDER BY d
                """), {"d": days}).fetchall()
                analytics["engagement"]["dau"] = [{"date": str(r[0]), "users": r[1]} for r in rows]
                
                rows = conn.execute(text("""
                    SELECT DATE(created_at) as d, 
                           SUM(CASE WHEN event_type = 'message_received' THEN 1 ELSE 0 END) as received,
                           SUM(CASE WHEN event_type = 'message_sent' THEN 1 ELSE 0 END) as sent
                    FROM usage_events 
                    WHERE created_at >= NOW() - make_interval(days => :d)
                    GROUP BY d ORDER BY d
                """), {"d": days}).fetchall()
                analytics["engagement"]["messages_by_day"] = [{"date": str(r[0]), "received": r[1], "sent": r[2]} for r in rows]
                
                rows = conn.execute(text("SELECT channel, COUNT(DISTINCT user_id) FROM usage_events WHERE created_at >= NOW() - make_interval(days => :d) GROUP BY channel"), {"d": days}).fetchall()
                analytics["engagement"]["channel_distribution"] = [{"name": r[0] or 'unknown', "value": r[1]} for r in rows]
                
                # 3. Features
                rows = conn.execute(text("""
                    SELECT tool_name, COUNT(*) 
                    FROM usage_events 
                    WHERE event_type = 'tool_called' AND created_at >= NOW() - make_interval(days => :d) 
                    GROUP BY tool_name ORDER BY COUNT(*) DESC LIMIT 10
                """), {"d": days}).fetchall()
                analytics["features"]["tools_ranking"] = [{"name": r[0] or 'unknown', "calls": r[1]} for r in rows]
                
                top_tools = [r[0] for r in rows[:5] if r[0]]
                if top_tools:
                    tool_filter = "('" + "','".join(top_tools) + "')"
                    rows = conn.execute(text(f"""
                        SELECT DATE(created_at) as d, tool_name, COUNT(*)
                        FROM usage_events
                        WHERE event_type = 'tool_called' AND tool_name IN {tool_filter} AND created_at >= NOW() - make_interval(days => :d)
                        GROUP BY d, tool_name ORDER BY d
                    """), {"d": days}).fetchall()
                    trend = {}
                    for r in rows:
                        dt = str(r[0])
                        if dt not in trend: trend[dt] = {"date": dt}
                        trend[dt][r[1]] = r[2]
                    analytics["features"]["tools_trend_by_day"] = list(trend.values())
                    
                rows = conn.execute(text("SELECT status, COUNT(*) FROM tasks GROUP BY status")).fetchall()
                for r in rows:
                    analytics["features"]["tasks_stats"][r[0] or 'unknown'] = r[1]
                
                rows = conn.execute(text("SELECT status, COUNT(*) FROM background_tasks WHERE task_type = 'carousel' GROUP BY status")).fetchall()
                for r in rows:
                    analytics["features"]["carousel_stats"][r[0] or 'unknown'] = r[1]
                    
                # 4. Operational
                failed = conn.execute(text("SELECT COUNT(*) FROM usage_events WHERE event_type = 'tool_failed' AND created_at >= NOW() - make_interval(days => :d)"), {"d": days}).scalar() or 0
                called = conn.execute(text("SELECT COUNT(*) FROM usage_events WHERE event_type = 'tool_called' AND created_at >= NOW() - make_interval(days => :d)"), {"d": days}).scalar() or 1
                if called == 0: called = 1
                analytics["operational"]["error_rate"] = round((failed / called) * 100, 2)
                
                rows = conn.execute(text("""
                    SELECT tool_name, AVG(latency_ms) 
                    FROM usage_events 
                    WHERE event_type = 'tool_called' AND latency_ms IS NOT NULL AND created_at >= NOW() - make_interval(days => :d)
                    GROUP BY tool_name ORDER BY AVG(latency_ms) DESC LIMIT 10
                """), {"d": days}).fetchall()
                analytics["operational"]["latency_by_tool"] = [{"name": r[0] or 'unknown', "avg_ms": int(r[1] or 0)} for r in rows]
                
        else:
            with _get_sqlite_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COALESCE(SUM(amount_cents), 0) FROM subscriptions s JOIN billing_plans p ON s.plan_code = p.code WHERE s.status IN ('active', 'trialing')")
                analytics["financial"]["mrr_cents"] = int(cur.fetchone()[0] or 0)
                
                cur.execute("SELECT COUNT(*) FROM subscriptions WHERE status IN ('active', 'trialing')")
                analytics["financial"]["active_subs"] = int(cur.fetchone()[0] or 0)
                
                cur.execute("SELECT status, COUNT(*) FROM subscriptions GROUP BY status")
                analytics["financial"]["status_distribution"] = [{"name": r[0] or 'unknown', "value": r[1]} for r in cur.fetchall()]
                
                cur.execute(f"SELECT date(created_at) as d, COUNT(DISTINCT user_id) FROM usage_events WHERE created_at >= date('now', '-{days} days') GROUP BY d ORDER BY d")
                analytics["engagement"]["dau"] = [{"date": r[0], "users": r[1]} for r in cur.fetchall()]
                
                cur.execute(f"SELECT date(created_at) as d, SUM(CASE WHEN event_type = 'message_received' THEN 1 ELSE 0 END), SUM(CASE WHEN event_type = 'message_sent' THEN 1 ELSE 0 END) FROM usage_events WHERE created_at >= date('now', '-{days} days') GROUP BY d ORDER BY d")
                analytics["engagement"]["messages_by_day"] = [{"date": r[0], "received": r[1] or 0, "sent": r[2] or 0} for r in cur.fetchall()]
                
                cur.execute(f"SELECT channel, COUNT(DISTINCT user_id) FROM usage_events WHERE created_at >= date('now', '-{days} days') GROUP BY channel")
                analytics["engagement"]["channel_distribution"] = [{"name": r[0] or 'unknown', "value": r[1]} for r in cur.fetchall()]
                
                cur.execute(f"SELECT tool_name, COUNT(*) FROM usage_events WHERE event_type = 'tool_called' AND created_at >= date('now', '-{days} days') GROUP BY tool_name ORDER BY COUNT(*) DESC LIMIT 10")
                rows = cur.fetchall()
                analytics["features"]["tools_ranking"] = [{"name": r[0] or 'unknown', "calls": r[1]} for r in rows]
                
                top_tools = [r[0] for r in rows[:5] if r[0]]
                if top_tools:
                    tool_filter = "('" + "','".join(top_tools) + "')"
                    cur.execute(f"SELECT date(created_at) as d, tool_name, COUNT(*) FROM usage_events WHERE event_type = 'tool_called' AND tool_name IN {tool_filter} AND created_at >= date('now', '-{days} days') GROUP BY d, tool_name ORDER BY d")
                    trend = {}
                    for r in cur.fetchall():
                        dt = r[0]
                        if dt not in trend: trend[dt] = {"date": dt}
                        trend[dt][r[1]] = r[2]
                    analytics["features"]["tools_trend_by_day"] = list(trend.values())
                    
                cur.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status")
                for r in cur.fetchall(): analytics["features"]["tasks_stats"][r[0] or 'unknown'] = r[1]
                
                cur.execute(f"SELECT COUNT(*) FROM usage_events WHERE event_type = 'tool_failed' AND created_at >= date('now', '-{days} days')")
                failed = cur.fetchone()[0] or 0
                cur.execute(f"SELECT COUNT(*) FROM usage_events WHERE event_type = 'tool_called' AND created_at >= date('now', '-{days} days')")
                called = cur.fetchone()[0] or 1
                if called == 0: called = 1
                analytics["operational"]["error_rate"] = round((failed / called) * 100, 2)
                
                cur.execute(f"SELECT tool_name, AVG(latency_ms) FROM usage_events WHERE event_type = 'tool_called' AND latency_ms IS NOT NULL AND created_at >= date('now', '-{days} days') GROUP BY tool_name ORDER BY AVG(latency_ms) DESC LIMIT 10")
                analytics["operational"]["latency_by_tool"] = [{"name": r[0] or 'unknown', "avg_ms": int(r[1] or 0)} for r in cur.fetchall()]

            with _get_sqlite_sys_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT status, COUNT(*) FROM background_tasks WHERE task_type = 'carousel' GROUP BY status")
                for r in cur.fetchall(): analytics["features"]["carousel_stats"][r[0] or 'unknown'] = r[1]
                
    except Exception as e:
        print(f"[ANALYTICS] Erro ao buscar metricas: {e}")
        
    return analytics
