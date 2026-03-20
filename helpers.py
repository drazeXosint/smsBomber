import random
import string
import uuid
import time


# ---------------------------------------------------------------------------
# Real browser user agents — rotated per request
# ---------------------------------------------------------------------------

USER_AGENTS = [
    # Chrome Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    # Chrome Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    # Firefox
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) Gecko/20100101 Firefox/120.0",
    # Safari
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    # Mobile Chrome Android
    "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Redmi Note 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    # Mobile Safari iOS
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
]

SEC_CH_UA_LIST = [
    '"Not:A-Brand";v="99", "Chromium";v="120", "Google Chrome";v="120"',
    '"Not:A-Brand";v="99", "Chromium";v="122", "Google Chrome";v="122"',
    '"Not:A-Brand";v="99", "Chromium";v="124", "Google Chrome";v="124"',
    '"Not:A-Brand";v="99", "Chromium";v="145", "Google Chrome";v="145"',
    '"Not:A-Brand";v="99", "Brave";v="120", "Chromium";v="120"',
    '"Not:A-Brand";v="99", "Brave";v="122", "Chromium";v="122"',
    '"Not:A-Brand";v="99", "Brave";v="145", "Chromium";v="145"',
    '"Not:A-Brand";v="8", "Chromium";v="120", "Microsoft Edge";v="120"',
]

PLATFORMS = ['"Windows"', '"macOS"', '"Linux"', '"Android"']

ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-US,en;q=0.8",
    "en-GB,en;q=0.9",
    "en-IN,en;q=0.9,hi;q=0.8",
    "en-US,en;q=0.7",
    "en-US,en;q=0.9,hi;q=0.8",
]


def randomUserAgent() -> str:
    return random.choice(USER_AGENTS)

def randomSecChUa() -> str:
    return random.choice(SEC_CH_UA_LIST)

def randomPlatform() -> str:
    return random.choice(PLATFORMS)

def randomAcceptLanguage() -> str:
    return random.choice(ACCEPT_LANGUAGES)

def randomDeviceId() -> str:
    return uuid.uuid4().hex

def randomSessionId() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=32))

def randomEmail() -> str:
    names  = ["user", "test", "info", "hello", "contact", "mail", "dev"]
    domains = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "proton.me"]
    name   = random.choice(names) + str(random.randint(100, 9999))
    return f"{name}@{random.choice(domains)}"

def randomName() -> str:
    firsts = ["Rahul", "Amit", "Priya", "Anjali", "Vikram", "Neha", "Suresh", "Pooja",
              "Rajesh", "Kavya", "Arjun", "Deepa", "Sanjay", "Meera", "Arun", "Divya"]
    lasts  = ["Kumar", "Singh", "Sharma", "Patel", "Gupta", "Nair", "Reddy", "Joshi",
              "Mishra", "Rao", "Iyer", "Menon", "Shah", "Verma", "Tiwari", "Yadav"]
    return f"{random.choice(firsts)} {random.choice(lasts)}"

def randomPassword() -> str:
    chars = string.ascii_letters + string.digits + "!@#$"
    return "".join(random.choices(chars, k=random.randint(10, 14)))


def injectRotatedHeaders(headers: dict) -> dict:
    """
    Inject fresh randomized fingerprint into headers.
    Replaces user-agent, device IDs, sec-ch-ua etc on every call.
    Only replaces — never adds fields that weren't in the original config.
    """
    if not headers:
        return headers

    result = dict(headers)
    ua     = randomUserAgent()

    # Replace user-agent wherever it appears (case insensitive key)
    for key in list(result.keys()):
        kl = key.lower()
        if kl == "user-agent":
            result[key] = ua
        elif kl == "sec-ch-ua":
            result[key] = randomSecChUa()
        elif kl == "sec-ch-ua-platform":
            result[key] = randomPlatform()
        elif kl == "accept-language":
            result[key] = randomAcceptLanguage()
        elif kl in ("x-device-id", "deviceid", "device_id", "device-id"):
            result[key] = randomDeviceId()
        elif kl in ("x-session-id", "session-id", "sessionid"):
            result[key] = randomSessionId()
        elif kl in ("x-request-id", "x-correlation-id", "request-id"):
            result[key] = str(uuid.uuid4())

    return result


def replacePlaceholders(obj, phone: str):
    """Replace all placeholders in API config with fresh random values per call."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {k: replacePlaceholders(v, phone) for k, v in obj.items()}
    if isinstance(obj, list):
        return [replacePlaceholders(v, phone) for v in obj]
    if isinstance(obj, str):
        obj = obj.replace("{phone}", phone)
        obj = obj.replace("{uuid}", str(uuid.uuid4()))
        obj = obj.replace("{device_id}", randomDeviceId())
        obj = obj.replace("{session_id}", randomSessionId())
        if "{random_email}" in obj:
            obj = randomEmail()
        if "{random_name}" in obj:
            obj = randomName()
        if "{random_password}" in obj:
            obj = randomPassword()
        if "{timestamp}" in obj:
            obj = obj.replace("{timestamp}", str(int(time.time() * 1000)))
        return obj
    return obj