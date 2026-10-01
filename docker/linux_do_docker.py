# -*- coding: utf-8 -*-
"""
Linux.do 自动刷帖 Docker 版
支持真实浏览器 + 随机定时调度
"""

import os
import sys
import random
import time
import json
import signal
import argparse
import schedule
from datetime import datetime, timedelta

try:
    from DrissionPage import ChromiumPage, ChromiumOptions
except ImportError:
    print("错误: pip install DrissionPage")
    sys.exit(1)

# WxPusher 通知（可选，零依赖）。docker/ 与仓库根目录同级，需把根目录加入搜索路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from wxpusher import WxPusherNotifier, build_report
except ImportError:
    WxPusherNotifier = None
    build_report = None


# ============================================================================
# 配置
# ============================================================================

CATEGORIES = [
    {"name": "开发调优", "url": "/c/develop/4"},
    {"name": "国产替代", "url": "/c/domestic/98"},
    {"name": "资源荟萃", "url": "/c/resource/14"},
    {"name": "网盘资源", "url": "/c/resource/cloud-asset/94"},
    {"name": "文档共建", "url": "/c/wiki/42"},
    {"name": "非我莫属", "url": "/c/job/27"},
    {"name": "读书成诗", "url": "/c/reading/32"},
    {"name": "前沿快讯", "url": "/c/news/34"},
    {"name": "网络记忆", "url": "/c/feeds/92"},
    {"name": "福利羊毛", "url": "/c/welfare/36"},
    {"name": "搞七捻三", "url": "/c/gossip/11"},
    {"name": "虫洞广场", "url": "/c/square/110"},
]

BASE_URL = "https://linux.do"


# ============================================================================
# 日志
# ============================================================================


class Log:
    @staticmethod
    def _ts():
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def info(msg):
        print(f"[{Log._ts()}] [INFO] {msg}", flush=True)

    @staticmethod
    def ok(msg):
        print(f"[{Log._ts()}] [OK] {msg}", flush=True)

    @staticmethod
    def warn(msg):
        print(f"[{Log._ts()}] [WARN] {msg}", flush=True)

    @staticmethod
    def err(msg):
        print(f"[{Log._ts()}] [ERROR] {msg}", flush=True)

    @staticmethod
    def debug(msg):
        if os.environ.get("DEBUG"):
            print(f"[{Log._ts()}] [DEBUG] {msg}", flush=True)


# ============================================================================
# 核心浏览器操作
# ============================================================================


