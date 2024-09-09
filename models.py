from pydantic import BaseModel
import datetime
import json


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
    stops: int


class Hotel(BaseModel):
    name: str
    city: str
    country: str
    price: float
    rating: float
    checkin_date: datetime.date
    checkout_date: datetime.date
