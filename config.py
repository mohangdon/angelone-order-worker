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
    # How often (in hours) to quietly re-download the scrip master in the background.
    # Set to 0 to turn the refresh off (then it only loads at startup).
    INSTRUMENT_REFRESH_HOURS: float = 6.0

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
