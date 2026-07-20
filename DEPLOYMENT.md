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

## Публичные Telegram-каналы без API ID

Основной источник использует публичные страницы `https://t.me/s/<channel>` и не
требует Telegram API-приложения или пользовательской сессии.

1. Добавьте в Railway `TELEGRAM_WEB_ENABLED=true`.
2. При необходимости переопределите список через `TELEGRAM_WEB_CHANNELS`, указав
   имена через запятую. По умолчанию подключаются восемь проверенных каналов:
   `forproducts`, `jobs_pm`, `careerstation_pm`, `remotegeekjob`, `remoteit`,
   `evacuatejobs`, `it_vakansii_jobs`, `careerspace`.
3. Первый запуск обрабатывает только публикации за последние 72 часа и не более
   пяти подходящих вакансий с каждого канала. Значения настраиваются переменными
   `TELEGRAM_WEB_LOOKBACK_HOURS` и `TELEGRAM_WEB_MAX_POSTS_PER_CHANNEL`.
4. Проверьте список командой `/sources`, состояние — командой `/health`.

Парсер пропускает резюме, публикации без целевой управленческой роли и вакансии
без явно указанного удалённого формата. Сбой одного канала не останавливает сбор
из остальных. MTProto-адаптер сохранён для будущего использования и включается
отдельно через `TELEGRAM_SOURCES_ENABLED=true` после получения API ID.

## Команды

- `/health` — состояние сервисов;
- `/digest` — ручная подборка из уже собранных вакансий;
- `/sources` — активные Telegram-каналы;
- `/source_add @channel` — добавить канал;
- `/source_remove @channel` — отключить канал.
