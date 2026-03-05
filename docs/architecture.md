# Arquitetura do Agente WhatsApp - Diario Teq

## Visão Geral

O `agenteteq` é uma API em Python baseada no FastAPI responsável por receber webhooks do WhatsApp, processar áudios utilizando serviços de transcrição, e alimentar um Agente Agno chamado **Teq**.
O Teq possui ferramentas para conversar de forma descontraída, publicar posts no blog, gerenciar memória, pesquisar na web, realizar pesquisas profundas com múltiplos sub-agentes, gerenciar uma lista de tarefas pessoal, consultar previsão do tempo e agendar mensagens proativas.

## Fluxo Principal

1. **Webhook (FastAPI)**: Recebe payload da Meta (WhatsApp Cloud API) com um áudio ou texto.
2. **Módulo de Identidade (Determinístico)**: Antes de acionar a IA, o sistema verifica o número de telefone no banco de dados. Se for um usuário novo, realiza um onboarding determinístico pedindo o nome. Se já for cadastrado, verifica `last_seen_at` para detectar nova sessão.
3. **Detecção de Nova Sessão**: Se o usuário ficou mais de 4 horas sem enviar mensagens, o orchestrator injeta um contexto especial (`GREETING_INJECTION`) no prompt, instruindo o Teq a iniciar com uma saudação personalizada. O Teq consulta as memórias do usuário para saber quais informações incluir (por padrão: previsão do tempo + tarefas pendentes; configurável via conversa).
4. **Integração WhatsApp**: Faz o download da mídia do áudio e permite enviar mensagens de texto (respostas/confirmações). A integração é **plug-and-play**, permitindo usar a **API Oficial da Meta** ou a **Evolution API** (configurável via `.env` na variável `WHATSAPP_PROVIDER`).
5. **Transcrição (Desacoplada)**: Lê o áudio e transforma em texto. O serviço é parametrizado via `.env` (ex. Whisper, Groq, Gemini) para permitir fácil troca.
6. **Agente Teq (Agno)**:
   - Recebe a transcrição ou texto (com possível GREETING_INJECTION prefixado).
   - Possui estado (histórico) salvo em SQLite (Agno SqliteDb), usando o número de WhatsApp como `session_id`.
   - É instanciado com ferramentas contextuais injetadas pelo orchestrator (notifier, search tools).
   - Pode conversar de forma descontraída, pesquisar na web, publicar posts no blog, gerenciar memórias, consultar tempo e agendar mensagens.
7. **Atualização de last_seen_at**: Após cada mensagem processada com sucesso, o orchestrator atualiza o `last_seen_at` do usuário no banco.
8. **Ferramenta de Publicação**: Após aprovação final, o Agente aciona a ferramenta que:
   - Gera o conteúdo e converte para base64.
   - Utiliza a **GitHub REST API** para criar ou atualizar o arquivo `YYYY-MM-DD-slug.mdx` diretamente no repositório remoto do blog (ex. `webdurand/diario-teq`), no diretório `content/posts/`.
   - Essa ação dispara automaticamente um deploy na Vercel.

## Fluxo de Pesquisa

Quando o agente identifica necessidade de pesquisa, ele tem três caminhos:

```
Mensagem do usuário
       ↓
 Agente Principal
       ↓
  ┌────┴────┐
  │Sem      │ web_search()      deep_research()
  │pesquisa │      ↓                  ↓
  └────┬────┘  notifica()        notifica()
       ↓       busca via        busca inicial
  Resposta     provider         analisa escopo
  direta       configurado          ↓
                    ↓         precisa aprofundar?
               retorna ao       ↓          ↓
                agente         não        sim
                            compila    notifica()
                                      Agno Team
                                      broadcast
                                    (N sub-agentes
                                     em paralelo)
                                          ↓
                                     sintetiza
                                          ↓
                                    add_memory()
                                          ↓
                                     retorna ao
                                       agente
```

## Módulos de Pesquisa (novos)

### Status Notifier (`src/integrations/status_notifier.py`)

- Classe `StatusNotifier` que envia mensagens determinísticas de feedback ao usuário via WhatsApp
- Usa `httpx` **síncrono** (as tools do Agno rodam dentro de `agent.run()`, contexto síncrono)
- Suporta Meta e Evolution API via `WHATSAPP_PROVIDER`
- Reutilizável por qualquer feature futura que precise de feedback intermediário

### Web Search Tools (`src/tools/web_search.py`)

