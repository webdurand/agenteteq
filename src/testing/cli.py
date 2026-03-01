import os
import asyncio
from dotenv import load_dotenv
from rich.console import Console
from rich.prompt import Prompt

# Força o carregamento do .env local para testes
load_dotenv()

from src.agent.assistant import get_assistant
from src.memory.knowledge import get_vector_db
from src.memory.extractor import extract_and_save_facts
from src.integrations.status_notifier import StatusNotifier
from src.tools.web_search import create_web_search_tool, create_fetch_page_tool
from src.tools.deep_research import create_deep_research_tool

console = Console()


class CliStatusNotifier(StatusNotifier):
    """Notifier para o CLI: imprime no console em vez de enviar mensagem no WhatsApp."""

    def notify(self, message: str) -> None:
        console.print(f"[bold yellow][STATUS] {message}[/bold yellow]")


async def main():
    console.print("[bold green]=== CLI Test do Agente Diario Teq ===[/bold green]")
    console.print("Digite 'sair' ou 'exit' para encerrar.\n")

    session_id = Prompt.ask("Digite o número do seu telefone simulado", default="local_test_user")

    notifier = CliStatusNotifier(to_number=session_id)
    search_tools = [
        create_web_search_tool(notifier),
        create_fetch_page_tool(notifier),
        create_deep_research_tool(notifier, session_id),
    ]

    agent = get_assistant(session_id=session_id, extra_tools=search_tools)
    memory_mode = os.getenv("MEMORY_MODE", "agentic").lower()
    
    console.print(f"[dim]Sessão iniciada como: {session_id} | Memory Mode: {memory_mode}[/dim]\n")
    
    while True:
        try:
            text_body = Prompt.ask("[bold blue]Você[/bold blue]")
            if text_body.lower() in ["sair", "exit", "quit"]:
                break
                
            if not text_body.strip():
                continue
                
            with console.status("[bold yellow]Processando...[/bold yellow]", spinner="dots"):
                # Inject context if always-on
                original_body = text_body
                if memory_mode == "always-on":
                    vector_db = get_vector_db()
                    if vector_db:
                        try:
                            results = vector_db.search(query=text_body, limit=3, filters={"user_id": session_id})
                            if results:
                                memories = "\n".join([f"- {doc.content}" for doc in results])
                                context_text = f"\n\n[Contexto da Memória para considerar:\n{memories}]"
                                text_body += context_text
                        except Exception as e:
                            console.print(f"[red]Erro ao buscar memórias always-on: {e}[/red]")

                # Run Agent
                response = agent.run(text_body, knowledge_filters={"user_id": session_id})
                
                # Extração de memória em background (asyncio task)
                if response and response.content:
                    asyncio.create_task(
                        asyncio.to_thread(extract_and_save_facts, session_id, original_body, response.content)
                    )

            if response and response.content:
                console.print(f"[bold magenta]Agente:[/bold magenta] {response.content}\n")
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            console.print(f"[bold red]Erro:[/bold red] {e}")

if __name__ == "__main__":
    asyncio.run(main())
