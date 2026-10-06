"""
Скрипт для обновления токена Modeus через GitHub Actions.
Запускается каждые 30 минут, обновляет токен в Vercel.
"""

import os
import json
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

MODEUS_URL = "https://urfu.modeus.org/schedule-calendar/my"
VERCEL_API = "https://api.vercel.com/v9/projects"
VERCEL_TOKEN = os.environ.get("VERCEL_TOKEN")
PROJECT_ID = os.environ.get("PROJECT_ID")


def get_modeus_token(username: str, password: str) -> str:
    """Получение токена через Selenium."""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--ignore-certificate-errors")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    
    try:
        driver.get(MODEUS_URL)
        
        # Вводим логин и пароль
        username_field = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "username"))
        )
        username_field.send_keys(username)
        
        password_field = driver.find_element(By.NAME, "password")
        password_field.send_keys(password)
        
        driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
        
        # Ждём получения токена
        import time
        time.sleep(10)
        
        # Получаем токен из localStorage
        token = driver.execute_script("return localStorage.getItem('id_token');")
        return token
    finally:
        driver.quit()


def update_vercel_env(token: str):
    """Обновление переменной окружения в Vercel."""
    headers = {
        "Authorization": f"Bearer {VERCEL_TOKEN}",
        "Content-Type": "application/json",
    }
    
    data = {
        "key": "MODEUS_TOKEN",
        "value": token,
        "target": ["production"],
    }
    
    response = requests.patch(
        f"{VERCEL_API}/{PROJECT_ID}/env",
        headers=headers,
        json=data,
    )
    
    if response.status_code == 200:
        print("✅ Токен обновлён в Vercel")
    else:
        print(f"❌ Ошибка: {response.status_code}")


if __name__ == "__main__":
    username = os.environ.get("URFU_USERNAME")
    password = os.environ.get("URFU_PASSWORD")
    
    if not username or not password:
        print("❌ Укажите URFU_USERNAME и URFU_PASSWORD")
        exit(1)
    
    token = get_modeus_token(username, password)
    if token:
        update_vercel_env(token)
    else:
        print("❌ Не удалось получить токен")