- **`get_search_toolkit()`**: factory de provider de busca, controlada por `SEARCH_PROVIDER` no `.env`
- **`get_scraper_toolkit()`**: factory de provider de scraping, controlada por `SCRAPER_PROVIDER` no `.env`
- **`web_search_raw()`** / **`fetch_page_raw()`**: camada interna, sem notificação, usada por sub-agentes
- **`create_web_search_tool(notifier)`** / **`create_fetch_page_tool(notifier)`**: camada externa para o agente principal, notifica o usuário na primeira busca

### Multi-Agent Coordinator (`src/agent/multi_agent.py`)

- **`run_team(members, task, mode, ...)`**: wrapper genérico sobre `agno.team.Team`
- Suporta todos os modos do Agno: `coordinate`, `broadcast`, `route`, `tasks`
- Agnóstico ao caso de uso — reutilizável por qualquer feature multi-agent futura

### Deep Research (`src/tools/deep_research.py`)

- **`create_deep_research_tool(notifier, user_id)`**: factory que compõe os módulos acima
- Fluxo: notifica → busca inicial → agente decisor → (se necessário) Team broadcast → salva na memória
- Sub-agentes do Team recebem `get_search_toolkit()` + `get_scraper_toolkit()` para pesquisa paralela

## Lista de Tarefas (`src/tools/task_manager.py`)

Ferramenta para gerenciar tarefas pessoais do usuário via WhatsApp.

### Funções

| Função | Descrição |
|--------|-----------|
| `add_task(user_id, title, description, due_date, location, notes)` | Cria uma nova tarefa para o usuário |
| `list_tasks(user_id, status)` | Lista tarefas filtrando por `pending`, `done` ou `all` |
| `complete_task(user_id, task_id)` | Marca uma tarefa como concluída |
| `delete_task(user_id, task_id)` | Remove uma tarefa |

### Banco de Dados

Tabela `tasks` no mesmo banco já utilizado por `identity.py` (PostgreSQL via `DATABASE_URL` ou SQLite local):

```sql
CREATE TABLE tasks (
    id          INTEGER/SERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL,   -- número de telefone (WhatsApp)
    title       TEXT NOT NULL,
    description TEXT,
    due_date    TEXT,            -- texto livre ou ISO 8601
    location    TEXT,
    notes       TEXT,
    status      TEXT DEFAULT 'pending',  -- 'pending' | 'done'
    created_at  TEXT NOT NULL
)
```

### Fluxo de Interação

O agente faz perguntas contextuais antes de salvar a tarefa (prazo, local, observações), confirma o resumo com o usuário e só então chama `add_task`. O `user_id` sempre é o `session_id` (número de WhatsApp), garantindo isolamento entre usuários.

### Decisão Técnica

- Reutiliza o mesmo banco e padrão de conexão do `identity.py` — sem novo banco, sem nova dependência.
- Não precisa de `notifier` nem de factory, pois a interação é síncrona e conversacional (o agente faz as perguntas, não a tool).
- O `whatsapp.py` (orchestrator) não foi alterado: as tools são registradas diretamente no `get_assistant()`.

## Personalidade do Agente (Teq)

O agente tem tom descontraído e informal, como um amigo próximo e inteligente. As instruções de personalidade estão em `src/agent/assistant.py` no parâmetro `instructions[]` do `Agent`. Principais características:

- Linguagem informal brasileira, contrações naturais, emojis com moderação
- Conciso e direto ao ponto; sem introduções longas ou repetições
- Usa ferramentas de memória para personalizar as respostas ao longo do tempo

## Previsão do Tempo (`src/tools/weather.py`)

Ferramenta `get_weather(city)` que consulta `wttr.in` (gratuito, sem API key). Retorna temperatura atual, sensação térmica, umidade, vento e previsão dos próximos 2 dias em português. Usada tanto na saudação automática quanto em consultas diretas do usuário.

## Motor de Agendamento (`src/scheduler/`)

Permite que o Teq envie mensagens proativas sem precisar de input do usuário. Recentemente refatorado para utilizar PostgreSQL garantindo persistência forte em deploys efêmeros.

### Componentes

