"""Копирование текста в буфер обмена (без вставки в окно)."""

from __future__ import annotations


# ----------------------------------------
# Кладёт текст в буфер; на Linux pyperclip берёт wl-copy, xclip или xsel
# ----------------------------------------
def copy(text: str) -> None:
    import pyperclip

    try:
        pyperclip.copy(text)
    except pyperclip.PyperclipException as error:
        raise RuntimeError(f"буфер обмена недоступен: {error}. На Linux поставь wl-clipboard или xclip") from error
