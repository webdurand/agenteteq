"""
Script para marcar todos os usuarios existentes como tendo aceitado os termos v1.0.
Usar apenas uma vez, para a migracao inicial.

Uso: python -m scripts.mark_existing_users_terms
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from src.db.session import get_db
from src.db.models import User
from src.auth.terms import CURRENT_TERMS_VERSION


def main():
    now = datetime.now(timezone.utc)
    with get_db() as session:
        users = session.query(User).filter(User.terms_accepted_version.is_(None)).all()
        count = len(users)
        for user in users:
            user.terms_accepted_version = CURRENT_TERMS_VERSION
            user.terms_accepted_at = now
        session.commit()
    print(f"[TERMS] {count} usuario(s) marcado(s) como tendo aceitado termos v{CURRENT_TERMS_VERSION}.")


if __name__ == "__main__":
    main()
