#!/usr/bin/env python3
"""Autopilot kolejki Buffer.

Uzupełnia wolne sloty z autopilot/harmonogram.json postami z tego repo
(filmy, posty na feed, stories) i tworzy je w Buffer jako zaplanowane.

Uruchomienie:
    python autopilot/uzupelnij.py --dry-run   # tylko plan, nic nie tworzy w Buffer
    python autopilot/uzupelnij.py             # tworzy posty w Buffer

Klucz API bierze ze zmiennej środowiskowej BUFFER_API_KEY.
Klucza nigdy nie wypisuje i nigdzie go nie zapisuje.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo

import requests

API_URL = "https://api.buffer.com"
KOMUNIKAT_KLUCZ = (
    "Klucz Buffer wygasł albo jest błędny — wygeneruj nowy na "
    "publish.buffer.com/settings/api i podmień sekret BUFFER_API_KEY w GitHub"
)

KATALOG = Path(__file__).resolve().parent
REPO = KATALOG.parent
PLIK_HARMONOGRAMU = KATALOG / "harmonogram.json"
PLIK_UZYTYCH = KATALOG / "uzyte.json"
PLIK_OSTATNIE = KATALOG / "ostatnie_uruchomienie.txt"
PLIK_TABELI = REPO / "HARMONOGRAM.md"
NAGLOWEK_TABELI = "## Zaplanowane / użyte"

TYPY_KANALU = {"instagram": ("story", "post", "reels"), "tiktok": ("film",), "youtube": ("film",)}
NAZWA_KANALU = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube"}
SKROT_KANALU = {"instagram": "IG", "tiktok": "TikTok", "youtube": "YT"}
KOLEJNOSC_KANALOW = {"instagram": 0, "tiktok": 1, "youtube": 2}
DNI_SKROT = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]
DNI_PREFIKSY = [("pon", 0), ("wt", 1), ("sr", 2), ("cz", 3), ("pi", 4), ("pt", 4),
                ("so", 5), ("ni", 6), ("nd", 6)]

OBRAZY = {".png", ".jpg", ".jpeg"}
WIDEO = {".mp4", ".mov"}
MAX_ROZMIAR = 50 * 1024 * 1024
MAX_SLAJDOW = 10
MAX_TYTUL_YT = 60
STATUSY_DO_POBRANIA = ["scheduled", "sending", "sent", "error", "needs_approval"]
STATUSY_W_KOLEJCE = {"scheduled", "sending", "needs_approval"}
MAX_STRON = 30


class BladKonfiguracji(Exception):
    pass


class BladKlucza(Exception):
    pass


class BladApi(Exception):
    pass


# ---------------------------------------------------------------------------
# Konfiguracja
# ---------------------------------------------------------------------------

@dataclass
class Produkt:
    folder: str
    nazwa: str
    link: str
    cena: str = ""


@dataclass
class WpisTygodnia:
    dzien: int  # 0 = poniedziałek
    godzina: int
    minuta: int
    kanal: str
    typ: str


@dataclass
class Konfiguracja:
    strefa: ZoneInfo
    horyzont: int
    limit: int
    powtorka_dni: int
    organizacja: str
    kanaly: dict[str, str]
    repo_raw: str
    produkty: list[Produkt]
    youtube: dict
    tydzien: list[WpisTygodnia]

    def produkt(self, folder: str) -> Produkt:
        for p in self.produkty:
            if p.folder == folder:
                return p
        raise KeyError(folder)


def bez_ogonkow(tekst: str) -> str:
    tekst = tekst.replace("ł", "l").replace("Ł", "L")
    tekst = unicodedata.normalize("NFKD", tekst).encode("ascii", "ignore").decode()
    return tekst.lower().strip()


def numer_dnia(nazwa: str) -> int:
    n = bez_ogonkow(nazwa)
    for prefiks, numer in DNI_PREFIKSY:
        if n.startswith(prefiks):
            return numer
    raise BladKonfiguracji(f"Nieznany dzień tygodnia w harmonogram.json: „{nazwa}”")


def parsuj_wpis(dzien: int, tekst: str) -> WpisTygodnia:
    czesci = tekst.split()
    if len(czesci) != 3:
        raise BladKonfiguracji(
            f"Wpis „{tekst}” w harmonogram.json ma mieć postać „GODZINA KANAŁ TYP”, np. „18:00 instagram reels”")
    godz, kanal, typ = czesci[0], bez_ogonkow(czesci[1]), bez_ogonkow(czesci[2])
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", godz)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise BladKonfiguracji(f"Zła godzina „{godz}” we wpisie „{tekst}” (wzór: 08:30)")
    kanal = {"ig": "instagram", "yt": "youtube"}.get(kanal, kanal)
    typ = {"reel": "reels", "rolka": "reels", "stories": "story"}.get(typ, typ)
    if kanal not in TYPY_KANALU:
        raise BladKonfiguracji(f"Nieznany kanał „{czesci[1]}” we wpisie „{tekst}” (instagram, tiktok, youtube)")
    if typ not in TYPY_KANALU[kanal]:
        raise BladKonfiguracji(
            f"Typ „{czesci[2]}” nie pasuje do kanału {kanal} we wpisie „{tekst}” "
            f"(dozwolone: {', '.join(TYPY_KANALU[kanal])})")
    return WpisTygodnia(dzien, int(m.group(1)), int(m.group(2)), kanal, typ)


def konfiguracja_z_danych(dane: dict) -> Konfiguracja:
    try:
        produkty = [Produkt(p["folder"], p["nazwa"], p["link"], p.get("cena") or "") for p in dane["produkty"]]
        tydzien = []
        for dzien, wpisy in dane["tydzien"].items():
            nr = numer_dnia(dzien)
            for w in wpisy:
                tydzien.append(parsuj_wpis(nr, w))
        kanaly = {bez_ogonkow(k): v for k, v in dane["kanaly"].items()}
        repo_raw = dane["repo_raw"]
        if not repo_raw.endswith("/"):
            repo_raw += "/"
        return Konfiguracja(
            strefa=ZoneInfo(dane.get("strefa_czasowa", "Europe/Warsaw")),
            horyzont=int(dane.get("horyzont_dni", 7)),
            limit=int(dane.get("limit_na_kanal", 10)),
            powtorka_dni=int(dane.get("story_powtorka_po_dniach", 14)),
            organizacja=dane["organizacja_buffer"],
            kanaly=kanaly,
            repo_raw=repo_raw,
            produkty=produkty,
            youtube=dane.get("youtube", {}),
            tydzien=tydzien,
        )
    except KeyError as e:
        raise BladKonfiguracji(f"W harmonogram.json brakuje pola {e}") from e


def wczytaj_konfiguracje(sciezka: Path = PLIK_HARMONOGRAMU) -> Konfiguracja:
    try:
        dane = json.loads(sciezka.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise BladKonfiguracji(
            f"Plik harmonogram.json ma błąd w linii {e.lineno} (kolumna {e.colno}): {e.msg}. "
            "Najczęściej to brakujący lub nadmiarowy przecinek albo cudzysłów.") from e
    return konfiguracja_z_danych(dane)


# ---------------------------------------------------------------------------
# Sloty
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Slot:
    kiedy: datetime  # ze strefą czasową
    kanal: str
    typ: str

    @property
    def klucz(self) -> tuple[str, str]:
        return (self.kanal, self.kiedy.strftime("%Y-%m-%d %H:%M"))


def wyznacz_sloty(konf: Konfiguracja, dzis: date) -> list[Slot]:
    """Sloty od jutra przez `horyzont` dni, w czasie lokalnym ze strefą."""
    sloty = []
    for i in range(1, konf.horyzont + 1):
        d = dzis + timedelta(days=i)
        for w in konf.tydzien:
            if w.dzien == d.weekday():
                kiedy = datetime(d.year, d.month, d.day, w.godzina, w.minuta, tzinfo=konf.strefa)
                sloty.append(Slot(kiedy, w.kanal, w.typ))
    sloty = sorted(set(sloty), key=lambda s: (s.kiedy, KOLEJNOSC_KANALOW[s.kanal], s.typ))
    return sloty


def czas_iso(kiedy: datetime) -> str:
    return kiedy.isoformat(timespec="seconds")


def parsuj_czas(tekst: str | None, strefa: ZoneInfo) -> datetime | None:
    if not tekst:
        return None
    dt = datetime.fromisoformat(tekst.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=strefa)
    return dt.astimezone(strefa)


# ---------------------------------------------------------------------------
# Biblioteka materiałów
# ---------------------------------------------------------------------------

@dataclass
class Material:
    produkt: str          # folder produktu, np. "premium"
    rodzaj: str           # film / post / story
    klucz: str            # ścieżka w repo (plik albo folder posta)
    pliki: list[str]      # ścieżki plików do wysłania
    teksty: dict = field(default_factory=dict)

    @property
    def nazwa(self) -> str:
        return self.klucz.rsplit("/", 1)[-1]


def pominac(nazwa: str) -> bool:
    n = nazwa.lower()
    return (n.endswith("_bez_muzyki.mp4") or n.startswith("storyboard") or n.startswith("podglad")
            or n.startswith(".") or n.startswith("_"))


def czytaj_tekst(sciezka: Path) -> str | None:
    if not sciezka.is_file():
        return None
    tekst = sciezka.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    return tekst or None


def rozdziel_tytul(tekst: str) -> tuple[str, str]:
    linie = tekst.split("\n", 1)
    return linie[0].strip(), (linie[1].strip() if len(linie) > 1 else "")


def tytul_youtube(tytul: str) -> str:
    baza = re.sub(r"\s*#shorts\b", "", tytul, flags=re.IGNORECASE)
    baza = " ".join(baza.split())
    miejsce = MAX_TYTUL_YT - len(" #shorts")
    if len(baza) > miejsce:
        baza = baza[:miejsce].rstrip(" ,.;:-–—")
    return f"{baza} #shorts"


def bez_linku_w_bio(tekst: str) -> str:
    linie = [l for l in tekst.split("\n") if "link w bio" not in l.lower() and "linkiem w bio" not in l.lower()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(linie)).strip()


def teksty_filmu(plik: Path) -> dict:
    stem = plik.stem
    glowny = czytaj_tekst(plik.with_name(stem + ".txt"))
    tytul, reszta = rozdziel_tytul(glowny) if glowny else ("", "")
    teksty = {}
    tiktok = czytaj_tekst(plik.with_name(stem + ".tiktok.txt")) or reszta
    instagram = czytaj_tekst(plik.with_name(stem + ".instagram.txt")) or reszta
    yt = czytaj_tekst(plik.with_name(stem + ".youtube.txt"))
    if yt:
        yt_tytul, yt_opis = rozdziel_tytul(yt)
    else:
        yt_tytul, yt_opis = tytul, bez_linku_w_bio(reszta)
    if tiktok:
        teksty["tiktok"] = tiktok
    if instagram:
        teksty["instagram"] = instagram
    if yt_tytul:
        teksty["youtube_tytul"] = tytul_youtube(yt_tytul)
        teksty["youtube_opis"] = yt_opis
    return teksty


def _za_duzy(p: Path, repo: Path, ostrzezenia: list[str]) -> bool:
    if p.stat().st_size > MAX_ROZMIAR:
        ostrzezenia.append(f"Pominięto {p.relative_to(repo).as_posix()} – plik ma ponad 50 MB.")
        return True
    return False


def zbuduj_biblioteke(repo: Path, konf: Konfiguracja) -> tuple[list[Material], list[str]]:
    biblioteka: list[Material] = []
    ostrzezenia: list[str] = []
    rel = lambda p: p.relative_to(repo).as_posix()

    for prod in konf.produkty:
        baza = repo / prod.folder
        if not baza.is_dir():
            continue
        filmy_dir, posty_dir, stories_dir = baza / "filmy", baza / "posty", baza / "stories"
        nazwa_story = f"{prod.nazwa} – szablon Notion"

        # Filmy
        if filmy_dir.is_dir():
            for p in sorted(filmy_dir.iterdir()):
                if not p.is_file() or p.suffix.lower() not in WIDEO or pominac(p.name):
                    continue
                if _za_duzy(p, repo, ostrzezenia):
                    continue
                teksty = teksty_filmu(p)
                if not teksty:
                    ostrzezenia.append(f"Film {rel(p)} nie ma pliku opisu {p.stem}.txt – pominięty.")
                    continue
                biblioteka.append(Material(prod.folder, "film", rel(p), [rel(p)], teksty))

        # Posty na feed
        if posty_dir.is_dir():
            for d in sorted(posty_dir.iterdir()):
                if not d.is_dir() or pominac(d.name):
                    continue
                obrazy = [p for p in sorted(d.iterdir())
                          if p.is_file() and p.suffix.lower() in OBRAZY and not pominac(p.name)]
                if not obrazy:
                    continue
                opis = czytaj_tekst(d / "opis.txt")
                if not opis:
                    ostrzezenia.append(f"Post {rel(d)} nie ma pliku opis.txt – pominięty.")
                    continue
                if any(_za_duzy(p, repo, ostrzezenia) for p in obrazy):
                    continue
                if len(obrazy) > MAX_SLAJDOW:
                    ostrzezenia.append(f"Post {rel(d)} ma {len(obrazy)} obrazów – użyję pierwszych {MAX_SLAJDOW}.")
                    obrazy = obrazy[:MAX_SLAJDOW]
                biblioteka.append(Material(prod.folder, "post", rel(d), [rel(p) for p in obrazy], {"opis": opis}))

        # Stories: folder stories/ i okładki filmów
        kandydaci = []
        if stories_dir.is_dir():
            kandydaci += [p for p in sorted(stories_dir.iterdir())
                          if p.is_file() and p.suffix.lower() in OBRAZY | WIDEO]
        if filmy_dir.is_dir():
            kandydaci += [p for p in sorted(filmy_dir.iterdir())
                          if p.is_file() and p.suffix.lower() in OBRAZY and p.name.lower().startswith("okladka")]
        for p in kandydaci:
            if pominac(p.name) or _za_duzy(p, repo, ostrzezenia):
                continue
            wlasny = czytaj_tekst(p.with_suffix(".txt")) if p.parent == stories_dir else None
            tekst = rozdziel_tytul(wlasny)[0] if wlasny else nazwa_story
            biblioteka.append(Material(prod.folder, "story", rel(p), [rel(p)], {"tekst": tekst}))

    return biblioteka, ostrzezenia


# ---------------------------------------------------------------------------
# Historia użycia
# ---------------------------------------------------------------------------

@dataclass
class Uzycie:
    plik: str
    kanal: str
    typ: str  # story / post / reels / film
    kiedy: datetime | None
    zrodlo: str = ""


def sciezka_z_url(url: str, repo_raw: str) -> str | None:
    if not url or not url.startswith(repo_raw):
        return None
    return unquote(url[len(repo_raw):].split("?", 1)[0])


def typ_z_buffer(kanal: str, typ_metadanych: str | None) -> str:
    if kanal != "instagram":
        return "film"
    return {"story": "story", "reel": "reels"}.get((typ_metadanych or "").lower(), "post")


def uzycia_z_buffer(posty: list[dict], konf: Konfiguracja) -> list[Uzycie]:
    kanal_po_id = {v: k for k, v in konf.kanaly.items()}
    wynik = []
    for p in posty:
        if p.get("status") == "draft":
            continue
        kanal = kanal_po_id.get(p.get("channelId"))
        if not kanal:
            continue
        typ = typ_z_buffer(kanal, (p.get("metadata") or {}).get("type"))
        kiedy = parsuj_czas(p.get("dueAt") or p.get("sentAt") or p.get("createdAt"), konf.strefa)
        for a in p.get("assets") or []:
            sciezka = sciezka_z_url(a.get("source", ""), konf.repo_raw)
            if sciezka:
                wynik.append(Uzycie(sciezka, kanal, typ, kiedy, "buffer"))
    return wynik


def wczytaj_uzyte(sciezka: Path = PLIK_UZYTYCH) -> list[dict]:
    if not sciezka.is_file():
        return []
    dane = json.loads(sciezka.read_text(encoding="utf-8-sig"))
    return dane.get("wpisy", []) if isinstance(dane, dict) else dane


def uzycia_z_pliku(wpisy: list[dict], konf: Konfiguracja) -> list[Uzycie]:
    return [Uzycie(w["plik"], w["kanal"], w["typ"], parsuj_czas(w.get("data"), konf.strefa), "uzyte.json")
            for w in wpisy if w.get("plik") and w.get("kanal")]


KANAL_Z_TABELI = {"ig": "instagram", "instagram": "instagram", "tiktok": "tiktok", "tt": "tiktok",
                  "yt": "youtube", "youtube": "youtube"}


def uzycia_z_tabeli(tekst: str, konf: Konfiguracja) -> list[Uzycie]:
    wynik = []
    w_sekcji = False
    for linia in tekst.splitlines():
        if linia.startswith("## "):
            w_sekcji = linia.strip() == NAGLOWEK_TABELI
            continue
        if not w_sekcji or not linia.startswith("|"):
            continue
        kolumny = [k.strip().strip("`") for k in linia.strip().strip("|").split("|")]
        if len(kolumny) < 4 or not re.match(r"\d{4}-\d{2}-\d{2}", kolumny[0]):
            continue
        try:
            kiedy = datetime.strptime(kolumny[0][:16], "%Y-%m-%d %H:%M").replace(tzinfo=konf.strefa)
        except ValueError:
            kiedy = datetime.strptime(kolumny[0][:10], "%Y-%m-%d").replace(tzinfo=konf.strefa)
        typ = bez_ogonkow(kolumny[2])
        typ = {"reel": "reels", "rolka": "reels", "stories": "story", "karuzela": "post"}.get(typ, typ)
        plik = re.sub(r"\s*\(\d+\)\s*$", "", kolumny[3])
        for k in kolumny[1].split(","):
            kanal = KANAL_Z_TABELI.get(bez_ogonkow(k))
            if not kanal:
                continue
            t = typ if kanal == "instagram" else "film"
            if kanal == "instagram" and t == "film":
                t = "reels"
            wynik.append(Uzycie(plik, kanal, t, kiedy, "HARMONOGRAM.md"))
    return wynik


def zajete_i_kolejka(posty: list[dict], konf: Konfiguracja) -> tuple[set, dict]:
    kanal_po_id = {v: k for k, v in konf.kanaly.items()}
    zajete = set()
    w_kolejce = {k: 0 for k in konf.kanaly}
    for p in posty:
        kanal = kanal_po_id.get(p.get("channelId"))
        if not kanal or p.get("status") == "draft":
            continue
        if p.get("status") in STATUSY_W_KOLEJCE:
            w_kolejce[kanal] += 1
        kiedy = parsuj_czas(p.get("dueAt"), konf.strefa)
        if kiedy:
            zajete.add((kanal, kiedy.strftime("%Y-%m-%d %H:%M")))
    return zajete, w_kolejce


# ---------------------------------------------------------------------------
# Planowanie
# ---------------------------------------------------------------------------

RODZAJ_DLA_TYPU = {"story": "story", "post": "post", "reels": "film", "film": "film"}


@dataclass
class Decyzja:
    slot: Slot
    status: str              # zaplanowany / zajety / pusty
    material: Material | None = None
    powod: str = ""
    powtorka: bool = False
    id_posta: str | None = None
    blad: str | None = None


def typy_blokujace(slot: Slot) -> set[str]:
    """Jakie wcześniejsze użycia na tym kanale oznaczają, że materiał jest już zużyty."""
    if slot.kanal != "instagram":
        return {"film", "reels", "post", "story"}
    return {"story": {"story"}, "post": {"post"}, "reels": {"reels", "post"}}[slot.typ]


def typ_uzycia(slot: Slot) -> str:
    return slot.typ if slot.kanal == "instagram" else "film"


def pasuje(u: Uzycie, m: Material) -> bool:
    return u.plik == m.klucz or (m.rodzaj == "post" and (u.plik.startswith(m.klucz + "/") or u.plik in m.pliki))


def daty_uzyc(m: Material, kanal: str, typy: set[str], uzycia: list[Uzycie]) -> list[datetime]:
    minimum = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return [u.kiedy or minimum for u in uzycia if u.kanal == kanal and u.typ in typy and pasuje(u, m)]


def produkt_sciezki(sciezka: str, konf: Konfiguracja) -> str | None:
    folder = sciezka.split("/", 1)[0]
    return folder if any(p.folder == folder for p in konf.produkty) else None


def kolejnosc_produktow(uzycia: list[Uzycie], kanal: str, typ: str, konf: Konfiguracja) -> list[str]:
    """Rotacja: zaczynamy od produktu po tym, który był ostatnio na tym kanale i w tym typie."""
    foldery = [p.folder for p in konf.produkty]
    ostatnie = [u for u in uzycia if u.kanal == kanal and u.typ == typ and u.kiedy
                and produkt_sciezki(u.plik, konf)]
    if not ostatnie:
        return foldery
    ostatni = produkt_sciezki(max(ostatnie, key=lambda u: u.kiedy).plik, konf)
    i = foldery.index(ostatni)
    return foldery[i + 1:] + foldery[:i + 1]


def ma_tekst(m: Material, kanal: str) -> bool:
    if m.rodzaj != "film":
        return True
    wymagane = {"instagram": "instagram", "tiktok": "tiktok", "youtube": "youtube_tytul"}[kanal]
    return bool(m.teksty.get(wymagane))


def preferencja_story(m: Material, slot: Slot) -> int:
    """Story z „cta” w nazwie wolimy wieczorem, pozostałe rano."""
    wieczor = slot.kiedy.hour >= 17
    cta = "cta" in m.nazwa.lower()
    return 0 if cta == wieczor else 1


def wybierz_material(slot: Slot, biblioteka: list[Material], uzycia: list[Uzycie],
                     konf: Konfiguracja, wykluczone: set[str] = frozenset()):
    rodzaj = RODZAJ_DLA_TYPU[slot.typ]
    blokujace = typy_blokujace(slot)
    kolejnosc = kolejnosc_produktow(uzycia, slot.kanal, typ_uzycia(slot), konf)
    kandydaci = {p: [m for m in biblioteka
                     if m.produkt == p and m.rodzaj == rodzaj and m.klucz not in wykluczone and ma_tekst(m, slot.kanal)]
                 for p in kolejnosc}

    # 1. Nieużyte, w kolejności rotacji produktów
    for p in kolejnosc:
        wolne = [m for m in kandydaci[p] if not daty_uzyc(m, slot.kanal, blokujace, uzycia)]
        if wolne:
            if rodzaj == "story":
                wolne.sort(key=lambda m: preferencja_story(m, slot))  # sort stabilny
            return wolne[0], False, ""

    if rodzaj == "film":
        return None, False, "brak nowego filmu (każdy był już na tym kanale, bez powtórek)"
    if rodzaj == "post":
        return None, False, "brak nieużytego posta na feed"

    # 2. Stories: powtórka najdawniej użytego, min. N dni od ostatniej publikacji
    granica = slot.kiedy - timedelta(days=konf.powtorka_dni)
    for p in kolejnosc:
        stare = []
        for m in kandydaci[p]:
            ostatnio = max(daty_uzyc(m, slot.kanal, blokujace, uzycia))
            if ostatnio <= granica:
                stare.append((ostatnio, preferencja_story(m, slot), m))
        if stare:
            stare.sort(key=lambda x: (x[0], x[1]))
            return stare[0][2], True, ""
    return None, False, f"brak story: wszystkie były użyte w ostatnich {konf.powtorka_dni} dniach"


def zaplanuj(sloty: list[Slot], biblioteka: list[Material], uzycia: list[Uzycie], zajete: set,
             w_kolejce: dict, konf: Konfiguracja, wykluczone: set[str] = frozenset()) -> list[Decyzja]:
    uzycia = list(uzycia)
    zajete = set(zajete)
    wolne = {k: konf.limit - w_kolejce.get(k, 0) for k in konf.kanaly}
    decyzje = []
    for slot in sloty:
        if slot.klucz in zajete:
            decyzje.append(Decyzja(slot, "zajety", powod="w Buffer jest już post o tej porze"))
            continue
        if wolne.get(slot.kanal, 0) <= 0:
            decyzje.append(Decyzja(slot, "pusty", powod=f"limit {konf.limit} zaplanowanych postów na kanale"))
            continue
        material, powtorka, powod = wybierz_material(slot, biblioteka, uzycia, konf, wykluczone)
        if material is None:
            decyzje.append(Decyzja(slot, "pusty", powod=powod))
            continue
        decyzje.append(Decyzja(slot, "zaplanowany", material, powtorka=powtorka))
        wolne[slot.kanal] -= 1
        zajete.add(slot.klucz)
        uzycia.append(Uzycie(material.klucz, slot.kanal, typ_uzycia(slot), slot.kiedy, "plan"))
    return decyzje


# ---------------------------------------------------------------------------
# Treść posta dla Buffer
# ---------------------------------------------------------------------------

def url_pliku(sciezka: str, konf: Konfiguracja) -> str:
    return konf.repo_raw + quote(sciezka, safe="/")


def opis_youtube(opis: str, prod: Produkt) -> str:
    if prod.link in opis:
        return opis
    cena = f" ({prod.cena})" if prod.cena else ""
    return f"{opis.rstrip()}\n\nSzablon{cena}: {prod.link}".strip()


def zbuduj_input(d: Decyzja, konf: Konfiguracja, szkic: bool = False) -> dict:
    m, s = d.material, d.slot
    prod = konf.produkt(m.produkt)
    wejscie = {
        "channelId": konf.kanaly[s.kanal],
        "schedulingType": "automatic",
        "mode": "customScheduled",
        "dueAt": czas_iso(s.kiedy),
    }
    if szkic:
        wejscie["saveToDraft"] = True

    def wideo(p):
        return {"video": {"url": url_pliku(p, konf)}}

    def obraz(p, alt):
        return {"image": {"url": url_pliku(p, konf), "metadata": {"altText": alt}}}

    if s.kanal == "instagram" and s.typ == "story":
        plik = m.pliki[0]
        tekst = m.teksty.get("tekst") or prod.nazwa
        wejscie["text"] = tekst
        wejscie["assets"] = [wideo(plik) if Path(plik).suffix.lower() in WIDEO else obraz(plik, tekst)]
        wejscie["metadata"] = {"instagram": {"type": "story", "shouldShareToFeed": False, "link": prod.link}}
    elif s.kanal == "instagram" and s.typ == "reels":
        wejscie["text"] = m.teksty["instagram"]
        wejscie["assets"] = [wideo(m.pliki[0])]
        wejscie["metadata"] = {"instagram": {"type": "reel", "shouldShareToFeed": True}}
    elif s.kanal == "instagram" and s.typ == "post":
        n = len(m.pliki)
        nazwa = re.sub(r"^\d+[_-]", "", m.nazwa).replace("_", " ")
        wejscie["text"] = m.teksty["opis"]
        wejscie["assets"] = [obraz(p, f"{prod.nazwa} – {nazwa}" + (f" – slajd {i}" if n > 1 else ""))
                             for i, p in enumerate(m.pliki, 1)]
        wejscie["metadata"] = {"instagram": {"type": "post", "shouldShareToFeed": True}}
    elif s.kanal == "tiktok":
        wejscie["text"] = m.teksty["tiktok"]
        wejscie["assets"] = [wideo(m.pliki[0])]
    elif s.kanal == "youtube":
        yt = konf.youtube
        wejscie["text"] = opis_youtube(m.teksty.get("youtube_opis", ""), prod)
        wejscie["assets"] = [wideo(m.pliki[0])]
        wejscie["metadata"] = {"youtube": {
            "title": m.teksty["youtube_tytul"],
            "categoryId": str(yt.get("kategoria", "26")),
            "privacy": yt.get("prywatnosc", "public"),
            "madeForKids": bool(yt.get("dla_dzieci", False)),
        }}
    else:
        raise ValueError(f"Nieobsługiwany slot {s}")
    return wejscie


# ---------------------------------------------------------------------------
# Buffer API
# ---------------------------------------------------------------------------

ZAPYTANIE_POSTY = """
query Posty($org: OrganizationId!, $kanaly: [ChannelId!], $po: String) {
  posts(first: 100, after: $po, input: {organizationId: $org, filter: {channelIds: $kanaly}}) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      id channelId status dueAt sentAt createdAt
      assets { source }
      metadata { ... on InstagramPostMetadata { type } }
    } }
  }
}
"""

MUTACJA_UTWORZ = """
mutation Utworz($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess { post { id dueAt status } }
    ... on MutationError { message }
  }
}
"""


class Buffer:
    def __init__(self, klucz: str):
        self._naglowki = {"Authorization": f"Bearer {klucz}", "Content-Type": "application/json",
                          "User-Agent": "media-szablony-autopilot"}
        self.zapytania = 0

    def zapytaj(self, zapytanie: str, zmienne: dict | None = None) -> dict:
        for proba in range(1, 4):
            try:
                r = requests.post(API_URL, json={"query": zapytanie, "variables": zmienne or {}},
                                  headers=self._naglowki, timeout=60)
            except requests.RequestException as e:
                if proba == 3:
                    raise BladApi(f"Brak połączenia z Buffer: {e.__class__.__name__}") from None
                time.sleep(10 * proba)
                continue
            self.zapytania += 1
            if r.status_code in (401, 403):
                raise BladKlucza(KOMUNIKAT_KLUCZ)
            if r.status_code == 429:
                czekaj = int(r.headers.get("Retry-After", "60") or 60)
                if proba == 3 or czekaj > 300:
                    raise BladApi(f"Przekroczony limit zapytań Buffer – spróbuj za {czekaj} s.")
                time.sleep(czekaj + random.uniform(1, 5))
                continue
            if r.status_code >= 500:
                if proba == 3:
                    raise BladApi(f"Serwer Buffer zwrócił błąd {r.status_code}.")
                time.sleep(15 * proba)
                continue
            try:
                tresc = r.json()
            except ValueError:
                raise BladApi(f"Nieczytelna odpowiedź Buffer (HTTP {r.status_code}).") from None
            bledy = tresc.get("errors") or []
            if bledy:
                kod = (bledy[0].get("extensions") or {}).get("code", "")
                if kod in ("UNAUTHORIZED", "FORBIDDEN", "UNAUTHENTICATED"):
                    raise BladKlucza(KOMUNIKAT_KLUCZ)
                if kod == "RATE_LIMIT_EXCEEDED" and proba < 3:
                    time.sleep(60)
                    continue
                if not tresc.get("data"):
                    raise BladApi(f"Błąd Buffer API ({kod}): {bledy[0].get('message')}")
            if r.status_code != 200:
                raise BladApi(f"Buffer zwrócił HTTP {r.status_code}.")
            return tresc.get("data") or {}
        raise BladApi("Nie udało się połączyć z Buffer.")

    def posty(self, konf: Konfiguracja) -> list[dict]:
        wynik, po = [], None
        for _ in range(MAX_STRON):
            # Bez filtra statusu: Buffer przy kilku statusach naraz zwraca pustą listę.
            # Szkice odrzucamy niżej, w kodzie.
            dane = self.zapytaj(ZAPYTANIE_POSTY, {"org": konf.organizacja, "kanaly": list(konf.kanaly.values()),
                                                  "po": po})
            strona = dane["posts"]
            wynik += [e["node"] for e in strona.get("edges") or []
                      if e["node"].get("status") in STATUSY_DO_POBRANIA]
            if not strona["pageInfo"]["hasNextPage"]:
                break
            po = strona["pageInfo"]["endCursor"]
        return wynik

    def utworz(self, wejscie: dict) -> tuple[str | None, str | None, str]:
        dane = self.zapytaj(MUTACJA_UTWORZ, {"input": wejscie})["createPost"]
        if dane.get("post"):
            return dane["post"]["id"], None, dane["__typename"]
        return None, dane.get("message") or dane.get("__typename"), dane.get("__typename", "")


def plik_dostepny(url: str, pamiec: dict) -> bool:
    if url not in pamiec:
        try:
            r = requests.head(url, allow_redirects=True, timeout=30)
            pamiec[url] = r.status_code == 200
        except requests.RequestException:
            pamiec[url] = False
    return pamiec[url]


def zaplanuj_ze_sprawdzeniem(sloty, biblioteka, uzycia, zajete, w_kolejce, konf, wykluczaj=True):
    """Planuje i sprawdza, czy pliki są już dostępne pod linkiem GitHub.

    wykluczaj=True (prawdziwe uruchomienie): niedostępne materiały wyklucza i planuje od nowa.
    wykluczaj=False (dry-run): tylko zwraca listę niedostępnych.
    """
    wykluczone: set[str] = set()
    niedostepne: list[str] = []
    pamiec: dict = {}
    for _ in range(10):
        decyzje = zaplanuj(sloty, biblioteka, uzycia, zajete, w_kolejce, konf, wykluczone)
        nowe = set()
        for d in decyzje:
            if d.material and not all(plik_dostepny(url_pliku(p, konf), pamiec) for p in d.material.pliki):
                nowe.add(d.material.klucz)
        if not nowe or not wykluczaj:
            return decyzje, niedostepne + sorted(nowe - set(niedostepne))
        wykluczone |= nowe
        niedostepne += sorted(nowe)
    return decyzje, niedostepne


# ---------------------------------------------------------------------------
# Zapis stanu i raport
# ---------------------------------------------------------------------------

def opis_pliku(d: Decyzja) -> str:
    m = d.material
    tekst = f"{m.klucz} ({len(m.pliki)})" if m.rodzaj == "post" else m.klucz
    return tekst + (" – powtórka" if d.powtorka else "")


def zapisz_uzyte(decyzje: list[Decyzja], sciezka: Path = PLIK_UZYTYCH) -> None:
    wpisy = wczytaj_uzyte(sciezka)
    for d in decyzje:
        if d.id_posta:
            wpis = {"plik": d.material.klucz, "kanal": d.slot.kanal, "typ": typ_uzycia(d.slot),
                    "data": czas_iso(d.slot.kiedy), "id_posta": d.id_posta}
            if d.material.rodzaj == "post":
                wpis["slajdy"] = len(d.material.pliki)
            wpisy.append(wpis)
    dane = {"opis": "Lista postów utworzonych przez autopilota (plik, kanał, typ, data publikacji, ID posta w Buffer). "
                    "Nie edytuj ręcznie, chyba że chcesz, żeby autopilot uznał jakiś plik za nieużyty (usuń jego wpis).",
            "wpisy": wpisy}
    sciezka.write_text(json.dumps(dane, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def dopisz_do_tabeli(tekst: str, decyzje: list[Decyzja]) -> str:
    wiersze = [f"| {d.slot.kiedy.strftime('%Y-%m-%d %H:%M')} | {SKROT_KANALU[d.slot.kanal]} | {d.slot.typ} | "
               f"{opis_pliku(d)} |" for d in decyzje if d.id_posta]
    if not wiersze:
        return tekst
    linie = tekst.split("\n")
    try:
        start = next(i for i, l in enumerate(linie) if l.strip() == NAGLOWEK_TABELI)
    except StopIteration:
        dodatek = [NAGLOWEK_TABELI, "| Data | Kanał | Typ | Plik |", "|---|---|---|---|", *wiersze, ""]
        return tekst.rstrip("\n") + "\n\n" + "\n".join(dodatek)
    ostatni = start
    for i in range(start + 1, len(linie)):
        if linie[i].startswith("|"):
            ostatni = i
        elif linie[i].startswith("## ") or (ostatni > start and linie[i].strip() and not linie[i].startswith("|")):
            break
    if ostatni == start:
        linie[start + 1:start + 1] = ["| Data | Kanał | Typ | Plik |", "|---|---|---|---|", *wiersze]
    else:
        linie[ostatni + 1:ostatni + 1] = wiersze
    return "\n".join(linie)


def tabela_md(naglowki: list[str], wiersze: list[list[str]]) -> str:
    out = ["| " + " | ".join(naglowki) + " |", "|" + "---|" * len(naglowki)]
    out += ["| " + " | ".join(w) + " |" for w in wiersze]
    return "\n".join(out)


def data_slotu(s: Slot) -> str:
    return f"{s.kiedy.strftime('%Y-%m-%d')} ({DNI_SKROT[s.kiedy.weekday()]})"


def raport(decyzje, w_kolejce, konf, dry_run, teraz, ostrzezenia, bledy, zapytania) -> str:
    linie = [f"## Autopilot Buffer – {teraz.strftime('%Y-%m-%d %H:%M')} (czas polski)", ""]
    linie.append("Tryb: **podgląd (dry-run)** – nic nie zostało utworzone w Buffer." if dry_run
                 else "Tryb: **prawdziwy** – posty są tworzone w Buffer.")
    if w_kolejce is not None:
        stan = ", ".join(f"{NAZWA_KANALU[k]} {w_kolejce.get(k, 0)}/{konf.limit}" for k in konf.kanaly)
        linie.append(f"Zaplanowane w Buffer przed uruchomieniem: {stan}.")
    linie.append("")

    plan = [d for d in decyzje if d.status == "zaplanowany"]
    tytul = "Do zaplanowania" if dry_run else "Zaplanowane"
    linie.append(f"### {tytul} ({sum(1 for d in plan if not d.blad)})")
    if plan:
        wiersze = []
        for d in plan:
            plik = opis_pliku(d) + (f" – **BŁĄD: {d.blad}**" if d.blad else "")
            wiersze.append([data_slotu(d.slot), d.slot.kiedy.strftime("%H:%M"), NAZWA_KANALU[d.slot.kanal],
                            d.slot.typ, plik])
        linie.append(tabela_md(["Data", "Godzina", "Kanał", "Typ", "Plik"], wiersze))
    else:
        linie.append("Nic.")
    linie.append("")

    puste = [d for d in decyzje if d.status == "pusty"]
    linie.append(f"### Puste sloty ({len(puste)})")
    if puste:
        linie.append(tabela_md(["Data", "Godzina", "Kanał", "Typ", "Dlaczego"],
                               [[data_slotu(d.slot), d.slot.kiedy.strftime("%H:%M"), NAZWA_KANALU[d.slot.kanal],
                                 d.slot.typ, d.powod] for d in puste]))
    else:
        linie.append("Brak – wszystkie sloty obsadzone.")
    zajete = sum(1 for d in decyzje if d.status == "zajety")
    if zajete:
        linie += ["", f"Pominięte, bo już zajęte w Buffer: {zajete} slotów."]

    uwagi = list(ostrzezenia) + list(bledy)
    if uwagi:
        linie += ["", "### Uwagi"] + [f"- {u}" for u in uwagi]
    linie += ["", f"Zapytania do Buffer API w tym uruchomieniu: {zapytania}."]
    return "\n".join(linie) + "\n"


def zapisz_ostatnie(teraz, dry_run, decyzje, bledy, zapytania, sciezka: Path = PLIK_OSTATNIE) -> None:
    zaplanowane = sum(1 for d in decyzje if d.id_posta) if not dry_run else \
        sum(1 for d in decyzje if d.status == "zaplanowany")
    puste = sum(1 for d in decyzje if d.status == "pusty")
    tresc = [
        f"Ostatnie uruchomienie autopilota: {teraz.strftime('%Y-%m-%d %H:%M:%S')} (czas polski)",
        f"Tryb: {'podgląd (dry-run), nic nie utworzono w Buffer' if dry_run else 'prawdziwy'}",
        f"{'Do zaplanowania' if dry_run else 'Zaplanowano'}: {zaplanowane}",
        f"Puste sloty: {puste}",
        f"Zapytania do Buffer API: {zapytania}",
        "Wynik: " + ("OK" if not bledy else "BŁĄD – " + " | ".join(bledy)),
    ]
    sciezka.write_text("\n".join(tresc) + "\n", encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# Główny program
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    for strumien in (sys.stdout, sys.stderr):
        try:
            strumien.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Uzupełnia kolejkę postów w Buffer.")
    parser.add_argument("--dry-run", action="store_true", help="tylko pokaż plan, nic nie twórz w Buffer")
    parser.add_argument("--dzis", help="udawana data dzisiejsza RRRR-MM-DD (do testów)")
    args = parser.parse_args(argv)

    try:
        konf = wczytaj_konfiguracje()
    except BladKonfiguracji as e:
        teraz = datetime.now(ZoneInfo("Europe/Warsaw"))
        zapisz_ostatnie(teraz, args.dry_run, [], [str(e)], 0)
        print(f"::error::{e}" if os.environ.get("GITHUB_ACTIONS") else f"BŁĄD: {e}", file=sys.stderr)
        podsumowanie = os.environ.get("GITHUB_STEP_SUMMARY")
        if podsumowanie:
            with open(podsumowanie, "a", encoding="utf-8") as f:
                f.write(f"## Autopilot Buffer – błąd konfiguracji\n\n{e}\n")
        return 1
    teraz = datetime.now(konf.strefa)
    dzis = date.fromisoformat(args.dzis) if args.dzis else teraz.date()

    decyzje: list[Decyzja] = []
    ostrzezenia: list[str] = []
    bledy: list[str] = []
    w_kolejce = None
    api = None
    try:
        klucz = os.environ.get("BUFFER_API_KEY", "").strip()
        if not klucz:
            raise BladKlucza("Brak klucza: ustaw zmienną BUFFER_API_KEY (w GitHub: sekret BUFFER_API_KEY). "
                             + KOMUNIKAT_KLUCZ)
        api = Buffer(klucz)
        posty = api.posty(konf)
        biblioteka, ostrzezenia = zbuduj_biblioteke(REPO, konf)
        wpisy = wczytaj_uzyte()
        tabela = PLIK_TABELI.read_text(encoding="utf-8") if PLIK_TABELI.is_file() else ""
        uzycia = uzycia_z_buffer(posty, konf) + uzycia_z_pliku(wpisy, konf) + uzycia_z_tabeli(tabela, konf)
        zajete, w_kolejce = zajete_i_kolejka(posty, konf)
        sloty = wyznacz_sloty(konf, dzis)
        decyzje, niedostepne = zaplanuj_ze_sprawdzeniem(sloty, biblioteka, uzycia, zajete, w_kolejce, konf,
                                                         wykluczaj=not args.dry_run)
        for k in niedostepne:
            ostrzezenia.append(f"{k} – pliku nie ma jeszcze pod linkiem GitHub (nie wypchnięty?)"
                               + ("; w prawdziwym uruchomieniu zostałby pominięty." if args.dry_run
                                  else ", pominięty."))

        if not args.dry_run:
            limit_osiagniety = set()
            for d in decyzje:
                if d.status != "zaplanowany":
                    continue
                if d.slot.kanal in limit_osiagniety:
                    d.blad = "Buffer zgłosił limit postów na kanale"
                    continue
                id_posta, blad, typ = api.utworz(zbuduj_input(d, konf))
                if id_posta:
                    d.id_posta = id_posta
                else:
                    d.blad = blad
                    if typ == "LimitReachedError":
                        limit_osiagniety.add(d.slot.kanal)
            nieudane = [d for d in decyzje if d.blad]
            if nieudane:
                bledy.append(f"Nie udało się utworzyć {len(nieudane)} postów – szczegóły w tabeli.")
    except BladKlucza as e:
        bledy.append(str(e))
    except (BladApi, BladKonfiguracji) as e:
        bledy.append(str(e))
    finally:
        zapytania = api.zapytania if api else 0
        if not args.dry_run and any(d.id_posta for d in decyzje):
            zapisz_uzyte(decyzje)
            if PLIK_TABELI.is_file():
                tekst = PLIK_TABELI.read_text(encoding="utf-8")
                PLIK_TABELI.write_text(dopisz_do_tabeli(tekst, decyzje), encoding="utf-8", newline="\n")
        zapisz_ostatnie(teraz, args.dry_run, decyzje, bledy, zapytania)
        tekst_raportu = raport(decyzje, w_kolejce, konf, args.dry_run, teraz, ostrzezenia, bledy, zapytania)
        print(tekst_raportu)
        podsumowanie = os.environ.get("GITHUB_STEP_SUMMARY")
        if podsumowanie:
            with open(podsumowanie, "a", encoding="utf-8") as f:
                f.write(tekst_raportu)
    for b in bledy:
        print(f"::error::{b}" if os.environ.get("GITHUB_ACTIONS") else f"BŁĄD: {b}", file=sys.stderr)
    return 1 if bledy else 0


if __name__ == "__main__":
    sys.exit(main())
