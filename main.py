import asyncio
import html as html_module
import json
import os
import re
from datetime import datetime, timedelta, timezone

BJ_TZ = timezone(timedelta(hours=8))

# ---------- 内联 Stealth 脚本 ----------
STEALTH_JS = """
(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
    Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
    Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
    Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
    if (!window.chrome) window.chrome = {};
    window.chrome.runtime = {};
    try {
        const gp = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(p) {
            if (p === 37445) return 'Google Inc. (Intel)';
            if (p === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 630, OpenGL 4.6)';
            return gp.call(this, p);
        };
    } catch(e) {}
    try {
        const gp2 = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function(p) {
            if (p === 37445) return 'Google Inc. (Intel)';
            if (p === 37446) return 'ANGLE (Intel, Intel(R) UHD Graphics 630, OpenGL 4.6)';
            return gp2.call(this, p);
        };
    } catch(e) {}
    try {
        const origQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
        window.navigator.permissions.query = (params) => (
            params.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : origQuery(params)
        );
    } catch(e) {}
    try {
        if (navigator.getBattery) {
            navigator.getBattery = () => Promise.resolve({
                charging: true, chargingTime: 0,
                dischargingTime: Infinity, level: 1
            });
        }
    } catch(e) {}
    try {
        Object.defineProperty(navigator, 'connection', {
            get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false })
        });
    } catch(e) {}
    try {
        for (const key of Object.keys(document)) {
            if (key.startsWith('cdc_')) delete document[key];
        }
    } catch(e) {}
})()
"""


def bj_now():
    return datetime.now(BJ_TZ)


# ========== 1. 抓取文章 ==========
async def fetch_articles():
    from playwright.async_api import async_playwright

    articles = []

    async with async_playwright() as p:
        print("  [1] 启动 Chromium 浏览器（headless）...")
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=AutomationControlled",
            ],
        )

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )

        # 内联注入 stealth 脚本
        await context.add_init_script(STEALTH_JS)
        print("  Stealth 脚本已注入")

        page = await context.new_page()

        # ------ 列表页 ------
        list_url = (
            "https://data.eastmoney.com/report/"
            "personalpublish.jshtml?authorid=11000254529"
        )
        print(f"  [2] 加载列表页: {list_url}")
        try:
            await page.goto(list_url, wait_until="domcontentloaded", timeout=60000)
            print("  页面 DOM 已加载")
        except Exception as e:
            print(f"  页面加载异常（仍尝试继续）: {e}")

        # 提取 initdata
        print("  [3] 提取文章列表数据...")
        initdata = None

        # 方式 A：直接读取 JS 全局变量
        try:
            await page.wait_for_function(
                "typeof initdata !== 'undefined'", timeout=20000
            )
            initdata = await page.evaluate("initdata")
            print("  ✔ 通过 JS 全局变量提取成功")
        except Exception as e:
            print(f"  JS 变量提取失败: {e}")
            # 方式 B：正则匹配页面源码
            print("  尝试从页面源码正则匹配...")
            src = await page.content()
            m = re.search(r"var initdata\s*=\s*(\{.*?\});\s*</script>", src, re.DOTALL)
            if m:
                try:
                    initdata = json.loads(m.group(1))
                    print("  ✔ 通过正则匹配提取成功")
                except json.JSONDecodeError as je:
                    print(f"  JSON 解析失败: {je}")
            else:
                print("  ✘ 未能匹配到 initdata")

        if not initdata or not initdata.get("data"):
            print("  未获取到文章数据，返回空列表")
            await browser.close()
            return []

        data_list = initdata["data"]
        total_hits = initdata.get("hits", len(data_list))
        print(f"  共 {total_hits} 篇，取最新 2 篇")

        latest_2 = data_list[:2]

        # ------ 逐篇抓正文 ------
        for idx, item in enumerate(latest_2):
            title = item.get("title", "无标题")
            authors_raw = item.get("author", [])
            authors = ", ".join(
                a.split(".")[-1] if "." in a else a for a in authors_raw
            )
            org_name = item.get("orgName", "未知机构")
            publish_date = item.get("publishDate", "未知日期").split(" ")[0]
            encode_url = item.get("encodeUrl", "")
            column_type = item.get("columnType", "")

            print(f"  --- 文章 {idx + 1} ---")
            print(f"    标题: {title}")
            print(f"    作者: {authors}")
            print(f"    机构: {org_name}")
            print(f"    日期: {publish_date}")
            print(f"    类型: {column_type}")
            print(f"    encodeUrl: {encode_url}")

            content_text = ""

            # 用 encodeUrl 构建详情页 URL
            if encode_url:
                detail_url = (
                    "https://data.eastmoney.com/report/"
                    f"zw_strategy.jshtml?encodeUrl={encode_url}"
                )
                print(f"    加载正文页: {detail_url}")

                try:
                    await page.goto(
                        detail_url, wait_until="domcontentloaded", timeout=60000
                    )
                    print("    详情页 DOM 已加载")

                    # 等待正文容器 #ctx-content 出现
                    try:
                        await page.wait_for_selector(
                            "#ctx-content", timeout=25000
                        )
                        print("    #ctx-content 元素已出现")
                    except Exception:
                        print("    #ctx-content 等待超时，仍尝试提取")

                    # 额外等待，确保动态内容加载完毕
                    await asyncio.sleep(3)

                    # 提取正文文本
                    content_text = await page.evaluate("""
                        () => {
                            const el = document.querySelector('#ctx-content');
                            if (el && el.innerText.trim().length > 30) {
                                return el.innerText.trim();
                            }
                            // 备用选择器
                            const fallbacks = [
                                '.ctx-content',
                                '#ContentBody',
                                '.newsContent',
                                '.txtcont'
                            ];
                            for (const sel of fallbacks) {
                                const fb = document.querySelector(sel);
                                if (fb && fb.innerText.trim().length > 30) {
                                    return fb.innerText.trim();
                                }
                            }
                            return '';
                        }
                    """)

                    if content_text and len(content_text) > 30:
                        print(f"    ✔ 正文提取成功，{len(content_text)} 字符")
                    else:
                        print(
                            f"    正文内容不足"
                            f"（{len(content_text) if content_text else 0} 字符）"
                        )
                        # 尝试获取完整 HTML 分析原因
                        debug_html = await page.evaluate("""
                            () => {
                                const el = document.querySelector('#ctx-content');
                                if (!el) return 'ELEMENT_NOT_FOUND';
                                return el.innerHTML.substring(0, 500);
                            }
                        """)
                        print(f"    调试 - #ctx-content 内联HTML前500字符: {debug_html}")
                        content_text = ""

                except Exception as e:
                    print(f"    正文页加载失败: {e}")
                    content_text = ""
            else:
                print("    无 encodeUrl，跳过正文获取")

            articles.append(
                {
                    "title": title,
                    "authors": authors,
                    "org_name": org_name,
                    "publish_date": publish_date,
                    "content": content_text if content_text else "正文获取失败",
                }
            )

        await browser.close()
        print("  浏览器已关闭")

    return articles


