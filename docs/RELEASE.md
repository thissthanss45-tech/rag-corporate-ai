# Release / Rollback

## Staging deploy

```bash
docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d --build
python scripts/smoke_checks.py --mode staging
```

## Production deploy

```bash
export BOT_IMAGE_TAG=release-YYYYMMDD-HHMM
docker compose up -d --build
python scripts/smoke_checks.py --mode release
```

## Rollback

```bash
bash scripts/rollback.sh <previous_image_tag>
```

После rollback проверь:

```bash
docker compose ps
docker compose logs -f rag-bot
python scripts/healthcheck.py
```