| Arquivo | Responsabilidade |
|---------|-----------------|
| `src/models/reminders.py` | CRUD da tabela `reminders` no PostgreSQL. Funciona como a **fonte de verdade** dos agendamentos, suportando canais dinâmicos. |
| `src/scheduler/engine.py` | Singleton do APScheduler com PostgreSQL job store (`DATABASE_URL`). Possui `reconcile_reminders()` no startup para recriar jobs órfãos a partir do banco. |
| `src/scheduler/dispatcher.py` | Função `dispatch_proactive_message(reminder_id)` executada pelo scheduler. Busca no banco, verifica status, cria um Agno Agent, roda as instruções e envia via canal (ex: `whatsapp_text`). |
| `src/tools/scheduler_tool.py` | Tools para o agente: `schedule_message` (grava no DB e adiciona job), `list_schedules` (lê do DB) e `cancel_schedule` (marca DB e remove job). |

### Tipos de Gatilho

| trigger_type | Parâmetro | Exemplo de uso |
|---|---|---|
| `date` | `run_date` (ISO 8601) | "daqui 5 minutos manda um oi" |
| `cron` | `cron_expression` (5 campos) | "todo dia às 8h me manda as tarefas" |
| `interval` | `interval_minutes` (int) | "a cada 30 minutos verifica algo" |

### Fluxo de Agendamento

```
Usuário: "todo dia às 8h me manda tarefas e tempo"
       ↓
Teq chama schedule_message(trigger_type="cron", ...)
       ↓
Grava na tabela `reminders` do PostgreSQL (status: active)
       ↓
Adiciona job no APScheduler (PostgreSQL jobstore) com `reminder_id`
       ↓
Todo dia às 8h (BRT): scheduler dispara → dispatcher.py
       ↓
Busca `reminder_id` no banco → Cria Agno Agent
       ↓
agent.run(task_instructions) → resposta
       ↓
Envia resposta via notification_channel (whatsapp_text)
```

### Decisão Técnica: PostgreSQL + Reconciliação

O SQLite local como job store do APScheduler sofria perda de dados durante redeploys em containers efêmeros (como na Koyeb). O sistema foi migrado para PostgreSQL.
A tabela `reminders` atua como fonte de verdade: no startup da aplicação (`start_scheduler()`), a função `reconcile_reminders()` garante que todo lembrete `active` possua um job no motor em background, recriando-o caso tenha sido perdido num restart. Além disso, o sistema resolve nativamente os fusos horários baseando-se na configuração de `timezone` (ex: `America/Sao_Paulo`) do usuário.

## Detecção de Nova Sessão com Escolha do Usuário

Adicionada coluna `last_seen_at` na tabela `users`. Funções em `src/memory/identity.py`:
- `update_last_seen(phone)`: atualiza o timestamp após cada mensagem processada
- `is_new_session(user, threshold_hours=4)`: retorna `True` se o usuário ficou mais de 4h sem contato

### Fluxo de nova sessão (texto)

```
Usuário envia mensagem após >4h
       ↓
Orchestrator (determinístico, sem LLM) pergunta:
"Ei, passou um tempinho... quer continuar ou começar conversa nova?"
       ↓
Mensagem original guardada em pending_session_choices[phone]
last_seen_at atualizado (evita re-disparar a pergunta)
       ↓
Usuário responde:
  "sim/bora/claro/..."  →  CONTINUATION_INJECTION: agente resume tópico anterior + responde mensagem original
  "não/nova/..."        →  GREETING_INJECTION: agente saúda com tempo/tarefas/preferências + responde mensagem original
  mensagem diferente    →  tratado como "não" (discard original, processa nova mensagem com GREETING_INJECTION)
```

### Fluxo de nova sessão (áudio)

Para áudio, pula a pergunta e aplica `GREETING_INJECTION` diretamente (transcrever para guardar ficaria complexo).

### Preferências de cumprimento

As preferências são controladas pelo usuário via conversa e armazenadas na memória vetorial:
- "todo dia adicione as notícias no meu cumprimento" → agente salva em `add_memory` → próximas saudações incluem notícias
- "tira as notícias do cumprimento" → agente usa `list_memories` + `delete_memory` → saudações voltam ao padrão
- O Agno (`search_knowledge=True`) encontra essas preferências automaticamente antes de cada saudação

## Dashboard Web e Real-time (`agenteteq-front`)

A aplicação React separada evoluiu de uma simples interface de voz para um **Dashboard Completo**, permitindo interação multimodal (voz e texto), gerenciamento de tarefas/lembretes via interface e visualização de ações do agente em tempo real.

### Repositório

`agenteteq-front/` (Vite + React + TypeScript + Tailwind CSS com design Glassmorphism)

