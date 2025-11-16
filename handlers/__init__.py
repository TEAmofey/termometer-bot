from aiogram import Router
from . import events, feedback, registration, sos, thermometer


def get_routers() -> list[Router]:
    return [
        events.router,
        feedback.router,
        sos.router,
        thermometer.router,
        registration.router,
    ]
