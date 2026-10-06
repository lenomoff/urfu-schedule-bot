"""Обновляет id_token УрФУ. Токен — в stdout, диагностика — в stderr и файл."""

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
DUMP = "modeus_debug.html"

# Селекторы проверены по фактической разметке ADFS УрФУ:
#   userNameInput|UserName|email , passwordInput|Password|password
USER_FIELDS = [
    (By.CSS_SELECTOR, "input#userNameInput"),
    (By.CSS_SELECTOR, "input[name='UserName']"),
    (By.CSS_SELECTOR, "input#txtUserName"),
    (By.CSS_SELECTOR, "input[type='email']"),
]
PASS_FIELDS = [
    (By.CSS_SELECTOR, "input#passwordInput"),
    (By.CSS_SELECTOR, "input[name='Password']"),
    (By.CSS_SELECTOR, "input[type='password']"),
]
SUBMIT = [
    (By.CSS_SELECTOR, "input#submitButton"),
    (By.CSS_SELECTOR, "input[name='submitButton']"),
    (By.CSS_SELECTOR, "button[type='submit']"),
    (By.XPATH, "//input[@type='submit' and @value='Sign in']"),
]


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def build():
    o = Options()
    o.add_argument("--headless=new")
    o.add_argument("--no-sandbox")
    o.add_argument("--disable-dev-shm-usage")
    o.add_argument("--disable-gpu")
    o.add_argument("--ignore-certificate-errors")
    o.add_argument("--window-size=1920,1080")
    o.add_argument("--lang=ru-RU")
    o.add_argument("--disable-blink-features=AutomationControlled")
    o.add_experimental_option("excludeSwitches", ["enable-automation"])
    o.add_experimental_option("useAutomationExtension", False)
    return webdriver.Chrome(options=o)


def read_token(driver):
    try:
        return driver.execute_script("return localStorage.getItem('id_token');")
    except Exception:
        return None


def wait_token(driver, seconds, label):
    """Поллит localStorage: после OAuth-редиректа SPA ставит токен не мгновенно."""
    for i in range(seconds * 2):
        token = read_token(driver)
        if token:
            log(f"Токен получен ({label}), жд {i // 2} с")
            return token
        time.sleep(0.5)
    return None


def ready(driver, timeout=45):
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass


def probe(driver):
    log("--- ДИАГНОСТИКА ---")
    log(f"url  = {driver.current_url[:160]}")
    log(f"title= {driver.title[:160]}")
    try:
        log(f"iframes = {len(driver.find_elements(By.TAG_NAME, 'iframe'))}")
        inputs = driver.execute_script(
            "return Array.from(document.querySelectorAll('input'))"
            ".map(e => e.id + '|' + e.name + '|' + e.type)"
        )
        log(f"inputs = {inputs[:20]}")
        text = driver.execute_script("return document.body ? document.body.innerText : ''")
        log(f"text  = {text[:600]!r}")
    except Exception as e:
        log(f"probe не удался: {e}")
    try:
        with open(DUMP, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        log(f"HTML сохранён в {DUMP}")
    except OSError as e:
        log(f"HTML не сохранился: {e}")


def locate(driver, options, timeout=30):
    for by, value in options:
        try:
            el = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((by, value))
            )
            if el.is_displayed():
                log(f"нашёл поле: {value}")
                return el
        except Exception:
            continue
    # запасной путь: те же поля внутри iframe
    try:
        for frame in driver.find_elements(By.TAG_NAME, "iframe"):
            driver.switch_to.frame(frame)
            for by, value in options:
                try:
                    el = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((by, value))
                    )
                    if el.is_displayed():
                        log(f"нашёл поле в iframe: {value}")
                        return el
                except Exception:
                    continue
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:
            pass
    return None


def run() -> str | None:
    driver = build()
    try:
        driver.get(URL)
        ready(driver)

        # 1. Возможно, сессия уже есть — токен лежит в localStorage
        token = wait_token(driver, 25, "без логина")
        if token:
            return token

        log("Авторизации нет, ищу форму входа…")

        # 2. Форма входа УрФУ (ADFS)
        user = locate(driver, USER_FIELDS)
        if user is None:
            probe(driver)
            return None
        user.clear()
        user.send_keys(USERNAME)

        pwd = locate(driver, PASS_FIELDS, timeout=15)
        if pwd is None:
            probe(driver)
            return None
        pwd.clear()
        pwd.send_keys(PASSWORD)

        submit = locate(driver, SUBMIT, timeout=15)
        if submit is not None:
            submit.click()
        else:
            driver.switch_to.active_element.submit()
        log("Вход отправлен")

        # 3. После редиректа обратно в Modeus дожидаемся токена
        token = wait_token(driver, 60, "после логина")
        if token:
            return token

        probe(driver)
        return None
    finally:
        driver.quit()


if __name__ == "__main__":
    result = run()
    if result:
        print(result)
    else:
        sys.exit(1)
