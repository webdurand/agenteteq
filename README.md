# Agente WhatsApp - Diario Teq

API em Python baseada no FastAPI e agentes Agno para transcrever áudios do WhatsApp e criar postagens no blog diarioteq.

## Configuração do Ambiente

Utilizamos o `uv` para uma instalação de pacotes mais rápida. Para configurar seu ambiente:

```bash
make setup
```

Isso irá criar o ambiente virtual em `.venv`, instalar o `uv` e em seguida as dependências do `requirements.txt`.

## Como rodar o servidor

Para rodar o servidor em modo de desenvolvimento:

```bash
make dev
```
O servidor estará disponível em `http://localhost:8000`.
