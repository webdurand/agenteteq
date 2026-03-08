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

## Suporte a Imagens Multimodal e Debounce

O Teq suporta entrada multimodal através do modelo Gemini (visão) tanto pelo canal Web quanto pelo WhatsApp.

### Fluxo de Debounce Universal (WhatsApp)
Como o webhook do WhatsApp (Evolution/Meta) emite um evento por mensagem, implementamos um **MessageBuffer Universal** para agrupar mensagens consecutivas do mesmo usuário:
- O buffer aguarda 3 segundos de silêncio antes de processar.
- **Concatena textos** (ex: "oi", "me ajuda", "com isso" -> 1 prompt).
- **Agrupa múltiplas imagens** e áudios enviados em sequência.
- O *typing indicator* é enviado imediatamente na primeira mensagem recebida.

### Armazenamento e Indexação de Imagens (`src/integrations/image_storage.py`)
1. **Upload**: Toda imagem enviada é convertida e enviada para o **Cloudinary** (na pasta `user_uploads/{user_id}`).
2. **Processamento**: As imagens (em bytes) são injetadas no Agente (`agent.run(images=[Image(content=bytes)])`).
3. **Indexação na Base de Conhecimento**: Em background, o próprio Gemini gera uma descrição curta para cada imagem, que é então salva na base de conhecimento `PgVector` junto com a URL permanente. Quando o agente busca em sua memória no futuro, ele pode resgatar a referência e URL originais.

### Edição e Transformação de Imagens (`src/tools/image_editor.py`)
O agente pode **editar ou transformar imagens** enviadas pelo usuário usando o modelo Gemini (`gemini-3-pro-image-preview`) que suporta image editing nativo.

**Fluxo:**
1. O usuário envia uma imagem + instrução textual (ex: "coloca um dragão nessa cena").
2. Os bytes da imagem são armazenados em `_session_images[session_id]` antes de `agent.run()`.
3. O agente detecta a intenção de edição e chama `edit_image_tool`.
4. A tool recupera os bytes da sessão e dispara `_process_edit_background` (assíncrono):
   - Envia evento WS `image_editing` → frontend exibe indicador de loading no chat.
   - Chama `provider.edit(prompt, reference_image)` no Gemini.
   - Faz upload do resultado no Cloudinary (`edited_images/{user_id}`).
   - Envia evento WS `image_edit_ready` com a URL → frontend substitui loading pela imagem.
   - Indexa a imagem editada na knowledge base para memória de longo prazo.
5. No WhatsApp, o resultado é enviado como mídia diretamente.

**Provider (`src/tools/image_generation/nano_banana.py`):**
- `generate(prompt, aspect_ratio)`: geração do zero (text-to-image).
- `edit(prompt, reference_image, aspect_ratio)`: edição de imagem existente (image+text-to-image). Usa `Part.from_bytes` para injetar a imagem original no `contents` da API.

## Módulos de Pesquisa (novos)

### Status Notifier (`src/integrations/status_notifier.py`)

- Classe `StatusNotifier` que envia mensagens determinísticas de feedback ao usuário via WhatsApp
- Usa `httpx` **síncrono** (as tools do Agno rodam dentro de `agent.run()`, contexto síncrono)
- Suporta Meta e Evolution API via `WHATSAPP_PROVIDER`
- Reutilizável por qualquer feature futura que precise de feedback intermediário

### Web Search Tools (`src/tools/web_search.py`)

- **`get_search_toolkit()`**: factory de provider de busca, controlada por `SEARCH_PROVIDER` no `.env`
- **`get_scraper_toolkit()`**: factory de provider de scraping, controlada por `SCRAPER_PROVIDER` no `.env` (padrão atual: `jina`, acessando `r.jina.ai` para retornar Markdown otimizado)
- **`web_search_raw()`** / **`fetch_page_raw()`**: camada interna, sem notificação, usada por sub-agentes
- **`create_web_search_tool(notifier)`** / **`create_fetch_page_tool(notifier)`**: camada externa para o agente principal, notifica o usuário na primeira busca. `fetch_page` agora lê qualquer site, não apenas artigos de notícias.
- **`create_explore_site_tool(notifier)`**: tool especializada em navegação que extrai links (seções) de um site usando Jina Reader para que o agente possa escolher páginas filhas para aprofundar a pesquisa.

### Multi-Agent Coordinator (`src/agent/multi_agent.py`)

- **`run_team(members, task, mode, ...)`**: wrapper genérico sobre `agno.team.Team`
- Suporta todos os modos do Agno: `coordinate`, `broadcast`, `route`, `tasks`
- Agnóstico ao caso de uso — reutilizável por qualquer feature multi-agent futura

### Deep Research (`src/tools/deep_research.py`)

- **`create_deep_research_tool(notifier, user_id)`**: factory que compõe os módulos acima
- Fluxo: notifica → busca inicial → agente decisor → (se necessário) Team broadcast → salva na memória
- Sub-agentes do Team recebem `get_search_toolkit()` + `get_scraper_toolkit()` para pesquisa paralela

