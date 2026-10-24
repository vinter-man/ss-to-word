"""Interface strings for the window, in the four languages the language switcher offers.

Only the window is translated. The log, the command line, the code and the documentation are
English, because those are read by whoever is fixing the program rather than using it.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_UI_LANGUAGE = "en"


@dataclass(frozen=True)
class UiLanguage:
    code: str
    name: str            # shown in the switcher, in the language itself


UI_LANGUAGES: tuple[UiLanguage, ...] = (
    UiLanguage("en", "English"),
    UiLanguage("bg", "Български"),
    UiLanguage("uk", "Українська"),
    UiLanguage("ru", "Русский"),
)

STRINGS: dict[str, dict[str, str]] = {
    "window_title": {
        "en": "Photos → Word",
        "bg": "Снимки → Word",
        "uk": "Фото → Word",
        "ru": "Фото → Word",
    },
    "input_label": {
        "en": "1. Folder with photos or screenshots:",
        "bg": "1. Папка със снимки или екранни снимки:",
        "uk": "1. Папка з фото або знімками екрана:",
        "ru": "1. Папка с фото или скриншотами:",
    },
    "output_label": {
        "en": "2. Save the result as:",
        "bg": "2. Запази резултата като:",
        "uk": "2. Зберегти результат як:",
        "ru": "2. Сохранить результат как:",
    },
    "format_label": {
        "en": "3. Format:",
        "bg": "3. Формат:",
        "uk": "3. Формат:",
        "ru": "3. Формат:",
    },
    "ocr_language_label": {
        "en": "4. Language of the text on the images:",
        "bg": "4. Език на текста върху изображенията:",
        "uk": "4. Мова тексту на зображеннях:",
        "ru": "4. Язык текста на изображениях:",
    },
    "browse": {"en": "Browse…", "bg": "Избери…", "uk": "Вибрати…", "ru": "Выбрать…"},
    "file_headings": {
        "en": "Put the file name before each image's text",
        "bg": "Име на файла преди текста на всяка снимка",
        "uk": "Ім'я файлу перед текстом кожного фото",
        "ru": "Имя файла перед текстом каждого фото",
    },
    "page_breaks": {
        "en": "Start every image on a new page (Word)",
        "bg": "Всяка снимка на нова страница (Word)",
        "uk": "Кожне фото з нової сторінки (Word)",
        "ru": "Каждое фото с новой страницы (Word)",
    },
    "start": {"en": "▶  Start", "bg": "▶  Старт", "uk": "▶  Старт", "ru": "▶  Старт"},
    "cancel": {"en": "Cancel", "bg": "Отказ", "uk": "Скасувати", "ru": "Отмена"},
    "open_result": {
        "en": "Open the result",
        "bg": "Отвори резултата",
        "uk": "Відкрити результат",
        "ru": "Открыть результат",
    },
    "ready": {
        "en": "Choose a folder with photos and press Start.",
        "bg": "Изберете папка със снимки и натиснете Старт.",
        "uk": "Виберіть папку з фото та натисніть Старт.",
        "ru": "Выберите папку с фото и нажмите «Старт».",
    },
    "starting": {"en": "Starting…", "bg": "Стартиране…", "uk": "Запуск…", "ru": "Запуск…"},
    "processing": {
        "en": "Processing {done} of {total}: {name}",
        "bg": "Обработка {done} от {total}: {name}",
        "uk": "Обробка {done} з {total}: {name}",
        "ru": "Обработка {done} из {total}: {name}",
    },
    "finished_status": {
        "en": "Finished: {name}",
        "bg": "Готово: {name}",
        "uk": "Готово: {name}",
        "ru": "Готово: {name}",
    },
    "cancelled_status": {
        "en": "Cancelled.", "bg": "Отказано.", "uk": "Скасовано.", "ru": "Отменено.",
    },
    "cancelling": {
        "en": "Stopping after the current file…",
        "bg": "Спиране след текущия файл…",
        "uk": "Зупинка після поточного файлу…",
        "ru": "Остановка после текущего файла…",
    },
    "error_status": {"en": "Error.", "bg": "Грешка.", "uk": "Помилка.", "ru": "Ошибка."},
    "pick_input_title": {
        "en": "Folder with photos",
        "bg": "Папка със снимки",
        "uk": "Папка з фото",
        "ru": "Папка с фото",
    },
    "pick_output_title": {
        "en": "Save as", "bg": "Запази като", "uk": "Зберегти як", "ru": "Сохранить как",
    },
    "all_files": {
        "en": "All files", "bg": "Всички файлове", "uk": "Усі файли", "ru": "Все файлы",
    },
    "need_folder": {
        "en": "Choose a folder with photos that exists.",
        "bg": "Изберете съществуваща папка със снимки.",
        "uk": "Виберіть наявну папку з фото.",
        "ru": "Укажите существующую папку с фотографиями.",
    },
    "need_output": {
        "en": "Choose where to save the result.",
        "bg": "Изберете къде да запазите резултата.",
        "uk": "Вкажіть, куди зберегти результат.",
        "ru": "Укажите, куда сохранить результат.",
    },
    "need_file_not_folder": {
        "en": "Give a file name for the result, not a folder.",
        "bg": "Задайте име на файл за резултата, а не папка.",
        "uk": "Вкажіть ім'я файлу для результату, а не папку.",
        "ru": "Укажите имя файла для результата, а не папку.",
    },
    "cannot_save": {
        "en": "Cannot save here:\n{path}\n\n{reason}",
        "bg": "Тук не може да се запише:\n{path}\n\n{reason}",
        "uk": "Сюди зберегти не можна:\n{path}\n\n{reason}",
        "ru": "Нельзя сохранить сюда:\n{path}\n\n{reason}",
    },
    "overwrite": {
        "en": "The file already exists:\n{path}\n\nOverwrite it?",
        "bg": "Файлът вече съществува:\n{path}\n\nДа бъде ли презаписан?",
        "uk": "Файл уже існує:\n{path}\n\nПерезаписати?",
        "ru": "Файл уже существует:\n{path}\n\nПерезаписать?",
    },
    "quit_while_running": {
        "en": "The images are still being processed. Close the program?",
        "bg": "Обработката още тече. Да се затвори ли програмата?",
        "uk": "Обробка ще триває. Закрити програму?",
        "ru": "Обработка ещё идёт. Закрыть программу?",
    },
    "cannot_open": {
        "en": "Could not open the file:\n{error}",
        "bg": "Файлът не може да бъде отворен:\n{error}",
        "uk": "Не вдалося відкрити файл:\n{error}",
        "ru": "Не удалось открыть файл:\n{error}",
    },
    "summary": {
        "en": "Done: {ok} of {total} files.\n\n{path}",
        "bg": "Готово: {ok} от {total} файла.\n\n{path}",
        "uk": "Готово: {ok} з {total} файлів.\n\n{path}",
        "ru": "Готово: {ok} из {total} файлов.\n\n{path}",
    },
    "summary_failed": {
        "en": "\n\nNot processed: {count} (see the log).",
        "bg": "\n\nНеобработени: {count} (вижте дневника).",
        "uk": "\n\nНе оброблено: {count} (див. журнал).",
        "ru": "\n\nНе обработано: {count} (см. журнал).",
    },
    "summary_empty": {
        "en": "\n\nNo text found in {count} file(s).",
        "bg": "\n\nВ {count} файл(а) не е открит текст.",
        "uk": "\n\nТекст не знайдено у {count} файлі(ах).",
        "ru": "\n\nТекст не найден в {count} файле(ах).",
    },
    "summary_low_confidence": {
        "en": "\n\nRead with low confidence: {count} file(s) — worth checking.",
        "bg": "\n\nРазпознати с ниска увереност: {count} файл(а) — проверете ги.",
        "uk": "\n\nРозпізнано з низькою впевненістю: {count} файлів — варто перевірити.",
        "ru": "\n\nНизкая уверенность распознавания: {count} файл(ов) — стоит проверить.",
    },
    "summary_open": {
        "en": "\n\nOpen the result?",
        "bg": "\n\nДа се отвори ли резултатът?",
        "uk": "\n\nВідкрити результат?",
        "ru": "\n\nОткрыть результат?",
    },
    "cancelled_log": {
        "en": "Cancelled by the user.",
        "bg": "Отказано от потребителя.",
        "uk": "Скасовано користувачем.",
        "ru": "Отменено пользователем.",
    },
    "language_menu": {
        "en": "Interface language",
        "bg": "Език на интерфейса",
        "uk": "Мова інтерфейсу",
        "ru": "Язык интерфейса",
    },
}


def translate(key: str, language: str = DEFAULT_UI_LANGUAGE, **fields: object) -> str:
    """The string for `key` in `language`, falling back to English for anything missing."""
    variants = STRINGS[key]
    text = variants.get(language) or variants[DEFAULT_UI_LANGUAGE]
    return text.format(**fields) if fields else text


def find_ui_language(code: str) -> UiLanguage | None:
    return next((language for language in UI_LANGUAGES if language.code == code), None)
