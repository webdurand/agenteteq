# Arquitetura do Agente WhatsApp - Diario Teq

## Visão Geral

O `agenteteq` é uma API em Python baseada no FastAPI responsável por receber webhooks do WhatsApp, processar áudios utilizando serviços de transcrição, e alimentar um Agente Agno.
O Agente Agno possui ferramentas para conversar, publicar posts no blog, gerenciar memória, pesquisar na web e realizar pesquisas profundas com múltiplos sub-agentes.

## Fluxo Principal

1. **Webhook (FastAPI)**: Recebe payload da Meta (WhatsApp Cloud API) com um áudio ou texto.
2. **Módulo de Identidade (Determinístico)**: Antes de acionar a IA, o sistema verifica o número de telefone no banco de dados. Se for um usuário novo, realiza um onboarding determinístico pedindo o nome. Se já for cadastrado, injeta as preferências no contexto do agente.
3. **Integração WhatsApp**: Faz o download da mídia do áudio e permite enviar mensagens de texto (respostas/confirmações). A integração é **plug-and-play**, permitindo usar a **API Oficial da Meta** ou a **Evolution API** (configurável via `.env` na variável `WHATSAPP_PROVIDER`).
4. **Transcrição (Desacoplada)**: Lê o áudio e transforma em texto. O serviço é parametrizado via `.env` (ex. Whisper, Groq, Gemini) para permitir fácil troca.
5. **Agente (Agno)**:
   - Recebe a transcrição ou texto.
   - Possui estado (histórico) salvo em SQLite (Agno SqliteDb), usando o número de WhatsApp como `session_id`.
   - É instanciado com ferramentas contextuais injetadas pelo orchestrator (notifier, search tools).
   - Pode conversar, pesquisar na web, publicar posts no blog e gerenciar memórias.
6. **Ferramenta de Publicação**: Após aprovação final, o Agente aciona a ferramenta que:
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

*(Este arquivo deve ser atualizado sempre que novas ferramentas, rotas ou fluxos forem adicionados)*
