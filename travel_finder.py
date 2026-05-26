import datetime
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from flight_api import FlightAPI
from hotel_api import HotelAPI
from flight_db import FlightDB
from hotel_db import HotelDB
from models import Airport, Flight, Hotel, TravelOption
from tqdm import tqdm  # type: ignore


def _default_max_workers() -> int:
    return max(1, int(os.getenv("MAX_WORKERS", "8")))


@dataclass(frozen=True)
class HotelSearchTask:
    destination_code: str
    city: str
    country: str
    checkin_date: datetime.date
    checkout_date: datetime.date


@dataclass
class DestinationFlightOptions:
    outbound_one_way: list[Flight]
    return_one_way: list[Flight]
    round_trips: list[tuple[Flight, Flight]]


class TravelFinder:
    def __init__(self):
        self.flight_api = FlightAPI()
        self.hotel_api = HotelAPI()
        self.flight_db = FlightDB()
        self.hotel_db = HotelDB()
        self._inflight_lock = threading.Lock()
        self._inflight: dict[tuple, threading.Event] = {}

        self.flight_db.load_db()
        self.hotel_db.load_db()

    @staticmethod
    def _pick_cheapest_hotel(
        hotels: list[Hotel],
        min_hotel_rating: float | None = None,
        min_hotel_stars: int | None = None,
    ) -> Hotel | None:
        eligible = hotels
        if min_hotel_rating is not None:
            eligible = [hotel for hotel in eligible if hotel.rating >= min_hotel_rating]
        if min_hotel_stars is not None:
            eligible = [hotel for hotel in eligible if hotel.stars >= min_hotel_stars]
        if not eligible:
            return None
        return min(eligible, key=lambda hotel: hotel.price)

    def find_cheapest_travels(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
        fly_from: Airport,
        destinations: list[Airport],
        exclude_dates: list[datetime.date] = [],
        vacation_days: int = 0,
        min_days: int = 0,
        max_days: int = 0,
        max_stops: int = 0,
        max_flight_duration: int | None = None,
        remove_bad_flights: bool = True,
        min_full_days: int = 0,
        auto_extend_trip: bool = True,
        adults: int = 2,
        currency_code: str = "DKK",
        include_hotels: bool = False,
        min_hotel_rating: float | None = None,
        min_hotel_stars: int | None = None,
        max_workers: int | None = None,
    ):
        """
        A full day is defined as a day from 12am to 11:59pm. or 8am to 20pm.
        A bad flight is defined as a flight arriving between 22pm and 6am.
        Auto extend trips will fly after work if cheaper
        """

        if auto_extend_trip:  # + 1 to allow to fly after work
            dates = self._generate_days(
                start_date,
                end_date,
                exclude_dates,
                vacation_days + 1,
                min_days,
                max_days,
            )
        else:
            dates = self._generate_days(
                start_date, end_date, exclude_dates, vacation_days, min_days, max_days
            )

        workers = max_workers or _default_max_workers()
        print(f"Using {workers} parallel workers (override with MAX_WORKERS in .env)")

        flights = self.find_flights(
            dates, fly_from, destinations, adults, currency_code, workers
        )

        reduced_flights: list[tuple[Flight, Flight]] = []
        for flight_options in flights:
            cheapest_trip = self._select_cheapest_trip(
                flight_options,
                vacation_days=vacation_days,
                max_stops=max_stops,
                max_flight_duration=max_flight_duration,
                remove_bad_flights=remove_bad_flights,
                min_full_days=min_full_days,
                auto_extend_trip=auto_extend_trip,
            )
            if cheapest_trip is not None:
                reduced_flights.append(cheapest_trip)

        # Find cheapest trip for each destination
        cheapest_travels: dict[str, TravelOption] = {}
        hotel_tasks: list[HotelSearchTask] = []
        trips_by_destination: dict[str, list[tuple[Flight, Flight]]] = {}

        for destination in destinations:
            trips_to_dest = [
                (out, back)
                for (out, back) in reduced_flights
                if back.departure.code == destination.code
            ]
            print(f"Found {len(trips_to_dest)} trips to {destination.code}")
            if len(trips_to_dest) == 0:
                continue

            trips_by_destination[destination.code] = trips_to_dest

            if not include_hotels:
                continue

            trips_by_stay: dict[
                tuple[datetime.date, datetime.date], list[tuple[Flight, Flight]]
            ] = {}
            for outbound, inbound in trips_to_dest:
                stay_key = (
                    outbound.arrival_time.date(),
                    inbound.departure_time.date(),
                )
                trips_by_stay.setdefault(stay_key, []).append((outbound, inbound))

            for checkin_date, checkout_date in trips_by_stay:
                hotel_tasks.append(
                    HotelSearchTask(
                        destination_code=destination.code,
                        city=destination.city,
                        country=destination.country,
                        checkin_date=checkin_date,
                        checkout_date=checkout_date,
                    )
                )

        hotel_prices: dict[tuple[str, datetime.date, datetime.date], Hotel] = {}
        if include_hotels and hotel_tasks:
            hotel_prices = self._lookup_hotels_parallel(
                hotel_tasks,
                adults,
                currency_code,
                workers,
                min_hotel_rating,
                min_hotel_stars,
            )

        for destination in destinations:
            trips_to_dest = trips_by_destination.get(destination.code, [])
            if not trips_to_dest:
                continue

            if not include_hotels:
                outbound, inbound = min(
                    trips_to_dest, key=lambda trip: trip[0].price + trip[1].price
                )
                cheapest_travels[destination.code] = TravelOption(
                    outbound=outbound, inbound=inbound
                )
                continue

            best_option: TravelOption | None = None
            best_total = float("inf")

            trips_by_stay: dict[
                tuple[datetime.date, datetime.date], list[tuple[Flight, Flight]]
            ] = {}
            for outbound, inbound in trips_to_dest:
                stay_key = (
                    outbound.arrival_time.date(),
                    inbound.departure_time.date(),
                )
                trips_by_stay.setdefault(stay_key, []).append((outbound, inbound))

            for (checkin_date, checkout_date), trips in trips_by_stay.items():
                hotel = hotel_prices.get(
                    (destination.code, checkin_date, checkout_date)
                )
                if hotel is None:
                    continue

                outbound, inbound = min(
                    trips, key=lambda trip: trip[0].price + trip[1].price
                )
                option = TravelOption(
                    outbound=outbound, inbound=inbound, hotel=hotel
                )
                if option.total < best_total:
                    best_total = option.total
                    best_option = option

            if best_option is not None:
                cheapest_travels[destination.code] = best_option

        self.flight_db.save_db()
        if include_hotels:
            self.hotel_db.save_db()

        return cheapest_travels

    def find_travels(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
        fly_from: Airport,
        destinations: list[Airport],
        exclude_dates: list[datetime.date] = [],
        vacation_days: int = 0,
        min_days: int = 0,
        max_days: int = 0,
        adults: int = 2,
        currency_code: str = "DKK",
    ):
        dates = self._generate_days(
            start_date, end_date, exclude_dates, vacation_days, min_days, max_days
        )
        flights = self.find_flights(
            dates, fly_from, destinations, adults, currency_code
        )
        return flights

    def _wait_for_inflight(self, key: tuple) -> None:
        with self._inflight_lock:
            event = self._inflight.get(key)
        if event is not None:
            event.wait()

    def _begin_inflight(self, key: tuple) -> bool:
        with self._inflight_lock:
            if key in self._inflight:
                return False
            self._inflight[key] = threading.Event()
            return True

    def _finish_inflight(self, key: tuple) -> None:
        with self._inflight_lock:
            event = self._inflight.pop(key, None)
        if event is not None:
            event.set()

    def _lookup_flights(
        self,
        date: datetime.date,
        from_airport: Airport,
        to_airport: Airport,
        adults: int = 2,
        currency_code: str = "DKK",
    ) -> list[Flight]:
        """Find a flight between two airports on a specific date."""
        key = ("one_way", from_airport.code, to_airport.code, date)

        if self.flight_db.has_no_flights(from_airport.code, to_airport.code, date):
            return []

        cached = self.flight_db.get_flights(
            from_airport.code, to_airport.code, date
        )
        if cached:
            return cached

        if not self._begin_inflight(key):
            self._wait_for_inflight(key)
            if self.flight_db.has_no_flights(from_airport.code, to_airport.code, date):
                return []
            return self.flight_db.get_flights(
                from_airport.code, to_airport.code, date
            )

        try:
            if self.flight_db.has_no_flights(from_airport.code, to_airport.code, date):
                return []

            cached = self.flight_db.get_flights(
                from_airport.code, to_airport.code, date
            )
            if cached:
                return cached

            result = self.flight_api.get_flights(
                from_airport.code, to_airport.code, date, adults, currency_code
            )

            for flight in result.flights:
                self.flight_db.add_flight(flight)

            if result.no_flights_confirmed:
                self.flight_db.mark_no_flights(
                    from_airport.code, to_airport.code, date
                )

            return result.flights
        finally:
            self._finish_inflight(key)

    def _lookup_round_trip_flights(
        self,
        outbound_date: datetime.date,
        return_date: datetime.date,
        from_airport: Airport,
        to_airport: Airport,
        adults: int = 2,
        currency_code: str = "DKK",
    ) -> list[tuple[Flight, Flight]]:
        key = (
            "round_trip",
            from_airport.code,
            to_airport.code,
            outbound_date,
            return_date,
        )

        db_trips = self.flight_db.get_round_trips(
            from_airport.code, to_airport.code, outbound_date, return_date
        )
        if db_trips:
            return [(trip.outbound, trip.inbound) for trip in db_trips]

        if self.flight_db.has_no_round_trips(
            from_airport.code, to_airport.code, outbound_date, return_date
        ):
            return []

        if not self._begin_inflight(key):
            self._wait_for_inflight(key)
            if self.flight_db.has_no_round_trips(
                from_airport.code, to_airport.code, outbound_date, return_date
            ):
                return []
            db_trips = self.flight_db.get_round_trips(
                from_airport.code, to_airport.code, outbound_date, return_date
            )
            return [(trip.outbound, trip.inbound) for trip in db_trips]

        try:
            if self.flight_db.has_no_round_trips(
                from_airport.code, to_airport.code, outbound_date, return_date
            ):
                return []

            db_trips = self.flight_db.get_round_trips(
                from_airport.code, to_airport.code, outbound_date, return_date
            )
            if db_trips:
                return [(trip.outbound, trip.inbound) for trip in db_trips]

            result = self.flight_api.get_round_trip_flights(
                from_airport.code,
                to_airport.code,
                outbound_date,
                return_date,
                adults,
                currency_code,
            )

            for outbound, inbound in result.trips:
                self.flight_db.add_round_trip(outbound, inbound)

            if result.no_flights_confirmed:
                self.flight_db.mark_no_round_trips(
                    from_airport.code,
                    to_airport.code,
                    outbound_date,
                    return_date,
                )

            return result.trips
        finally:
            self._finish_inflight(key)

    def _lookup_cheapest_hotel(
        self,
        city: str,
        country: str,
        checkin_date: datetime.date,
        checkout_date: datetime.date,
        adults: int = 2,
        currency_code: str = "DKK",
        min_hotel_rating: float | None = None,
        min_hotel_stars: int | None = None,
    ) -> Hotel | None:
        key = ("hotel", city, country, checkin_date, checkout_date)

        db_hotels = self.hotel_db.get_hotels(
            checkin_date, checkout_date, city, country
        )
        if db_hotels:
            return self._pick_cheapest_hotel(
                db_hotels, min_hotel_rating, min_hotel_stars
            )

        if not self.hotel_api.rapid_api_key:
            return None

        if not self._begin_inflight(key):
            self._wait_for_inflight(key)
            db_hotels = self.hotel_db.get_hotels(
                checkin_date, checkout_date, city, country
            )
            if db_hotels:
                return self._pick_cheapest_hotel(
                    db_hotels, min_hotel_rating, min_hotel_stars
                )
            return None

        hotels: list[Hotel] = []
        try:
            db_hotels = self.hotel_db.get_hotels(
                checkin_date, checkout_date, city, country
            )
            if db_hotels:
                return self._pick_cheapest_hotel(
                    db_hotels, min_hotel_rating, min_hotel_stars
                )

            hotels = self.hotel_api.get_hotels(
                city,
                country,
                checkin_date,
                checkout_date,
                adults,
                currency_code,
            )
        except (ValueError, OSError):
            return None
        finally:
            self._finish_inflight(key)

        if not hotels:
            return None

        for hotel in hotels:
            self.hotel_db.add_hotel(hotel)

        return self._pick_cheapest_hotel(hotels, min_hotel_rating, min_hotel_stars)

    def _lookup_hotels_parallel(
        self,
        tasks: list[HotelSearchTask],
        adults: int,
        currency_code: str,
        max_workers: int,
        min_hotel_rating: float | None = None,
        min_hotel_stars: int | None = None,
    ) -> dict[tuple[str, datetime.date, datetime.date], Hotel]:
        results: dict[tuple[str, datetime.date, datetime.date], Hotel] = {}

        def fetch(task: HotelSearchTask) -> tuple[HotelSearchTask, Hotel | None]:
            hotel = self._lookup_cheapest_hotel(
                task.city,
                task.country,
                task.checkin_date,
                task.checkout_date,
                adults,
                currency_code,
                min_hotel_rating,
                min_hotel_stars,
            )
            return task, hotel

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(fetch, task) for task in tasks]
            for future in tqdm(
                as_completed(futures),
                total=len(futures),
                desc="Hotels",
            ):
                task, hotel = future.result()
                if hotel is not None:
                    results[
                        (task.destination_code, task.checkin_date, task.checkout_date)
                    ] = hotel

        return results

    def _fetch_destination_flight_options(
        self,
        departure: datetime.date,
        return_date: datetime.date,
        fly_from: Airport,
        destination: Airport,
        adults: int,
        currency_code: str,
    ) -> DestinationFlightOptions:
        outbound_one_way = self._lookup_flights(
            departure, fly_from, destination, adults, currency_code
        )
        return_one_way = self._lookup_flights(
            return_date, destination, fly_from, adults, currency_code
        )
        round_trips = self._lookup_round_trip_flights(
            departure,
            return_date,
            fly_from,
            destination,
            adults,
            currency_code,
        )
        return DestinationFlightOptions(
            outbound_one_way=outbound_one_way,
            return_one_way=return_one_way,
            round_trips=round_trips,
        )

    def _filter_flights(
        self,
        flights: list[Flight],
        max_stops: int,
        max_flight_duration: int | None,
        remove_bad_flights: bool,
    ) -> list[Flight]:
        filtered = [flight for flight in flights if flight.stops <= max_stops]

        if max_flight_duration:
            filtered = [
                flight
                for flight in filtered
                if (flight.arrival_time - flight.departure_time).total_seconds() / 3600
                <= max_flight_duration
            ]

        if remove_bad_flights:
            filtered = [
                flight
                for flight in filtered
                if flight.arrival_time.hour > 6 and flight.arrival_time.hour < 22
            ]

        return filtered

    def _trip_passes_constraints(
        self,
        outbound: Flight,
        inbound: Flight,
        vacation_days: int,
        min_full_days: int,
        auto_extend_trip: bool,
    ) -> bool:
        vacation_days_used = self._count_vacation_days_in_stay(
            outbound.departure_time.date(),
            inbound.departure_time.date(),
        )

        if auto_extend_trip and vacation_days_used > vacation_days:
            if outbound.departure_time.hour <= 16:
                return False

        days_between = (
            inbound.departure_time.date() - outbound.arrival_time.date()
        ).days + 1

        if days_between == min_full_days:
            if outbound.arrival_time.hour >= 12 or inbound.departure_time.hour <= 20:
                return False

        return True

    def _select_cheapest_trip(
        self,
        flight_options: DestinationFlightOptions,
        vacation_days: int,
        max_stops: int,
        max_flight_duration: int | None,
        remove_bad_flights: bool,
        min_full_days: int,
        auto_extend_trip: bool,
    ) -> tuple[Flight, Flight] | None:
        candidates: list[tuple[Flight, Flight]] = []

        outbound_one_way = self._filter_flights(
            flight_options.outbound_one_way,
            max_stops,
            max_flight_duration,
            remove_bad_flights,
        )
        return_one_way = self._filter_flights(
            flight_options.return_one_way,
            max_stops,
            max_flight_duration,
            remove_bad_flights,
        )

        if outbound_one_way and return_one_way:
            if self._trip_passes_constraints(
                outbound_one_way[0],
                return_one_way[0],
                vacation_days,
                min_full_days,
                auto_extend_trip,
            ):
                valid_outbound = outbound_one_way
                valid_return = return_one_way

                if auto_extend_trip and self._count_vacation_days_in_stay(
                    outbound_one_way[0].departure_time.date(),
                    return_one_way[0].departure_time.date(),
                ) > vacation_days:
                    valid_outbound = [
                        flight
                        for flight in outbound_one_way
                        if flight.departure_time.hour > 16
                    ]

                days_between = (
                    return_one_way[0].departure_time.date()
                    - outbound_one_way[0].arrival_time.date()
                ).days + 1
                if days_between == min_full_days:
                    valid_outbound = [
                        flight
                        for flight in valid_outbound
                        if flight.arrival_time.hour < 12
                    ]
                    valid_return = [
                        flight
                        for flight in return_one_way
                        if flight.departure_time.hour > 20
                    ]

                if valid_outbound and valid_return:
                    candidates.append(
                        (
                            min(valid_outbound, key=lambda flight: flight.price),
                            min(valid_return, key=lambda flight: flight.price),
                        )
                    )

        for outbound, inbound in flight_options.round_trips:
            filtered_outbound = self._filter_flights(
                [outbound], max_stops, max_flight_duration, remove_bad_flights
            )
            filtered_inbound = self._filter_flights(
                [inbound], max_stops, max_flight_duration, remove_bad_flights
            )
            if not filtered_outbound or not filtered_inbound:
                continue

            outbound = filtered_outbound[0]
            inbound = filtered_inbound[0]
            if self._trip_passes_constraints(
                outbound,
                inbound,
                vacation_days,
                min_full_days,
                auto_extend_trip,
            ):
                candidates.append((outbound, inbound))

        if not candidates:
            return None

        return min(candidates, key=lambda trip: trip[0].price + trip[1].price)

    def find_flights(
        self,
        dates: list[tuple[datetime.date, datetime.date]],
        fly_from: Airport,
        destinations: list[Airport],
        adults: int = 2,
        currency_code: str = "DKK",
        max_workers: int | None = None,
    ) -> list[DestinationFlightOptions]:
        workers = max_workers or _default_max_workers()
        tasks = [
            (departure, return_date, destination)
            for departure, return_date in dates
            for destination in destinations
        ]

        flights: list[DestinationFlightOptions] = []

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    self._fetch_destination_flight_options,
                    departure,
                    return_date,
                    fly_from,
                    destination,
                    adults,
                    currency_code,
                )
                for departure, return_date, destination in tasks
            ]
            for future in tqdm(
                as_completed(futures),
                total=len(futures),
                desc="Flights",
            ):
                flights.append(future.result())

        self.flight_db.save_db()
        return flights

    def _count_vacation_days_in_stay(
        self, departure: datetime.date, return_date: datetime.date
    ) -> int:
        """Helper function to count weekdays (vacation days) in the stay period."""
        vacation_day_count = 0
        current_day = departure
        while current_day <= return_date:
            if current_day.weekday() < 5:
                vacation_day_count += 1
            current_day += datetime.timedelta(days=1)
        return vacation_day_count

    def _generate_days(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
        exclude_dates: list[datetime.date] = [],
        vacation_days: int = 0,
        min_days: int = 0,
        max_days: int = 0,
    ) -> list[tuple[datetime.date, datetime.date]]:
        """Generate a list of possible departure-return days between start_date and end_date.
        The duration of the stay is between min_days and max_days.
        It will only use up the amount of vacation days specified.
        The stay will not include any dates specified in exclude_dates.
        """
        if min_days > max_days:
            raise ValueError("min_days cannot be greater than max_days")

        if min_days < 1:
            raise ValueError("min_days cannot be less than 1")

        if start_date > end_date:
            raise ValueError("start_date cannot be after end_date")

        if vacation_days < 0:
            raise ValueError("vacation_days cannot be less than 0")

        valid_days = []

        # Iterate over each possible departure date
        for dep_day in range((end_date - start_date).days + 1):
            departure = start_date + datetime.timedelta(days=dep_day)

            for stay in range(min_days, max_days + 1):
                # -1 due to include departure day
                return_date = departure + datetime.timedelta(days=stay - 1)

                if return_date > end_date:
                    continue

                if [date for date in exclude_dates if departure <= date <= return_date]:
                    break

                vacation_days_needed = self._count_vacation_days_in_stay(
                    departure, return_date
                )

                if vacation_days_needed > vacation_days:
                    continue

                valid_days.append((departure, return_date))

        return valid_days


