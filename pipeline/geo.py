"""Country/place names -> the owner's 8 news regions (Optimal News in Plan My Day)."""
from __future__ import annotations

import re

from . import common

REGIONS = ["world", "asia", "africa", "north-america", "south-america", "middle-east", "europe", "oceania"]
REGION_NAMES = {
    "world": "World", "asia": "Asia", "africa": "Africa", "north-america": "North America",
    "south-america": "South America", "middle-east": "Middle East", "europe": "Europe", "oceania": "Australia / Oceania",
}

_PLACES = {
    "asia": "china chinese beijing taiwan japan japanese tokyo korea korean seoul pyongyang india indian delhi mumbai "
            "pakistan pakistani islamabad bangladesh dhaka nepal sri_lanka afghanistan afghan kabul myanmar burma thailand thai "
            "bangkok vietnam vietnamese hanoi philippines philippine manila indonesia indonesian jakarta malaysia singapore "
            "cambodia laos mongolia kazakhstan uzbekistan kyrgyzstan tajikistan turkmenistan hong_kong maldives bhutan",
    "africa": "africa african nigeria nigerian lagos abuja kenya kenyan nairobi ethiopia ethiopian addis sudan sudanese khartoum "
              "south_africa johannesburg cape_town congo drc kinshasa ghana uganda tanzania rwanda somalia mogadishu mali niger "
              "burkina chad cameroon senegal ivory_coast zimbabwe zambia mozambique angola namibia botswana malawi madagascar "
              "eritrea libya tunisia algeria morocco sahel darfur guinea sierra_leone liberia benin togo gabon",
    "north-america": "united_states u_s america american washington white_house congress senate canada canadian "
                     "ottawa toronto mexico mexican california texas new_york florida chicago los_angeles guatemala "
                     "honduras el_salvador nicaragua costa_rica panama cuba haiti jamaica dominican puerto_rico",
    "south-america": "brazil brazilian brasilia sao_paulo rio argentina argentine buenos_aires chile chilean santiago peru "
                     "peruvian lima colombia colombian bogota venezuela venezuelan caracas ecuador bolivia paraguay uruguay "
                     "guyana suriname ",
    "middle-east": "israel israeli gaza hamas west_bank jerusalem tel_aviv palestinian iran iranian tehran iraq iraqi baghdad "
                   "syria syrian damascus lebanon lebanese beirut hezbollah yemen yemeni houthi saudi riyadh qatar doha uae "
                   "dubai abu_dhabi kuwait bahrain oman jordan amman egypt egyptian cairo turkey turkish ankara istanbul "
                   "",
    "europe": "europe european eu brussels uk britain british london england scotland wales ireland irish dublin france "
              "french paris germany german berlin italy italian rome spain spanish madrid portugal netherlands dutch "
              "belgium switzerland austria poland polish warsaw ukraine ukrainian kyiv russia russian moscow "
              "kremlin sweden swedish norway finland denmark greece greek hungary czech romania bulgaria serbia croatia "
              "nato ",
    "oceania": "australia australian sydney melbourne canberra new_zealand auckland wellington papua_new_guinea fiji samoa "
               "tonga solomon_islands vanuatu ",
}
_INDEX = {region: set(words.split()) for region, words in _PLACES.items()}


def guess_regions(text: str) -> list[str]:
    """Regions of the places named in `text`, in order of first mention (world excluded).
    Multi-word places are written with "_" and only match as whole phrases; leaders'
    names are deliberately not places ("Trump" in a Ukraine story is not North America)."""
    t = " " + common.norm(text) + " "
    first: dict[str, int] = {}
    for region, words in _PLACES.items():
        for place in words.split():
            at = t.find(" " + place.replace("_", " ") + " ")
            if at >= 0:
                first[region] = min(first.get(region, 10**9), at)
    return sorted(first, key=first.get)
