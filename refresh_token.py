"""Обновляет id_token УрФУ. Токен печатает в stdout, диагностику — в stderr и файл."""

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

USER_FIELDS = [
    (By.CSS_SELECTOR, "input#username"),
    (By.CSS_SELECTOR, "input[name='username']"),
    (By.CSS_SELECTOR, "input#userName"),
    (By.CSS_SELECTOR, "input[type='text']"),
    (By.CSS_SELECTOR, "input[type='email']"),
]
PASS_FIELDS = [
    (By.CSS_SELECTOR, "input#password"),
    (By.CSS_SELECTOR, "input[name='password']"),
    (By.CSS_SELECTOR, "input[type='password']"),
]
SUBMIT = [
    (By.CSS_SELECTOR, "input#submitButton"),
    (By.CSS_SELECTOR, "input[type='submit']"),
    (By.CSS_SELECTOR, "button[type='submit']"),
    (By.CSS_SELECTOR, "button[name='submit']"),
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


def ready(driver, timeout=45):
    """Ждём полной отрисовки страницы."""
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass


def probe(driver):
    """Печатает, что реально есть на странице."""
    log("--- ДИАГНОСТИКА ---")
    log(f"url  = {driver.current_url[:160]}")
    log(f"title= {driver.title[:160]}")
    try:
        frames = len(driver.find_elements(By.TAG_NAME, "iframe"))
        log(f"iframes = {frames}")
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


def locate(driver, options, timeout=45):
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
        log("Загрузил Modeus, жду форму входа УрФУ…")

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
        log("Вход отправлен, жду токен…")

        for _ in range(45):
            try:
                if driver.execute_script("return document.readyState") == "complete":
                    token = driver.execute_script("return localStorage.getItem('id_token');")
                    if token:
                        return token
            except Exception:
                pass
            time.sleep(2)

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
