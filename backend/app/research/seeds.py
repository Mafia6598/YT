"""Candidate search queries per channel category.

These are only *names of things to look up*. No facts live here: every topic is
kept only if a real Wikipedia article is found for it at research time, and all
facts used later come from that fetched article and its cited references.
"""

CATEGORIES: dict[str, list[str]] = {
    "Untold History": ["Ghost Army", "Night Witches", "Harlem Hellfighters", "Navajo code talker",
                       "Operation Highjump"],
    "Dark History": ["Dancing plague of 1518", "Radium Girls", "Boston molasses disaster",
                     "Halifax Explosion", "Great Stink"],
    "Historical Mysteries": ["Dyatlov Pass incident", "Roanoke Colony", "Mary Celeste",
                             "Voynich manuscript", "Tamam Shud case", "D. B. Cooper"],
    "Forgotten Events": ["Year Without a Summer", "Tunguska event", "Carrington Event",
                         "Johnstown Flood", "Great Emu War"],
    "Soviet Union History": ["Kyshtym disaster", "Tsar Bomba", "Nedelin catastrophe",
                             "Kola Superdeep Borehole", "Vasily Arkhipov", "Duga radar"],
    "Secret Operations": ["Operation Mincemeat", "Operation Paperclip", "Project MKUltra",
                          "Operation Northwoods", "Project Azorian", "Operation Gold"],
    "Historical Figures": ["Hiroo Onoda", "Simo Häyhä", "Grigori Rasputin", "Mata Hari",
                           "Witold Pilecki"],
    "Wars and Political Events": ["Battle of Los Angeles", "Christmas truce", "Cuban Missile Crisis",
                                  "Winter War", "Able Archer 83"],
    "Billionaires and Historical Wealth": ["Mansa Musa", "Jakob Fugger", "Hetty Green",
                                           "John D. Rockefeller", "Andrew Carnegie"],
}
