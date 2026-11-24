"""TEST FIXTURE ONLY - used to exercise the pipeline when Wikipedia is unreachable
(e.g. in a sandbox). Real runs always fetch the live article. Do not publish."""

DYATLOV_TEXT = """The Dyatlov Pass incident was an event in which nine Soviet ski hikers died in the northern Ural Mountains between 1 and 2 February 1959, under undetermined circumstances. The group was led by Igor Dyatlov, and the pass where the incident occurred was later named after him. The hikers had made camp on the slopes of Kholat Syakhl, a mountain whose name in the Mansi language is often translated as Dead Mountain.

== Background ==
The expedition was organized by students and graduates of the Ural Polytechnical Institute in Sverdlovsk. Their goal was to reach Otorten, a mountain about ten kilometres north of the site of the incident. The route was rated Category III, the most difficult category at the time. One member of the group, Yuri Yudin, turned back early because of illness, and he was the only one to survive the expedition.

== Search and discovery ==
When the group failed to send a telegram on schedule, relatives pressed the institute to act, and a search began on 20 February 1959. Searchers found the abandoned tent on 26 February. The tent had been cut open from the inside, and most of the hikers' belongings, including shoes, had been left behind. Footprints led down the slope toward the edge of a nearby forest. Searchers found the first bodies near a large cedar tree, along with the remains of a small fire. The last four hikers were not found until May, buried under snow in a ravine.

== Investigation ==
A Soviet criminal investigation concluded that a compelling natural force had caused the deaths, and the case was closed. Some of the hikers had severe injuries, including skull and chest fractures, while others had died of hypothermia. For decades the lack of a clear explanation fueled many theories. In 2019 Russian authorities reopened the investigation, and in 2020 they announced that an avalanche was the most likely cause.

== Scientific analysis ==
In 2021 researchers Johan Gaume and Alexander Puzrin published a study in the journal Communications Earth and Environment. Using computer models, they argued that a small slab avalanche could explain the tent damage and some of the injuries. The study did not settle every question, and debate about the incident continues.

== See also ==
List of unsolved deaths
"""

PACKET = {
    "title": "Dyatlov Pass incident", "url": "https://en.wikipedia.org/wiki/Dyatlov_Pass_incident",
    "revision": 0, "text": DYATLOV_TEXT, "references": [],
    "strong_references": [{"title": "Unravelling the mechanisms of the Dyatlov Pass incident",
                           "author": "Gaume, Puzrin", "publisher": "Communications Earth & Environment",
                           "date": "2021", "url": "https://doi.org/10.1038/s43247-020-00081-8"}],
    "related_articles": [], "source_count": 2, "verification_note": "fixture",
}
