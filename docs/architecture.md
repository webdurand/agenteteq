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

Permite que o Teq envie mensagens proativas sem precisar de input do usuário.

### Componentes

| Arquivo | Responsabilidade |
|---------|-----------------|
| `src/scheduler/engine.py` | Singleton do APScheduler com SQLite job store (`scheduler.db`). Iniciado/parado via lifespan do FastAPI. |
| `src/scheduler/dispatcher.py` | Função `dispatch_proactive_message(user_phone, task_instructions)` executada pelo scheduler. Cria um Agno Agent, roda as instruções e envia o resultado via WhatsApp. |
| `src/tools/scheduler_tool.py` | Tools para o agente: `schedule_message`, `list_schedules`, `cancel_schedule`. |

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
Teq chama schedule_message(trigger_type="cron", cron_expression="0 8 * * *", ...)
       ↓
APScheduler persiste job no scheduler.db
       ↓
Todo dia às 8h: scheduler dispara → dispatcher.py
       ↓
Cria Agno Agent com session_id=user_phone
       ↓
agent.run(task_instructions) → resposta
       ↓
whatsapp_client.send_text_message(user_phone, resposta)
```

### Decisão Técnica: APScheduler + Agno

O Agno não oferece scheduling nativo baseado em relógio (cron/interval/date). O APScheduler complementa o Agno: cuida do gatilho de tempo, enquanto o Agno cuida da execução inteligente da tarefa. O job store SQLite garante que os agendamentos sobrevivam a restarts do servidor.

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

*(Este arquivo deve ser atualizado sempre que novas ferramentas, rotas ou fluxos forem adicionados)*