# ========== 2. 构建邮件 HTML ==========
def build_email_html(articles, now):
    footer_time = now.strftime("%Y-%m-%d %H:%M UTC+8")

    if not articles:
        body_rows = """
        <tr>
          <td class="content-cell"
              style="padding:48px 28px;text-align:center;">
            <p style="margin:0;font-size:17px;color:#64748b;line-height:1.8;">
              💤 No articles today.
            </p>
          </td>
        </tr>"""
    else:
        rows = []
        for i, art in enumerate(articles):
            sep = (
                "border-bottom:1px solid #e2e8f0;"
                if i < len(articles) - 1
                else ""
            )
            safe_title = html_module.escape(art["title"])
            safe_authors = html_module.escape(art["authors"])
            safe_org = html_module.escape(art["org_name"])
            safe_date = html_module.escape(art["publish_date"])
            safe_content = html_module.escape(art["content"]).replace(
                "\n", "<br>\n"
            )

            rows.append(
                f"""
            <tr>
              <td class="content-cell" style="padding:28px;{sep}">
                <h2 class="article-title"
                    style="margin:0 0 14px;font-size:18px;font-weight:700;
                           color:#1e293b;line-height:1.5;">
                  {safe_title}
                </h2>
                <p style="margin:0 0 4px;font-size:14px;color:#475569;line-height:1.6;">
                  ✍️ {safe_authors}
                </p>
                <p style="margin:0 0 4px;font-size:14px;color:#475569;line-height:1.6;">
                  🏛️ {safe_org}
                </p>
                <p style="margin:0 0 18px;font-size:14px;color:#475569;line-height:1.6;">
                  📅 {safe_date}
                </p>
                <div class="article-content"
                     style="font-size:14px;color:#334155;line-height:1.9;
                            background:#f8fafc;border-radius:8px;
                            padding:18px 20px;word-break:break-word;
                            overflow-wrap:break-word;">
                  {safe_content}
                </div>
              </td>
            </tr>"""
            )
        body_rows = "".join(rows)

    return f"""\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>策略研究</title>
<style>
  /* 全局 border-box，防止 padding 导致宽度溢出 */
  body, table, td, div, h1, h2, p {{
    box-sizing: border-box;
  }}
  body {{
    margin:0;padding:0;width:100%;background:#f1f5f9;
    -webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;
  }}
  table {{
    border-collapse:collapse;mso-table-lspace:0;mso-table-rspace:0;
  }}
  img {{border:0}}
  /* 移动端适配：强制内容单元格占满宽度 */
  @media only screen and (max-width:620px) {{
    .main-table {{
      width:100% !important;
      min-width:0 !important;
      table-layout: fixed !important;
    }}
    .content-cell {{
      padding:18px 14px !important;
      width:100% !important;
      box-sizing: border-box !important;
    }}
    .article-title {{
      font-size:16px !important;
    }}
    .article-content {{
      padding:14px !important;
      font-size:13px !important;
      word-break: break-word !important;
    }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background:#f1f5f9;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:#f1f5f9;">
<tr><td align="center" style="padding:24px 10px;">
<table class="main-table" role="presentation" width="600" cellpadding="0"
       cellspacing="0"
       style="max-width:600px;width:100%;border-radius:12px;overflow:hidden;
              box-shadow:0 4px 6px -1px rgba(0,0,0,.1),0 2px 4px -1px rgba(0,0,0,.06);
              background:#fff;table-layout:fixed;">
  <!-- HEADER -->
  <tr>
    <td style="background:linear-gradient(135deg,#0F172A 0%,#1e293b 50%,#0F172A 100%);
               padding:30px 28px;text-align:center;">
      <h1 style="margin:0;font-size:30px;font-weight:700;color:#fff;letter-spacing:1px;">
        🌿 策略研究
      </h1>
    </td>
  </tr>
  <!-- BODY -->
  {body_rows}
  <!-- FOOTER -->
  <tr>
    <td style="background:linear-gradient(135deg,#1e293b 0%,#0F172A 50%,#1e293b 100%);
               padding:20px 28px;text-align:center;">
      <p style="margin:0;font-size:12px;color:#94a3b8;">
        Updated at {footer_time}
      </p>
    </td>
  </tr>
</table>
</td></tr>
</table>
</body>
</html>"""


