"""Обновляет id_token УрФУ и печатает его в stdout (логи — в stderr)."""

import os
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

URL = "https://urfu.modeus.org/schedule-calendar/my"
USERNAME = os.environ["URFU_USERNAME"]
PASSWORD = os.environ["URFU_PASSWORD"]

USER_FIELDS = [(By.ID, "username"), (By.NAME, "username"), (By.CSS_SELECTOR, "input[type='text']")]
PASS_FIELDS = [(By.ID, "password"), (By.NAME, "password"),
               (By.CSS_SELECTOR, "input[type='password']")]
SUBMIT = [(By.ID, "submitButton"), (By.CSS_SELECTOR, "input[type='submit']"),
          (By.CSS_SELECTOR, "button[type='submit']")]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def find(driver, options, timeout=25):
    for by, value in options:
        try:
            return WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((by, value))
            )
        except Exception:
            continue
    return None


def run() -> str | None:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--ignore-certificate-errors")
    opts.add_argument("--window-size=1920,1080")
    # Selenium 4.6+ сам подбирает chromedriver под установленный Chrome

    driver = webdriver.Chrome(options=opts)
    try:
        driver.get(URL)
        log("Загрузил Modeus, жду форму входа УрФУ…")

        field = find(driver, USER_FIELDS)
        if field is None:
            log(f"Поле логина не найдено. url={driver.current_url[:120]}")
            return None
        field.clear()
        field.send_keys(USERNAME)

        field = find(driver, PASS_FIELDS)
        if field is None:
            log("Поле пароля не найдено")
            return None
        field.clear()
        field.send_keys(PASSWORD)

        button = find(driver, SUBMIT)
        if button is not None:
            button.click()
        else:
            driver.switch_to.active_element.submit()
        log("Вход отправлен, жду токен…")

        for _ in range(30):
            try:
                token = driver.execute_script("return localStorage.getItem('id_token');")
            except Exception:
                token = None
            if token:
                return token
            time.sleep(2)

        log(f"Токен не получен. url={driver.current_url[:120]}")
        return None
    finally:
        driver.quit()


if __name__ == "__main__":
    result = run()
    if result:
        print(result)  # в stdout — так workflow захватит только токен
    else:
        sys.exit(1)
