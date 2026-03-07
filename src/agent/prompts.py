GREETING_INJECTION = (
    "[INSTRUCAO DE SISTEMA: O usuario ficou mais de 4 horas sem enviar mensagens e optou por comecar uma conversa nova. "
    "Cumprimente de forma curta e descontraida (1-2 frases). "
    "NAO execute nenhuma ferramenta no cumprimento. Apenas resuma brevemente que voce pode ajudar com tarefas, lembretes, "
    "clima, blog, agendas e o que mais ele precisar. Seja conciso. "
    "Mensagem real do usuario: ]"
)

GREETING_INJECTION_WEB = (
    "[INSTRUCAO DE SISTEMA: O usuario ficou mais de 4 horas sem enviar mensagens. "
    "Cumprimente de forma curta e descontraida (1-2 frases). "
    "NAO execute nenhuma ferramenta no cumprimento. Apenas resuma brevemente que voce pode ajudar com tarefas, lembretes, "
    "clima, blog, agendas e o que mais ele precisar. Seja conciso. "
    "Mensagem real do usuario: ]"
)

CONTINUATION_INJECTION = (
    "[INSTRUCAO DE SISTEMA: O usuario quer continuar a conversa anterior. "
    "Consulte o historico da sessao e mencione em 1 linha de forma casual o que voces estavam discutindo "
    "(ex: 'ah certo, a gente tava falando de [assunto]...'), "
    "depois responda a mensagem do usuario normalmente. "
    "Mensagem do usuario: ]"
)
