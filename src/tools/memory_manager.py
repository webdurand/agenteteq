from sqlalchemy import select
from agno.knowledge.document import Document

from src.memory.knowledge import get_vector_db

def add_memory(fato: str, user_id: str) -> str:
    """
    Adiciona um fato ou instrução à memória de longo prazo do usuário.
    Use esta ferramenta quando o usuário pedir para você lembrar de algo ("Lembre-se que...").
    
    Args:
        fato (str): O fato a ser memorizado.
        user_id (str): O identificador do usuário (normalmente o número de WhatsApp).
        
    Returns:
        str: Mensagem de sucesso confirmando a adição.
    """
    vector_db = get_vector_db()
    if not vector_db:
        return "Erro: Banco de dados vetorial não configurado."
        
    doc = Document(
        content=fato,
        meta_data={"user_id": user_id}
    )
    
    # Inserir no banco
    vector_db.insert(content_hash=str(hash(fato)), documents=[doc])
    
    return f"Fato adicionado com sucesso à memória: {fato}"

def delete_memory(query: str, user_id: str) -> str:
    """
    Remove uma memória ou fato específico do usuário.
    Use quando o usuário pedir para você esquecer algo ("Esqueça que...").
    
    Args:
        query (str): O termo ou frase que descreve a memória a ser deletada.
        user_id (str): O identificador do usuário.
        
    Returns:
        str: Mensagem de sucesso ou erro.
    """
    vector_db = get_vector_db()
    if not vector_db:
        return "Erro: Banco de dados vetorial não configurado."
        
    # Busca o documento mais próximo
    results = vector_db.search(query=query, limit=1, filters={"user_id": user_id})
    if not results:
        return f"Não encontrei nenhuma memória relacionada a '{query}' para deletar."
        
    doc = results[0]
    vector_db.delete_by_id(doc.id)
    return f"Memória removida com sucesso: {doc.content}"

def list_memories(user_id: str) -> str:
    """
    Lista todos os fatos memorizados sobre o usuário.
    Use esta ferramenta quando o usuário perguntar o que você sabe sobre ele.
    
    Args:
        user_id (str): O identificador do usuário.
        
    Returns:
        str: Uma lista em formato de texto com todos os fatos.
    """
    vector_db = get_vector_db()
    if not vector_db:
        return "Erro: Banco de dados vetorial não configurado."
        
    try:
        with vector_db.Session() as sess:
            # Consulta direta usando SQLAlchemy para buscar por metadata
            stmt = select(vector_db.table.c.content).where(
                vector_db.table.c.meta_data.contains({"user_id": user_id})
            )
            results = sess.execute(stmt).fetchall()
            
            if not results:
                return "Não há memórias salvas para este usuário."
                
            memories = [f"- {row[0]}" for row in results]
            return "Aqui estão as memórias salvas:\n" + "\n".join(memories)
    except Exception as e:
        return f"Erro ao listar memórias: {str(e)}"
