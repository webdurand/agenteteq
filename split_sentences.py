import re

def split_into_sentences(text: str) -> list[str]:
    """Splits a string into sentences using common punctuation marks."""
    # Split using regular expression that looks for punctuation followed by space or end of string
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    # Filter out any empty strings
    return [s.strip() for s in sentences if s.strip()]

text = "Hello there. How are you? I am fine! This is a test."
print(split_into_sentences(text))
