import datetime
import json
from models import Flight, Airport
import requests
import os
from dotenv import load_dotenv
import airportsdata


class FlightAPI:
    def __init__(self):
        self.airports = airportsdata.load("IATA")
        self.ryanair = RyanAirAPI()
        self.booking = BookingDotComAPI()

    def get_flights(
        self,
        departure_code: str,
        arrival_code: str,
        departure_date: datetime.date,
        adults: int = 1,
        currency_code: str = "DKK",
    ) -> list[Flight]:

        if departure_date <= datetime.date.today():
            raise ValueError("Departure date must be in the future")

        if adults < 1:
            raise ValueError("At least one adult is required")

        from_airport = self.get_airport(departure_code)
        to_airport = self.get_airport(arrival_code)
        ryanair_flights = self.ryanair.get_flights(
            from_airport, to_airport, departure_date, adults, currency_code
        )
        booking_flights = self.booking.get_flights(
            from_airport, to_airport, departure_date, adults, currency_code
        )
        return ryanair_flights + booking_flights

    def get_airport(self, code: str) -> Airport:
        if code not in self.airports:
            raise ValueError(f"Airport code '{code}' not found")
        data = self.airports[code]
        return Airport(
            code=code,
            name=data["name"],
            city=data["city"],
            country=data["country"],
        )

    def get_airports(self) -> list[Airport]:
        airports = []
        for code in self.airports.keys():
            airports.append(self.get_airport(code))
        return airports


class RyanAirAPI:
    def _parse_flights(
        self,
        data: dict,
        from_airport: Airport,
        to_airport: Airport,
        adults: int = 2,
    ) -> list[Flight]:

        offers = data["fares"]
        flights = []

        for offer in offers:
            departure_time = datetime.datetime.fromisoformat(
                offer["outbound"]["departureDate"]
            )
            arrival_time = datetime.datetime.fromisoformat(
                offer["outbound"]["arrivalDate"]
            )
            flights.append(
                Flight(
                    departure=from_airport,
                    arrival=to_airport,
                    departure_time=departure_time,
                    arrival_time=arrival_time,
                    price=offer["outbound"]["price"]["value"] * adults,
                    stops=0,
                )
            )

        return flights

    def get_flights(
        self,
        from_airport: Airport,
        to_airport: Airport,
        departure_date: datetime.date,
        adults: int = 2,
        currency_code: str = "DKK",
    ) -> list[Flight]:
        url = f"https://services-api.ryanair.com/farfnd/3/oneWayFares"

        # Parse date to 'YYYY-MM-DD' format
        departure_date_str = departure_date.strftime("%Y-%m-%d")

        query_params = {
            "departureAirportIataCode": from_airport.code,
            "arrivalAirportIataCode": to_airport.code,
            "outboundDepartureDateFrom": departure_date_str,
            "outboundDepartureDateTo": departure_date_str,
            "currency": currency_code,
        }

        response = requests.get(url, params=query_params)
        response.raise_for_status()
        data = response.json()
        return self._parse_flights(data, from_airport, to_airport, adults)


class BookingDotComAPI:

    def __init__(self):
        load_dotenv()
        self.rapidapi_key = os.getenv("RAPIDAPI_KEY")

    def _parse_flights(
        self,
        data: dict,
        from_airport: Airport,
        to_airport: Airport,
    ) -> list[Flight]:

        offers = data["data"]["flightOffers"]
        flights = []

        for offer in offers:
            departure_time = datetime.datetime.fromisoformat(
                offer["segments"][0]["departureTime"]
            )
            arrival_time = datetime.datetime.fromisoformat(
                offer["segments"][-1]["arrivalTime"]
            )
            flights.append(
                Flight(
                    departure=from_airport,
                    arrival=to_airport,
                    departure_time=departure_time,
                    arrival_time=arrival_time,
                    price=offer["priceBreakdown"]["total"]["units"],
                    stops=len(offer["segments"]) - 1,
                )
            )

        return flights

    def get_flights(
        self,
        from_airport: Airport,
        to_airport: Airport,
        departure_date: datetime.date,
        adults: int = 2,
        currency_code: str = "DKK",
    ) -> list[Flight]:
        url = "https://booking-com15.p.rapidapi.com/api/v1/flights/searchFlights"

        departDate_str = departure_date.strftime("%Y-%m-%d")

        querystring = {
            "fromId": f"{from_airport.code}.AIRPORT",
            "toId": f"{to_airport.code}.AIRPORT",
            "departDate": departDate_str,
            "pageNo": "1",
            "adults": adults,
            "sort": "BEST",
            "cabinClass": "ECONOMY",
            "currency_code": currency_code,
        }

        headers = {
            "x-rapidapi-key": self.rapidapi_key,
            "x-rapidapi-host": "booking-com15.p.rapidapi.com",
        }
        response = requests.get(url, headers=headers, params=querystring)
        response.raise_for_status()
        data = response.json()
        return self._parse_flights(data, from_airport, to_airport)


if __name__ == "__main__":
    flight_api = FlightAPI()
    print(flight_api.get_airports())
