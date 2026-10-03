from photo_cleaner.source import match_sidecar

M = {"photoTakenTime": {"timestamp": "1"}, "url": "u"}


def sc(title, url):
    return {**M, "title": title, "url": url}


def test_plain_and_supplemental_names():
    assert match_sidecar("a.jpg", {"a.jpg.json": sc("a.jpg", "1")})["url"] == "1"
    assert match_sidecar("a.jpg", {"a.jpg.supplemental-metadata.json": sc("a.jpg", "2")})["url"] == "2"


def test_counter_moves_behind_extension():
    jsons = {"a.jpg.json": sc("a.jpg", "1"), "a.jpg(1).json": sc("a.jpg", "2"),
             "a.jpg.supplemental-metadata(2).json": sc("a.jpg", "3")}
    assert match_sidecar("a.jpg", jsons)["url"] == "1"
    assert match_sidecar("a(1).jpg", jsons)["url"] == "2"
    assert match_sidecar("a(2).jpg", jsons)["url"] == "3"


def test_edited_uses_original_sidecar():
    assert match_sidecar("a-edited.jpg", {"a.jpg.json": sc("a.jpg", "1")})["url"] == "1"


def test_truncated_sidecar_name():
    name = "Family_reunion_at_grandmas_garden_summer_2024_final.jpg"
    jname = (name + ".supplemental-metadata")[:46] + ".json"
    assert match_sidecar(name, {jname: sc(name, "9")})["url"] == "9"


def test_title_fallback_and_album_metadata_ignored():
    jsons = {"metadata.json": {"title": "Holiday"}, "weird.json": sc("b.jpg", "7")}
    assert match_sidecar("b.jpg", jsons)["url"] == "7"
    assert match_sidecar("c.jpg", jsons) is None