if __name__ == "__main__":
    tf = TravelFinder()
    flights = tf.find_cheapest_travels(
        start_date=datetime.date(2024, 10, 8),
        end_date=datetime.date(2024, 10, 31),
        fly_from=Airport(
            code="CPH",
            name="Kastrup Lufthavn",
            country="Denmark",
            city="Copenhagen",
        ),
        destinations=[
            Airport(
                code="AGP",
                name="Malaga",
                country="Spain",
                city="Malaga",
            ),
            Airport(
                code="PMI",
                name="Palma de Mallorca",
                country="Spain",
                city="Palma de Mallorca",
            ),
            Airport(
                code="ALC",
                name="Alicante",
                country="Spain",
                city="Alicante",
            ),
            Airport(
                code="OLB",
                name="Olbia",
                country="Italy",
                city="Olbia",
            ),
            Airport(
                code="MLA",
                name="Malta International Airport",
                country="Malta",
                city="Malta",
            ),
            Airport(
                code="SVQ",
                name="Sevilla",
                country="Spain",
                city="Sevilla",
            ),
            Airport(
                code="TNG",
                name="Tangier Ibn Battouta Airport",
                country="Morocco",
                city="Tangier",
            ),
            Airport(
                code="GIB",
                name="Gibraltar Airport",
                country="Gibraltar",
                city="Gibraltar",
            ),
            Airport(
                code="GRX",
                name="Granada-Jaen Airport",
                country="Spain",
                city="Granada",
            ),
            Airport(
                code="RMU",
                name="Region of Murcia International Airport",
                country="Spain",
                city="Murcia",
            ),
            Airport(
                code="MAH",
                name="Menorca Airport",
                country="Spain",
                city="Menorca",
            ),
            Airport(
                code="CAG",
                name="Cagliari-Elmas Airport",
                country="Italy",
                city="Cagliari",
            ),
            Airport(
                code="LMP",
                name="Lampedusa Airport",
                country="Italy",
                city="Lampedusa",
            ),
            Airport(
                code="CIY",
                name="Comiso Airport",
                country="Italy",
                city="Comiso",
            ),
            Airport(
                code="CTA",
                name="Catania-Fontanarossa Airport",
                country="Italy",
                city="Catania",
            ),
            Airport(
                code="HER",
                name="Heraklion International Airport",
                country="Greece",
                city="Heraklion",
            ),
            Airport(
                code="JTR",
                name="Santorini Airport",
                country="Greece",
                city="Santorini",
            ),
            Airport(
                code="JMK",
                name="Mykonos Airport",
                country="Greece",
                city="Mykonos",
            ),
            Airport(
                code="ATH",
                name="Athens International Airport",
                country="Greece",
                city="Athens",
            ),
            Airport(
                code="RHO",
                name="Rhodes Diagoras Airport",
                country="Greece",
                city="Rhodes",
            ),
            Airport(
                code="DLM",
                name="Dalaman Airport",
                country="Turkey",
                city="Dalaman",
            ),
            Airport(
                code="BJV",
                name="Bodrum-Milas Airport",
                country="Turkey",
                city="Bodrum",
            ),
            Airport(
                code="PFO",
                name="Paphos International Airport",
                country="Cyprus",
                city="Paphos",
            ),
            Airport(
                code="LCA",
                name="Larnaca International Airport",
                country="Cyprus",
                city="Larnaca",
            ),
            Airport(
                code="AYT",
                name="Antalya Airport",
                country="Turkey",
                city="Antalya",
            ),
            Airport(
                code="ZTH",
                name="Zakynthos International Airport",
                country="Greece",
                city="Zakynthos",
            ),
            Airport(
                code="CFU",
                name="Corfu International Airport",
                country="Greece",
                city="Corfu",
            ),
            Airport(
                code="BLJ",
                name="Mostepha Ben Boulaid Airport",
                country="Algeria",
                city="Batna",
            ),
            Airport(
                code="TOE",
                name="Touat Anglem Airport",
                country="Algeria",
                city="Touat Anglem",
            ),
            Airport(
                code="DJE",
                name="Djerba-Zarzis International Airport",
                country="Tunisia",
                city="Djerba",
            ),
            Airport(
                code="TUN",
                name="Tunis-Carthage International Airport",
                country="Tunisia",
                city="Tunis",
            ),
            Airport(
                code="HRG",
                name="Hurghada International Airport",
                country="Egypt",
                city="Hurghada",
            ),
            Airport(
                code="SSH",
                name="Sharm El Sheikh International Airport",
                country="Egypt",
                city="Sharm El Sheikh",
            ),
            Airport(
                code="RMF",
                name="Marsa Alam Airport",
                country="Egypt",
                city="Marsa Alam",
            ),
            Airport(
                code="IST",
                name="Istanbul Airport",
                country="Turkey",
                city="Istanbul",
            ),
            Airport(
                code="ESB",
                name="Ankara Esenboga International Airport",
                country="Turkey",
                city="Ankara",
            ),
            Airport(
                code="TFS",
                name="Tenerife South Airport",
                country="Spain",
                city="Tenerife",
            ),
            Airport(
                code="ACE",
                name="Lanzarote Airport",
                country="Spain",
                city="Lanzarote",
            ),
            Airport(
                code="FUE",
                name="Fuerteventura Airport",
                country="Spain",
                city="Fuerteventura",
            ),
            Airport(
                code="TFN",
                name="Tenerife North Airport",
                country="Spain",
                city="Tenerife",
            ),
            Airport(
                code="SPC",
                name="La Palma Airport",
                country="Spain",
                city="La Palma",
            ),
            Airport(
                code="VDE",
                name="El Hierro Airport",
                country="Spain",
                city="El Hierro",
            ),
            Airport(
                code="IBZ",
                name="Ibiza Airport",
                country="Spain",
                city="Ibiza",
            ),
            Airport(
                code="VLC",
                name="Valencia Airport",
                country="Spain",
                city="Valencia",
            ),
            Airport(
                code="BCN",
                name="Barcelona-El Prat Airport",
                country="Spain",
                city="Barcelona",
            ),
            Airport(
                code="XRY",
                name="Jerez Airport",
                country="Spain",
                city="Jerez",
            ),
            Airport(
                code="LEI",
                name="Almeria Airport",
                country="Spain",
                city="Almeria",
            ),
            Airport(
                code="REU",
                name="Reus Airport",
                country="Spain",
                city="Reus",
            ),
            Airport(
                code="FNC",
                name="Madeira Airport",
                country="Portugal",
                city="Funchal",
            ),
            Airport(
                code="PDL",
                name="Joao Paulo II Airport",
                country="Portugal",
                city="Ponta Delgada",
            ),
            Airport(
                code="TER",
                name="Lajes Airport",
                country="Portugal",
                city="Terceira",
            ),
            Airport(
                code="HOR",
                name="Horta Airport",
                country="Portugal",
                city="Horta",
            ),
            Airport(
                code="PXO",
                name="Porto Santo Airport",
                country="Portugal",
                city="Porto Santo",
            ),
            Airport(
                code="LIS",
                name="Humberto Delgado Airport",
                country="Portugal",
                city="Lisbon",
            ),
            Airport(
                code="OPO",
                name="Francisco Sa Carneiro Airport",
                country="Portugal",
                city="Porto",
            ),
            Airport(
                code="FAO",
                name="Faro Airport",
                country="Portugal",
                city="Faro",
            ),
            Airport(
                code="PMO",
                name="Falcone-Borsellino Airport",
                country="Italy",
                city="Palermo",
            ),
            Airport(
                code="NAP",
                name="Naples International Airport",
                country="Italy",
                city="Naples",
            ),
            Airport(
                code="BRI",
                name="Bari Karol Wojtyla Airport",
                country="Italy",
                city="Bari",
            ),
            Airport(
                code="BDS",
                name="Brindisi Airport",
                country="Italy",
                city="Brindisi",
            ),
            Airport(
                code="SUF",
                name="Lamezia Terme International Airport",
                country="Italy",
                city="Lamezia Terme",
            ),
            Airport(
                code="REG",
                name="Reggio Calabria Airport",
                country="Italy",
                city="Reggio Calabria",
            ),
            Airport(
                code="TPS",
                name="Vincenzo Florio Airport",
                country="Italy",
                city="Trapani",
            ),
            Airport(
                code="FCO",
                name="Leonardo da Vinci-Fiumicino Airport",
                country="Italy",
                city="Rome",
            ),
            Airport(
                code="CHQ",
                name="Chania International Airport",
                country="Greece",
                city="Chania",
            ),
            Airport(
                code="SKG",
                name="Thessaloniki Airport",
                country="Greece",
                city="Thessaloniki",
            ),
            Airport(
                code="KGS",
                name="Kos Island International Airport",
                country="Greece",
                city="Kos",
            ),
            Airport(
                code="EFL",
                name="Kefalonia International Airport",
                country="Greece",
                city="Kefalonia",
            ),
            Airport(
                code="PVK",
                name="Aktion National Airport",
                country="Greece",
                city="Preveza",
            ),
            Airport(
                code="JSI",
                name="Skiathos Airport",
                country="Greece",
                city="Skiathos",
            ),
            Airport(
                code="KLX",
                name="Kalamata International Airport",
                country="Greece",
                city="Kalamata",
            ),
            Airport(
                code="SMI",
                name="Samos International Airport",
                country="Greece",
                city="Samos",
            ),
            Airport(
                code="SPU",
                name="Split Airport",
                country="Croatia",
                city="Split",
            ),
            Airport(
                code="DBV",
                name="Dubrovnik Airport",
                country="Croatia",
                city="Dubrovnik",
            ),
            Airport(
                code="ZAD",
                name="Zadar Airport",
                country="Croatia",
                city="Zadar",
            ),
            Airport(
                code="TIA",
                name="Tirana International Airport",
                country="Albania",
                city="Tirana",
            ),
            Airport(
                code="TIV",
                name="Tivat Airport",
                country="Montenegro",
                city="Tivat",
            ),
            Airport(
                code="TGD",
                name="Podgorica Airport",
                country="Montenegro",
                city="Podgorica",
            ),
            Airport(
                code="LPA",
                name="Gran Canaria Airport",
                country="Spain",
                city="Gran Canaria",
            ),
            Airport(
                code="GRO",
                name="Girona-Costa Brava Airport",
                country="Spain",
                city="Girona",
            ),
            Airport(
                code="NCE",
                name="Nice Cote d Azur Airport",
                country="France",
                city="Nice",
            ),
            Airport(
                code="MRS",
                name="Marseille Provence Airport",
                country="France",
                city="Marseille",
            ),
            Airport(
                code="TLS",
                name="Toulouse-Blagnac Airport",
                country="France",
                city="Toulouse",
            ),
            Airport(
                code="AJA",
                name="Ajaccio Napoleon Bonaparte Airport",
                country="France",
                city="Ajaccio",
            ),
            Airport(
                code="BIA",
                name="Bastia-Poretta Airport",
                country="France",
                city="Bastia",
            ),
            Airport(
                code="CLY",
                name="Calvi-Sainte-Catherine Airport",
                country="France",
                city="Calvi",
            ),
            Airport(
                code="FSC",
                name="Figari-Sud Corse Airport",
                country="France",
                city="Figari",
            ),
            Airport(
                code="VCE",
                name="Venice Marco Polo Airport",
                country="Italy",
                city="Venice",
            ),
            Airport(
                code="AHO",
                name="Alghero-Fertilia Airport",
                country="Italy",
                city="Alghero",
            ),
            Airport(
                code="PSA",
                name="Pisa International Airport",
                country="Italy",
                city="Pisa",
            ),
            Airport(
                code="FLR",
                name="Florence Airport",
                country="Italy",
                city="Florence",
            ),
            Airport(
                code="GOA",
                name="Genoa Cristoforo Colombo Airport",
                country="Italy",
                city="Genoa",
            ),
            Airport(
                code="CRV",
                name="Crotone Airport",
                country="Italy",
                city="Crotone",
            ),
            Airport(
                code="PUY",
                name="Pula Airport",
                country="Croatia",
                city="Pula",
            ),
            Airport(
                code="ADB",
                name="Izmir Adnan Menderes Airport",
                country="Turkey",
                city="Izmir",
            ),
            Airport(
                code="JKH",
                name="Chios Island National Airport",
                country="Greece",
                city="Chios",
            ),
            Airport(
                code="MJT",
                name="Mytilene International Airport",
                country="Greece",
                city="Lesbos",
            ),
            Airport(
                code="LXS",
                name="Limnos International Airport",
                country="Greece",
                city="Lemnos",
            ),
            Airport(
                code="JNX",
                name="Naxos Island National Airport",
                country="Greece",
                city="Naxos",
            ),
            Airport(
                code="PAS",
                name="Paros National Airport",
                country="Greece",
                city="Paros",
            ),
            Airport(
                code="KVA",
                name="Kavala International Airport",
                country="Greece",
                city="Kavala",
            ),
            Airport(
                code="AGA",
                name="Agadir-Al Massira Airport",
                country="Morocco",
                city="Agadir",
            ),
            Airport(
                code="CMN",
                name="Mohammed V International Airport",
                country="Morocco",
                city="Casablanca",
            ),
            Airport(
                code="FEZ",
                name="Fes-Saiss Airport",
                country="Morocco",
                city="Fes",
            ),
            Airport(
                code="OUD",
                name="Angads Airport",
                country="Morocco",
                city="Oujda",
            ),
            Airport(
                code="NBE",
                name="Enfidha-Hammamet International Airport",
                country="Tunisia",
                city="Enfidha",
            ),
            Airport(
                code="RAK",
                name="Marrakech Menara Airport",
                country="Morocco",
                city="Marrakech",
            ),
        ],
        exclude_dates=[
            # datetime.date(2024, 10, 14),
            # datetime.date(2024, 10, 21),
            # datetime.date(2024, 10, 28),
            # datetime.date(2024, 11, 4),
            # datetime.date(2024, 11, 11),
            datetime.date(2024, 10, 19),
        ],
        vacation_days=2,
        min_days=4,
        max_days=8,
        min_full_days=3,
        max_stops=10,
        remove_bad_flights=False,
        max_flight_duration=12,
    )

    for option in flights.values():
        print(f"To: {option.outbound.arrival.city}")
        print(f"Airline out: {option.outbound.airline}")
        print(f"Out: {option.outbound.departure_time} - {option.outbound.arrival_time}")
        print(f"Airline back: {option.inbound.airline}")
        print(f"Back: {option.inbound.departure_time} - {option.inbound.arrival_time}")
        print(f"Flight price: {option.flight_total}")
        if option.hotel:
            print(f"Hotel: {option.hotel.name} ({option.hotel.price} DKK)")
        print(f"Total: {option.total}")
        print("")
