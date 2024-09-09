import datetime
from models import Hotel
import requests
import os
from dotenv import load_dotenv
import json


class HotelAPI:
    def __init__(self):
        load_dotenv()
        self.rapid_api_key = os.getenv("RAPID_API_KEY")

    def _get_city_id(self, city_name: str) -> str:

        # Read city id from city_ids.json
        with open("city_ids.json", "r") as file:
            city_ids = json.load(file)
            if city_name in city_ids:
                return city_ids[city_name]

        url = "https://booking-com15.p.rapidapi.com/api/v1/hotels/searchDestination"

        querystring = {
            "query": city_name,
        }

        headers = {
            "x-rapidapi-key": self.rapid_api_key,
            "x-rapidapi-host": "booking-com15.p.rapidapi.com",
        }
        response = requests.get(url, headers=headers, params=querystring)

        response.raise_for_status()

        data = response.json()["data"]
        for row in data:
            if row["search_type"] == "city":
                # Save city id to city_ids.json
                city_ids[city_name] = row["dest_id"]
                with open("city_ids.json", "w") as file:
                    json.dump(city_ids, file)

                return row["dest_id"]

        raise ValueError(f"City '{city_name}' not found")

    def _parse_hotels(self, data: dict, city: str, country: str) -> list[Hotel]:
        hotels = []
        for h in data["data"]["hotels"]:
            hotel = h["property"]
            checkin_date = datetime.datetime.fromisoformat(hotel["checkinDate"]).date()
            checkout_date = datetime.datetime.fromisoformat(
                hotel["checkoutDate"]
            ).date()
            price = hotel["priceBreakdown"]["grossPrice"]["value"]
            hotels.append(
                Hotel(
                    name=hotel["name"],
                    city=city,
                    country=country,
                    price=price,
                    rating=hotel["reviewScore"],
                    checkin_date=checkin_date,
                    checkout_date=checkout_date,
                )
            )
        return hotels

    def get_hotels(
        self,
        city: str,
        country: str,
        checkin_date: datetime.date,
        checkout_date: datetime.date,
        adults: int = 2,
        currency_code: str = "DKK",
    ) -> list[Hotel]:

        if checkin_date <= datetime.date.today():
            raise ValueError("Check-in date must be in the future")

        if checkout_date <= checkin_date:
            raise ValueError("Check-out date must be after check-in date")

        if adults < 1:
            raise ValueError("At least one adult is required")

        city_id = self._get_city_id(city)

        url = "https://booking-com15.p.rapidapi.com/api/v1/hotels/searchHotels"

        checkin_formatted = checkin_date.strftime("%Y-%m-%d")
        checkout_formatted = checkout_date.strftime("%Y-%m-%d")

        querystring = {
            "dest_id": city_id,
            "search_type": "CITY",
            "adults": adults,
            "departure_date": checkin_formatted,
            "arrival_date": checkout_formatted,
            "currency_code": currency_code,
            "sort_by": "popularity",
        }

        headers = {
            "x-rapidapi-key": self.rapid_api_key,
            "x-rapidapi-host": "booking-com15.p.rapidapi.com",
        }
        response = requests.get(url, headers=headers, params=querystring)
        response.raise_for_status()
        data = response.json()
        return self._parse_hotels(data, city, country)


if __name__ == "__main__":
    api = HotelAPI()
    hotels = api._get_city_id("Paris")
    print(hotels)
