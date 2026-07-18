# AI Career Agent: запуск мониторинга

## Локальная проверка

1. Создайте виртуальное окружение Python 3.12 и установите `requirements.txt`.
   Для разработки и тестов используйте `requirements-dev.txt`.
2. Скопируйте `.env.example` в `.env` и заполните `TELEGRAM_TOKEN` и
   `OPENAI_API_KEY`.
3. Запустите `python main.py`. Без `DATABASE_URL` используется локальная SQLite.
4. В Telegram выполните `/start`, затем `/health` и `/digest`.

## Railway

1. Добавьте PostgreSQL к Railway-проекту. Railway создаст `DATABASE_URL`.
2. Перенесите переменные из `.env.example` в Railway Variables. Не добавляйте
   реальные секреты в Git.
3. Зарегистрируйте приложение на `dev.hh.ru` и добавьте `HH_CLIENT_ID` и
   `HH_CLIENT_SECRET`: бот будет получать application token автоматически.
   Альтернатива — готовый `HH_ACCESS_TOKEN`. Анонимный поиск может возвращать
   captcha/403.
4. Первый деплой оставьте с `SHADOW_MODE=true`. Монитор будет собирать и
   ранжировать вакансии, но автоматический вечерний дайджест не отправится.
5. Выполните `/start`, чтобы бот сохранил chat ID.
6. После проверки `/health` и ручного `/digest` установите
   `SHADOW_MODE=false`.

## LinkedIn через Gmail

1. Создайте Gmail OAuth client с read-only scope.
2. Настройте LinkedIn Job Alerts и Gmail-метку `LinkedIn-Jobs`.
3. Добавьте `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` и
   включите `GMAIL_ENABLED=true`.

## Telegram-каналы

1. Создайте отдельный Telegram-аккаунт, получите `api_id` и `api_hash` на
   `my.telegram.org`, сформируйте Telethon StringSession.
2. Добавьте секреты в Railway и включите `TELEGRAM_SOURCES_ENABLED=true`.
3. Добавьте каналы командами `/source_add @channel` и проверьте `/sources`.

## Команды

- `/health` — состояние сервисов;
- `/digest` — ручная подборка из уже собранных вакансий;
- `/sources` — активные Telegram-каналы;
- `/source_add @channel` — добавить канал;
- `/source_remove @channel` — отключить канал.