### Google Search nativo (Gemini) e Deep Research da Google

- **Grounding com Google Search**: Com `LLM_PROVIDER=gemini`, é possível ativar busca web nativa do Gemini via `GEMINI_GOOGLE_SEARCH=true`. O modelo passa a poder consultar a web em tempo real e retornar respostas com citações (`groundingMetadata`). No Agno isso é feito com `Gemini(..., search=True)`. Funciona como capacidade do modelo, não como tool separada; pode coexistir com as tools `web_search` e `deep_research` (provider externo).
- **Deep Research oficial (Google)**: A Google oferece um agente "Deep Research" via **Interactions API** (`client.interactions.create(...)`), com planejamento iterativo e múltiplas buscas. O `deep_research` atual do projeto é uma implementação própria (Agno Team + sub-agentes + provider configurável). Para usar o Deep Research nativo da Google seria necessário integrar a Interactions API em um fluxo dedicado (ex.: nova tool ou endpoint que chame `genai.Client().interactions.create(...)`).

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

Modelo ORM `Task` em `src/db/models.py`, criado automaticamente via `Base.metadata.create_all()`.

### Fluxo de Interação

O agente faz perguntas contextuais antes de salvar a tarefa (prazo, local, observações), confirma o resumo com o usuário e só então chama `add_task`. O `user_id` sempre é o `session_id` (número de WhatsApp), garantindo isolamento entre usuários.

## Frontend: Tratamento de Erros e Feedback (Toasts)

Qualquer erro originado de chamadas à API no frontend (como retorno de status 4xx ou 5xx) **deve** ser tratado e exibido ao usuário utilizando o componente global de Toast (`ToastContext`). 

- **Nunca** utilizar o `alert()` nativo do navegador.
- O contexto de Toast provê a função `showToast(message: string, type: 'success' | 'error' | 'info')`.
- O cliente da API (`src/lib/api.ts`) extrai e lança a mensagem enviada pelo backend no campo `detail`, a qual deve ser passada diretamente para o `showToast(e.message, "error")`.

## Pós-processamento de Respostas (`src/agent/response_utils.py`)

Quando o Agno Agent faz múltiplas iterações de tool-calling (ex: tool falha, modelo corrige parâmetros e retenta), o `response.content` acumula todo texto intermediário gerado pelo LLM em cada iteração. Para evitar que o usuário receba narração de erros e retries, o sistema aplica duas camadas de tratamento:

- **`extract_final_response(response)`**: Percorre `response.messages` de trás pra frente e extrai apenas o conteúdo da última mensagem `assistant` (sem tool_calls), descartando texto intermediário.
- **`split_whatsapp_messages(text, max_length)`**: Divide respostas longas em blocos por parágrafo para envio em múltiplas mensagens no WhatsApp.

Aplicado em: `whatsapp.py`, `web.py` e `dispatcher.py`.

## Personalidade do Agente (Teq)

O agente se identifica como **Teq**, criado por **Pedro Durand**. Tem tom descontraído e informal, como um amigo próximo e inteligente. As instruções de identidade, personalidade e capacidades estão em `src/agent/assistant.py` no parâmetro `instructions[]` do `Agent`. O mesmo padrão de identidade é replicado em `src/endpoints/voice_live.py` para o canal de voz em tempo real. Principais características:

- Identidade clara: sabe que é o Teq, criado pelo Pedro Durand, e conhece todas as suas capacidades
- Linguagem informal brasileira, contrações naturais, emojis com moderação (WhatsApp) ou sem emojis/markdown (voz)
- Conciso e direto ao ponto; sem introduções longas ou repetições
- Usa ferramentas de memória para personalizar as respostas ao longo do tempo
- Capacidades listadas no prompt: memória, tarefas, lembretes/agendamentos, pesquisa web, previsão do tempo, blog, carrosséis de imagens, edição de imagens, voz e WhatsApp

## Previsão do Tempo (`src/tools/weather.py`)

Ferramenta `get_weather(city)` que consulta `wttr.in` (gratuito, sem API key). Retorna temperatura atual, sensação térmica, umidade, vento e previsão dos próximos 2 dias em português. Usada tanto na saudação automática quanto em consultas diretas do usuário.

## Motor de Agendamento (`src/scheduler/`)

Permite que o Teq envie mensagens proativas sem precisar de input do usuário. Recentemente refatorado para utilizar PostgreSQL garantindo persistência forte em deploys efêmeros.

### Componentes

