import os
import subprocess
from datetime import datetime
import re

def publish_post(title: str, content: str) -> str:
    """
    Ferramenta responsável por materializar o arquivo do post no blog
    e acionar um push na main.

    Parâmetros:
    - title: O título do post (será usado para gerar o nome do arquivo).
    - content: O conteúdo do post em markdown (.mdx), incluindo o frontmatter necessário.
    """
    # 1. Gerar o nome do arquivo (YYYY-MM-DD-slug.mdx)
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Criar um slug simples a partir do título
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    
    filename = f"{today}-{slug}.mdx"
    
    # O diretório esperado é o irmão "diarioteq" -> content/posts
    # A raiz do backend atual é "agenteteq", então base_dir aponta para diario-teq/diarioteq
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../diarioteq"))
    posts_dir = os.path.join(base_dir, "content", "posts")
    
    # Verifica se o diretório existe
    if not os.path.exists(posts_dir):
        return f"Erro: O diretório do blog não foi encontrado em {posts_dir}."
        
    filepath = os.path.join(posts_dir, filename)
    
    # 2. Escrever o conteúdo no arquivo
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        return f"Erro ao criar o arquivo do post: {str(e)}"
        
    # 3. Executar os comandos git
    try:
        subprocess.run(["git", "add", filepath], cwd=base_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", f"docs(blog): post automático - {title}"], cwd=base_dir, check=True, capture_output=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=base_dir, check=True, capture_output=True)
        
        return f"Sucesso! Post '{title}' publicado no arquivo {filename} e enviado para o GitHub."
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode('utf-8') if e.stderr else str(e)
        return f"Erro ao fazer commit/push do post. O arquivo foi criado localmente. Erro: {error_msg}"
