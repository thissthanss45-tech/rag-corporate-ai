# Worker Service

Очереди и тяжёлая обработка документов:

- consume задач из RabbitMQ
- парсинг PDF/DOCX
- эмбеддинг + запись в Qdrant
- обновление статуса задач в Redis
