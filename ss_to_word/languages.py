"""The languages the app can read, and how a user's spelling of one maps onto them."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    """One selectable language.

    `code` is what Tesseract calls it and what names the bundled model file;
    `tag` is the BCP-47 tag written into the .docx so Word proofs the text in that language.
    """

    code: str
    name: str            # shown in the language picker, in the language itself
    tag: str             # e.g. "bg-BG"
    aliases: tuple[str, ...] = ()

    @property
    def spellings(self) -> tuple[str, ...]:
        """Everything a user might type for this language, including its own name."""
        return tuple(dict.fromkeys((self.code, self.name.casefold(), *self.aliases)))


LANGUAGES: tuple[Language, ...] = (
    Language("bul", "Български", "bg-BG", ("bg", "bulgarian", "болгарский", "болгарська")),
    Language("rus", "Русский", "ru-RU", ("ru", "russian", "русский", "російська")),
    Language("ukr", "Українська", "uk-UA", ("uk", "ua", "ukrainian", "украинский", "українська")),
    Language("eng", "English", "en-US", ("en", "english", "английский", "англійська")),
)
DEFAULT_LANGUAGE = LANGUAGES[0]

_BY_SPELLING = {spelling.casefold(): language
                for language in LANGUAGES for spelling in language.spellings}


def find(spelling: str) -> Language | None:
    """The language a user's spelling refers to ("ru", "Русский", "eng"), or None."""
    return _BY_SPELLING.get(spelling.strip().casefold())


def resolve(spelling: str) -> Language:
    """Like `find`, but raises with the list of accepted spellings."""
    language = find(spelling)
    if language is None:
        known = ", ".join(lang.code for lang in LANGUAGES)
        raise ValueError(f"unknown language {spelling!r}; known languages: {known}")
    return language


def tag_for(code: str) -> str:
    """The BCP-47 tag for a Tesseract language spec, including combinations like 'bul+eng'."""
    first = code.split("+")[0]
    language = find(first)
    return language.tag if language else DEFAULT_LANGUAGE.tag
