import os
from typing import Optional
from agno.knowledge.knowledge import Knowledge
from agno.vectordb.pgvector import PgVector, SearchType
from agno.knowledge.embedder.google import GeminiEmbedder

# Cache para a base de conhecimento
_knowledge_base = None

def get_vector_db() -> Optional[PgVector]:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("[Aviso] DATABASE_URL não configurado. Memória não funcionará.")
        return None

    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://")

    # Inicializa o embedder. As variáveis GOOGLE_API_KEY devem estar presentes no .env
    embedder = GeminiEmbedder()

    vector_db = PgVector(
        table_name="user_memories",
        db_url=db_url,
        search_type=SearchType.vector,
        embedder=embedder,
    )
    
    # Cria a tabela se não existir
    vector_db.create()
    
    return vector_db

def get_knowledge_base() -> Optional[Knowledge]:
    global _knowledge_base
    if _knowledge_base is not None:
        return _knowledge_base
        
    vector_db = get_vector_db()
    if not vector_db:
        return None
        
    _knowledge_base = Knowledge(
        vector_db=vector_db
    )
    return _knowledge_base
