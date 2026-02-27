# Arquitetura do Agente WhatsApp - Diario Teq

## Visão Geral

O `agenteteq` é uma API em Python baseada no FastAPI responsável por receber webhooks do WhatsApp, processar áudios utilizando serviços de transcrição, e alimentar um Agente Agno.
O Agente Agno possui ferramentas para coletar informações faltantes com o usuário e, após confirmação, enviar o post finalizado em markdown (.mdx) para o repositório do blog `diarioteq`, acionando git push na main.

## Fluxo Principal

1. **Webhook (FastAPI)**: Recebe payload da Meta (WhatsApp Cloud API) com um áudio.
2. **Integração WhatsApp**: Faz o download da mídia do áudio e permite enviar mensagens de texto (respostas/confirmações).
3. **Transcrição (Desacoplada)**: Lê o áudio e transforma em texto. O serviço é parametrizado via `.env` (ex. Whisper, Groq, Gemini) para permitir fácil troca.
4. **Agente (Agno)**:
   - Recebe a transcrição.
   - Possui estado (histórico) salvo em SQLite (Agno SqliteDb), usando o número de WhatsApp como `session_id`.
   - Pode conversar com o usuário, pedindo mais detalhes ou gerando uma sugestão de post.
   - Aguarda a confirmação do usuário (sim/não ou ajustes).
5. **Ferramenta de Publicação**: Após aprovação final, o Agente aciona a ferramenta que:
   - Gera um arquivo `YYYY-MM-DD-slug.mdx` no diretório `../diarioteq/content/posts/`.
   - Executa os comandos git (`add`, `commit` e `push origin main`) no repositório `../diarioteq/`.

## Decisões Técnicas

- **Python & FastAPI**: Fornecem agilidade e facilidade para hospedar webhooks.
- **Agno**: Framework para construção de agentes stateful.
- **Desacoplamento**: Tanto o LLM do agente quanto a API de transcrição podem ser trocados alterando apenas a injeção de dependência/variáveis de ambiente, tornando o sistema Future-proof.
- **Armazenamento de Sessão**: Agno SqliteDb para manter histórico por telefone do usuário.
- **Integração Git Local**: Para simplicidade, o agente faz interações git nativas via shell no repositório irmão para publicar o post.

*(Este arquivo deve ser atualizado sempre que novas ferramentas, rotas ou fluxos forem adicionados)*
