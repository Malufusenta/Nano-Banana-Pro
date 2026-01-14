-- Добавляем колонку first_generation_done в таблицу users
ALTER TABLE users 
ADD COLUMN IF NOT EXISTS first_generation_done BOOLEAN DEFAULT FALSE;

-- Для существующих пользователей ставим FALSE (по умолчанию)
UPDATE users 
SET first_generation_done = FALSE 
WHERE first_generation_done IS NULL;