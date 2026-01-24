from aiogram import Router
from . import admin, events, feedback, motherlode, registration, sos, thermometer


def get_routers() -> list[Router]:
    return [
        admin.router,
        events.router,
        feedback.router,
        motherlode.router,
        sos.router,
        thermometer.router,
        registration.router,
    ]
