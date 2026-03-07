"""
Dispatcher para mensagens proativas agendadas.
Quando um job do APScheduler dispara, esta funcao busca o reminder no banco de dados,
verifica se ainda esta ativo, cria o Agno Agent, executa as instrucoes e envia 
o resultado via canal configurado.

Nota: APScheduler executa jobs em threads, por isso usamos asyncio.run()
para chamar o cliente async do WhatsApp a partir de um contexto sincrono.
"""
import asyncio
import traceback


def dispatch_proactive_message(reminder_id: int):
    """
    Funcao chamada pelo APScheduler quando um job agendado dispara.
    Busca o lembrete no banco, cria o agente, executa e envia.

    Args:
        reminder_id: ID do lembrete na tabela reminders.
    """
    from src.db.session import get_engine, _is_sqlite
    engine = get_engine() if not _is_sqlite() else None
    
    if engine:
        from sqlalchemy import text
        with engine.connect() as conn:
            # Hash reminder_id to use as int lock key
            lock_acquired = conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:id))"), {"id": str(reminder_id)}).scalar()
            if not lock_acquired:
                print(f"[DISPATCHER] Outro pod ja esta processando o reminder {reminder_id}. Abortando localmente.")
                return
    
    print(f"[DISPATCHER] Disparando reminder_id {reminder_id}...")
    try:
        from src.models.reminders import get_reminder, mark_fired
        
        reminder = get_reminder(reminder_id)
        if not reminder:
            print(f"[DISPATCHER] Reminder {reminder_id} nao encontrado no banco. Abortando.")
            return
            
        if reminder["status"] != "active":
            print(f"[DISPATCHER] Reminder {reminder_id} nao esta ativo (status: {reminder['status']}). Abortando.")
            return

        user_phone = reminder["user_id"]
        task_instructions = reminder["task_instructions"]
        channel = reminder["notification_channel"]
        
        print(f"[DISPATCHER] Reminder {reminder_id} | User: {user_phone} | Channel: {channel} | Task: {task_instructions[:60]}...")

        # 1. Obter resposta do Agente (se o canal precisar)
        response_content = None
        if channel in ["whatsapp_text", "whatsapp_call", "web_voice", "web_text", "web_whatsapp"]:
            from src.agent.factory import create_agent_with_tools
            from src.agent.response_utils import extract_final_response

            reminder_instructions = [
                "EXECUCAO DE LEMBRETE AGENDADO: Voce esta executando um lembrete que o usuario agendou anteriormente.",
                "NAO peca mais informacoes, NAO tente agendar nada novo, NAO faca perguntas.",
                "Execute as instrucoes diretamente e envie o resultado pronto.",
            ]

            agent_channel = "web" if channel in {"web_voice", "web_text"} else "whatsapp"
            agent = create_agent_with_tools(
                session_id=user_phone,
                user_id=user_phone,
                channel=agent_channel,
                extra_instructions=reminder_instructions,
            )
            response = agent.run(task_instructions, knowledge_filters={"user_id": user_phone})
            
            if response and response.content:
                response_content = extract_final_response(response)
            else:
                print(f"[DISPATCHER] Agente retornou resposta vazia para {user_phone}.")
                return

        # 2. Enviar pelo canal especifico
        if channel == "whatsapp_text":
            from src.integrations.whatsapp import whatsapp_client
            asyncio.run(whatsapp_client.send_text_message(user_phone, response_content))
            print(f"[DISPATCHER] Mensagem enviada com sucesso para {user_phone} via whatsapp_text.")
            
        elif channel == "whatsapp_call":
            # Futuro: Implementar chamada de audio WhatsApp aqui
            print(f"[DISPATCHER] [FUTURO] Ligacao WhatsApp para {user_phone} solicitada. Falaria: {response_content}")
            
        elif channel == "web_voice":
            from src.endpoints.web import ws_manager
            from src.integrations.tts import get_tts
            import base64
            
            if not ws_manager.is_online(user_phone):
                print(f"[DISPATCHER] Usuario {user_phone} nao esta online na web. Fazendo fallback para whatsapp_text.")
                from src.integrations.whatsapp import whatsapp_client
                fallback_msg = f"(Lembrete do Agente de Voz)\n\n{response_content}"
                asyncio.run(whatsapp_client.send_text_message(user_phone, fallback_msg))
            else:
                print(f"[DISPATCHER] Usuario {user_phone} online na web. Gerando audio para falar...")
                tts = get_tts()
                try:
                    audio_out, mime_type = asyncio.run(tts.synthesize(response_content))
                    audio_b64 = base64.b64encode(audio_out).decode() if audio_out else ""
                except Exception as e:
                    print(f"[DISPATCHER] TTS falhou: {e}")
                    audio_b64 = ""
                    mime_type = "browser"
                
                msg_payload = {
                    "type": "response",
                    "text": response_content,
                    "audio_b64": audio_b64,
                    "mime_type": mime_type,
                    "needs_follow_up": False
                }
                asyncio.run(ws_manager.send_personal_message(user_phone, msg_payload))
                print(f"[DISPATCHER] Lembrete falado enviado com sucesso para {user_phone} via web_voice.")

        elif channel == "web_text":
            from src.endpoints.web import ws_manager
            from src.integrations.whatsapp import whatsapp_client

            msg_payload = {
                "type": "response",
                "text": response_content,
                "audio_b64": "",
                "mime_type": "none",
                "needs_follow_up": False,
            }

            if asyncio.run(ws_manager.send_personal_message(user_phone, msg_payload)):
                print(f"[DISPATCHER] Lembrete em texto enviado com sucesso para {user_phone} via web_text.")
            else:
                print(f"[DISPATCHER] Usuario {user_phone} nao esta online na web. Fallback para WhatsApp.")
                fallback_msg = f"(Lembrete da web)\n\n{response_content}"
                asyncio.run(whatsapp_client.send_text_message(user_phone, fallback_msg))

        elif channel == "web_whatsapp":
            from src.endpoints.web import ws_manager
            from src.integrations.whatsapp import whatsapp_client

            # 1) Sempre envia no WhatsApp
            asyncio.run(whatsapp_client.send_text_message(user_phone, response_content))
            print(f"[DISPATCHER] Lembrete enviado para {user_phone} via whatsapp_text (canal combinado).")

            # 2) Tenta enviar na web (se online)
            msg_payload = {
                "type": "response",
                "text": response_content,
                "audio_b64": "",
                "mime_type": "none",
                "needs_follow_up": False,
            }
            if asyncio.run(ws_manager.send_personal_message(user_phone, msg_payload)):
                print(f"[DISPATCHER] Lembrete enviado para {user_phone} via web_text (canal combinado).")
            else:
                print(f"[DISPATCHER] Usuario {user_phone} offline na web. Entrega feita apenas no WhatsApp.")
            
        else:
            print(f"[DISPATCHER] Canal nao suportado: {channel}")

        # 3. Se for disparo unico, marcar como fired
        if reminder["trigger_type"] == "date":
            mark_fired(reminder_id)
            print(f"[DISPATCHER] Reminder {reminder_id} marcado como 'fired'.")

    except Exception as e:
        print(f"[DISPATCHER] Erro ao disparar reminder {reminder_id}: {e}")
        traceback.print_exc()
        
    finally:
        if engine:
            try:
                from sqlalchemy import text
                with engine.connect() as conn:
                    conn.execute(text("SELECT pg_advisory_unlock(hashtext(:id))"), {"id": str(reminder_id)})
                    conn.commit()
            except Exception as e:
                print(f"[DISPATCHER] Erro ao liberar lock do reminder {reminder_id}: {e}")
