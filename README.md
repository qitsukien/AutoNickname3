# AutoNickname4 Configured

Готовый Discord-бот для регистрации участников.

Что внутри:
- регистрация через кнопку и modal
- SQLite база пользователей
- восстановление из БД кнопкой и командой
- persistent view после рестарта
- slash-команды администратора
- история имён
- автобэкап в ZIP
- антимат и нормализация имени
- автовосстановление при повторном входе

## Быстрый запуск

1. Установи зависимости:
```bash
pip install -r requirements.txt
```

2. Переименуй `.env.example` в `.env`
3. Вставь токен бота в `.env`
4. Проверь `config.json`
5. Запусти:
```bash
python bot.py
```

## Нужные права бота

- Manage Nicknames
- Manage Roles
- Send Messages
- Embed Links
- Use Application Commands
- Read Message History
- View Channels

## Команды

- `/register_panel` — отправить или обновить панель регистрации
- `/system_check` — проверка ролей, каналов, прав и БД
- `/restore_user user:@user` — восстановить пользователя из БД
- `/delete_user user:@user` — удалить пользователя из БД
- `/name_history user:@user` — история имён
- `/export_users` — экспорт регистраций в CSV
- `/backup_now` — создать backup вручную

## Важно

Роль бота должна быть выше ролей незарегистрированного и участника.
