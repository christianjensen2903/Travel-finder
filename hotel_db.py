import datetime
import json
from models import Hotel


class HotelDB:
    def __init__(self):
        self.hotels: list[Hotel] = []

    def save_db(self) -> None:
        """Saves the hotels to a file"""
        with open("hotels.json", "w") as f:
            data = [f.model_dump_json() for f in self.hotels]
            json.dump(data, f)

    def load_db(self) -> None:
        """Loads the hotels from a file"""
        try:
            with open("hotels.json", "r") as f:
                data = json.load(f)
                self.hotels = [Hotel.model_validate_json(d) for d in data]
        except FileNotFoundError:
            self.hotels = []

    def add_hotel(self, hotel: Hotel) -> None:
        self.hotels.append(hotel)

    def get_hotels(
        self,
        checkin_date: datetime.date,
        checkout_date: datetime.date,
        city: str | None = None,
        country: str | None = None,
    ) -> list[Hotel]:
        hotels = []
        for hotel in self.hotels:
            if (
                hotel.checkin_date <= checkin_date
                and hotel.checkout_date >= checkout_date
                and (city is None or hotel.city == city)
                and (country is None or hotel.country == country)
            ):
                hotels.append(hotel)
        return hotels
