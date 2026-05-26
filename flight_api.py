import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from models import Flight, Airport
import requests  # type: ignore
import os
from dotenv import load_dotenv
import airportsdata  # type: ignore


@dataclass
class OneWaySearchResult:
    flights: list[Flight] = field(default_factory=list)
    no_flights_confirmed: bool = False


@dataclass
class RoundTripSearchResult:
    trips: list[tuple[Flight, Flight]] = field(default_factory=list)
    no_flights_confirmed: bool = False


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
    ) -> OneWaySearchResult:

        if departure_date <= datetime.date.today():
            raise ValueError("Departure date must be in the future")

        if adults < 1:
            raise ValueError("At least one adult is required")

        from_airport = self.get_airport(departure_code)
        to_airport = self.get_airport(arrival_code)

        def fetch_ryanair() -> tuple[list[Flight], bool]:
            try:
                return self.ryanair.get_flights(
                    from_airport, to_airport, departure_date, adults, currency_code
                ), True
            except requests.RequestException:
                return [], False

        def fetch_booking() -> tuple[list[Flight], bool]:
            if not self.booking.rapidapi_key:
                return [], False
            try:
                return self.booking.get_flights(
                    from_airport, to_airport, departure_date, adults, currency_code
                ), True
            except requests.RequestException:
                return [], False

        with ThreadPoolExecutor(max_workers=2) as executor:
            ryanair_future = executor.submit(fetch_ryanair)
            booking_future = executor.submit(fetch_booking)
            ryanair_flights, ryanair_searched = ryanair_future.result()
            booking_flights, booking_searched = booking_future.result()

        flights = ryanair_flights + booking_flights
        if flights:
            return OneWaySearchResult(flights=flights)

        booking_attempted = bool(self.booking.rapidapi_key)
        all_sources_ok = ryanair_searched and (
            booking_searched if booking_attempted else True
        )
        return OneWaySearchResult(flights=[], no_flights_confirmed=all_sources_ok)

    def get_round_trip_flights(
        self,
        departure_code: str,
        arrival_code: str,
        outbound_date: datetime.date,
        return_date: datetime.date,
        adults: int = 1,
        currency_code: str = "DKK",
    ) -> RoundTripSearchResult:
        if outbound_date <= datetime.date.today():
            raise ValueError("Outbound date must be in the future")

        if return_date <= outbound_date:
            raise ValueError("Return date must be after outbound date")

        if adults < 1:
            raise ValueError("At least one adult is required")

        from_airport = self.get_airport(departure_code)
        to_airport = self.get_airport(arrival_code)

        def fetch_ryanair() -> tuple[list[tuple[Flight, Flight]], bool]:
            try:
                return self.ryanair.get_round_trip_flights(
                    from_airport,
                    to_airport,
                    outbound_date,
                    return_date,
                    adults,
                    currency_code,
                ), True
            except requests.RequestException:
                return [], False

        def fetch_booking() -> tuple[list[tuple[Flight, Flight]], bool]:
            if not self.booking.rapidapi_key:
                return [], False
            try:
                return self.booking.get_round_trip_flights(
                    from_airport,
                    to_airport,
                    outbound_date,
                    return_date,
                    adults,
                    currency_code,
                ), True
            except requests.RequestException:
                return [], False

        with ThreadPoolExecutor(max_workers=2) as executor:
            ryanair_future = executor.submit(fetch_ryanair)
            booking_future = executor.submit(fetch_booking)
            ryanair_trips, ryanair_searched = ryanair_future.result()
            booking_trips, booking_searched = booking_future.result()

        trips = ryanair_trips + booking_trips
        if trips:
            return RoundTripSearchResult(trips=trips)

        booking_attempted = bool(self.booking.rapidapi_key)
        all_sources_ok = ryanair_searched and (
            booking_searched if booking_attempted else True
        )
        return RoundTripSearchResult(trips=[], no_flights_confirmed=all_sources_ok)

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
                    airline="Ryanair",
                )
            )

        return flights

    def _parse_round_trip_flights(
        self,
        data: dict,
        from_airport: Airport,
        to_airport: Airport,
        adults: int = 2,
    ) -> list[tuple[Flight, Flight]]:
        trips: list[tuple[Flight, Flight]] = []

        for offer in data.get("fares", []):
            outbound = offer["outbound"]
            inbound = offer["inbound"]

            outbound_flight = Flight(
                departure=from_airport,
                arrival=to_airport,
                departure_time=datetime.datetime.fromisoformat(
                    outbound["departureDate"]
                ),
                arrival_time=datetime.datetime.fromisoformat(outbound["arrivalDate"]),
                price=outbound["price"]["value"] * adults,
                stops=0,
                airline="Ryanair",
            )
            inbound_flight = Flight(
                departure=to_airport,
                arrival=from_airport,
                departure_time=datetime.datetime.fromisoformat(inbound["departureDate"]),
                arrival_time=datetime.datetime.fromisoformat(inbound["arrivalDate"]),
                price=inbound["price"]["value"] * adults,
                stops=0,
                airline="Ryanair",
            )
            trips.append((outbound_flight, inbound_flight))

        return trips

    def get_flights(
        self,
        from_airport: Airport,
        to_airport: Airport,
        departure_date: datetime.date,
        adults: int = 2,
        currency_code: str = "DKK",
    ) -> list[Flight]:
        url = f"https://services-api.ryanair.com/farfnd/3/oneWayFares"

        departure_date_str = departure_date.strftime("%Y-%m-%d")

        query_params = {
            "departureAirportIataCode": from_airport.code,
            "arrivalAirportIataCode": to_airport.code,
            "outboundDepartureDateFrom": departure_date_str,
            "outboundDepartureDateTo": departure_date_str,
            "currency": currency_code,
        }

        response = requests.get(url, params=query_params)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        data = response.json()
        return self._parse_flights(data, from_airport, to_airport, adults)

    def get_round_trip_flights(
        self,
        from_airport: Airport,
        to_airport: Airport,
        outbound_date: datetime.date,
        return_date: datetime.date,
        adults: int = 2,
        currency_code: str = "DKK",
    ) -> list[tuple[Flight, Flight]]:
        url = "https://services-api.ryanair.com/farfnd/3/roundTripFares"

        query_params = {
            "departureAirportIataCode": from_airport.code,
            "arrivalAirportIataCode": to_airport.code,
            "outboundDepartureDateFrom": outbound_date.strftime("%Y-%m-%d"),
            "outboundDepartureDateTo": outbound_date.strftime("%Y-%m-%d"),
            "inboundDepartureDateFrom": return_date.strftime("%Y-%m-%d"),
            "inboundDepartureDateTo": return_date.strftime("%Y-%m-%d"),
            "currency": currency_code,
        }

        response = requests.get(url, params=query_params)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        data = response.json()
        return self._parse_round_trip_flights(data, from_airport, to_airport, adults)


