"""Shared registry of the messaging channel platforms Runtime supports.

Every shared membership set, label, id pattern, limit and credential-redaction
prefix derives from this one descriptor table, so wiring the next platform is
one entry here plus its adapter-specific modules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

DM_POLICIES: tuple[str, ...] = ("disabled", "allowlist", "pairing", "open")


@dataclass(frozen=True)
class ChannelCredential:
    """One configurable credential field for a channel platform.

    Secret fields are write-only and never returned in channel state. Non-secret
    fields are ordinary settings (host, URL, nickname, channel) that state
    reports under its ``credentials`` object. Every field is stored in the
    profile ``.env`` under its Hermes env name.
    """

    field: str
    env: str
    pattern: str
    secret: bool = False
    required: bool = False
    # Reject a value another profile already stores. Applies to bot tokens and
    # client secrets; shared passwords (mail, IRC server) are not identities.
    unique: bool = False
    # Accept a JSON object, validate it, and store it compacted on one .env line.
    json: bool = False


@dataclass(frozen=True)
class ChannelPlatform:
    """One supported channel platform and its shared behavior."""

    id: str
    label: str
    auth: Literal["qr", "token"]
    # Auth kind the connect card uses; Zalo Bot reports the same "api_key"
    # value as an MCP connector, the personal account a QR sign-in.
    connect_auth_kind: str
    id_pattern: str
    # Group approval applies to this platform.
    groups: bool
    # Declared default; the loaded plugin may still report its own limit.
    max_message_length: int
    tools_module: str | None
    # File name under the social-channels skill's ``platforms/`` directory.
    skill_section: str
    # Request-body redaction prefix for a write-only credential operation.
    token_operation_prefix: str | None = None
    # User-id pattern for allowlists; defaults to the thread/chat id pattern.
    user_id_pattern: str | None = None
    # Credential fields this platform reads from the profile environment.
    credentials: tuple[ChannelCredential, ...] = ()
    # DM allowlist env keys; defaults to ``<PREFIX>_ALLOWED_USERS``.
    dm_allowlist_envs: tuple[str, ...] = ()
    # Adapter opt-in flag written for ``dm_policy: open``.
    allow_all_env_name: str | None = None
    # Cron/notification delivery env key; defaults to ``<PREFIX>_HOME_CHANNEL``.
    home_channel_env_name: str | None = None
    # Direct-message policies this adapter can actually enforce end to end.
    dm_policies: tuple[str, ...] = DM_POLICIES
    # Env values every product write must pin (for example a forced transport).
    fixed_env: tuple[tuple[str, str], ...] = ()

    @property
    def env_prefix(self) -> str:
        """Upper-case platform prefix Hermes uses for its env keys."""
        return self.id.upper()

    @property
    def allowed_users_envs(self) -> tuple[str, ...]:
        """Every env key that holds the direct-message allowlist."""
        if self.dm_allowlist_envs:
            return self.dm_allowlist_envs
        return (f"{self.env_prefix}_ALLOWED_USERS",)

    @property
    def allow_all_env(self) -> str:
        """Adapter env key that opts into accepting every direct message."""
        return self.allow_all_env_name or f"{self.env_prefix}_ALLOW_ALL_USERS"

    @property
    def home_channel_env(self) -> str:
        """Adapter env key for the cron/notification home channel."""
        return self.home_channel_env_name or f"{self.env_prefix}_HOME_CHANNEL"

    @property
    def secret_credentials(self) -> tuple[ChannelCredential, ...]:
        """Write-only credential fields, in descriptor order."""
        return tuple(item for item in self.credentials if item.secret)

    @property
    def required_credentials(self) -> tuple[ChannelCredential, ...]:
        """Credential fields that must be set before the channel can connect."""
        return tuple(item for item in self.credentials if item.required)

    def allowlist_pattern(self) -> str:
        """Pattern for allowlist entries; groups keep their thread pattern."""
        return self.user_id_pattern or self.id_pattern


_EMAIL_ADDRESS = r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}$"
_HOSTNAME = r"^[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?$"
_HTTP_URL = r"^https?://[^\s]{1,500}$"
_WS_URL = r"^wss?://[^\s]{1,500}$"
_MATTERMOST_ID = r"^[a-z0-9]{26}$"
_IRC_ID = r"^[#&]?[A-Za-z0-9_.\-\[\]\\`^{}|]{1,64}$"
_IRC_USER = r"^[A-Za-z0-9_.\-\[\]\\`^{}|]{1,64}$"
_IRC_CHANNELS = r"^[#&][^\s]{1,400}$"
_WECOM_ID = r"^[A-Za-z0-9_@.\-]{1,128}$"
_QQBOT_ID = r"^[A-Za-z0-9_.\-]{1,64}$"
_YUANBAO_ID = r"^(?:(?:direct|group):)?[A-Za-z0-9_.\-]{1,64}$"
_YUANBAO_USER = r"^[A-Za-z0-9_.\-]{1,64}$"
_PASSWORD = r"^[^\x00-\x1f]{1,256}$"
_SECRET = r"^[A-Za-z0-9._\-]{1,256}$"
_TOK_EN = r"^[A-Za-z0-9._~+\-/=]{10,1024}$"
_MATRIX_ID = r"^[@!][A-Za-z0-9._=\-]{1,190}:[A-Za-z0-9.\-]{1,190}(?::[0-9]{1,5})?$"
_MATRIX_USER = r"^@[A-Za-z0-9._=\-]{1,190}:[A-Za-z0-9.\-]{1,190}(?::[0-9]{1,5})?$"
_FEISHU_ID = r"^(?:oc|ou|on|cli)_[A-Za-z0-9]{1,200}$"
_FEISHU_USER = r"^(?:ou|on)_[A-Za-z0-9]{1,200}$"
_FEISHU_APP_ID = r"^cli_[A-Za-z0-9]{1,200}$"
_DINGTALK_ID = r"^[A-Za-z0-9_@.$=\-:]{1,200}$"
_GOOGLE_CHAT_ID = (
    r"^(?:(?:spaces|users)/[A-Za-z0-9_-]{1,200}"
    r"|[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63})$"
)
_GOOGLE_CHAT_USER = _EMAIL_ADDRESS
_JSON_OBJECT = r"^[\s\S]{2,20000}$"
_GCP_PROJECT = r"^[a-z][a-z0-9\-]{4,61}[a-z0-9]$"
_GCP_SUBSCRIPTION = (
    r"^projects/[A-Za-z0-9_\-]{1,100}/subscriptions/[A-Za-z0-9_\-]{1,200}$"
)
_ZALO_BOT_TOKEN = r"^[0-9]{1,20}:[A-Za-z0-9_-]{8,128}$"
_DISCORD_TOKEN = r"^[A-Za-z0-9_.-]{40,200}$"
_SLACK_BOT_TOKEN = r"^xoxb-[A-Za-z0-9-]{10,250}$"
_SLACK_APP_TOKEN = r"^xapp-[A-Za-z0-9-]{10,250}$"

PLATFORMS: dict[str, ChannelPlatform] = {
    "zalo": ChannelPlatform(
        id="zalo",
        label="Zalo",
        auth="qr",
        connect_auth_kind="qr",
        id_pattern=r"^[0-9]{1,32}$",
        groups=True,
        max_message_length=2000,
        tools_module="xnobrain.integrations.zalo_tools",
        skill_section="zalo.md",
    ),
    "zalo_bot": ChannelPlatform(
        id="zalo_bot",
        label="Zalo Bot",
        auth="token",
        connect_auth_kind="api_key",
        id_pattern=r"^[A-Za-z0-9_-]{1,64}$",
        groups=True,
        max_message_length=2000,
        tools_module="xnobrain.integrations.zalo_bot_tools",
        skill_section="zalo_bot.md",
        token_operation_prefix="channels_zalo_bot_",
        credentials=(
            ChannelCredential(
                field="bot_token",
                env="ZALO_BOT_TOKEN",
                pattern=_ZALO_BOT_TOKEN,
                secret=True,
                required=True,
                unique=True,
            ),
        ),
    ),
    "telegram": ChannelPlatform(
        id="telegram",
        label="Telegram",
        auth="token",
        connect_auth_kind="api_key",
        # Telegram user ids are positive; group and supergroup chat ids are
        # negative (for example -1001234567890).
        id_pattern=r"^-?[0-9]{1,20}$",
        groups=True,
        # The platform plugin registers max_message_length=4096.
        max_message_length=4096,
        # Hermes already ships the Telegram adapter and its tools; Runtime adds
        # no Telegram-specific tool module.
        tools_module=None,
        skill_section="telegram.md",
        token_operation_prefix="channels_telegram_",
        user_id_pattern=r"^[0-9]{1,20}$",
        credentials=(
            ChannelCredential(
                field="bot_token",
                env="TELEGRAM_BOT_TOKEN",
                pattern=_ZALO_BOT_TOKEN,
                secret=True,
                required=True,
                unique=True,
            ),
        ),
    ),
    "discord": ChannelPlatform(
        id="discord",
        label="Discord",
        auth="token",
        connect_auth_kind="api_key",
        # Discord user, channel and thread ids are numeric snowflakes. The
        # adapter puts the thread id itself in ``chat_id`` for a thread.
        id_pattern=r"^[0-9]{1,20}$",
        groups=True,
        # The platform plugin registers max_message_length=2000.
        max_message_length=2000,
        # Hermes already ships the Discord adapter; Runtime adds no
        # Discord-specific tool module.
        tools_module=None,
        skill_section="discord.md",
        token_operation_prefix="channels_discord_",
        credentials=(
            ChannelCredential(
                field="bot_token",
                env="DISCORD_BOT_TOKEN",
                pattern=_DISCORD_TOKEN,
                secret=True,
                required=True,
                unique=True,
            ),
        ),
    ),
    "slack": ChannelPlatform(
        id="slack",
        label="Slack",
        auth="token",
        connect_auth_kind="api_key",
        # Slack channel ids are ``C``/``G``/``D`` and member ids ``U``/``W``,
        # all uppercase alphanumerics. A thread reply keeps the channel id in
        # ``chat_id`` and carries the thread timestamp separately.
        id_pattern=r"^[A-Z][A-Z0-9]{1,32}$",
        groups=True,
        # The platform plugin registers max_message_length=39000.
        max_message_length=39000,
        # Hermes already ships the Slack adapter; Runtime adds no Slack-specific
        # tool module.
        tools_module=None,
        skill_section="slack.md",
        token_operation_prefix="channels_slack_",
        credentials=(
            ChannelCredential(
                field="bot_token",
                env="SLACK_BOT_TOKEN",
                pattern=_SLACK_BOT_TOKEN,
                secret=True,
                required=True,
                unique=True,
            ),
            ChannelCredential(
                field="app_token",
                env="SLACK_APP_TOKEN",
                pattern=_SLACK_APP_TOKEN,
                secret=True,
                required=True,
                unique=True,
            ),
        ),
    ),
    "whatsapp": ChannelPlatform(
        id="whatsapp",
        label="WhatsApp",
        auth="qr",
        connect_auth_kind="qr",
        # Baileys JIDs: a phone number (``8490...@s.whatsapp.net``), a LID
        # (``1234...@lid``), or a group (``1203...@g.us``). ``to_whatsapp_jid``
        # also accepts a bare number and strips a legacy ``:device`` suffix.
        id_pattern=(
            r"^\+?[0-9]{1,20}(?::[0-9]{1,10})?"
            r"(?:@(?:s\.whatsapp\.net|g\.us|lid))?$"
        ),
        groups=True,
        # The platform plugin registers max_message_length=4096.
        max_message_length=4096,
        # Hermes already ships the WhatsApp adapter; Runtime adds no
        # WhatsApp-specific tool module.
        tools_module=None,
        skill_section="whatsapp.md",
    ),
    "email": ChannelPlatform(
        id="email",
        label="Email",
        auth="token",
        connect_auth_kind="api_key",
        # Email addresses are both the direct-message thread id and the sender
        # id. Hermes' adapter is IMAP-polled and has no group conversations.
        id_pattern=_EMAIL_ADDRESS,
        groups=False,
        # The platform plugin registers max_message_length=50000.
        max_message_length=50000,
        tools_module=None,
        skill_section="email.md",
        token_operation_prefix="channels_email_",
        # Email intake has no gateway pairing step: it drops a sender that is
        # not allowlisted unless open access is on. Pairing would silently
        # swallow every unknown sender instead of asking for approval.
        dm_policies=("disabled", "allowlist", "open"),
        credentials=(
            ChannelCredential(
                field="address",
                env="EMAIL_ADDRESS",
                pattern=_EMAIL_ADDRESS,
                required=True,
            ),
            ChannelCredential(
                field="password",
                env="EMAIL_PASSWORD",
                pattern=_PASSWORD,
                secret=True,
                required=True,
            ),
            ChannelCredential(
                field="imap_host",
                env="EMAIL_IMAP_HOST",
                pattern=_HOSTNAME,
                required=True,
            ),
            ChannelCredential(
                field="smtp_host",
                env="EMAIL_SMTP_HOST",
                pattern=_HOSTNAME,
                required=True,
            ),
        ),
        home_channel_env_name="EMAIL_HOME_ADDRESS",
    ),
    "mattermost": ChannelPlatform(
        id="mattermost",
        label="Mattermost",
        auth="token",
        connect_auth_kind="api_key",
        # Mattermost entity ids are 26 lower-case base32 characters, used for
        # users, channels, and DM channel ids alike.
        id_pattern=_MATTERMOST_ID,
        groups=True,
        # The platform plugin registers max_message_length=4000.
        max_message_length=4000,
        tools_module=None,
        skill_section="mattermost.md",
        token_operation_prefix="channels_mattermost_",
        credentials=(
            ChannelCredential(
                field="url",
                env="MATTERMOST_URL",
                pattern=_HTTP_URL,
                required=True,
            ),
            ChannelCredential(
                field="token",
                env="MATTERMOST_TOKEN",
                pattern=_SECRET,
                secret=True,
                required=True,
                unique=True,
            ),
        ),
    ),
    "irc": ChannelPlatform(
        id="irc",
        label="IRC",
        auth="token",
        connect_auth_kind="api_key",
        # A channel message uses ``#channel`` (or ``&channel``) as the thread
        # id and the sender's nick as the user id, so one pattern covers both.
        id_pattern=_IRC_ID,
        groups=True,
        # The platform plugin registers max_message_length=450.
        max_message_length=450,
        tools_module=None,
        skill_section="irc.md",
        token_operation_prefix="channels_irc_",
        user_id_pattern=_IRC_USER,
        credentials=(
            ChannelCredential(
                field="server",
                env="IRC_SERVER",
                pattern=_HOSTNAME,
                required=True,
            ),
            ChannelCredential(
                field="channel",
                env="IRC_CHANNEL",
                pattern=_IRC_CHANNELS,
                required=True,
            ),
            ChannelCredential(
                field="nickname",
                env="IRC_NICKNAME",
                pattern=_IRC_USER,
            ),
            ChannelCredential(
                field="server_password",
                env="IRC_SERVER_PASSWORD",
                pattern=_PASSWORD,
                secret=True,
            ),
            ChannelCredential(
                field="nickserv_password",
                env="IRC_NICKSERV_PASSWORD",
                pattern=_PASSWORD,
                secret=True,
            ),
        ),
    ),
    "wecom": ChannelPlatform(
        id="wecom",
        label="WeCom (Enterprise WeChat)",
        auth="token",
        connect_auth_kind="api_key",
        # WeCom user ids and chat ids are opaque alphanumerics; user ids may
        # also contain ``@`` and ``.``.
        id_pattern=_WECOM_ID,
        groups=True,
        # The platform plugin registers max_message_length=4000.
        max_message_length=4000,
        tools_module=None,
        skill_section="wecom.md",
        token_operation_prefix="channels_wecom_",
        credentials=(
            ChannelCredential(
                field="bot_id",
                env="WECOM_BOT_ID",
                pattern=_SECRET,
                required=True,
            ),
            ChannelCredential(
                field="secret",
                env="WECOM_SECRET",
                pattern=_SECRET,
                secret=True,
                required=True,
                unique=True,
            ),
        ),
    ),
    "qqbot": ChannelPlatform(
        id="qqbot",
        label="QQ Bot",
        auth="token",
        connect_auth_kind="api_key",
        # QQ Bot OpenIDs are opaque alphanumeric identifiers for users, group
        # members, and groups.
        id_pattern=_QQBOT_ID,
        groups=True,
        # The built-in adapter registers max_message_length=4000.
        max_message_length=4000,
        tools_module=None,
        skill_section="qqbot.md",
        token_operation_prefix="channels_qqbot_",
        credentials=(
            ChannelCredential(
                field="app_id",
                env="QQ_APP_ID",
                pattern=_SECRET,
                required=True,
            ),
            ChannelCredential(
                field="client_secret",
                env="QQ_CLIENT_SECRET",
                pattern=_SECRET,
                secret=True,
                required=True,
                unique=True,
            ),
        ),
        dm_allowlist_envs=("QQ_ALLOWED_USERS",),
        allow_all_env_name="QQ_ALLOW_ALL_USERS",
        home_channel_env_name="QQBOT_HOME_CHANNEL",
    ),
    "yuanbao": ChannelPlatform(
        id="yuanbao",
        label="Yuanbao",
        auth="token",
        connect_auth_kind="api_key",
        # A direct chat is ``direct:<account>`` and a group chat is
        # ``group:<code>``; the sender id is the bare account.
        id_pattern=_YUANBAO_ID,
        groups=True,
        # The built-in adapter registers a 4000-character text chunk limit.
        max_message_length=4000,
        tools_module=None,
        skill_section="yuanbao.md",
        token_operation_prefix="channels_yuanbao_",
        user_id_pattern=_YUANBAO_USER,
        credentials=(
            ChannelCredential(
                field="app_id",
                env="YUANBAO_APP_ID",
                pattern=_SECRET,
                required=True,
            ),
            ChannelCredential(
                field="app_secret",
                env="YUANBAO_APP_SECRET",
                pattern=_SECRET,
                secret=True,
                required=True,
                unique=True,
            ),
            ChannelCredential(
                field="ws_url",
                env="YUANBAO_WS_URL",
                pattern=_WS_URL,
            ),
            ChannelCredential(
                field="api_domain",
                env="YUANBAO_API_DOMAIN",
                pattern=_HTTP_URL,
            ),
            ChannelCredential(
                field="bot_id",
                env="YUANBAO_BOT_ID",
                pattern=_YUANBAO_USER,
            ),
        ),
        # The adapter filters DM intake with extra.dm_allow_from, seeded from
        # YUANBAO_DM_ALLOW_FROM; gateway authorization reads
        # YUANBAO_ALLOWED_USERS. Keep both in step.
        dm_allowlist_envs=("YUANBAO_ALLOWED_USERS", "YUANBAO_DM_ALLOW_FROM"),
        allow_all_env_name="YUANBAO_ALLOW_ALL_USERS",
    ),
    "matrix": ChannelPlatform(
        id="matrix",
        label="Matrix",
        auth="token",
        connect_auth_kind="api_key",
        # Room ids are ``!room:server`` and user ids ``@user:server``; the
        # adapter keeps a room id in ``chat_id`` even inside a thread.
        id_pattern=_MATRIX_ID,
        groups=True,
        # The platform plugin registers max_message_length=16000.
        max_message_length=16000,
        tools_module=None,
        skill_section="matrix.md",
        token_operation_prefix="channels_matrix_",
        user_id_pattern=_MATRIX_USER,
        credentials=(
            ChannelCredential(
                field="homeserver",
                env="MATRIX_HOMESERVER",
                pattern=_HTTP_URL,
                required=True,
            ),
            ChannelCredential(
                field="access_token",
                env="MATRIX_ACCESS_TOKEN",
                pattern=_TOK_EN,
                secret=True,
                required=True,
                unique=True,
            ),
        ),
        allow_all_env_name="MATRIX_ALLOW_ALL_USERS",
        # The adapter reads the cron home from MATRIX_HOME_ROOM.
        home_channel_env_name="MATRIX_HOME_ROOM",
    ),
    "feishu": ChannelPlatform(
        id="feishu",
        label="Feishu / Lark",
        auth="token",
        connect_auth_kind="api_key",
        # Feishu ids are app-scoped open ids, tenant user ids, and chat ids.
        id_pattern=_FEISHU_ID,
        groups=True,
        # The platform plugin registers max_message_length=8000.
        max_message_length=8000,
        tools_module=None,
        skill_section="feishu.md",
        token_operation_prefix="channels_feishu_",
        user_id_pattern=_FEISHU_USER,
        credentials=(
            ChannelCredential(
                field="app_id",
                env="FEISHU_APP_ID",
                pattern=_FEISHU_APP_ID,
                required=True,
            ),
            ChannelCredential(
                field="app_secret",
                env="FEISHU_APP_SECRET",
                pattern=_SECRET,
                secret=True,
                required=True,
                unique=True,
            ),
            ChannelCredential(
                field="domain",
                env="FEISHU_DOMAIN",
                pattern=r"^(?:feishu|lark)$",
            ),
        ),
        allow_all_env_name="FEISHU_ALLOW_ALL_USERS",
        home_channel_env_name="FEISHU_HOME_CHANNEL",
        # The product only supports the outbound WebSocket transport.
        fixed_env=(("FEISHU_CONNECTION_MODE", "websocket"),),
    ),
    "dingtalk": ChannelPlatform(
        id="dingtalk",
        label="DingTalk",
        auth="token",
        connect_auth_kind="api_key",
        # Conversation ids and staff ids are opaque alphanumerics; conversation
        # ids commonly carry ``=`` and ``$`` characters.
        id_pattern=_DINGTALK_ID,
        groups=True,
        # The adapter caps text at 20000 characters.
        max_message_length=20000,
        tools_module=None,
        skill_section="dingtalk.md",
        token_operation_prefix="channels_dingtalk_",
        credentials=(
            ChannelCredential(
                field="client_id",
                env="DINGTALK_CLIENT_ID",
                pattern=_SECRET,
                required=True,
            ),
            ChannelCredential(
                field="client_secret",
                env="DINGTALK_CLIENT_SECRET",
                pattern=_SECRET,
                secret=True,
                required=True,
                unique=True,
            ),
        ),
        allow_all_env_name="DINGTALK_ALLOW_ALL_USERS",
    ),
    "google_chat": ChannelPlatform(
        id="google_chat",
        label="Google Chat",
        auth="token",
        connect_auth_kind="api_key",
        # Google Chat spaces are ``spaces/<id>`` or ``users/<id>``; the sender
        # id is the user's email when Chat provides one.
        id_pattern=_GOOGLE_CHAT_ID,
        groups=True,
        # The platform plugin registers max_message_length=4000.
        max_message_length=4000,
        tools_module=None,
        skill_section="google_chat.md",
        token_operation_prefix="channels_google_chat_",
        user_id_pattern=_GOOGLE_CHAT_USER,
        credentials=(
            ChannelCredential(
                field="service_account_json",
                env="GOOGLE_CHAT_SERVICE_ACCOUNT_JSON",
                pattern=_JSON_OBJECT,
                secret=True,
                required=True,
                json=True,
            ),
            ChannelCredential(
                field="project_id",
                env="GOOGLE_CHAT_PROJECT_ID",
                pattern=_GCP_PROJECT,
                required=True,
            ),
            ChannelCredential(
                field="subscription_name",
                env="GOOGLE_CHAT_SUBSCRIPTION_NAME",
                pattern=_GCP_SUBSCRIPTION,
                required=True,
            ),
        ),
        allow_all_env_name="GOOGLE_CHAT_ALLOW_ALL_USERS",
        home_channel_env_name="GOOGLE_CHAT_HOME_CHANNEL",
    ),
}

# Pydantic Literal type covering exactly the registered platform ids.
ChannelPlatformId = Literal[tuple(PLATFORMS)]


def platform_ids() -> tuple[str, ...]:
    """Every supported platform id, in registry order."""
    return tuple(PLATFORMS)


def get_platform(platform_id: object) -> ChannelPlatform | None:
    """Return the descriptor for one id, or ``None`` when unsupported."""
    return PLATFORMS.get(str(platform_id or "").strip().lower())


def group_platform_ids() -> tuple[str, ...]:
    """Platform ids where group approval applies, in registry order."""
    return tuple(entry.id for entry in PLATFORMS.values() if entry.groups)


def matches_id_pattern(platform_id: object, value: object) -> bool:
    """True when ``value`` matches the platform's own thread/chat id pattern."""
    entry = get_platform(platform_id)
    if entry is None:
        return False
    return re.fullmatch(entry.id_pattern, str(value or "")) is not None


def get_credential(platform_id: object, field: str) -> ChannelCredential | None:
    """Return one credential descriptor, or ``None`` when it is unsupported."""
    entry = get_platform(platform_id)
    if entry is None:
        return None
    for item in entry.credentials:
        if item.field == field:
            return item
    return None


def credential_pattern(platform_id: object, field: str) -> str:
    """Return one credential's validation pattern for a request model."""
    item = get_credential(platform_id, field)
    if item is None:
        raise KeyError(f"{platform_id!r} has no credential {field!r}")
    return item.pattern


def matches_user_id_pattern(platform_id: object, value: object) -> bool:
    """True when ``value`` matches the platform's allowlist id pattern."""
    entry = get_platform(platform_id)
    if entry is None:
        return False
    return re.fullmatch(entry.allowlist_pattern(), str(value or "")) is not None