### Fluxos e Comunicação

O Dashboard combina CRUD direto via REST e atualizações real-time via WebSocket:

1. **REST API (`/api/tasks`, `/api/reminders`)**: 
   - Ações manuais na interface (como "adicionar tarefa" ou "concluir lembrete") disparam chamadas HTTP diretamente para o banco, sem passar pelo Agente.
2. **WebSocket de Interação (`/ws/voice`)**:
   - Áudio (WebM/Opus) é enviado para transcrição e processamento.
   - Mensagens de texto (`mode="text"`) ignoram a geração de áudio (TTS) para um chat silencioso.
3. **Event Bus (`src/events.py`)**:
   - Quando o Agente modifica o estado (ex: agenda um aviso) ou o usuário altera via REST, um evento real-time (ex: `task_updated`, `reminder_updated`, `blog_preview`) é emitido.
   - O `ws_manager` propaga esse evento para o frontend conectado.
   - Os hooks do React (`useTasks`, `useReminders`) escutam os eventos e re-buscam os dados no backend para manter a UI sempre atualizada, criando uma sensação mágica de "co-piloto invisível" operando o sistema.

### Componentes Principais

| Arquivo | Responsabilidade |
|---|---|
| `components/Dashboard.tsx` | Layout principal com Orb, Sidebar (Tarefas/Lembretes) e ChatPanel. |
| `hooks/useWebSocket.ts` | Conexão compartilhada e barramento de eventos no frontend. |
| `hooks/useVoiceChat.ts` | Captura de microfone (VAD) + integração WebSocket + playback de áudio. |
| `components/TasksPanel.tsx` | Lista interativa de tarefas integrando REST + WS. |
| `components/BlogPreviewModal.tsx` | Recebe evento `blog_preview` e mostra o rascunho do post antes da publicação. |

### Identificação

O usuário informa seu número de telefone na primeira visita (salvo em `localStorage`). Esse número é o `session_id` do Agno, garantindo acesso à mesma memória e histórico do WhatsApp.

### Módulo TTS (`src/integrations/tts.py`)

Interface desacoplada `BaseTTS` com factory `get_tts()`:

| Provider | Variável | Custo | Observação |
|---|---|---|---|
| `gemini` (padrão) | `GOOGLE_API_KEY` | Grátis (tier atual) | `gemini-2.5-flash-tts` (pt-BR), voz `Puck` padrão |
| `openai` | `OPENAI_API_KEY` | ~$15/1M chars | `tts-1`, vozes configuráveis |
| `elevenlabs` | `ELEVENLABS_API_KEY` | Pago | `eleven_multilingual_v2` |
| `browser` | — | Grátis | Web Speech API (`SpeechSynthesisUtterance`) no cliente |

Configuração via `.env`:
```
TTS_PROVIDER=gemini   # gemini | openai | elevenlabs | browser
TTS_VOICE=Puck        # Gemini: Puck, Aoede, Fenrir | OpenAI: onyx, nova...
FRONTEND_ORIGIN=http://localhost:5173  # origin do React para CORS
```

### WebSocketNotifier

Equivalente ao `StatusNotifier` para a interface web. Como `agent.run()` executa em thread via `asyncio.to_thread()`, usa `asyncio.run_coroutine_threadsafe()` para enviar atualizações de status ao cliente em tempo real durante pesquisas.

### Componentes React

| Arquivo | Responsabilidade |
|---|---|
| `hooks/useVoiceChat.ts` | WebSocket + MediaRecorder + VAD + reprodução de áudio |
| `components/Orb.tsx` | Orb animado navy — idle/listening/thinking/speaking |
| `components/ChatHistory.tsx` | Painel lateral colapsável com histórico |
| `components/LoginModal.tsx` | Tela de identificação por telefone |
| `components/OnboardingModal.tsx` | Captura de nome no primeiro acesso |

### Variável de ambiente (frontend)

```
VITE_WS_URL=ws://localhost:8000   # em produção: wss://seu-dominio.com
```

## Decisões Técnicas

