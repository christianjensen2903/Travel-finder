import datetime
from flight_api import FlightAPI
from hotel_api import HotelAPI
from flight_db import FlightDB
from hotel_db import HotelDB
from models import Airport, Flight
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed


class TravelFinder:
    def __init__(self):
        self.flight_api = FlightAPI()
        self.hotel_api = HotelAPI()
        self.flight_db = FlightDB()
        self.hotel_db = HotelDB()

        self.flight_db.load_db()
        self.hotel_db.load_db()

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

        flights = self.find_flights(
            dates, fly_from, destinations, adults, currency_code
        )

        # Reduce each flight to the cheapest one
        reduced_flights: list[tuple[Flight, Flight]] = []
        for flights in flights:
            out_flights = flights[0]
            back_flights = flights[1]

            out_flights = [
                flight for flight in out_flights if flight.stops <= max_stops
            ]
            back_flights = [
                flight for flight in back_flights if flight.stops <= max_stops
            ]

            if max_flight_duration:

                out_flights = [
                    flight
                    for flight in out_flights
                    if (flight.arrival_time - flight.departure_time).total_seconds()
                    / 3600
                    <= max_flight_duration
                ]
                back_flights = [
                    flight
                    for flight in back_flights
                    if (flight.arrival_time - flight.departure_time).total_seconds()
                    / 3600
                    <= max_flight_duration
                ]

            # Remove bad flights i.e. flights arriving between 22pm and 5am
            if remove_bad_flights:
                out_flights = [
                    flight
                    for flight in out_flights
                    if flight.arrival_time.hour > 6 and flight.arrival_time.hour < 22
                ]
                back_flights = [
                    flight
                    for flight in back_flights
                    if flight.arrival_time.hour > 6 and flight.arrival_time.hour < 22
                ]

            if len(out_flights) == 0 or len(back_flights) == 0:
                continue

            # Remove flights before 18pm if an extended day i.e. if out-back are more than max_days
            vacation_days_used = self._count_vacation_days_in_stay(
                out_flights[0].arrival_time.date(),
                back_flights[0].departure_time.date(),
            )

            if auto_extend_trip and vacation_days_used > vacation_days:
                out_flights = [
                    flight for flight in out_flights if flight.departure_time.hour > 16
                ]
                if len(out_flights) == 0:
                    continue

            days_between = (
                back_flights[0].departure_time.date()
                - out_flights[0].arrival_time.date()
            ).days + 1

            # if the trip is exactly min_full_days, we need to remove flight arriving after 12 pm and departing before 20pm
            if days_between == min_full_days:
                out_flights = [
                    flight for flight in out_flights if flight.arrival_time.hour < 12
                ]
                back_flights = [
                    flight for flight in back_flights if flight.departure_time.hour > 20
                ]
                if len(out_flights) == 0 or len(back_flights) == 0:
                    continue

            cheapest_out = min(out_flights, key=lambda x: x.price)
            cheapest_back = min(back_flights, key=lambda x: x.price)
            reduced_flights.append((cheapest_out, cheapest_back))

        # Find cheapest flight for each destination
        cheapest_flights: dict[str, tuple[Flight, Flight]] = {}
        for destination in destinations:
            trips_to_dest = [
                (out, back)
                for (out, back) in reduced_flights
                if back.departure.code == destination.code
            ]
            print(f"Found {len(trips_to_dest)} trips to {destination.code}")
            if len(trips_to_dest) == 0:
                continue

            cheapest_flights[destination.code] = min(
                trips_to_dest, key=lambda x: x[0].price + x[1].price
            )

        self.flight_db.save_db()

        return cheapest_flights

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

    def _lookup_flights(
        self,
        date: datetime.date,
        from_airport: Airport,
        to_airport: Airport,
        adults: int = 2,
        currency_code: str = "DKK",
    ) -> list[Flight]:
        """Find a flight between two airports on a specific date."""
        db_flights = self.flight_db.get_flights(
            from_airport.code, to_airport.code, date
        )

        return db_flights

        if db_flights:
            return db_flights

        flights = self.flight_api.get_flights(
            from_airport.code, to_airport.code, date, adults, currency_code
        )

        for flight in flights:
            self.flight_db.add_flight(flight)

        return flights

    def find_flights(
        self,
        dates: list[tuple[datetime.date, datetime.date]],
        fly_from: Airport,
        destinations: list[Airport],
        adults: int = 2,
        currency_code: str = "DKK",
    ) -> list[tuple[list[Flight], list[Flight]]]:
        flights = []
        for departure, return_date in tqdm(dates):
            for destination in destinations:
                out = self._lookup_flights(
                    departure, fly_from, destination, adults, currency_code
                )
                back = self._lookup_flights(
                    return_date, destination, fly_from, adults, currency_code
                )
                flights.append((out, back))

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
                code="MLE",
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

    for flight in flights.values():
        print(f"To: {flight[0].arrival.city}")
        print(f"Airline out: {flight[0].airline}")
        print(f"Out: {flight[0].departure_time} - {flight[0].arrival_time}")
        print(f"Airline back: {flight[1].airline}")
        print(f"Back: {flight[1].departure_time} - {flight[1].arrival_time}")
        print(f"Price: {flight[0].price + flight[1].price}")
        print("")
