"""Abstract BaseScraper class that all platform scrapers must implement."""

from abc import ABC, abstractmethod


class BaseScraper(ABC):
    @abstractmethod
    async def fetch_listings(self) -> list[dict]:
        """Fetch and return raw listings.

        Each dict must contain:
            url, title, price, size, rooms, district,
            available_from, first_photo_url, description
        """
