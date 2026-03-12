# 🚀 Как задеплоить Athlete App на Railway
## Время: ~20 минут | Сложность: Просто следуй шагам

---

## ШАГ 1 — Установи нужные программы

### Mac:
Открой Terminal (Cmd+Space → "Terminal") и вставь по одной команде:

```bash
# Установи Homebrew (менеджер пакетов)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Установи Git и Python
brew install git python3
```

### Windows:
1. Скачай Git: https://git-scm.com/download/win → установи
2. Скачай Python: https://python.org/downloads → установи (✅ Add to PATH!)
3. Открой "Git Bash" (появится после установки Git)

---

## ШАГ 2 — Создай аккаунт на GitHub

1. Зайди на https://github.com
2. Sign up → введи email, пароль, username
3. Подтверди email

---

## ШАГ 3 — Загрузи код на GitHub

Открой Terminal/Git Bash в папке с проектом:

```bash
# Зайди в папку проекта
cd athlete-app

# Инициализируй Git репозиторий
git init
git add .
git commit -m "Initial commit"

# Создай репозиторий на GitHub (замени YOUR_USERNAME)
# Сначала создай репо на github.com → New repository → "athlete-app" → Create
git remote add origin https://github.com/YOUR_USERNAME/athlete-app.git
git push -u origin main
```

---

## ШАГ 4 — Создай аккаунт на Railway

1. Зайди на https://railway.app
2. "Login with GitHub" → разреши доступ
3. Это автоматически свяжет Railway с твоим GitHub

---

## ШАГ 5 — Задеплой на Railway

1. На Railway нажми **"New Project"**
2. **"Deploy from GitHub repo"**
3. Выбери **"athlete-app"**
4. Railway автоматически определит Python проект и задеплоит!

### Добавь переменную окружения (важно!):
В Railway → твой проект → **Variables** → Add:
```
DB_PATH = /data/athlete.db
```

### Добавь Volume (для базы данных):
Railway → проект → **Volumes** → Add Volume:
- Mount path: `/data`
- Это сохранит данные между перезапусками

5. Нажми **"Deploy"** — готово!

---

## ШАГ 6 — Получи свой URL

Railway → проект → **Settings** → **Domains** → **Generate Domain**

Ты получишь URL вида: `https://athlete-app-production-xxxx.up.railway.app`

**Открой этот URL на телефоне!** 🎉

---

## ШАГ 7 — Добавь на домашний экран iPhone (PWA)

1. Открой URL в Safari на iPhone
2. Нажми кнопку "Поделиться" (квадрат со стрелкой вверх)
3. "На экран «Домой»"
4. "Добавить"

Теперь приложение выглядит как нативное! 📱

---

## ШАГ 8 — Подключи Garmin

В приложении → Прогресс → Garmin:
1. Введи email и пароль от Garmin Connect
2. Нажми "Подключить"
3. Нажми "🔄 Синхронизировать"

Тренировки за последние 14 дней появятся автоматически!

---

## Локальный запуск (для проверки)

Если хочешь сначала проверить на компьютере:

```bash
# Установи зависимости
cd backend
pip3 install -r requirements.txt

# Запусти сервер
python3 main.py

# Открой в браузере:
# http://localhost:8000
```

---

## Обновление приложения

Когда я пришлю новую версию файлов:

```bash
git add .
git commit -m "Update app"
git push
```

Railway автоматически передеплоит! ✨

---

## Часто задаваемые вопросы

**Q: Garmin говорит "Authentication failed"**
A: Garmin иногда блокирует автологин. Попробуй через пару минут или
   зайди в Garmin Connect вручную сначала, потом синхронизируй.

**Q: Данные пропали после перезапуска**
A: Убедись что добавил Volume в Railway (Шаг 5).

**Q: Сколько стоит Railway?**
A: $5/месяц — план Hobby. Включает 8GB RAM, 100GB storage.
   Достаточно для личного приложения с запасом.

**Q: Могу ли я использовать с разных устройств?**
A: Да! URL один, данные общие. Телефон + компьютер + планшет.