# ========== 3. 发送邮件 ==========
async def send_email(subject, html_body):
    import aiohttp

    api_key = os.environ.get("MAILEROO_API_KEY", "")
    mail_from = os.environ.get("MAIL_FROM", "")
    mail_to_raw = os.environ.get("MAIL_TO", "")

    print("  [4] 发送邮件...")
    print(f"    MAILEROO_API_KEY: {'✔ 已设置' if api_key else '✘ 未设置'}")
    print(f"    MAIL_FROM: {'✔ 已设置' if mail_from else '✘ 未设置'}")
    print(f"    MAIL_TO: {'✔ 已设置' if mail_to_raw else '✘ 未设置'}")

    if not api_key or not mail_from or not mail_to_raw:
        print("  ✘ 邮件环境变量未完全配置，跳过发送")
        return False

    # 支持多收件人（逗号分隔）
    to_list = []
    for addr in mail_to_raw.split(","):
        addr = addr.strip()
        if addr:
            to_list.append(
                {"address": addr, "display_name": addr.split("@")[0]}
            )

    print(f"    发件人: Newsletter <{mail_from}>")
    print(f"    收件人: {', '.join(t['address'] for t in to_list)}")
    print(f"    标题: {subject}")
    print(f"    HTML 长度: {len(html_body)} 字符")

    payload = {
        "from": {"address": mail_from, "display_name": "Newsletter"},
        "to": to_list,
        "subject": subject,
        "html": html_body,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://smtp.maileroo.com/api/v2/emails",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                status = resp.status
                body = await resp.text()
                print(f"    HTTP 状态码: {status}")
                print(f"    响应内容: {body}")
                if 200 <= status < 300:
                    print("  ✔ 邮件发送成功")
                    return True
                else:
                    print("  ✘ 邮件发送失败")
                    return False
    except Exception as e:
        print(f"  ✘ 邮件发送异常: {e}")
        return False


# ========== 4. 主流程 ==========
async def main():
    print("  ==========================================")
    print("  ===      策略研究日报 · 自动推送       ===")
    print("  ==========================================")
    now = bj_now()
    print(f"  运行时间（北京时间）: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print("")

    # 抓取文章
    articles = await fetch_articles()
    print("")
    print(f"  共获取 {len(articles)} 篇文章")

    # 组装邮件
    now = bj_now()
    subject = f"🌿 策略研究 - {now.strftime('%Y-%m-%d')}"
    html_body = build_email_html(articles, now)
    print(f"  邮件标题: {subject}")
    print("")

    # 发送
    await send_email(subject, html_body)

    print("")
    print("  ==========================================")
    print("  ===          运行完成                  ===")
    print("  ==========================================")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"  ✘ 致命错误: {e}")
        import traceback

        traceback.print_exc()
