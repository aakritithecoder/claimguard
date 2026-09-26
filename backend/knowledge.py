import re

def load_policy(path="policy.txt") -> list[str]:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    words = text.split()
    chunk_size = 60
    return [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]


def search_policy(query: str, chunks: list[str], top_k: int = 2) -> list[str]:
    q_words = set(re.findall(r"\w+", query.lower()))
    scored = [(len(q_words & set(re.findall(r"\w+", c.lower()))), c) for c in chunks]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for score, c in scored[:top_k] if score > 0]