- **Python & FastAPI**: Fornecem agilidade e facilidade para hospedar webhooks.
- **Agno**: Framework para construção de agentes stateful.
- **Agno Team**: Usado para orquestração multi-agent nativa (em vez de implementar ThreadPoolExecutor custom). Suporta execução paralela no modo `broadcast`.
- **Identidade e Onboarding Determinístico**: Reduz custos de LLM e garante uma experiência controlada ao coletar os dados iniciais do usuário. A checagem de identidade usa PostgreSQL (NeonDB via `DATABASE_URL`) quando disponível, caindo para SQLite local apenas em ambiente sem banco externo. Isso garante que o usuário seja reconhecido mesmo após restarts do servidor.
- **Módulo de Memória**: Utiliza NeonDB com PgVector e a Knowledge Base do Agno para armazenar memórias do usuário em background e injetar contexto de forma "Agentic" ou "Always-on".
- **Desacoplamento**: LLM, transcrição, WhatsApp provider, search provider e scraper provider são todos configuráveis via `.env`. Trocar qualquer um exige apenas mudar a variável de ambiente.
- **Injeção de contexto por factory**: Tools que precisam de contexto do usuário (notifier, user_id) são criadas por factories no orchestrator e injetadas via `extra_tools`, mantendo o `get_assistant()` agnóstico ao contexto da requisição.
- **Armazenamento de Sessão**: Agno SqliteDb para manter histórico por telefone do usuário.
- **Integração via GitHub API**: A publicação do blog foi migrada de comandos git locais para a GitHub API (`httpx.put`), permitindo que a API e o blog sejam deployados em servidores diferentes e desacoplados (ex: backend na Koyeb, frontend na Vercel).

## Configuração de Providers

| Feature | Variável `.env` | Padrão | Opções |
|---------|----------------|--------|--------|
| LLM | `LLM_PROVIDER` | `openai` | `openai`, `anthropic`, `gemini` |
| WhatsApp | `WHATSAPP_PROVIDER` | `meta` | `meta`, `evolution` |
| Transcrição | `TRANSCRIBER_PROVIDER` | `openai` | `openai`, `mock` |
| Busca web | `SEARCH_PROVIDER` | `duckduckgo` | `duckduckgo`, `tavily`, `exa`, `serper`, `brave` |
| Scraping | `SCRAPER_PROVIDER` | `newspaper4k` | `newspaper4k`, `crawl4ai` |
| Memória | `MEMORY_MODE` | `agentic` | `agentic`, `always-on` |
| Agendamentos | `scheduler.db` | SQLite local | — (persistência automática) |
| TTS | `TTS_PROVIDER` | `gemini` | `gemini`, `openai`, `elevenlabs`, `browser` |

## Autenticação e Registro

O sistema de autenticação suporta login manual (email+senha) com 2FA via WhatsApp, login social (Google OAuth) e trial de 7 dias.

### Fluxos Principais

1. **Cadastro Manual**: Usuário preenche formulário no front → backend cria conta e gera código alfanumérico OTP de 6 dígitos → envia OTP via WhatsApp → usuário confirma o OTP → conta ativada, retorna JWT.
2. **Login Manual**: Usuário insere email e senha → backend valida bcrypt hash → gera OTP 2FA → envia via WhatsApp → usuário confirma o OTP → retorna JWT.
3. **Google OAuth**: Usuário loga com Google → backend valida `id_token` usando `google-auth` SDK → se conta não existe, solicita completude (username, telefone, etc) e segue fluxo OTP de cadastro → se existe, retorna JWT.

### Módulos Backend (`src/auth/`)

- `passwords.py`: Hashing e verificação com `bcrypt` puro.
- `jwt.py`: Criação e validação de tokens usando `PyJWT`.
- `otp.py`: Geração e verificação de códigos em memória RAM, puro, desacoplado do envio.
- `google.py`: Isola as chamadas ao SDK do Google para validação do token.
- `deps.py`: Dependências FastAPI para extrair JWT e validar plano ativo.
- `routes.py`: Endpoints REST, que orquestram a verificação de senha/token e o envio de mensagens chamando o `whatsapp_client`.

### Estrutura do JWT

- **Header**: Algoritmo HS256.
- **Payload**: `sub` (telefone para `session_id`), `username`, `email`, datas de emissão (`iat`) e expiração (`exp`).

### Trial e Planos Pagos

A arquitetura prepara o terreno para cobrança:
- Usuários recebem `plan_type = 'trial'` na criação da conta.
- Tabela `users` possui `trial_started_at` e `trial_ends_at` (padrão de 7 dias).
- Acesso à interface web de voz e aos webhooks é bloqueado se o trial expirar (validação via `is_plan_active()`).

*(Este arquivo deve ser atualizado sempre que novas ferramentas, rotas ou fluxos forem adicionados)*
