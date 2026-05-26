# Travel Finder 🌍✈️

A Python application that helps you find the cheapest flights and hotels by aggregating data from multiple travel platforms including RyanAir and Booking.com.

## Features

- **Multi-source Flight Search**: Searches both RyanAir and Booking.com APIs simultaneously to find the best flight deals
- **Hotel Search**: Finds hotels in your destination city with pricing and ratings
- **Local Caching**: Stores search results locally to minimize API calls and improve performance
- **Airport Database**: Built-in database of IATA airport codes with full airport information
- **Flexible Search Parameters**: Customize searches by date, number of adults, and currency
- **Data Validation**: Uses Pydantic models for robust data handling and validation

## Installation

### Prerequisites

- Python 3.10 or higher
- RapidAPI account with access to Booking.com API

### Setup

1. Clone the repository:
```bash
git clone https://github.com/christianjensen2903/Travel-finder.git
cd Travel-finder
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create a `.env` file in the project root:
```env
RAPID_API_KEY=your_rapidapi_key_here
```

### Required Dependencies

```txt
pydantic
requests
python-dotenv
airportsdata
```

## Usage

### Finding Flights

```python
from flight_api import FlightAPI
import datetime

# Initialize the API
flight_api = FlightAPI()

# Search for flights
flights = flight_api.get_flights(
    departure_code="CPH",  # Copenhagen
    arrival_code="AGP",    # Málaga
    departure_date=datetime.date(2024, 12, 1),
    adults=2,
    currency_code="DKK"
)

# Display results
for flight in flights:
    print(f"{flight.airline}: {flight.departure.city} → {flight.arrival.city}")
    print(f"  Departure: {flight.departure_time}")
    print(f"  Arrival: {flight.arrival_time}")
    print(f"  Price: {flight.price} DKK")
    print(f"  Stops: {flight.stops}")
    print()
```

### Finding Hotels

```python
from hotel_api import HotelAPI
import datetime

# Initialize the API
hotel_api = HotelAPI()

# Search for hotels
hotels = hotel_api.get_hotels(
    city="Málaga",
    country="Spain",
    checkin_date=datetime.date(2024, 12, 1),
    checkout_date=datetime.date(2024, 12, 5),
    adults=2,
    currency_code="DKK"
)

# Display results
for hotel in hotels:
    print(f"{hotel.name}")
    print(f"  Location: {hotel.city}, {hotel.country}")
    print(f"  Rating: {hotel.rating}/10")
    print(f"  Price: {hotel.price} DKK")
    print()
```


## Project Structure

```
Travel-finder/
├── models.py          # Pydantic models for Airport, Flight, and Hotel
├── flight_api.py      # Flight search API integrations (RyanAir, Booking.com)
├── flight_db.py       # Local flight database/cache
├── hotel_api.py       # Hotel search API integration (Booking.com)
├── hotel_db.py        # Local hotel database/cache
├── travel_finder.py   # Main application entry point
├── .env               # Environment variables (not tracked in git)
├── flights.json       # Cached flight data (generated)
├── hotels.json        # Cached hotel data (generated)
├── city_ids.json      # Cached city IDs for Booking.com (generated)
└── requirements.txt   # Python dependencies
```

## API Documentation

### FlightAPI

**`get_flights(departure_code, arrival_code, departure_date, adults=1, currency_code="DKK")`**
- Searches for one-way flights between two airports
- Returns a list of `Flight` objects sorted by price
- Aggregates results from RyanAir and Booking.com APIs

**`get_airport(code)`**
- Retrieves airport information by IATA code
- Returns an `Airport` object

**`get_airports()`**
- Returns a list of all available airports

### HotelAPI

**`get_hotels(city, country, checkin_date, checkout_date, adults=2, currency_code="DKK")`**
- Searches for hotels in a specific city
- Returns a list of `Hotel` objects sorted by popularity
- Includes pricing, ratings, and availability

## API Keys

### RapidAPI (Booking.com)

1. Sign up at [RapidAPI](https://rapidapi.com/)
2. Subscribe to the [Booking.com API](https://rapidapi.com/DataCrawler/api/booking-com15)
3. Copy your API key and add it to the `.env` file

### RyanAir API

The RyanAir API is publicly accessible and doesn't require authentication.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License

This project is open source and available under the [MIT License](LICENSE).

## Disclaimer

This tool is for personal use only. Always verify prices and availability on the official airline and hotel websites before making any bookings. The author is not responsible for any discrepancies or issues with bookings.

## Support

If you encounter any issues or have questions, please open an issue on the [GitHub repository](https://github.com/christianjensen2903/Travel-finder/issues).

---

**Happy Travels!** 🎒✨
