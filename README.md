# Garmin AI - שלד פרויקט (שלב 1: תשתית)

זהו הבסיס הראשוני של הפרויקט: FastAPI + PostgreSQL + Redis, הכל רץ דרך Docker Compose.
בשלב הזה אין עדיין auth, garmin sync, או AI - רק מוודאים שכל השירותים "מדברים" אחד עם השני.

## איך מריצים

1. ודא ש-Docker ו-Docker Compose מותקנים אצלך (`docker --version`).
2. העתק את קובץ הדוגמה למשתני הסביבה:
   ```bash
   cp .env.example .env
   ```
3. הרם את כל השירותים:
   ```bash
   docker compose up --build
   ```
4. פתח בדפדפן (או `curl`):
   ```
   http://localhost:8000/health
   ```
   אמור להחזיר: `{"status": "ok", "database": "connected", "redis": "connected"}`

5. תיעוד ה-API האוטומטי של FastAPI (Swagger UI) זמין ב:
   ```
   http://localhost:8000/docs
   ```

## מבנה התיקיות

```
garmin-ai/
  backend/
    app/
      main.py          # נקודת הכניסה - יצירת ה-FastAPI app וה-endpoints
      core/
        config.py       # קריאת משתני סביבה (Settings) עם pydantic-settings
      db/
        session.py       # חיבור ל-Postgres דרך SQLModel
    requirements.txt     # תלויות פייתון
    Dockerfile            # איך בונים את קונטיינר ה-backend
  docker-compose.yml       # מגדיר את שלושת השירותים: api, db, redis
  .env.example              # דוגמה למשתני סביבה נדרשים
  .gitignore
```

## מה הלאה (שלב 2)

הוספת auth: מודל `User` ב-SQLModel, endpoints של הרשמה/login, hashing עם bcrypt,
אימות מייל, והנפקת JWT.
