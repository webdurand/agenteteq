import os
import base64
import re
from datetime import datetime
import httpx

def publish_post(title: str, content: str) -> str:
    """
    Ferramenta responsável por criar o arquivo do post diretamente no repositório do blog
    no GitHub via API, acionando o deploy na Vercel indiretamente.

    Parâmetros:
    - title: O título do post (será usado para gerar o nome do arquivo).
    - content: O conteúdo do post em markdown (.mdx), incluindo o frontmatter necessário.
    """
    # 1. Obter configurações do ambiente
    github_token = os.environ.get("GITHUB_TOKEN")
    github_repo = os.environ.get("GITHUB_REPO", "webdurand/diario-teq")
    
    if not github_token:
        return "Erro: GITHUB_TOKEN não está configurado nas variáveis de ambiente. Não é possível publicar o post no GitHub."
        
    # 2. Gerar o nome do arquivo (YYYY-MM-DD-slug.mdx)
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Criar um slug simples a partir do título
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    
    filename = f"{today}-{slug}.mdx"
    file_path = f"content/posts/{filename}"
    
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
        "message": f"post: novo devlog automático - {title}",
        "content": content_base64,
        "branch": "main"  # ou a branch padrão
    }
    
    # 4. Fazer a requisição para o GitHub
    try:
        response = httpx.put(api_url, headers=headers, json=payload, timeout=20.0)
        
        if response.status_code in (201, 200):
            return f"Sucesso! Post '{title}' publicado no arquivo {filename} no repositório {github_repo} via GitHub API."
        else:
            return f"Erro ao publicar no GitHub. Status: {response.status_code}. Detalhes: {response.text}"
            
    except Exception as e:
        return f"Erro de conexão ao tentar publicar na API do GitHub: {str(e)}"