| Arquivo | Responsabilidade |
|---------|-----------------|
| `src/models/reminders.py` | CRUD da tabela `reminders` no PostgreSQL. Funciona como a **fonte de verdade** dos agendamentos, suportando canais dinâmicos. |
| `src/scheduler/engine.py` | Singleton do APScheduler com PostgreSQL job store (`DATABASE_URL`). Possui `reconcile_reminders()` no startup para recriar jobs órfãos a partir do banco. |
| `src/scheduler/dispatcher.py` | Função `dispatch_proactive_message(reminder_id)` executada pelo scheduler. Busca no banco, verifica status, cria um Agno Agent com prefixo proativo (evita que o agente confunda a execução com uma conversa normal e peça mais informações), roda as instruções e envia via canal (`whatsapp_text`, `web_text`, `web_voice` ou `web_whatsapp`). |
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
agent.run(prefixo_proativo + task_instructions) → resposta
       ↓
Envia resposta via notification_channel (ex: `whatsapp_text`, `web_text`, `web_voice`, `web_whatsapp`)
```

### Decisão Técnica: PostgreSQL + Reconciliação

O SQLite local como job store do APScheduler sofria perda de dados durante redeploys em containers efêmeros (como na Koyeb). O sistema foi migrado para PostgreSQL.
A tabela `reminders` atua como fonte de verdade: no startup da aplicação (`start_scheduler()`), a função `reconcile_reminders()` garante que todo lembrete `active` possua um job no motor em background, recriando-o caso tenha sido perdido num restart. Além disso, o sistema resolve nativamente os fusos horários baseando-se na configuração de `timezone` (ex: `America/Sao_Paulo`) do usuário.

### Atalho Determinístico para "me avisa daqui X min"

Para reduzir falhas de execução de tool em frases curtas de lembrete, existe um atalho antes do `agent.run()`:

- Arquivo: `src/tools/reminder_shortcuts.py`
- Entradas alvo: mensagens com intenção explícita de lembrete + tempo relativo em minutos (ex: "me avisa daqui 5 min")
- Comportamento: se o usuário **não informar canal**, o sistema pergunta antes de agendar ("web, WhatsApp ou ambos"), guarda a intenção original e só agenda após a resposta do canal. Se o canal já vier explícito na frase, agenda diretamente.
- Agendamento: usa `schedule_message(...)` com `trigger_type="date"` e `minutes_from_now`, persiste em `reminders` e emite `reminder_updated`. O `task_instructions` inclui a mensagem original do usuário na íntegra, delegando a interpretação ao LLM no momento do disparo.
- Canais suportados no reminder:
  - `whatsapp_text` (mensagem no WhatsApp)
  - `web_text` (mensagem em texto no app web; fallback para WhatsApp se offline)
  - `web_voice` (fala no app web; fallback para WhatsApp se offline)
  - `web_whatsapp` (envio nos dois canais)

Esse fallback mantém DRY porque reaproveita o mesmo motor (`scheduler_tool`) e atua apenas como roteamento determinístico de intenção, sem duplicar lógica de agendamento.

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

## Gerador de Carrossel para Instagram

Permite ao agente gerar múltiplos slides em paralelo para o Instagram, utilizando o modelo Gemini (Nano Banana Pro) e hospedando no Cloudinary.

### Fluxo de Geração
1. O usuário solicita a geração de um carrossel fornecendo assunto, slides e (opcionalmente) uma imagem de referência.
2. O agente aciona a tool `generate_carousel_tool` (em `src/tools/carousel_generator.py`).
   - Se o usuário enviou uma imagem junto com o pedido, o agente passa `use_reference_image=True`.
3. A tool grava um registro na tabela `carousels` do PostgreSQL (estado `generating`) e inicia uma rotina em background (`asyncio.create_task`), liberando o agente para responder imediatamente que o processamento foi iniciado.
4. Em background, as imagens são geradas em paralelo usando a interface `ImageProvider` (`src/tools/image_generation/base.py`).
   - **Com referência**: cada slide usa `provider.edit(prompt, reference_bytes)` — o Gemini recebe a foto como contexto visual e gera o slide baseado nela.
   - **Sem referência**: cada slide usa `provider.generate(prompt)` — geração puramente textual.
5. O provider padrão é o `NanoBananaProvider`, que encapsula o uso da API do Gemini (`gemini-3-pro-image-preview`).
6. Cada imagem gerada (em bytes) é enviada via upload para o **Cloudinary** (usado pelo seu plano gratuito generoso com CDN global).
7. O banco de dados é atualizado com o status `done` e as URLs finais das imagens geradas.
8. Um evento WebSocket (`carousel_generated`) é disparado sincronicamente (`emit_event_sync`) para atualizar o frontend.
9. No frontend, o componente `ImagesPanel` ouve o evento, re-busca a lista de carrosséis e exibe o resultado para o usuário em tempo real.

### Desacoplamento de Provedores
A geração de imagens é isolada na interface abstrata `ImageProvider`. Para substituir o Gemini por Dall-E, Midjourney, Fal.ai ou Replicate, basta criar uma nova classe herdando de `ImageProvider` e configurar a variável de ambiente `IMAGE_PROVIDER` no `src/tools/image_generation/__init__.py`.

## Separação Voz/Chat e Notificações Cross-Channel

O frontend foi unificado para **Voice Live-only**:
- A aba **Voice** usa `useVoiceLive` + `/ws/voice-live` (Gemini Live API) para áudio bidirecional em baixa latência.
- A aba **Chat** usa `useChat` + `/ws/voice` apenas para mensagens de texto e histórico.
- Não existe mais modo clássico de voz no frontend (SpeechRecognition/MediaRecorder/TTS browser).

As respostas gerais de voz continuam efêmeras. Exceção: no `voice-live`, retornos de tools críticas de imagem (geração/edição e mensagens de limite) também são persistidos em `chat_messages` para manter consistência após refresh.
No canal de chat (`/ws/voice`), quando o usuário está sem limite de geração e o pedido tem intenção de imagem, o backend também emite fallback `limit_reached` mesmo se o LLM responder só pelo contexto, garantindo renderização do card Premium no frontend.

### Action Log (Notificações de Ações)

Quando uma tool executa uma ação importante (edição de imagem, publicação de post, criação de tarefa, geração de carrossel, criação de lembrete), o sistema emite uma notificação que:
1. **Persiste** em `chat_messages` com `role="system"` (sobrevive reload).
2. **Faz broadcast** via WebSocket (`action_log` event) para o frontend em tempo real.

Isso funciona independente do canal de origem (voz, texto, WhatsApp, voice-live). O frontend renderiza notificações `system` como mensagens centralizadas e discretas no chat.

**Módulos envolvidos:**
- `src/events_broadcast.py`: `emit_action_log()` (async) e `emit_action_log_sync()` (para threads).
- Tools: `image_editor`, `blog_publisher`, `carousel_generator`, `task_manager`, `scheduler_tool` — todas chamam `emit_action_log` após ações bem-sucedidas, passando o `channel` de origem.
- Frontend: `useChat.ts` escuta `action_log` e insere como `role: "system"` na lista de mensagens.

### Compatibilidade Mobile (Voz)

Com Live-only, a captura usa `AudioWorklet` (com fallback `ScriptProcessorNode`) e stream PCM 16kHz para o backend. O controle de atividade de fala é prioritariamente do Gemini Live (`automaticActivityDetection`), reduzindo lógica client-side.

## Dashboard Web e Real-time (`agenteteq-front`)

A aplicação React separada evoluiu de uma simples interface de voz para um **Dashboard Completo**, permitindo interação multimodal (voz e texto), gerenciamento de tarefas/lembretes via interface e visualização de ações do agente em tempo real.

### Repositório

`agenteteq-front/` (Vite + React + TypeScript + Tailwind CSS com design Glassmorphism)

### Fluxos e Comunicação

O Dashboard combina CRUD direto via REST e atualizações real-time via WebSocket:

1. **REST API (`/api/tasks`, `/api/reminders`)**: 
   - Ações manuais na interface (como "adicionar tarefa" ou "concluir lembrete") disparam chamadas HTTP diretamente para o banco, sem passar pelo Agente.
2. **WebSocket de Interação (`/ws/voice`)**:
   - Usado como canal de chat texto no frontend (`useChat`).
   - Mensagens são enviadas com `mode="text"` para resposta silenciosa no painel.
3. **WebSocket de Voz Live (`/ws/voice-live`)**:
   - Recebe stream PCM 16kHz do frontend e responde com áudio PCM 24kHz em tempo real.
   - Encaminha tool-calls nativas do Gemini Live para `voice_tools.py`.
   - Emite eventos de ciclo de tool (`tool_call_start` com `label` amigavel e `tool_call_end`) para o Orb e status visual.
4. **Event Bus (`src/events.py`)**:
   - Quando o Agente modifica o estado (ex: agenda um aviso) ou o usuário altera via REST, um evento real-time (ex: `task_updated`, `reminder_updated`, `blog_preview`) é emitido.
   - O `ws_manager` propaga esse evento para múltiplas conexões por usuário (chat + voice-live), permitindo cross-tab real.
   - Os hooks do React (`useTasks`, `useReminders`) escutam os eventos e re-buscam os dados no backend para manter a UI sempre atualizada, criando uma sensação mágica de "co-piloto invisível" operando o sistema.

### Componentes Principais

| Arquivo | Responsabilidade |
|---|---|
| `components/Dashboard.tsx` | Layout principal com Orb, Sidebar (Tarefas/Lembretes) e ChatPanel. |
| `hooks/useWebSocket.ts` | Conexão compartilhada e barramento de eventos no frontend. |
| `hooks/useVoiceLive.ts` | Captura de microfone (AudioWorklet) + stream de áudio real-time no `/ws/voice-live` + playback. |
| `hooks/useChat.ts` | Canal de chat texto (`/ws/voice`) + histórico + eventos de UI (carrossel/edição/action log). |
| `components/TasksPanel.tsx` | Lista interativa de tarefas integrando REST + WS. |
| `components/BlogPreviewModal.tsx` | Recebe evento `blog_preview` e mostra o rascunho do post antes da publicação. |

### Identificação

O usuário informa seu número de telefone na primeira visita (salvo em `localStorage`). Esse número é o `session_id` do Agno, garantindo acesso à mesma memória e histórico do WhatsApp.

### Fluxo de Voz Real-time (Gemini Live API)

A aplicação opera voz em modo Live por padrão no frontend:
- O frontend (`useVoiceLive.ts`) envia PCM 16kHz via WebSocket para `/ws/voice-live`.
- O backend (`src/endpoints/voice_live.py`) atua como proxy bidirecional entre o frontend e a **Gemini Live API** via WSS.
- O modelo processa áudio nativamente e responde em áudio (eliminando a cadeia STT -> LLM -> TTS).
- A latência cai de ~3-10s para <1s.
- Suporta "barge-in" (interrupção pelo usuário) e chamadas de ferramentas (*tool calling* nativo do Gemini).
- Para as tools, usamos `src/agent/voice_tools.py` que espelha as functions existentes no Agno para o formato que a Live API do Google exige.
- O setup do Gemini Live usa `automaticActivityDetection` (VAD nativo) e a sessão web é encerrada automaticamente por inatividade.
- Fluxo de ativação UX: entrar na aba Voice conecta; tocar no Orb faz mute/unmute; sair da aba desconecta a sessão.
- Estados visuais principais do Live no frontend: `connecting`, `listening`, `speaking`, `processing`, `muted`, `idle`.
- Quando uma tool inicia, o backend envia `tool_call_start` (com texto amigavel) e o frontend entra em `processing`; no fim, recebe `tool_call_end` e volta para `listening`/`muted`.

### Módulo TTS Clássico (`src/integrations/tts.py`)

Interface desacoplada `BaseTTS` com factory `get_tts()`:

| Provider | Variável | Custo | Observação |
|---|---|---|---|
| `gemini` (padrão) | `GOOGLE_API_KEY` | Grátis (tier atual) | `gemini-2.5-flash-tts` (pt-BR), voz `Aoede` padrão |
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
| `hooks/useVoiceLive.ts` | Conexão de voz em tempo real com Gemini Live, estados (`connecting/listening/speaking/processing/muted/idle`), controle de mute/unmute e timeout de inatividade |
| `hooks/useChat.ts` | Mensagens de texto, histórico e atualização por eventos |
| `components/Orb.tsx` | Orb em estilo Siri-glow com diferenciação visual de entrada (user), saída (AI) e processamento de tools |
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
- **Camada de Dados ORM (`src/db/`)**: Toda a persistência é centralizada em SQLAlchemy ORM. `db/session.py` provê um engine único (PG via `DATABASE_URL` ou SQLite `app.db` em dev) e `db/models.py` contém todos os modelos declarativos. Nenhum módulo precisa saber qual backend está em uso — o ORM abstrai isso. A criação de tabelas é feita no startup via `db/init.py` → `Base.metadata.create_all()`.
- **Migrações (Alembic)**: Infraestrutura de migrações configurada em `alembic/`. Migrations existentes: baseline + `otp_codes`. O `ensure_tables()` atual continua funcionando para criação de tabelas; Alembic é usado para alterações de schema que `CREATE TABLE IF NOT EXISTS` não suporta (renomear colunas, alterar tipos, etc.).
- **Testes Automatizados**: Suite `pytest` em `tests/` com 25 testes cobrindo OTP, autenticação, identidade e criptografia de tokens. Usa SQLite in-memory para isolamento.
- **Identidade e Onboarding Determinístico**: Reduz custos de LLM e garante uma experiência controlada ao coletar os dados iniciais do usuário.
- **Módulo de Memória**: Utiliza NeonDB com PgVector e a Knowledge Base do Agno para armazenar memórias do usuário em background e injetar contexto de forma "Agentic" ou "Always-on".
- **Desacoplamento**: LLM, transcrição, WhatsApp provider, search provider e scraper provider são todos configuráveis via `.env`. Trocar qualquer um exige apenas mudar a variável de ambiente.
- **Agent Factory (`src/agent/factory.py`)**: Centraliza a criação de agentes com search tools. `create_agent_with_tools(session_id, notifier, ...)` é o ponto único de instanciação, evitando duplicação entre `whatsapp.py` e `web.py`.
- **Prompts Compartilhados (`src/agent/prompts.py`)**: Constantes de injeção de contexto (GREETING, CONTINUATION) ficam em módulo único, importadas por ambos os canais.
- **Armazenamento de Sessão**: Agno SqliteDb para manter histórico por telefone do usuário.
- **Integração via GitHub API**: A publicação do blog foi migrada de comandos git locais para a GitHub API (`httpx.put`), permitindo que a API e o blog sejam deployados em servidores diferentes e desacoplados (ex: backend na Koyeb, frontend na Vercel).

## Segurança

- **JWT**: Secret obrigatório via `JWT_SECRET` env var; o app emite warning e usa default inseguro apenas em dev.
- **Rate Limiting**: `slowapi` protege endpoints de auth (`/auth/register`, `/auth/login`, `/auth/verify-whatsapp`, `/auth/google`) contra brute force.
- **Webhook Signature**: O endpoint POST `/webhook/whatsapp` valida `X-Hub-Signature-256` usando HMAC-SHA256 com `WHATSAPP_APP_SECRET` (skip em dev se não configurado).
- **Error Handling**: Endpoints nunca expõem `str(e)` ao cliente; erros são logados server-side e retornam mensagem genérica.
- **Upload Validation**: Imagens são validadas por tamanho (10MB) e magic bytes antes do upload ao Cloudinary.
- **SSRF Protection**: O worker de download valida URLs contra IPs privados e mantém allowlist de hosts confiáveis.
- **Logging Estruturado**: `logging.basicConfig` configurado no startup com formato timestamped; **todos** os módulos usam `logger` (zero `print()` no código de produção).
- **Sentry**: Integração opcional via `SENTRY_DSN`. Quando configurado, captura exceções e traces automaticamente. Sem a env var, é no-op.
- **Tokens OAuth Encriptados**: Tokens de integrações (Google, etc.) são encriptados em repouso com Fernet (`TOKEN_ENCRYPTION_KEY`). Sem a chave, tokens ficam em plaintext (retrocompatível).
- **password_hash Isolado**: O campo `password_hash` foi removido de `User.to_dict()` — nunca é serializado em respostas. Funções dedicadas (`get_password_hash`, `get_password_hash_by_email`) buscam o hash diretamente do ORM quando necessário para verificação.

## Configuração de Providers

| Feature | Variável `.env` | Padrão | Opções |
|---------|----------------|--------|--------|
| LLM | `LLM_PROVIDER` | `openai` | `openai`, `anthropic`, `gemini` |
| WhatsApp | `WHATSAPP_PROVIDER` | `meta` | `meta`, `evolution` |
| Transcrição | `TRANSCRIBER_PROVIDER` | `openai` | `openai`, `mock` |
| Busca web | `SEARCH_PROVIDER` | `duckduckgo` | `duckduckgo`, `tavily`, `exa`, `serper`, `brave` |
| Google Search (Gemini) | `GEMINI_GOOGLE_SEARCH` | — | `true` habilita grounding nativo com citações (requer `LLM_PROVIDER=gemini`) |
| Scraping | `SCRAPER_PROVIDER` | `jina` | `jina`, `newspaper4k`, `crawl4ai` |
| Memória | `MEMORY_MODE` | `agentic` | `agentic`, `always-on` |
| Agendamentos | `scheduler.db` | SQLite local | — (persistência automática) |
| Sentry | `SENTRY_DSN` | — (desativado) | DSN do projeto Sentry |
| Token Encryption | `TOKEN_ENCRYPTION_KEY` | — (plaintext) | Chave para encriptar tokens OAuth |
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
- `otp.py`: Geração e verificação de códigos OTP persistidos em PostgreSQL (tabela `otp_codes`), com expiração, limite de 3 tentativas e cleanup periódico via scheduler.
- `crypto.py`: Encriptação/decriptação de tokens OAuth em repouso usando Fernet (AES). Retrocompatível com tokens plaintext legados.
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

## Painel Admin e Observabilidade

O sistema conta com um Painel Administrativo integrado ao frontend principal (`agenteteq-front`) e protegido por RBAC (Role-Based Access Control) no backend, visando monitorar métricas de negócio e a saúde do sistema.

### RBAC e Controle de Acesso
- O modelo de usuário (`users`) possui um campo `role` (ex: `user` ou `admin`).
- O token JWT e o endpoint `/auth/me` incluem a informação de role.
- Endpoints administrativos são protegidos pela dependência FastAPI `require_admin` em `src/auth/deps.py`.
- O primeiro usuário administrador é configurado manualmente ou "promovido" diretamente no banco, não existindo (por segurança) um endpoint público para virar admin de forma irrestrita.

### Auditoria e Uso (Analytics)
- Uma tabela `usage_events` registra eventos operacionais importantes (ex: `message_received`, `message_sent`, `tool_called`, `tool_failed`).
- Estes eventos permitem rastrear latência, volume de chamadas por canal e quais tools são mais utilizadas ou geram mais erros.
- Os pontos de entrada (`whatsapp.py`, `web.py` e o orquestrador do `assistant.py`) são instrumentados para gravar de forma assíncrona estes eventos.

### Endpoints Admin (`src/endpoints/admin.py`)
- `/admin/business/analytics`: Endpoint centralizado que retorna métricas agregadas de negócio divididas em financeiro, engajamento, features e operacional.
- `/admin/business/users`: Lista de usuários com informações de assinatura.
- `/admin/health/summary`: Checks de saúde do banco, scheduler e integrações.
- `/admin/admins`: Gestão básica de administradores.
- `/admin/system/*`: Gestão da fila de tarefas, configuração (`system_config`) e listagem de métricas de infraestrutura.
- `/admin/campaigns*`: CRUD de campanhas in-app para popup estratégico (imagem, título, mensagem, CTA, audiência e frequência).

### Dashboard Frontend (Admin)
- **Negócio**: Exibe cards financeiros (MRR, assinantes), gráficos de engajamento (DAU, mensagens por dia), rankings de features (top tools, tendências) e visão operacional (taxa de erro, latência). Gráficos renderizados com **Recharts**.
- **Sistema / Fila**: Monitoramento da fila em tempo real, edição de limites globais e por plano.
- **Saúde**: Foca na disponibilidade dos serviços (DB, PgVector, WhatsApp, TTS).
- **Admins & Usuários**: Interface de CRUD para definir papéis na plataforma e monitorar status de assinatura.
- **Planos & Assinaturas**: Gestão de pacotes do Stripe e atribuição manual de acessos.
- **Campanhas**: Gestão de popups in-app para contratação/novidades sem deploy.

## Experiência Web de Conversão (Onboarding + Limites + Popup)

O frontend web (`agenteteq-front`) passou a operar com três componentes independentes para conversão e clareza de uso:

1. **Onboarding full-screen** (`ProductOnboardingModal`)
   - Exibido após autenticação e após o onboarding de identidade (nome), com 4 etapas:
     - boas-vindas,
     - capacidades do Teq (imagens, carrosséis, backlog Diário Teq),
     - agendamento e recorrência,
     - limites + CTA de assinatura.
   - Possui controle “não exibir novamente”, persistido no `localStorage` por usuário.
   - Pode ser reaberto manualmente em Configurações de conta.

2. **Exibidor de limites de uso** (`PremiumLimitsCard`)
   - Componente fixo no dashboard (não modal), com:
     - plano atual (`free`/`premium`),
     - runs restantes (`runs_remaining` de `runs_limit`),
     - barra de consumo e data estimada de reset.
   - Para `free tier`, exibe CTA contextual: “Ganhar mais limites”.

3. **Popup estratégico configurável** (`CampaignPopupModal`)
   - Exibido por elegibilidade e prioridade, sem sobrepor onboarding da primeira sessão.
   - Campanhas são configuradas no admin com:
     - `title`, `message`, `image_url`,
     - `cta_label`, `cta_action`, `cta_url`,
     - `audience` (`all`, `free_only`, `paid_only`),
     - `frequency` (`once`, `per_session`, `daily`),
     - `priority`, `active`.

### Endpoints de suporte (Web)
- `GET /api/usage/limits`: retorna `plan_name`, `runs_limit`, `runs_used`, `runs_remaining`, `resets_at`.
- `GET /api/campaigns/active`: retorna a campanha ativa elegível para o usuário atual.

### Persistência
- Tabela `in_app_campaigns` (ORM `InAppCampaign`) para campanhas de popup.
- Preferências de exibição de onboarding e frequência de popup são armazenadas no frontend (storage do navegador), por usuário.

*(Este arquivo deve ser atualizado sempre que novas ferramentas, rotas ou fluxos forem adicionados)*

## Billing e Assinaturas (Stripe)

O projeto utiliza **Stripe Billing** como fonte de verdade para assinaturas recorrentes, com o backend atuando como espelho operacional para autorização e UI.

### Decisão de Produto (Brasil)
- **Métodos**: Cartão de crédito, Apple Pay e Google Pay (via Stripe Elements/Payment Element).
- **Não suportados no MVP**: PIX (não suporta recorrência no modo assinatura na Stripe BR), PayPal (indisponível para conta BR), Débito automático (não existe para o Brasil).
- **Trial**: 7 dias gratuitos para todos.

### Fluxo de Checkout (Embedded)
1. Frontend chama `POST /billing/subscribe` que cria um `Customer` e uma `Subscription` (`payment_behavior='default_incomplete'`, `trial_period_days=7`) e retorna um `client_secret`.
2. Frontend exibe o `Payment Element` embedded.
3. Usuário insere dados e chama `stripe.confirmSetup()`.
4. Webhooks sincronizam o status e liberam acesso.

### Arquitetura de Billing Desacoplada
- `src/endpoints/billing.py`: Rotas REST de checkout e webhook, não contém lógica Stripe, apenas orquestra chamadas.
- `src/billing/service.py`: Regras de negócio, acesso, cancelamento e refund.
- `src/billing/types.py`: Contratos internos de status (`trialing`, `active`, `past_due`, etc).
- `src/integrations/stripe.py`: Wrapper da SDK da Stripe.
- `src/models/subscriptions.py`: Persistência local (tabelas `billing_plans`, `subscriptions`, `billing_events` para idempotência de webhooks, `refund_logs`).

### Política de Acesso e Gate
A função `is_plan_active` (em `src/auth/deps.py`) foi expandida para validar a assinatura real do usuário:
- **Acesso liberado**: `trialing`, `active` ou `past_due` (com grace period de 5 dias). Admins têm bypass automático.
- **Acesso bloqueado**: `canceled` com período encerrado, `unpaid`, `incomplete_expired` ou sem assinatura ativa após trial.

## Fila Persistente e Concorrência (Multi-pod)

O projeto suporta escalabilidade horizontal (autoscaling) e delega tarefas demoradas (ex: geração e edição de imagens) para uma fila persistente, garantindo resiliência e controle de recursos.

### Infraestrutura Compartilhada
- **system_config**: Tabela no banco de dados (`src/config/system_config.py`) para definir limites dinamicamente (ex: `max_concurrent_images`, `max_tasks_per_user_daily`). Suporta sufixos por plano (ex: `:trial`, `:paid`). Modificada pelo painel Admin.
- **processed_messages**: Tabela no banco para deduplicação de mensagens recebidas de webhooks, substituindo o cache em memória para que todos os pods compartilhem o mesmo estado. A chave de idempotência é estável por `provider + message_id` (com fallback por hash quando `message_id` não existe), evitando respostas duplicadas quando o provedor reentrega o mesmo evento com pequenas variações de payload.
- **Agno PostgresStorage**: O histórico de sessões do agente foi migrado de `SqliteDb` para `PostgresStorage`, permitindo continuidade do contexto entre diferentes pods.

### Fila de Tasks (`background_tasks`)
Implementada via PostgreSQL usando a diretiva `FOR UPDATE SKIP LOCKED`.
- **Worker Integrado**: Roda como um job no `APScheduler` a cada 5 segundos. Limita a quantidade de processos concorrentes e usa `asyncio.Semaphore` nas ferramentas.
- **Recovery**: Tasks interrompidas por crash (ex: OOM) retornam ao estado `pending` durante o startup (`lifespan`) para reprocessamento.
- **Feedback ao Usuário**: O agente informa instantaneamente ao usuário a estimativa de tempo e a posição na fila antes de enfileirar.

### Soluções Multi-pod
- **Message Buffer (WhatsApp)**: Movido para o PostgreSQL. Mensagens próximas do mesmo usuário são enfileiradas na tabela e um job de "flush" consolida tudo, prevenindo race conditions entre pods.
- **WebSocket Broadcast**: Como as tasks ocorrem em background e os sockets estão atrelados ao pod de conexão, utiliza o mecanismo nativo `PG LISTEN/NOTIFY` para propagar eventos de conclusão entre pods.
- **Scheduler Dedup**: Implementa `pg_try_advisory_lock` durante os disparos de lembretes para que múltiplos pods não acionem as mesmas mensagens simultaneamente.

### Admin Dashboard (Sistema e Fila)
Uma interface exclusiva no painel administrativo permite visualizar o status da fila em tempo real, editar limites (`system_config`), gerenciar falhas, e extrair métricas detalhadas filtradas por período (total consumido, uso por plano, top usuários).

## Termos de Serviço e Política de Privacidade

O sistema possui controle de consentimento versionado, exigido pelo Google OAuth e pela LGPD.

### Páginas Legais

As páginas ficam no `agenteteq-front` (Vite + React), acessíveis sem autenticação:
- `/privacy` e `/terms` → `agenteteq-front/src/components/LegalPage.tsx`
- Roteamento por `window.location.pathname` no `App.tsx`, antes dos fluxos de auth.

### Versionamento de Termos

- A versão atual dos termos é definida em `src/auth/terms.py` → `CURRENT_TERMS_VERSION`.
- O modelo `User` possui `terms_accepted_version` e `terms_accepted_at`.
- O endpoint `POST /auth/accept-terms` grava a versão e timestamp no banco.
- O endpoint `GET /auth/me` retorna `terms_accepted_version` para o frontend.
- O frontend (`App.tsx`) compara com `CURRENT_TERMS_VERSION` e exibe um modal bloqueante (`TermsConsentModal`) se a versão for diferente ou nula.
- No registro (`RegisterForm.tsx`), um checkbox obrigatório exige que o usuário aceite antes de criar a conta.

### Como atualizar os termos

1. Edite as páginas em `diarioteq/app/privacy/page.tsx` e/ou `diarioteq/app/terms/page.tsx`.
2. Atualize a data de "Última atualização" nas páginas.
3. Incremente `CURRENT_TERMS_VERSION` em `src/auth/terms.py` (ex: `"1.0"` → `"1.1"`).
4. Atualize `CURRENT_TERMS_VERSION` em `agenteteq-front/src/App.tsx` para o mesmo valor.
5. No próximo login, todos os usuários verão o modal pedindo para aceitar os novos termos.

**IMPORTANTE**: Qualquer mudança no sistema que afete coleta de dados, integrações com terceiros, ou processamento de informações pessoais **deve** ser refletida nas páginas legais e gerar incremento de versão.
