from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

CYCLING_SPORT_TYPES = (
    "Ride",
    "GravelRide",
    "MountainBikeRide",
    "EBikeRide",
    "EMountainBikeRide",
    "VirtualRide",
    "Velomobile",
    "Handcycle",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://velotheque:velotheque@localhost:5432/velotheque"

    base_url: str = "http://localhost:8000"
    oauth_base_url: str | None = None

    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_api_base: str = "https://www.strava.com/api/v3"
    strava_oauth_base: str = "https://www.strava.com/oauth"
    strava_scopes: str = "read,activity:read_all,profile:read_all"
    sport_types: tuple[str, ...] = CYCLING_SPORT_TYPES
    sync_overlap_days: int = 14
    rate_limit_margin: int = 3

    map_style_url: str = "https://tiles.openfreemap.org/styles/liberty"

    @property
    def redirect_uri(self) -> str:
        return f"{(self.oauth_base_url or self.base_url).rstrip('/')}/strava/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