class BookingDotComAPI:

    def __init__(self):
        load_dotenv()
        self.rapidapi_key = os.getenv("RAPID_API_KEY")

    @staticmethod
    def _offer_price(offer: dict) -> float:
        total = offer["priceBreakdown"]["total"]
        return total["units"] + total.get("nanos", 0) / 1_000_000_000

    @staticmethod
    def _get_flight_offers(data: dict) -> list[dict]:
        payload = data.get("data")
        if not isinstance(payload, dict) or "error" in payload:
            return []
        offers = payload.get("flightOffers")
        if not isinstance(offers, list):
            return []
        return offers

    def _parse_flights(
        self,
        data: dict,
        from_airport: Airport,
        to_airport: Airport,
    ) -> list[Flight]:
        offers = self._get_flight_offers(data)
        if not offers:
            return []

        flights = []

        for offer in offers:
            departure_time = datetime.datetime.fromisoformat(
                offer["segments"][0]["departureTime"]
            )
            arrival_time = datetime.datetime.fromisoformat(
                offer["segments"][-1]["arrivalTime"]
            )
            airline = offer["segments"][0]["legs"][0]["carriersData"][0]["name"]

            flights.append(
                Flight(
                    departure=from_airport,
                    arrival=to_airport,
                    departure_time=departure_time,
                    arrival_time=arrival_time,
                    price=self._offer_price(offer),
                    stops=len(offer["segments"][0]["legs"]) - 1,
                    airline=airline,
                )
            )

        return flights

    def _parse_round_trip_flights(
        self,
        data: dict,
        from_airport: Airport,
        to_airport: Airport,
    ) -> list[tuple[Flight, Flight]]:
        offers = self._get_flight_offers(data)
        if not offers:
            return []

        trips: list[tuple[Flight, Flight]] = []

        for offer in offers:
            segments = offer.get("segments", [])
            if len(segments) < 2:
                continue

            outbound_segment = segments[0]
            inbound_segment = segments[1]
            total_price = self._offer_price(offer)

            outbound_flight = Flight(
                departure=from_airport,
                arrival=to_airport,
                departure_time=datetime.datetime.fromisoformat(
                    outbound_segment["departureTime"]
                ),
                arrival_time=datetime.datetime.fromisoformat(
                    outbound_segment["arrivalTime"]
                ),
                price=total_price,
                stops=len(outbound_segment["legs"]) - 1,
                airline=outbound_segment["legs"][0]["carriersData"][0]["name"],
            )
            inbound_flight = Flight(
                departure=to_airport,
                arrival=from_airport,
                departure_time=datetime.datetime.fromisoformat(
                    inbound_segment["departureTime"]
                ),
                arrival_time=datetime.datetime.fromisoformat(
                    inbound_segment["arrivalTime"]
                ),
                price=0,
                stops=len(inbound_segment["legs"]) - 1,
                airline=inbound_segment["legs"][0]["carriersData"][0]["name"],
            )
            trips.append((outbound_flight, inbound_flight))

        return trips

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
            "adults": str(adults),
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

    def get_round_trip_flights(
        self,
        from_airport: Airport,
        to_airport: Airport,
        outbound_date: datetime.date,
        return_date: datetime.date,
        adults: int = 2,
        currency_code: str = "DKK",
    ) -> list[tuple[Flight, Flight]]:
        url = "https://booking-com15.p.rapidapi.com/api/v1/flights/searchFlights"

        querystring = {
            "fromId": f"{from_airport.code}.AIRPORT",
            "toId": f"{to_airport.code}.AIRPORT",
            "departDate": outbound_date.strftime("%Y-%m-%d"),
            "returnDate": return_date.strftime("%Y-%m-%d"),
            "pageNo": "1",
            "adults": str(adults),
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
        return self._parse_round_trip_flights(data, from_airport, to_airport)


if __name__ == "__main__":
    flight_api = FlightAPI()
    flight_api.get_flights("CPH", "AGP", datetime.date(2024, 10, 5))
