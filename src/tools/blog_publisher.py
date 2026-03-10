import os
import base64
import re
from datetime import datetime
import httpx
from src.memory.identity import get_user

def create_blog_tools(session_id: str, channel: str = "web"):
    
    def publish_post(title: str, content: str) -> str:
        """
        Ferramenta responsável por criar o arquivo do post diretamente no repositório do blog
        no GitHub via API, acionando o deploy na Vercel indiretamente.

        IMPORTANTE: Aguarde confirmação explícita do usuário antes de chamar esta tool.
        Nunca publique automaticamente — sempre mostre o rascunho e peça aprovação.

        Parâmetros:
        - title: O título do post (será usado para gerar o nome do arquivo).
        - content: O conteúdo do post em markdown (.mdx), incluindo o frontmatter necessário.
        """
        # 1. Obter configurações do ambiente
        github_token = os.environ.get("GITHUB_TOKEN")
        github_repo = os.environ.get("GITHUB_REPO", "webdurand/diario-teq")
        
        if not github_token:
            return "Erro: GITHUB_TOKEN não está configurado nas variáveis de ambiente. Não é possível publicar o post no GitHub."
            
        # Obter username do autor
        user = get_user(session_id)
        author = user.get("username", "Desconhecido") if user else "Desconhecido"
            
        # 2. Gerar o nome do arquivo (YYYY-MM-DD-slug.mdx)
        today = datetime.now().strftime("%Y-%m-%d")
        
        # Criar um slug simples a partir do título
        slug = title.lower()
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        slug = slug.strip('-')
        
        filename = f"{today}-{slug}.mdx"
        file_path = f"content/posts/{filename}"
        
        # Inserir author no frontmatter se nao existir ou se precisar
        # O agente ja costuma enviar com frontmatter, mas por seguranca podemos injetar no inicio ou
        # deixar o agente escrever o frontmatter e adicionar apenas a chave author.
        if "author:" not in content.lower() and "---" in content:
            content = content.replace("---\n", f"---\nauthor: {author}\n", 1)
        elif not content.startswith("---"):
            content = f"---\nauthor: {author}\ntitle: {title}\n---\n\n" + content
        
        # 3. Preparar a chamada para a API do GitHub
        api_url = f"https://api.github.com/repos/{github_repo}/contents/{file_path}"
        
        headers = {
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        
        # O conteúdo precisa estar em base64
        content_base64 = base64.b64encode(content.encode('utf-8')).decode('utf-8')
        
        payload = {
            "message": f"post: novo devlog automático - {title} por {author}",
            "content": content_base64,
            "branch": "main"  # ou a branch padrão
        }
        
        # 4. Fazer a requisição para o GitHub
        try:
            from src.events import emit_event_sync
            emit_event_sync(session_id, "blog_preview", {"title": title, "content": content})
            
            response = httpx.put(api_url, headers=headers, json=payload, timeout=20.0)
            
            if response.status_code in (201, 200):
                from src.events_broadcast import emit_action_log_sync
                emit_action_log_sync(session_id, "Post publicado", title, channel)
                return f"Sucesso! Post '{title}' publicado no arquivo {filename} no repositório {github_repo} via GitHub API."
            else:
                return f"Erro ao publicar no GitHub. Status: {response.status_code}. Detalhes: {response.text}"
                
        except Exception as e:
            return f"Erro de conexão ao tentar publicar na API do GitHub: {str(e)}"
            
    return [publish_post]
