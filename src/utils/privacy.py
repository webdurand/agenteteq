"""Helpers para minimização de dados pessoais em logs (LGPD Art. 6 III)."""


def mask_phone(phone: str) -> str:
    """Mascara um telefone para uso em logs. Ex: '5521999991234' → '55***1234'."""
    if not phone or len(phone) < 6:
        return "***"
    return phone[:2] + "***" + phone[-4:]
