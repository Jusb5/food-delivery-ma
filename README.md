# Сервис доставки еды из ресторанов

Проект практических работ 9 и 10. В основе восемь сервисов и два набора тестов из ПР7-8.

## ПР9. Сборка и тесты

Из папки проекта при работающем Docker Desktop:

```powershell
docker compose build orders notifications profiles catalog kitchen delivery payments reviews
docker compose up -d --wait added-db rabbitmq
docker compose run --rm --no-deps -T ci-tests
pwsh -File ci/check_delivery.ps1
```

Первые три команды соответствуют шагам CI в `.github/workflows/ci-cd.yml`.
Тесты выполняются в Python 3.12 внутри контейнера с доступом к PostgreSQL и RabbitMQ.
На локальном стенде четвёртая команда публикует образы в Registry на 127.0.0.1:15000,
загружает образ NotificationService по digest и запускает его с `PORT=8080`.

### Внешняя доставка

Ручной запуск workflow с параметром `publish=true` из основной ветки запускает
два задания после успешного CI. OrderService отправляется в Docker Hub,
NotificationService - в Yandex Container Registry и затем в закрытый Serverless Container.
Оба образа берутся из артефакта CI и маркируются SHA коммита.

Секреты репозитория:

- `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` с правом записи в `food-orders`.
- `YC_KEYS` (авторизованный JSON-ключ учётной записи конвейера), `YC_REGISTRY_ID`,
  `YC_FOLDER_ID`, `YC_SA_ID` (учётная запись ревизии).

Переменные репозитория:

- `YC_CONTAINER_NAME` - учебный контейнер с фамилией в имени.
- `YC_NETWORK_ID` - сеть, из которой доступна внешняя PostgreSQL.
- `YC_DB_SECRET_ID`, `YC_DB_SECRET_VERSION` - Lockbox с ключом `database_url`.

Учётная запись конвейера должна иметь права загрузки образа, развёртывания
контейнера и использования учётной записи ревизии. Ревизии нужны права pull
образа и чтения секрета Lockbox. В облаке запускается HTTP-процесс
NotificationService. Для приёма событий потребитель RabbitMQ должен работать
отдельным постоянным процессом с доступом к той же базе уведомлений.

Фактические результаты в `evidence/` относятся к локальному стенду.
Запуск GitHub Actions, публикация в Docker Hub и ревизия Yandex Cloud требуют учётных записей и ресурсов.

## ПР10. Метрики и трассировка

```powershell
docker compose -f compose.yml -f compose.observability.yml up -d --build
python -m labs.observability_check
```

- Prometheus: http://127.0.0.1:19090/targets
- Grafana: http://127.0.0.1:13000/d/food-delivery-http/food-delivery-http
- Jaeger: http://127.0.0.1:16686

Проверка создаёт шесть учебных заказов, отправляет запрос на отсутствующий маршрут
и сверяет результаты с API Prometheus и Jaeger. Результаты сохраняются в
`evidence/observability.json` и `evidence/jaeger-trace.json`. Jaeger хранит данные
в памяти контейнера. Трассировка охватывает HTTP между сервисами.

Если стенд ПР5-8 занимает те же локальные порты, остановите его перед запуском
ПР9-10. Остановить ПР9-10 с сохранением данных:

```powershell
docker compose -f compose.yml -f compose.observability.yml -f compose.registry.yml stop
```
