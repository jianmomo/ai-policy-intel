from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = 'development'
    app_name: str = 'AI Policy Intel'
    app_host: str = '0.0.0.0'
    app_port: int = 8000
    app_base_url: str = 'http://localhost:8000'
    app_timezone: str = 'Asia/Hong_Kong'
    database_url: str = 'sqlite:///./data/app.db'
    data_dir: Path = Field(default=Path('./data'))
    config_dir: Path = Field(default=Path('./configs'))
    digest_dir: Path = Field(default=Path('./data/digests'))
    backup_dir: Path = Field(default=Path('./data/backups'))
    backup_keep_count: int = 14
    enable_mock_collectors: bool = True
    smtp_host: str = ''
    smtp_port: int = 587
    smtp_user: str = ''
    smtp_password: str = ''
    smtp_from: str = ''
    smtp_to: str = ''
    smtp_starttls: bool = True
    telegram_bot_token: str = ''
    telegram_chat_id: str = ''
    telegram_ai_chat_id: str = ''
    telegram_policy_chat_id: str = ''
    telegram_ops_chat_id: str = ''
    delivery_email_enabled: bool = False
    delivery_telegram_enabled: bool = False
    delivery_health_alerts_enabled: bool = True
    collector_stale_days: int = 3
    github_token: str = ''
    telegram_ai_limit: int = 5
    telegram_policy_limit: int = 5
    telegram_daily_ai_limit: int = 5
    telegram_daily_policy_limit: int = 5
    telegram_weekly_ai_limit: int = 10
    telegram_weekly_policy_limit: int = 10
    telegram_message_soft_limit: int = 3600
    telegram_max_items_per_topic: int = 3
    telegram_max_items_per_source: int = 2
    telegram_send_overview_first: bool = True
    telegram_enable_event_grouping: bool = True
    ui_admin_token: str = ''

    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.digest_dir.mkdir(parents=True, exist_ok=True)
settings.backup_dir.mkdir(parents=True, exist_ok=True)
