# Здесь мы храним настройки всех товаров.
# Меняешь здесь — меняется везде: и в меню, и в обработке платежей.

PACKAGES = {
    "mini": {"name": "Start", "gens": 10, "price": 99, "emoji": "", "suffix": "бананов"},    "standard": {"name": "Medium", "gens": 44, "price": 299, "emoji": "", "suffix": "банана"},
    "large": {"name": "Big", "gens": 140, "price": 699, "emoji": "🔥", "suffix": "бананов"},
    "xl": {"name": "Mega", "gens": 340, "price": 1499, "emoji": "", "suffix": "бананов"},
    "whale": {"name": "Whale", "gens": 832, "price": 3499, "emoji": "👑", "suffix": "банана"},
}

# Stars пакеты (тоже можно вынести сюда, чтобы не потерять)
STARS_PACKAGES = {
    "stars_4": {"bananas": 4, "stars": 35, "emoji": "🍌"},
    "stars_12": {"bananas": 12, "stars": 90, "emoji": "🍌"},
    "stars_24": {"bananas": 24, "stars": 160, "emoji": "🍌"},
    "stars_60": {"bananas": 60, "stars": 350, "emoji": "🍌"},
    "stars_120": {"bananas": 120, "stars": 650, "emoji": "🍌"},
}

# Fiat Crypto Pay — см. BANANA_PACKAGES_DEFAULT ниже и config.BANANA_PACKAGES (.env).
BANANA_PACKAGES_DEFAULT = {
    "s": {"bananas": 10, "usdt": "0.1"},
    "m": {"bananas": 44, "usdt": "4.49"},
    "l": {"bananas": 140, "usdt": "9.99"},
    "xl": {"bananas": 340, "usdt": "21.99"},
    "xxl": {"bananas": 832, "usdt": "49.99"},
}