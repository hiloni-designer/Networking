from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List
import os


class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str = Field(..., env="ANTHROPIC_API_KEY")

    # LinkedIn
    linkedin_email: str = Field(..., env="LINKEDIN_EMAIL")
    linkedin_password: str = Field(..., env="LINKEDIN_PASSWORD")

    # Agent behaviour
    max_invites_per_day: int = Field(15, env="MAX_INVITES_PER_DAY")
    min_delay_seconds: int = Field(8, env="MIN_DELAY_SECONDS")
    max_delay_seconds: int = Field(30, env="MAX_DELAY_SECONDS")
    poll_interval_minutes: int = Field(60, env="POLL_INTERVAL_MINUTES")
    review_mode: bool = Field(True, env="REVIEW_MODE")

    # Search
    search_queries_raw: str = Field(
        "Senior Designer,Design Director,Head of Design,Recruiter UX,Startup Founder,Product Manager Design",
        env="SEARCH_QUERIES",
    )

    # App
    app_host: str = Field("0.0.0.0", env="APP_HOST")
    app_port: int = Field(8000, env="APP_PORT")
    database_url: str = Field("sqlite:///./linkedin_agent.db", env="DATABASE_URL")
    secret_key: str = Field("change_me", env="SECRET_KEY")

    # Your profile
    your_name: str = Field("Your Name", env="YOUR_NAME")
    your_role: str = Field("UI/UX Designer", env="YOUR_ROLE")
    your_experience_years: int = Field(3, env="YOUR_EXPERIENCE_YEARS")

    @property
    def search_queries(self) -> List[str]:
        return [q.strip() for q in self.search_queries_raw.split(",") if q.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