class LinuxDoBot:
    def __init__(self, username, password, like_rate=0.3, notifier=None):
        self.username = username
        self.password = password
        self.like_rate = like_rate
        self.page = None
        self.stats = {"topics": 0, "likes": 0, "scrolls": 0}
        self.notifier = notifier
        self.login_ok = False

    def _delay(self, lo=1.0, hi=3.0, reason=""):
        t = random.uniform(lo, hi)
        Log.debug(f"等待 {t:.1f}s ({reason})")
        time.sleep(t)

    def start_browser(self):
        """启动浏览器（Docker 中使用真实 Chrome）"""
        Log.info("启动浏览器...")
        try:
            opts = ChromiumOptions()
            opts.set_argument("--no-sandbox")
            opts.set_argument("--disable-dev-shm-usage")
            opts.set_argument("--disable-gpu")
            opts.set_argument("--disable-blink-features=AutomationControlled")
            opts.set_argument("--window-size=1920,1080")
            opts.set_argument("--lang=zh-CN")

            # Docker 环境中使用 headless
            if os.environ.get("DISPLAY") is None:
                opts.set_argument("--headless=new")
                Log.info("无头模式")

            opts.set_argument(
                "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )

            # 持久化用户数据（保持登录状态）
            user_data = os.environ.get("CHROME_USER_DATA", "/app/chrome-data")
            opts.set_argument(f"--user-data-dir={user_data}")

            self.page = ChromiumPage(opts)
            Log.ok("浏览器启动成功")
            return True
        except Exception as e:
            Log.err(f"浏览器启动失败: {e}")
            return False

    def login(self):
        """登录（支持 Cookie 持久化，首次需要账号密码）"""
        Log.info("检查登录状态...")
        try:
            self.page.get(BASE_URL)
            self._delay(2, 4, "首页加载")

            # 检查是否已登录
            user_ele = self.page.ele("#current-user", timeout=5)
            if user_ele:
                Log.ok("已登录（Cookie 有效）")
                return True

            Log.info("需要登录...")
            self.page.get(f"{BASE_URL}/login")
            self._delay(2, 4, "登录页加载")

            # 输入用户名
            uname = self.page.ele("#login-account-name", timeout=10)
            if not uname:
                Log.err("未找到用户名输入框")
                return False
            uname.clear()
            uname.input(self.username)
            self._delay(0.5, 1)

            # 输入密码
            pwd = self.page.ele("#login-account-password", timeout=5)
            if not pwd:
                Log.err("未找到密码输入框")
                return False
            pwd.clear()
            pwd.input(self.password)
            self._delay(0.5, 1)

            # 点击登录
            btn = self.page.ele("#login-button", timeout=5)
            if not btn:
                Log.err("未找到登录按钮")
                return False
            btn.click()
            self._delay(3, 5, "登录中")

            # 验证
            self.page.get(BASE_URL)
            self._delay(2, 3)
            if self.page.ele("#current-user", timeout=5):
                Log.ok("登录成功")
                return True
            else:
                Log.err("登录失败")
                return False
        except Exception as e:
            Log.err(f"登录出错: {e}")
            return False

    def get_topics(self, category):
        """获取板块帖子"""
        url = BASE_URL + category["url"]
        Log.info(f"进入板块: {category['name']}")
        try:
            self.page.get(url)
            self._delay(2, 4)
            topics = self.page.run_js("""
            return Array.from(document.querySelectorAll('tr.topic-list-item')).map(row => {
                const link = row.querySelector('a.title.raw-link.raw-topic-link');
                if (link && !row.classList.contains('pinned')) {
                    return { url: link.getAttribute('href'), title: link.textContent.trim().substring(0, 50) };
                }
                return null;
            }).filter(Boolean);
            """)
            Log.debug(f"找到 {len(topics or [])} 个帖子")
            return topics or []
        except Exception as e:
            Log.err(f"获取帖子失败: {e}")
            return []

    def browse_topic(self, topic):
        """浏览帖子"""
        url = topic["url"]
        if url.startswith("/"):
            url = BASE_URL + url
        title = topic["title"][:30]
        Log.info(f"浏览: {title}...")
        try:
            self.page.get(url)
            self._delay(2, 3)

            # 随机滚动
            scrolls = random.randint(3, 8)
            for i in range(scrolls):
                dist = random.randint(300, 800)
                self.page.run_js(f"window.scrollBy(0, {dist})")
                self._delay(1.5, 3.5, f"滚动 {i + 1}/{scrolls}")
                if self.page.run_js(
                    "return (window.innerHeight + window.scrollY) >= document.body.offsetHeight - 100"
                ):
                    break

            self.stats["topics"] += 1
            self.stats["scrolls"] += scrolls

            # 随机点赞
            if random.random() < self.like_rate:
                self._try_like()

            return True
        except Exception as e:
            Log.err(f"浏览失败: {e}")
            return False

    def _try_like(self):
        """尝试点赞"""
        try:
            liked = self.page.run_js("""
            const btns = document.querySelectorAll('button.btn-toggle-reaction-like');
            if (btns.length > 0 && !btns[0].classList.contains('has-like') && !btns[0].classList.contains('my-likes')) {
                btns[0].click();
                return true;
            }
            return false;
            """)
            if liked:
                self.stats["likes"] += 1
                Log.ok("点赞 +1")
                self._delay(0.5, 1.5)
        except:
            pass

    def run_once(self, target_topics=None):
        """执行一次浏览任务"""
        if target_topics is None:
            target_topics = random.randint(15, 40)

        Log.info("=" * 50)
        Log.info(f"开始浏览任务 | 目标: {target_topics} 个帖子")
        Log.info("=" * 50)

        self.stats = {"topics": 0, "likes": 0, "scrolls": 0}
        start = time.time()

        try:
            if not self.start_browser():
                return
            if not self.login():
                return

            self.login_ok = True

            cats = CATEGORIES.copy()
            random.shuffle(cats)

            while self.stats["topics"] < target_topics:
                for cat in cats:
                    if self.stats["topics"] >= target_topics:
                        break
                    topics = self.get_topics(cat)
                    if not topics:
                        continue
                    selected = random.sample(
                        topics, min(random.randint(2, 5), len(topics))
                    )
                    for t in selected:
                        if self.stats["topics"] >= target_topics:
                            break
                        self.browse_topic(t)
                        self._delay(reason="切换帖子")
                random.shuffle(cats)

        except KeyboardInterrupt:
            Log.warn("用户中断")
        except Exception as e:
            Log.err(f"运行出错: {e}")
        finally:
            if self.page:
                try:
                    self.page.quit()
                except:
                    pass
                self.page = None

        elapsed = int(time.time() - start)
        Log.info("=" * 50)
        Log.ok(f"任务完成 | 用时 {elapsed // 60}分{elapsed % 60}秒")
        Log.ok(
            f"浏览 {self.stats['topics']} | 点赞 {self.stats['likes']} | 滚动 {self.stats['scrolls']}"
        )
        Log.info("=" * 50)

        self._notify(elapsed)

    def _notify(self, elapsed=None):
        """发送 WxPusher 通知（未配置则跳过；失败不影响主流程）"""
        if not self.notifier or WxPusherNotifier is None or build_report is None:
            return
        try:
            ok = self.stats["topics"] > 0
            content = build_report(
                topics=self.stats["topics"],
                likes=self.stats["likes"],
                floors=self.stats["scrolls"],
                elapsed=elapsed,
                login_ok=self.login_ok,
                ok=ok,
                title="Linux.do 自动浏览（Docker）",
                extra=f"- **时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            )
            summary = (
                f"浏览 {self.stats['topics']} 篇 / 点赞 {self.stats['likes']} 次"
                if self.login_ok
                else "登录失败，请检查"
            )
            self.notifier.send(content, summary=summary, content_type=3)
        except Exception as e:  # noqa: BLE001
            Log.debug(f"发送通知失败: {e}")


# ============================================================================
# 随机定时调度器
# ============================================================================


class RandomScheduler:
    """
    每天随机生成 N 个运行时间点，模拟真人使用习惯
    """

    def __init__(self, bot, runs_per_day=2, topics_range=(15, 40)):
        self.bot = bot
        self.runs_per_day = runs_per_day
        self.topics_range = topics_range
        self.today_schedule = []
        self.running = True

    def _generate_daily_schedule(self):
        """生成今天的随机运行时间"""
        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        times = []

        for _ in range(self.runs_per_day):
            # 在 7:00 - 23:00 之间随机选择时间
            hour = random.randint(7, 22)
            minute = random.randint(0, 59)
            run_time = today.replace(hour=hour, minute=minute)
            # 只保留未来的时间
            if run_time > now:
                times.append(run_time)

        times.sort()
        self.today_schedule = times

        Log.info(f"今日计划 ({len(times)} 次):")
        for t in times:
            topics = random.randint(*self.topics_range)
            Log.info(f"  {t.strftime('%H:%M')} - 浏览约 {topics} 个帖子")

        return times

    def _run_task(self):
        """执行一次任务"""
        topics = random.randint(*self.topics_range)
        Log.info(f"定时任务触发 | 目标 {topics} 个帖子")
        self.bot.run_once(target_topics=topics)

    def start(self):
        """启动调度器"""
        Log.info("=" * 50)
        Log.info("Linux.do 自动刷帖调度器启动")
        Log.info(f"每天运行 {self.runs_per_day} 次")
        Log.info(f"每次浏览 {self.topics_range[0]}-{self.topics_range[1]} 个帖子")
        Log.info("=" * 50)

        # 首次启动立即运行一次
        startup_run = os.environ.get("RUN_ON_START", "true").lower() == "true"
        if startup_run:
            Log.info("首次启动，立即执行一次...")
            self._run_task()

        while self.running:
            # 每天凌晨生成新的计划
            self._generate_daily_schedule()

            # 等待并执行今天的计划
            for run_time in self.today_schedule:
                if not self.running:
                    break

                now = datetime.now()
                if run_time <= now:
                    continue

                wait_seconds = (run_time - now).total_seconds()
                # 添加 ±15 分钟的随机偏移
                jitter = random.randint(-900, 900)
                wait_seconds = max(60, wait_seconds + jitter)

                next_run = now + timedelta(seconds=wait_seconds)
                Log.info(
                    f"下次运行: {next_run.strftime('%H:%M:%S')} (等待 {int(wait_seconds / 60)} 分钟)"
                )

                # 分段等待，方便中断
                while wait_seconds > 0 and self.running:
                    time.sleep(min(60, wait_seconds))
                    wait_seconds -= 60

                if self.running:
                    self._run_task()

            # 今天的计划执行完毕，等到明天
            if self.running:
                now = datetime.now()
                tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0)
                wait = (tomorrow - now).total_seconds()
                Log.info(f"今日任务完成，等待明天 ({int(wait / 3600)} 小时后)")
                while wait > 0 and self.running:
                    time.sleep(min(300, wait))
                    wait -= 300

    def stop(self):
        self.running = False


# ============================================================================
# 入口
# ============================================================================


def main():
    parser = argparse.ArgumentParser(description="Linux.do 自动刷帖 Docker 版")
    parser.add_argument("-u", "--username", help="用户名")
    parser.add_argument("-p", "--password", help="密码")
    parser.add_argument(
        "--like-rate", type=int, default=30, help="点赞概率 0-100，默认 30"
    )
    parser.add_argument(
        "--runs-per-day", type=int, default=2, help="每天运行次数，默认 2"
    )
    parser.add_argument(
        "--topics-min", type=int, default=15, help="每次最少浏览帖子数，默认 15"
    )
    parser.add_argument(
        "--topics-max", type=int, default=40, help="每次最多浏览帖子数，默认 40"
    )
    parser.add_argument("--once", action="store_true", help="只运行一次，不启动调度器")
    parser.add_argument("--debug", action="store_true", help="调试模式")
    parser.add_argument(
        "--no-notify", action="store_true", help="禁用 WxPusher 通知"
    )
    args = parser.parse_args()

    if args.debug:
        os.environ["DEBUG"] = "1"

    username = args.username or os.environ.get("LINUXDO_USERNAME")
    password = args.password or os.environ.get("LINUXDO_PASSWORD")

    if not username or not password:
        print("错误: 请提供用户名和密码")
        print("  环境变量: LINUXDO_USERNAME / LINUXDO_PASSWORD")
        print("  命令行:   -u 用户名 -p 密码")
        sys.exit(1)

    like_rate = int(args.like_rate or os.environ.get("LIKE_RATE", "30"))
    runs_per_day = int(args.runs_per_day or os.environ.get("RUNS_PER_DAY", "2"))
    topics_min = int(args.topics_min or os.environ.get("TOPICS_MIN", "15"))
    topics_max = int(args.topics_max or os.environ.get("TOPICS_MAX", "40"))

    # WxPusher 通知器（未配置则 enabled=False，静默跳过）
    notifier = None
    if not args.no_notify and WxPusherNotifier is not None:
        notifier = WxPusherNotifier(logger=Log)
        Log.info(f"WxPusher 通知: {notifier.describe()}")
        if not notifier.enabled:
            Log.warn(
                "WxPusher 未配置（需 WXPUSHER_APP_TOKEN + WXPUSHER_UIDS），本次不发送通知"
            )

    bot = LinuxDoBot(
        username=username,
        password=password,
        like_rate=like_rate / 100,
        notifier=notifier,
    )

    if args.once:
        topics = random.randint(topics_min, topics_max)
        bot.run_once(target_topics=topics)
    else:
        scheduler = RandomScheduler(
            bot,
            runs_per_day=runs_per_day,
            topics_range=(topics_min, topics_max),
        )

        def handle_signal(sig, frame):
            Log.info("收到停止信号，正在退出...")
            scheduler.stop()

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        scheduler.start()


if __name__ == "__main__":
    main()
