#!/usr/bin/env bash
# Запуск: один сервер віддає і інтерфейс, і API.
set -e
pip install -r requirements.txt
echo ""
echo "Відкрийте у браузері:  http://localhost:8000"
echo ""
uvicorn api.main:app --reload
