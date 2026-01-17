"""
🛡️ CONTENT FILTER - Фильтрация нежелательных промптов
Защита от случайных вопросов и списания бананов за бред
"""

import re
from typing import Optional, Tuple
from enum import Enum


class FilterMode(Enum):
    """Режимы работы фильтра"""
    SHADOW = "shadow"      # Теневой режим (только логирование)
    ACTIVE = "active"      # Боевой режим (блокировка)
    DISABLED = "disabled"  # Выключен


class TriggerType(Enum):
    """Типы триггеров"""
    QUESTION_MARK = "question_mark"
    STOP_WORD = "stop_word"
    NSFW_WORD = "nsfw_word"
    WHITELIST = "whitelist"


# =====================================================================
# 📋 СПИСКИ ФИЛЬТРАЦИИ
# =====================================================================

# 🔴 СТОП-СЛОВА (вопросы, общение, техподдержка)
STOP_WORDS = [
    # Вопросительные слова
    r'\bкак\b', r'\bчто\b', r'\bпочему\b', r'\bзачем\b', r'\bсколько\b',
    r'\bгде\b', r'\bкуда\b', r'\bкогда\b', r'\bкакой\b', r'\bкакая\b',
    r'\bкакие\b', r'\bчей\b', r'\bчья\b', r'\bчьё\b',
    
    # Приветствия
    r'\bпривет\b', r'\bздравствуй\b', r'\bдобрый\s+день\b', 
    r'\bдоброе\s+утро\b', r'\bдобрый\s+вечер\b', r'\bхай\b', 
    r'\bhello\b', r'\bhi\b', r'\bhey\b',
    
    # Просьбы о помощи
    r'\bподскажи\b', r'\bпомоги\b', r'\bрасскажи\b', r'\bобъясни\b',
    r'\bhelp\b', r'\bsupport\b', r'\bпомощь\b',
    
    # Вопросы о боте/оплате
    r'\bбанан\b', r'\bоплат\b', r'\bтариф\b', r'\bцен\b', r'\bкупить\b',
    r'\bподписк\b', r'\bкак\s+работа\b', r'\bкак\s+использ\b',
    r'\bпочему\s+не\b', r'\bне\s+работа\b',
    
    # Общие фразы
    r'\bспасибо\b', r'\bблагодар\b', r'\bок\b', r'\bхорошо\b',
    r'\bпонятно\b', r'\bясно\b', r'\bthanks\b', r'\bthank\s+you\b'
]

# 🔞 NSFW-слова (контент 18+)
NSFW_WORDS = [
    r'\bголая\b', r'\bголый\b', r'\bраздет\b', r'\bраздень\b',
    r'\bобнажен\b', r'\bсекс\b', r'\bпорно\b', r'\bxxx\b',
    r'\bnude\b', r'\bnaked\b', r'\bnsfw\b', r'\bсиськ\b', r'\bгруд\b'
]

# ✅ БЕЛЫЙ СПИСОК (исключения из фильтра)
WHITELIST_PATTERNS = [
    r'\bраздельный\b',      # "купальник раздельный"
    r'\bразделен\b',         # "разделенный экран"
    r'\bкупальник\b',        # контекст одежды
    r'\bплатье\b',           # контекст одежды
]


# =====================================================================
# 🔍 ОСНОВНОЙ КЛАСС ФИЛЬТРА
# =====================================================================

