# Arquitetura do Agente WhatsApp - Diario Teq

## Visão Geral

O `agenteteq` é uma API em Python baseada no FastAPI responsável por receber webhooks do WhatsApp, processar áudios utilizando serviços de transcrição, e alimentar um Agente Agno.
O Agente Agno possui ferramentas para coletar informações faltantes com o usuário e, após confirmação, enviar o post finalizado em markdown (.mdx) para o repositório do blog `diarioteq`, acionando git push na main.

## Fluxo Principal

1. **Webhook (FastAPI)**: Recebe payload da Meta (WhatsApp Cloud API) com um áudio ou texto.
2. **Módulo de Identidade (Determinístico)**: Antes de acionar a IA, o sistema verifica o número de telefone no banco de dados. Se for um usuário novo, realiza um onboarding determinístico pedindo o nome. Se já for cadastrado, injeta as preferências no contexto do agente.
3. **Integração WhatsApp**: Faz o download da mídia do áudio e permite enviar mensagens de texto (respostas/confirmações). A integração é **plug-and-play**, permitindo usar a **API Oficial da Meta** ou a **Evolution API** (configurável via `.env` na variável `WHATSAPP_PROVIDER`).
4. **Transcrição (Desacoplada)**: Lê o áudio e transforma em texto. O serviço é parametrizado via `.env` (ex. Whisper, Groq, Gemini) para permitir fácil troca.
5. **Agente (Agno)**:
   - Recebe a transcrição ou texto.
   - Possui estado (histórico) salvo em SQLite (Agno SqliteDb), usando o número de WhatsApp como `session_id`.
   - É instanciado com o contexto do usuário fornecido pelo Módulo de Identidade.
   - Pode conversar com o usuário, pedindo mais detalhes ou gerando uma sugestão de post.
   - Aguarda a confirmação do usuário (sim/não ou ajustes).
6. **Ferramenta de Publicação**: Após aprovação final, o Agente aciona a ferramenta que:
   - Gera o conteúdo e converte para base64.
   - Utiliza a **GitHub REST API** para criar ou atualizar o arquivo `YYYY-MM-DD-slug.mdx` diretamente no repositório remoto do blog (ex. `webdurand/diario-teq`), no diretório `content/posts/`.
   - Essa ação dispara automaticamente um deploy na Vercel (onde o blog está hospedado), sem depender de acessos ao sistema de arquivos local.

## Decisões Técnicas

- **Python & FastAPI**: Fornecem agilidade e facilidade para hospedar webhooks.
- **Agno**: Framework para construção de agentes stateful.
- **Identidade e Onboarding Determinístico**: Reduz custos de LLM e garante uma experiência controlada ao coletar os dados iniciais do usuário. A checagem de identidade é feita antes de instanciar o agente, usando um banco de dados SQLite local simples (`users.db` via `src/memory/identity.py`) que mapeia números de telefone para nomes de usuário, evitando alucinações e criando memória inicial de forma segura e determinística.
- **Módulo de Memória**: Utiliza NeonDB com PgVector e a Knowledge Base do Agno para armazenar memórias do usuário em background e injetar contexto de forma "Agentic" ou "Always-on".
- **Desacoplamento**: Tanto o LLM do agente quanto a API de transcrição podem ser trocados alterando apenas a injeção de dependência/variáveis de ambiente, tornando o sistema Future-proof.
- **Armazenamento de Sessão**: Agno SqliteDb para manter histórico por telefone do usuário.
- **Integração via GitHub API**: A publicação do blog foi migrada de comandos git locais para a GitHub API (`httpx.put`), permitindo que a API e o blog sejam deployados em servidores diferentes e desacoplados (ex: backend na Koyeb, frontend na Vercel).

*(Este arquivo deve ser atualizado sempre que novas ferramentas, rotas ou fluxos forem adicionados)*
