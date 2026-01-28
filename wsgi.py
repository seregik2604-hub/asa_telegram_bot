from main import app

if __name__ == "__main__":
    app.run()
```

**5. Нажми "Commit changes"**

---

## Потом измени Procfile:

**1. Открой Procfile**

**2. Нажми карандаш (Edit)**

**3. Замени на:**
```
web: gunicorn wsgi:app
