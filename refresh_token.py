"""Токен УрФУ из кэша; протух — логин через Selenium. Токен в stdout, логи в stderr."""

import os
import re
import sys
import time
from urllib.parse import parse_qs, urlparse

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

URL = "https://urfu.modeus.org/schedule-calendar/my"
USERNAME = os.environ["URFU_USERNAME"]
PASSWORD = os.environ["URFU_PASSWORD"]
CACHE = "token.cache"
DUMP = "modeus_debug.html"
MAX_AGE = 25 * 60  # кэш считаем свежим 25 минут

# Селекторы проверены по разметке ADFS УрФУ
USER_FIELDS = [
    (By.CSS_SELECTOR, "input#userNameInput"),
    (By.CSS_SELECTOR, "input[name='UserName']"),
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
]

JWT_RE = re.compile(r"^[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}$")


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def read_cache():
    try:
        if time.time() - os.stat(CACHE).st_mtime > MAX_AGE:
            log("Кэш протух")
            return None
        with open(CACHE, encoding="utf-8") as f:
            token = f.read().strip()
        return token if JWT_RE.match(token) else None
    except (OSError, ValueError):
        return None


def write_cache(token):
    with open(CACHE, "w", encoding="utf-8") as f:
        f.write(token)


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


def dump_storage(driver):
    try:
        return driver.execute_script("""
            const out = {};
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                out['local:' + k] = localStorage.getItem(k);
            }
            for (let i = 0; i < sessionStorage.length; i++) {
                const k = sessionStorage.key(i);
                out['session:' + k] = sessionStorage.getItem(k);
            }
            return out;
        """) or {}
    except Exception:
        return {}


def token_from_url(driver):
    try:
        frag = driver.execute_script("return window.location.hash;") or ""
    except Exception:
        return None
    if not frag:
        return None
    qs = parse_qs(urlparse("http://x" + frag).query)
    for key in ("id_token", "access_token", "token"):
        value = (qs.get(key) or [None])[0]
        if value and JWT_RE.match(value):
            log(f"Токен во фрагменте URL: {key}")
            return value
    return None


def find_token(driver):
    for name, value in dump_storage(driver).items():
        if isinstance(value, str) and JWT_RE.match(value.strip()):
            log(f"Токен в {name} (длина {len(value)})")
            return value.strip()
    return None


def wait_token(driver, seconds, label):
    for i in range(seconds * 2):
        token = token_from_url(driver) or find_token(driver)
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
    log(f"url  = {driver.current_url[:200]}")
    log(f"title= {driver.title[:160]}")
    try:
        storage = dump_storage(driver)
        log(f"storage keys = {[(k, len(str(v))) for k, v in storage.items()]}")
        text = driver.execute_script("return document.body ? document.body.innerText : ''")
        log(f"text  = {text[:400]!r}")
    except Exception as e:
        log(f"probe не удался: {e}")
    try:
        with open(DUMP, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except OSError:
        pass


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


def login():
    driver = build()
    try:
        driver.get(URL)
        ready(driver)

        token = wait_token(driver, 20, "без логина")
        if token:
            return token

        log("Авторизации нет, ищу форму входа…")
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

        token = wait_token(driver, 60, "после логина")
        if token:
            return token

        probe(driver)
        return None
    finally:
        driver.quit()


if __name__ == "__main__":
    token = read_cache()
    if token:
        log("Взял токен из кэша")
    else:
        token = login()
        if token:
            try:
                write_cache(token)
            except OSError as e:
                log(f"Кэш не записан: {e}")
    if token:
        print(token)
    else:
        sys.exit(1)
