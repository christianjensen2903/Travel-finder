from pydantic import BaseModel, computed_field
import datetime


class Airport(BaseModel):
    code: str
    name: str
    city: str
    country: str


class Flight(BaseModel):
    departure: Airport
    arrival: Airport
    departure_time: datetime.datetime
    arrival_time: datetime.datetime
    price: float
    airline: str
    stops: int


class Hotel(BaseModel):
    name: str
    city: str
    country: str
    price: float
    rating: float  # Booking.com guest review score (0–10)
    stars: int = 0  # Official star class (0 = unrated / hostel)
    checkin_date: datetime.date
    checkout_date: datetime.date


class TravelOption(BaseModel):
    outbound: Flight
    inbound: Flight
    hotel: Hotel | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def flight_total(self) -> float:
        return self.outbound.price + self.inbound.price

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> float:
        return self.flight_total + (self.hotel.price if self.hotel else 0.0)
