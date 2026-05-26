import datetime
import json
import threading
from dataclasses import dataclass
from models import Flight


@dataclass
class RoundTripFlights:
    outbound: Flight
    inbound: Flight


class FlightDB:
    def __init__(self):
        self._lock = threading.Lock()
        self.flights: list[Flight] = []
        self.round_trips: list[RoundTripFlights] = []
        self.no_one_way_flights: set[tuple[str, str, datetime.date]] = set()
        self.no_round_trip_flights: set[
            tuple[str, str, datetime.date, datetime.date]
        ] = set()

    def save_db(self) -> None:
        """Saves the flights to a file"""
        with self._lock:
            with open("flights.json", "w") as f:
                data = [flight.model_dump_json() for flight in self.flights]
                json.dump(data, f)

            with open("round_trips.json", "w") as f:
                data = [
                    {
                        "outbound": trip.outbound.model_dump_json(),
                        "inbound": trip.inbound.model_dump_json(),
                    }
                    for trip in self.round_trips
                ]
                json.dump(data, f)

            with open("flight_misses.json", "w") as f:
                json.dump(
                    {
                        "one_way": [
                            [departure, arrival, date.isoformat()]
                            for departure, arrival, date in sorted(
                                self.no_one_way_flights
                            )
                        ],
                        "round_trip": [
                            [
                                departure,
                                arrival,
                                outbound.isoformat(),
                                return_date.isoformat(),
                            ]
                            for departure, arrival, outbound, return_date in sorted(
                                self.no_round_trip_flights
                            )
                        ],
                    },
                    f,
                )

    def load_db(self) -> None:
        """Loads the flights from a file"""
        with self._lock:
            try:
                with open("flights.json", "r") as f:
                    data = json.load(f)
                    self.flights = [Flight.model_validate_json(d) for d in data]
            except FileNotFoundError:
                self.flights = []

            try:
                with open("round_trips.json", "r") as f:
                    data = json.load(f)
                    self.round_trips = [
                        RoundTripFlights(
                            outbound=Flight.model_validate_json(row["outbound"]),
                            inbound=Flight.model_validate_json(row["inbound"]),
                        )
                        for row in data
                    ]
            except FileNotFoundError:
                self.round_trips = []

            self.no_one_way_flights = set()
            self.no_round_trip_flights = set()
            try:
                with open("flight_misses.json", "r") as f:
                    data = json.load(f)
                    for departure, arrival, date_str in data.get("one_way", []):
                        self.no_one_way_flights.add(
                            (departure, arrival, datetime.date.fromisoformat(date_str))
                        )
                    for (
                        departure,
                        arrival,
                        outbound_str,
                        return_str,
                    ) in data.get("round_trip", []):
                        self.no_round_trip_flights.add(
                            (
                                departure,
                                arrival,
                                datetime.date.fromisoformat(outbound_str),
                                datetime.date.fromisoformat(return_str),
                            )
                        )
            except FileNotFoundError:
                pass

    def add_flight(self, flight: Flight) -> None:
        with self._lock:
            self.flights.append(flight)
            self.no_one_way_flights.discard(
                (
                    flight.departure.code,
                    flight.arrival.code,
                    flight.departure_time.date(),
                )
            )

    def add_round_trip(self, outbound: Flight, inbound: Flight) -> None:
        with self._lock:
            self.round_trips.append(RoundTripFlights(outbound=outbound, inbound=inbound))
            self.no_round_trip_flights.discard(
                (
                    outbound.departure.code,
                    outbound.arrival.code,
                    outbound.departure_time.date(),
                    inbound.departure_time.date(),
                )
            )

    def has_no_flights(
        self, departure_code: str, arrival_code: str, departure_date: datetime.date
    ) -> bool:
        with self._lock:
            return (
                departure_code,
                arrival_code,
                departure_date,
            ) in self.no_one_way_flights

    def mark_no_flights(
        self, departure_code: str, arrival_code: str, departure_date: datetime.date
    ) -> None:
        with self._lock:
            self.no_one_way_flights.add(
                (departure_code, arrival_code, departure_date)
            )

    def has_no_round_trips(
        self,
        departure_code: str,
        arrival_code: str,
        outbound_date: datetime.date,
        return_date: datetime.date,
    ) -> bool:
        with self._lock:
            return (
                departure_code,
                arrival_code,
                outbound_date,
                return_date,
            ) in self.no_round_trip_flights

    def mark_no_round_trips(
        self,
        departure_code: str,
        arrival_code: str,
        outbound_date: datetime.date,
        return_date: datetime.date,
    ) -> None:
        with self._lock:
            self.no_round_trip_flights.add(
                (departure_code, arrival_code, outbound_date, return_date)
            )

    def get_flights(
        self, departure_code: str, arrival_code: str, departure_date: datetime.date
    ) -> list[Flight]:
        with self._lock:
            flights = []
            for flight in self.flights:
                if (
                    flight.departure.code == departure_code
                    and flight.arrival.code == arrival_code
                    and flight.departure_time.date() == departure_date
                ):
                    flights.append(flight)
            return flights

    def get_round_trips(
        self,
        departure_code: str,
        arrival_code: str,
        outbound_date: datetime.date,
        return_date: datetime.date,
    ) -> list[RoundTripFlights]:
        with self._lock:
            trips = []
            for trip in self.round_trips:
                if (
                    trip.outbound.departure.code == departure_code
                    and trip.outbound.arrival.code == arrival_code
                    and trip.outbound.departure_time.date() == outbound_date
                    and trip.inbound.departure.code == arrival_code
                    and trip.inbound.arrival.code == departure_code
                    and trip.inbound.departure_time.date() == return_date
                ):
                    trips.append(trip)
            return trips
