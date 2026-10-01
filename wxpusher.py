# -*- coding: utf-8 -*-
"""
WxPusher 微信推送通知模块（零依赖，仅用标准库）

用于把脚本运行结果推送到微信，便于无人值守时掌握运行情况。

开通步骤：
    1. 打开 https://wxpusher.zjiecode.com/ ，微信扫码登录
    2. 「应用管理」-> 新建应用，拿到 APP_TOKEN
    3. 微信关注生成的二维码，或在「用户管理」拿到 UID
    4. 把 APP_TOKEN / UID 填入环境变量或命令行参数

环境变量：
    WXPUSHER_APP_TOKEN  WxPusher 应用 Token（AT_ 开头）
    WXPUSHER_UIDS       接收者 UID，多个用英文逗号分隔
    WXPUSHER_TOPIC_IDS  主题 ID，多个用英文逗号分隔（可选，用于群发）

用法：
    from wxpusher import WxPusherNotifier

    notifier = WxPusherNotifier()          # 自动读环境变量
    if notifier.enabled:
        notifier.send("任务完成", summary="浏览 30 篇，点赞 6 次")

设计原则：
    - 通知失败绝不抛异常打断主流程（网络异常/配置缺失只记日志）
    - 不引入第三方依赖（urllib 足够），避免拖慢安装/打包
"""

import json
import os
import urllib.request
import urllib.error
from typing import Optional, Sequence, Union

API_URL = "https://wxpusher.zjiecode.com/api/send/message"


def _as_list(value: Union[str, Sequence[str], None]) -> list:
    """把字符串（逗号/分号/空白分隔）或序列统一转成去重后的字符串列表"""
    if value is None:
        return []
    if isinstance(value, str):
        import re

        items = re.split(r"[,，;；\s]+", value)
    else:
        items = []
        for v in value:
            items.extend(_as_list(v))
    seen, out = set(), []
    for s in items:
        s = s.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


class WxPusherNotifier:
    """WxPusher 推送客户端"""

    def __init__(
        self,
        app_token: Optional[str] = None,
        uids: Union[str, Sequence[str], None] = None,
        topic_ids: Union[str, Sequence[str], None] = None,
        url: Optional[str] = None,
        logger=None,
        timeout: int = 10,
    ):
        """初始化

        Args:
            app_token: 应用 Token，缺省读环境变量 WXPUSHER_APP_TOKEN
            uids: 接收者 UID，缺省读环境变量 WXPUSHER_UIDS
            topic_ids: 主题 ID，缺省读环境变量 WXPUSHER_TOPIC_IDS
            url: 消息点击后跳转链接（可选）
            logger: 日志对象（需有 info/warning/error/debug 方法），可选
            timeout: 单次请求超时（秒）
        """
        self.app_token = (app_token or os.environ.get("WXPUSHER_APP_TOKEN") or "").strip()
        self.uids = _as_list(uids if uids is not None else os.environ.get("WXPUSHER_UIDS"))
        self.topic_ids = _as_list(
            topic_ids if topic_ids is not None else os.environ.get("WXPUSHER_TOPIC_IDS")
        )
        self.url = (url or "").strip()
        self.timeout = timeout
        self.log = logger

    # ------------------------------------------------------------------ 日志
    def _log(self, level: str, msg: str):
        if not self.log:
            return
        fn = getattr(self.log, level, None)
        if callable(fn):
            fn(msg)

    # ------------------------------------------------------------ 可用性判断
    @property
    def enabled(self) -> bool:
        """是否已配置到足以发送（有 token 且至少一个接收者）"""
        return bool(self.app_token) and bool(self.uids or self.topic_ids)

    def describe(self) -> str:
        """返回配置摘要（用于日志，不泄露 token 全文）"""
        token_hint = (self.app_token[:6] + "***") if self.app_token else "(未设置)"
        return (
            f"enabled={self.enabled} token={token_hint} "
            f"uids={len(self.uids)} topics={len(self.topic_ids)}"
        )

    # ------------------------------------------------------------------ 发送
    def send(
        self,
        content: str,
        summary: str = "",
        content_type: int = 1,
        verify: bool = False,
    ) -> bool:
        """发送消息

        Args:
            content: 正文。content_type=1 时为纯文本；=2/3 时支持 Markdown/HTML
            summary: 摘要（微信通知栏显示，最长 20 字左右）
            content_type: 1=纯文本, 2=HTML, 3=Markdown
            verify: 是否校验订阅状态

        Returns:
            bool: 是否发送成功（失败不抛异常）
        """
        if not self.enabled:
            self._log("warning", "WxPusher 未配置（缺少 APP_TOKEN 或 UID），跳过推送")
            return False

        payload = {
            "appToken": self.app_token,
            "content": content,
            "summary": summary[:100] if summary else "",
            "contentType": content_type,
            "uids": self.uids,
            "topicIds": self.topic_ids,
            "verifyPay": verify,
        }
        if self.url:
            payload["url"] = self.url

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            API_URL,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            result = json.loads(body) if body else {}
            # WxPusher 约定：code == 1000 为成功
            if result.get("code") == 1000:
                self._log("info", f"WxPusher 推送成功: {summary or content[:20]}")
                return True
            self._log(
                "warning",
                f"WxPusher 推送失败: code={result.get('code')} msg={result.get('msg')}",
            )
            return False
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            self._log("warning", f"WxPusher 推送 HTTP 错误: {e.code} {detail}")
            return False
        except Exception as e:  # noqa: BLE001  通知失败不应影响主流程
            self._log("warning", f"WxPusher 推送异常: {e}")
            return False


# ---------------------------------------------------------------------- 工具
def build_report(
    topics: int,
    likes: int,
    floors: int = 0,
    elapsed: Optional[float] = None,
    login_ok: bool = True,
    ok: bool = True,
    title: str = "Linux.do 自动浏览",
    extra: str = "",
) -> str:
    """生成运行结果报告文本（Markdown）

    Args:
        topics: 浏览帖子数
        likes: 点赞数
        floors: 滚动/爬楼次数
        elapsed: 耗时（秒）
        login_ok: 登录是否成功
        ok: 任务是否正常完成
        title: 标题
        extra: 追加的补充说明
    """
    def _fmt_duration(sec: Optional[float]) -> str:
        if sec is None:
            return "-"
        m, s = divmod(int(sec), 60)
        return f"{m}分{s}秒" if m else f"{s}秒"

    status = "✅ 完成" if (ok and login_ok) else ("⚠️ 异常" if login_ok else "❌ 登录失败")
    lines = [
        f"### {title}",
        "",
        f"- **状态**: {status}",
        f"- **登录**: {'成功' if login_ok else '失败'}",
        f"- **浏览帖子**: {topics}",
        f"- **点赞**: {likes}",
        f"- **滚动次数**: {floors}",
        f"- **用时**: {_fmt_duration(elapsed)}",
    ]
    if extra:
        lines += ["", extra]
    return "\n".join(lines)
