from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ANGEL_API_KEY: str = ""
    ANGEL_CLIENT_CODE: str = ""
    ANGEL_PIN: str = ""
    ANGEL_TOTP_SECRET: str = ""
    WORKER_API_TOKEN: str = ""
    WORKER_ACCOUNT_NAME: str = "Angel account"
    WORKER_HOST: str = "0.0.0.0"
    WORKER_PORT: int = 7100
    ORDER_CONFIRM_TIMEOUT_SECONDS: float = 3.0

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
