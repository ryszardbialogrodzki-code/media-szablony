"""Testy logiki autopilota (bez połączenia z Buffer). Uruchom: python -m pytest autopilot"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import uzupelnij as u

WAW = ZoneInfo("Europe/Warsaw")
RAW = "https://raw.githubusercontent.com/ryszardbialogrodzki-code/media-szablony/main/"


@pytest.fixture
def konf():
    return u.wczytaj_konfiguracje()


def mat(produkt, rodzaj, nazwa, pliki=None):
    if rodzaj == "film":
        klucz = f"{produkt}/filmy/{nazwa}.mp4"
        teksty = {"tiktok": "opis tt", "instagram": "opis ig", "youtube_tytul": "Tytuł #shorts", "youtube_opis": "opis"}
    elif rodzaj == "post":
        klucz = f"{produkt}/posty/{nazwa}"
        teksty = {"opis": "opis posta"}
    else:
        klucz = f"{produkt}/stories/{nazwa}.png"
        teksty = {"tekst": "story"}
    return u.Material(produkt, rodzaj, klucz, pliki or [klucz + ("/slajd_01.png" if rodzaj == "post" else "")], teksty)


def slot(dzien, godz, kanal, typ):
    return u.Slot(datetime(2026, 12, dzien, godz, 0, tzinfo=WAW), kanal, typ)


def uzycie(plik, kanal, typ, kiedy):
    return u.Uzycie(plik, kanal, typ, kiedy)


# --- konfiguracja i sloty -------------------------------------------------

def test_harmonogram_ma_caly_tydzien(konf):
    assert len(konf.tydzien) == 27
    assert konf.limit == 10 and konf.horyzont == 7
    assert [p.folder for p in konf.produkty][:3] == ["second-brain", "premium", "student-os"]
    assert all(p.link.startswith("https://www.naffy.io/") for p in konf.produkty)


def test_sloty_od_jutra_przez_7_dni(konf):
    sloty = u.wyznacz_sloty(konf, date(2026, 11, 23))  # poniedziałek
    assert sloty[0].kiedy.date() == date(2026, 11, 24)
    assert sloty[-1].kiedy.date() == date(2026, 11, 30)
    assert len(sloty) == 27
    assert sloty == sorted(sloty, key=lambda s: s.kiedy)


def test_offset_zimowy(konf):
    sloty = u.wyznacz_sloty(konf, date(2026, 11, 23))
    reels = [s for s in sloty if s.typ == "reels"][0]
    assert u.czas_iso(reels.kiedy) == "2026-11-24T18:00:00+01:00"


def test_zmiana_czasu_wiosna_28_03_2027(konf):
    sloty = u.wyznacz_sloty(konf, date(2027, 3, 26))
    sob = [s for s in sloty if s.kiedy.date() == date(2027, 3, 27) and s.typ == "story"][0]
    nd = [s for s in sloty if s.kiedy.date() == date(2027, 3, 28) and s.typ == "story"][0]
    assert u.czas_iso(sob.kiedy) == "2027-03-27T10:00:00+01:00"
    assert u.czas_iso(nd.kiedy) == "2027-03-28T10:00:00+02:00"


def test_zmiana_czasu_jesien_31_10_2027(konf):
    sloty = u.wyznacz_sloty(konf, date(2027, 10, 29))
    sob = [s for s in sloty if s.kiedy.date() == date(2027, 10, 30) and s.typ == "story"][0]
    nd = [s for s in sloty if s.kiedy.date() == date(2027, 10, 31) and s.typ == "story"][0]
    assert u.czas_iso(sob.kiedy) == "2027-10-30T10:00:00+02:00"
    assert u.czas_iso(nd.kiedy) == "2027-10-31T10:00:00+01:00"


def test_zly_wpis_w_harmonogramie():
    with pytest.raises(u.BladKonfiguracji):
        u.parsuj_wpis(0, "18:00 tiktok story")
    with pytest.raises(u.BladKonfiguracji):
        u.parsuj_wpis(0, "25:00 instagram story")
    assert u.numer_dnia("Środa") == 2 and u.numer_dnia("piątek") == 4 and u.numer_dnia("nd") == 6


# --- limit i zajęte sloty -------------------------------------------------

def test_limit_10_liczy_to_co_juz_jest(konf):
    stories = [mat("second-brain", "story", f"{i:02d}") for i in range(1, 6)]
    sloty = [slot(1, 8, "instagram", "story"), slot(2, 8, "instagram", "story"), slot(3, 8, "instagram", "story")]
    d = u.zaplanuj(sloty, stories, [], set(), {"instagram": 9}, konf)
    assert [x.status for x in d] == ["zaplanowany", "pusty", "pusty"]
    assert "limit 10" in d[1].powod


def test_zajety_slot_pomijany(konf):
    s = slot(1, 18, "tiktok", "film")
    d = u.zaplanuj([s], [mat("premium", "film", "wersja_A")], [], {s.klucz}, {}, konf)
    assert d[0].status == "zajety"


def test_zajete_i_kolejka_z_buffer(konf):
    posty = [
        {"channelId": konf.kanaly["tiktok"], "status": "scheduled", "dueAt": "2026-12-01T17:00:00.000Z"},
        {"channelId": konf.kanaly["tiktok"], "status": "draft", "dueAt": "2026-12-02T17:00:00.000Z"},
        {"channelId": konf.kanaly["tiktok"], "status": "sent", "dueAt": "2026-11-20T17:00:00.000Z"},
    ]
    zajete, kolejka = u.zajete_i_kolejka(posty, konf)
    assert ("tiktok", "2026-12-01 18:00") in zajete
    assert ("tiktok", "2026-12-02 18:00") not in zajete  # szkic nie blokuje
    assert kolejka["tiktok"] == 1


# --- brak powtórek --------------------------------------------------------

def test_film_bez_powtorek_na_kanale(konf):
    film = mat("second-brain", "film", "wersja_A")
    uz = [uzycie(film.klucz, "tiktok", "film", datetime(2026, 11, 25, 18, tzinfo=WAW))]
    sloty = [slot(1, 18, "tiktok", "film"), slot(1, 18, "youtube", "film")]
    d = u.zaplanuj(sloty, [film], uz, set(), {}, konf)
    assert d[0].status == "pusty" and "brak nowego filmu" in d[0].powod
    assert d[1].status == "zaplanowany"  # ten sam film na innym kanale jest OK


def test_dwa_sloty_dwa_rozne_filmy(konf):
    filmy = [mat("premium", "film", "wersja_A"), mat("premium", "film", "wersja_B")]
    sloty = [slot(1, 18, "tiktok", "film"), slot(3, 18, "tiktok", "film"), slot(5, 18, "tiktok", "film")]
    d = u.zaplanuj(sloty, filmy, [], set(), {}, konf)
    assert [x.material.nazwa if x.material else None for x in d] == ["wersja_A.mp4", "wersja_B.mp4", None]


def test_story_filmu_nie_blokuje_reelsa(konf):
    film = mat("second-brain", "film", "wersja_A")
    uz = [uzycie(film.klucz, "instagram", "story", datetime(2026, 11, 25, 20, tzinfo=WAW))]
    d = u.zaplanuj([slot(1, 18, "instagram", "reels")], [film], uz, set(), {}, konf)
    assert d[0].status == "zaplanowany"


def test_post_uzyty_po_obrazku_z_buffer(konf):
    post = mat("second-brain", "post", "01_chaos", ["second-brain/posty/01_chaos/slajd_01.png"])
    uz = [uzycie("second-brain/posty/01_chaos/slajd_01.png", "instagram", "post", datetime(2026, 11, 25, tzinfo=WAW))]
    d = u.zaplanuj([slot(2, 11, "instagram", "post")], [post], uz, set(), {}, konf)
    assert d[0].status == "pusty"


def test_slajd_jako_story_nie_blokuje_posta(konf):
    post = mat("second-brain", "post", "03_postep", ["second-brain/posty/03_postep/slajd_01.png"])
    uz = [uzycie("second-brain/posty/03_postep/slajd_01.png", "instagram", "story", datetime(2026, 11, 26, tzinfo=WAW))]
    d = u.zaplanuj([slot(2, 11, "instagram", "post")], [post], uz, set(), {}, konf)
    assert d[0].status == "zaplanowany"


# --- rotacja produktów ----------------------------------------------------

def test_rotacja_produktow(konf):
    posty = [mat(p, "post", n) for p in ("second-brain", "premium", "student-os") for n in ("01_a", "02_b")]
    uz = [uzycie("second-brain/posty/00_stary", "instagram", "post", datetime(2026, 11, 27, tzinfo=WAW))]
    sloty = [slot(d, 11, "instagram", "post") for d in (1, 2, 3, 4)]
    d = u.zaplanuj(sloty, posty, uz, set(), {}, konf)
    assert [x.material.produkt for x in d] == ["premium", "student-os", "second-brain", "premium"]


def test_rotacja_pomija_produkt_bez_materialu(konf):
    filmy = [mat("second-brain", "film", "f1"), mat("second-brain", "film", "f2"), mat("student-os", "film", "f1")]
    sloty = [slot(1, 18, "tiktok", "film"), slot(3, 18, "tiktok", "film"), slot(5, 18, "tiktok", "film")]
    d = u.zaplanuj(sloty, filmy, [], set(), {}, konf)
    assert [x.material.produkt for x in d] == ["second-brain", "student-os", "second-brain"]


# --- powtórki stories -----------------------------------------------------

def test_story_najpierw_nieuzyte(konf):
    a, b = mat("second-brain", "story", "01_hook"), mat("second-brain", "story", "02_funkcja")
    uz = [uzycie(a.klucz, "instagram", "story", datetime(2026, 11, 1, 8, 30, tzinfo=WAW))]
    d = u.zaplanuj([slot(1, 8, "instagram", "story")], [a, b], uz, set(), {}, konf)
    assert d[0].material is b and not d[0].powtorka


def test_story_powtorka_po_14_dniach(konf):
    s = slot(20, 8, "instagram", "story")
    stara = mat("premium", "story", "01_hook")
    swieza = mat("second-brain", "story", "01_hook")
    uz = [uzycie(stara.klucz, "instagram", "story", s.kiedy - timedelta(days=15)),
          uzycie(swieza.klucz, "instagram", "story", s.kiedy - timedelta(days=13))]
    d = u.zaplanuj([s], [stara, swieza], uz, set(), {}, konf)
    assert d[0].material is stara and d[0].powtorka


def test_story_bez_powtorki_przed_14_dniami(konf):
    s = slot(20, 8, "instagram", "story")
    st = mat("second-brain", "story", "01_hook")
    uz = [uzycie(st.klucz, "instagram", "story", s.kiedy - timedelta(days=13))]
    d = u.zaplanuj([s], [st], uz, set(), {}, konf)
    assert d[0].status == "pusty" and "14" in d[0].powod


def test_story_powtorka_najdawniej_uzytego(konf):
    s = slot(20, 8, "instagram", "story")
    a, b = mat("second-brain", "story", "01_hook"), mat("second-brain", "story", "02_funkcja")
    uz = [uzycie(a.klucz, "instagram", "story", s.kiedy - timedelta(days=20)),
          uzycie(b.klucz, "instagram", "story", s.kiedy - timedelta(days=30))]
    d = u.zaplanuj([s], [a, b], uz, set(), {}, konf)
    assert d[0].material is b


def test_story_cta_wieczorem(konf):
    hook, cta = mat("second-brain", "story", "01_hook"), mat("second-brain", "story", "06_cta")
    d = u.zaplanuj([slot(2, 20, "instagram", "story")], [hook, cta], [], set(), {}, konf)
    assert d[0].material is cta
    d = u.zaplanuj([slot(2, 8, "instagram", "story")], [hook, cta], [], set(), {}, konf)
    assert d[0].material is hook


# --- historia: Buffer, uzyte.json, HARMONOGRAM.md -------------------------

def test_uzycia_z_buffer(konf):
    posty = [{"channelId": konf.kanaly["instagram"], "status": "scheduled", "dueAt": "2026-11-24T07:30:00.000Z",
              "assets": [{"source": RAW + "second-brain/filmy/okladka_A.png"}], "metadata": {"type": "story"}},
             {"channelId": konf.kanaly["youtube"], "status": "sent", "dueAt": "2026-11-25T17:00:00.000Z",
              "assets": [{"source": RAW + "premium/filmy/wersja%20A.mp4"}], "metadata": None},
             {"channelId": konf.kanaly["instagram"], "status": "draft", "dueAt": None,
              "assets": [{"source": RAW + "x.png"}], "metadata": {"type": "post"}}]
    uz = u.uzycia_z_buffer(posty, konf)
    assert [(x.plik, x.kanal, x.typ) for x in uz] == [
        ("second-brain/filmy/okladka_A.png", "instagram", "story"),
        ("premium/filmy/wersja A.mp4", "youtube", "film")]
    assert uz[0].kiedy.hour == 8 and uz[0].kiedy.minute == 30


def test_uzycia_z_tabeli(konf):
    tekst = (u.REPO / "HARMONOGRAM.md").read_text(encoding="utf-8")
    uz = u.uzycia_z_tabeli(tekst, konf)
    pliki = {(x.plik, x.kanal, x.typ) for x in uz}
    assert ("second-brain/posty/01_chaos", "instagram", "post") in pliki
    assert ("second-brain/filmy/wersja_A.mp4", "tiktok", "film") in pliki
    assert ("second-brain/filmy/wersja_A.mp4", "youtube", "film") in pliki
    assert ("second-brain/filmy/wersja_A.mp4", "instagram", "reels") in pliki


def test_dopisz_do_tabeli():
    tekst = "# X\n\n## Zaplanowane / użyte\n| Data | Kanał | Typ | Plik |\n|---|---|---|---|\n| a | b | c | d |\n\nNiewykorzystane:\n- coś\n"
    d = u.Decyzja(slot(1, 18, "tiktok", "film"), "zaplanowany", mat("premium", "film", "wersja_A"), id_posta="1")
    nowy = u.dopisz_do_tabeli(tekst, [d])
    assert "| a | b | c | d |\n| 2026-12-01 18:00 | TikTok | film | premium/filmy/wersja_A.mp4 |\n\nNiewykorzystane:" in nowy


# --- biblioteka i treść posta ---------------------------------------------

def test_biblioteka_wg_konwencji(tmp_path, konf):
    f = tmp_path / "premium" / "filmy"
    f.mkdir(parents=True)
    (f / "wersja_A.mp4").write_bytes(b"x")
    (f / "wersja_A_bez_muzyki.mp4").write_bytes(b"x")
    (f / "wersja_A.txt").write_text("Bardzo fajny tytuł\nOpis z linkiem.\nLink w bio", encoding="utf-8")
    (f / "wersja_A.tiktok.txt").write_text("Opis TikTok", encoding="utf-8")
    (f / "wersja_C.mp4").write_bytes(b"x")  # bez opisu
    (f / "okladka_A.png").write_bytes(b"x")
    (f / "storyboard_A.png").write_bytes(b"x")
    p = tmp_path / "premium" / "posty" / "01_x"
    p.mkdir(parents=True)
    (p / "slajd_02.png").write_bytes(b"x")
    (p / "slajd_01.png").write_bytes(b"x")
    (p / "opis.txt").write_text("Opis posta", encoding="utf-8")
    s = tmp_path / "premium" / "stories"
    s.mkdir()
    (s / "01_hook.png").write_bytes(b"x")
    (s / "podglad.png").write_bytes(b"x")

    bib, ostrz = u.zbuduj_biblioteke(tmp_path, konf)
    klucze = {(m.rodzaj, m.klucz) for m in bib}
    assert klucze == {("film", "premium/filmy/wersja_A.mp4"), ("post", "premium/posty/01_x"),
                      ("story", "premium/stories/01_hook.png"), ("story", "premium/filmy/okladka_A.png")}
    film = next(m for m in bib if m.rodzaj == "film")
    assert film.teksty["tiktok"] == "Opis TikTok"
    assert film.teksty["instagram"] == "Opis z linkiem.\nLink w bio"
    assert film.teksty["youtube_tytul"] == "Bardzo fajny tytuł #shorts"
    assert film.teksty["youtube_opis"] == "Opis z linkiem."
    post = next(m for m in bib if m.rodzaj == "post")
    assert post.pliki == ["premium/posty/01_x/slajd_01.png", "premium/posty/01_x/slajd_02.png"]
    assert any("wersja_C" in o for o in ostrz)


def test_tytul_youtube_max_60():
    assert u.tytul_youtube("Krótki") == "Krótki #shorts"
    assert u.tytul_youtube("Już jest #shorts") == "Już jest #shorts"
    dlugi = u.tytul_youtube("a" * 100)
    assert len(dlugi) <= 60 and dlugi.endswith(" #shorts")


def test_input_story_z_naklejka(konf):
    d = u.Decyzja(slot(1, 8, "instagram", "story"), "zaplanowany", mat("student-os", "story", "01_hook"))
    w = u.zbuduj_input(d, konf)
    assert w["mode"] == "customScheduled" and w["schedulingType"] == "automatic"
    assert w["dueAt"] == "2026-12-01T08:00:00+01:00"
    ig = w["metadata"]["instagram"]
    assert ig == {"type": "story", "shouldShareToFeed": False,
                  "link": "https://www.naffy.io/szablony-notion/student-os-system-do-nauki-w-notion-nqc"}
    assert w["assets"][0]["image"]["url"] == RAW + "student-os/stories/01_hook.png"
    assert "saveToDraft" not in w


def test_input_youtube_z_linkiem(konf):
    d = u.Decyzja(slot(1, 18, "youtube", "film"), "zaplanowany", mat("second-brain", "film", "wersja_A"))
    w = u.zbuduj_input(d, konf)
    assert w["text"].endswith("Szablon (79 zł): https://www.naffy.io/szablony-notion/second-brain-twoj-drugi-mozg-podstawa")
    assert w["metadata"]["youtube"] == {"title": "Tytuł #shorts", "categoryId": "26", "privacy": "public",
                                        "madeForKids": False}
    assert w["assets"] == [{"video": {"url": RAW + "second-brain/filmy/wersja_A.mp4"}}]


def test_input_reels_i_post(konf):
    r = u.zbuduj_input(u.Decyzja(slot(1, 18, "instagram", "reels"), "zaplanowany",
                                 mat("premium", "film", "wersja_A")), konf)
    assert r["metadata"]["instagram"] == {"type": "reel", "shouldShareToFeed": True}
    p = u.zbuduj_input(u.Decyzja(slot(1, 11, "instagram", "post"), "zaplanowany",
                                 mat("premium", "post", "01_x", ["a/1.png", "a/2.png"])), konf)
    assert len(p["assets"]) == 2 and p["metadata"]["instagram"]["type"] == "post"
