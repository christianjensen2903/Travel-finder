import datetime
import json
from models import Flight


class FlightDB:
    def __init__(self):
        self.flights: list[Flight] = []

    def save_db(self) -> None:
        """Saves the flights to a file"""
        with open("flights.json", "w") as f:
            data = [f.model_dump_json() for f in self.flights]
            json.dump(data, f)

    def load_db(self) -> None:
        """Loads the flights from a file"""
        try:
            with open("flights.json", "r") as f:
                data = json.load(f)
                self.flights = [Flight.model_validate_json(d) for d in data]
        except FileNotFoundError:
            self.flights = []

    def add_flight(self, flight: Flight) -> None:
        self.flights.append(flight)

    def get_flights(
        self, departure_code: str, arrival_code: str, departure_date: datetime.date
    ) -> list[Flight]:
        flights = []
        for flight in self.flights:
            if (
                flight.departure.code == departure_code
                and flight.arrival.code == arrival_code
                and flight.departure_time.date() == departure_date
            ):
                flights.append(flight)
        return flights