class ContentFilter:
    """
    Фильтр контента с поддержкой разных режимов работы
    """
    
    def __init__(self, mode: FilterMode = FilterMode.SHADOW):
        self.mode = mode
        
        # Компилируем регулярки один раз при инициализации
        self.stop_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in STOP_WORDS]
        self.nsfw_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in NSFW_WORDS]
        self.whitelist_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in WHITELIST_PATTERNS]
    
    def check(self, text: str) -> Tuple[bool, Optional[TriggerType], Optional[str]]:
        """
        Проверяет текст на триггеры фильтра
        
        Returns:
            (should_block, trigger_type, matched_word)
            - should_block: True если нужно заблокировать (зависит от режима)
            - trigger_type: Тип сработавшего триггера
            - matched_word: Конкретное слово/паттерн который сработал
        """
        
        if self.mode == FilterMode.DISABLED:
            return False, None, None
        
        # 1️⃣ Проверка белого списка (приоритет)
        for pattern in self.whitelist_patterns:
            if pattern.search(text):
                return False, TriggerType.WHITELIST, pattern.pattern
        
        # 2️⃣ Проверка знака вопроса
        # Игнорируем если ? в конце длинного предложения (>3 слов)
        if '?' in text:
            words = text.split()
            # Если вопрос короткий (1-3 слова) - явно вопрос
            if len(words) <= 3:
                should_block = (self.mode == FilterMode.ACTIVE)
                return should_block, TriggerType.QUESTION_MARK, "?"
            
            # Если длинное предложение - проверяем позицию ?
            # Если ? в начале/середине - вопрос, в конце - может быть промпт
            question_pos = text.index('?')
            text_length = len(text)
            
            # Если ? в первой половине текста - точно вопрос
            if question_pos < text_length * 0.5:
                should_block = (self.mode == FilterMode.ACTIVE)
                return should_block, TriggerType.QUESTION_MARK, "?"
        
        # 3️⃣ Проверка NSFW-слов (высокий приоритет)
        for pattern in self.nsfw_patterns:
            match = pattern.search(text)
            if match:
                should_block = (self.mode == FilterMode.ACTIVE)
                return should_block, TriggerType.NSFW_WORD, match.group()
        
        # 4️⃣ Проверка стоп-слов
        for pattern in self.stop_patterns:
            match = pattern.search(text)
            if match:
                should_block = (self.mode == FilterMode.ACTIVE)
                return should_block, TriggerType.STOP_WORD, match.group()
        
        # ✅ Всё чисто
        return False, None, None
    
    def set_mode(self, mode: FilterMode):
        """Изменить режим работы фильтра"""
        self.mode = mode
    
    def get_mode(self) -> FilterMode:
        """Получить текущий режим"""
        return self.mode


# =====================================================================
# 📝 ГЕНЕРАТОР СООБЩЕНИЙ
# =====================================================================

def get_filter_message(trigger_type: TriggerType) -> str:
    """
    Генерирует текст сообщения для пользователя
    в зависимости от типа триггера
    """
    
    if trigger_type == TriggerType.QUESTION_MARK:
        return (
            "✋ <b>Стоп! Это не похоже на описание картинки.</b>\n\n"
            "Я робот-художник 🎨, я не умею отвечать на вопросы текстом.\n\n"
            "• Если вы хотите <b>картинку</b> — просто опишите её (например: «Кот в шляпе»).\n"
            "• Если у вас <b>вопрос или проблема</b> — нажмите кнопку [💬 Поддержка] в меню.\n\n"
            "💰 Ваш банан 🍌 <b>не списан</b>."
        )
    
    elif trigger_type == TriggerType.STOP_WORD:
        return (
            "✋ <b>Похоже, это вопрос или обращение.</b>\n\n"
            "Я создаю изображения по описаниям, а не отвечаю на вопросы.\n\n"
            "• Чтобы создать картинку — напишите <b>подробное описание</b> (например: «Закат на пляже»).\n"
            "• Для вопросов и помощи — нажмите [💬 Поддержка].\n\n"
            "💰 Ваш банан 🍌 <b>не списан</b>."
        )
    
    elif trigger_type == TriggerType.NSFW_WORD:
        return (
            "🔞 <b>Обнаружен запрещенный контент</b>\n\n"
            "Генерация контента 18+ запрещена правилами сервиса.\n"
            "Пожалуйста, измените запрос.\n\n"
            "💰 Ваш банан 🍌 <b>не списан</b>."
        )
    
    else:
        return (
            "⚠️ <b>Запрос не прошел проверку</b>\n\n"
            "Пожалуйста, опишите что вы хотите увидеть на картинке.\n\n"
            "💰 Ваш банан 🍌 <b>не списан</b>."
        )


# =====================================================================
# 🧪 ТЕСТОВАЯ ФУНКЦИЯ
# =====================================================================

def test_filter():
    """Тестирование фильтра"""
    
    filter_obj = ContentFilter(FilterMode.SHADOW)
    
    test_cases = [
        "Кот в шляпе",                          # ✅ OK
        "Как дела?",                             # ❌ Вопрос
        "Девушка в шляпе?",                      # ⚠️ Длинное предложение с ?
        "Привет, как оплатить подписку?",        # ❌ Стоп-слова + вопрос
        "Купальник раздельный на пляже",         # ✅ Белый список
        "Голая девушка",                         # ❌ NSFW
    ]
    
    print("🧪 Тестирование фильтра:\n")
    for text in test_cases:
        should_block, trigger, matched = filter_obj.check(text)
        status = "🚫 БЛОК" if should_block else "✅ OK"
        print(f"{status} | {text[:40]:<40} | {trigger.value if trigger else 'None':<15} | {matched or ''}")


# Запуск теста если файл запущен напрямую
if __name__ == "__main__":
    test_filter()