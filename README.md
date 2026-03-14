# AutoNickname5

Модульный Discord-бот для регистрации участников.

## Что умеет
- регистрация через кнопку и modal
- формат ника по шаблону, например `{login} ({name})`
- отдельное хранение `discord_login`, `discord_display_name`, `registered_name`, `final_nickname`
- история изменений имени
- `/check_name` для предпросмотра итогового ника
- улучшенная нормализация имени
- антимат именно для поля имени
- whitelist / blacklist через config
- `/system_check`
- скрытая `/admin_panel`
- умное восстановление из БД
- защита от двойного нажатия
- restart-safe панель регистрации
- backup db/config/badwords

## Запуск
1. Переименуй `.env.example` в `.env`
2. Вставь токен бота
3. `pip install -r requirements.txt`
4. `python bot.py`

## Основные команды
- `/register_panel`
- `/admin_panel`
- `/system_check`
- `/check_name name:<имя> [user:@пользователь]`
- `/restore_user user:@пользователь`
- `/name_history user:@пользователь`
- `/set_nick_format template:<шаблон>`
- `/refresh_user_nick user:@пользователь`
- `/backup_now`
