import asyncio
import json
import re
import requests
from datetime import datetime
import pytz
from playwright.async_api import async_playwright

AUTHOR_URL = "https://data.eastmoney.com/report/personalpublish.jshtml?authorid=11000254529"
MAILEROO_API = "https://smtp.maileroo.com/api/v2/emails"

# ========== 工具函数 ==========
def bj_now():
    tz = pytz.timezone("Asia/Shanghai")
    return datetime.now(tz)

def format_date(dt):
    return dt.strftime("%Y-%m-%d")

def format_datetime(dt):
    return dt.strftime("%Y-%m-%d %H:%M")

# ========== 内联 stealth ==========
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
"""

# ========== 获取文章 ==========
async def fetch_articles():
    print("▶ 启动 Playwright...")
    articles = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_init_script(STEALTH_JS)
        page = await context.new_page()

        print("▶ 打开作者页面...")
        await page.goto(AUTHOR_URL, timeout=60000)
        await page.wait_for_timeout(5000)

        html = await page.content()

        print("▶ 解析 initdata JSON...")
        match = re.search(r"var initdata = (\{.*?\});", html, re.S)
        if not match:
            print("❌ 未找到 initdata")
            return []

        data = json.loads(match.group(1))
        items = data.get("data", [])[:2]

        print(f"✅ 找到 {len(items)} 篇文章")

        for item in items:
            title = item["title"]
            author = item["researcher"]
            org = item["orgName"]
            publish_date = item["publishDate"].split(" ")[0]
            info_code = item["infoCode"]

            detail_url = f"https://data.eastmoney.com/report/info/{info_code}.html"

            print(f"  ➜ 获取正文: {title}")
            await page.goto(detail_url)
            await page.wait_for_timeout(5000)

            content_html = await page.content()
            content_match = re.search(r'<div class="ctx-content">(.*?)</div>', content_html, re.S)
            content = re.sub("<.*?>", "", content_match.group(1)).strip() if content_match else "（未能获取正文）"

            articles.append({
                "title": title,
                "author": author,
                "org": org,
                "date": publish_date,
                "content": content[:5000]
            })

        await browser.close()

    return articles

# ========== 构建邮件 HTML ==========
def build_email(articles):
    now = bj_now()
    today_str = format_date(now)
    updated_str = format_datetime(now)

    header_style = """
    background: linear-gradient(90deg,#0F172A,#1E293B);
    color:white;
    padding:20px;
    text-align:center;
    """

    footer_style = """
    background:#0F172A;
    color:white;
    padding:20px;
    text-align:center;
    font-size:12px;
    """

    body = ""

    if not articles:
        body += "<p style='text-align:center;font-size:18px;'>💤 No articles today.</p>"
    else:
        for art in articles:
            body += f"""
            <div style="margin-bottom:40px;">
                <h2>{art['title']}</h2>
                <p>✍️ {art['author']}<br>
                🏢 {art['org']}<br>
                📅 {art['date']}</p>
                <p style="line-height:1.6;white-space:pre-wrap;">{art['content']}</p>
            </div>
            """

    html = f"""
    <html>
    <body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto;">
        <div style="{header_style}">
            <h1>🌿 策略研究</h1>
            <p>{today_str}</p>
        </div>

        <div style="padding:20px;">
            {body}
        </div>

        <div style="{footer_style}">
            Updated at {updated_str} UTC+8
        </div>
    </body>
    </html>
    """

    subject = f"🌿 策略研究 - {today_str}"
    return subject, html

# ========== 发送邮件 ==========
def send_email(subject, html):
    import os

    api_key = os.environ.get("MAILEROO_API_KEY")
    mail_from = os.environ.get("MAIL_FROM")
    mail_to = os.environ.get("MAIL_TO")

    recipients = []
    for addr in mail_to.split(","):
        recipients.append({"address": addr.strip()})

    print("▶ 发送邮件...")

    payload = {
        "from": {
            "address": mail_from,
            "display_name": "Newsletter"
        },
        "to": recipients,
        "subject": subject,
        "html": html
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    response = requests.post(MAILEROO_API, headers=headers, json=payload)
    print("✅ 邮件发送状态:", response.status_code)
    print(response.text)

# ========== 主程序 ==========
async def main():
    print("========== 开始运行 ==========")
    print("当前北京时间:", bj_now())

    articles = await fetch_articles()

    subject, html = build_email(articles)

    send_email(subject, html)

    print("========== 运行结束 ==========")

if __name__ == "__main__":
    asyncio.run(main())
