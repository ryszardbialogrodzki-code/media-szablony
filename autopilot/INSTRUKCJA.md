# Autopilot Buffer – instrukcja

Autopilot sam uzupełnia kolejkę postów w Buffer **w środę i w niedzielę wieczorem**
(ok. 19:37 zimą, 20:37 latem). Działa na GitHubie, więc Twój komputer może być wyłączony.

Przy każdym uruchomieniu:

1. sprawdza, co już jest zaplanowane w Buffer (Instagram, TikTok, YouTube),
2. patrzy na szablon tygodnia w `autopilot/harmonogram.json` na 7 dni do przodu (od jutra),
3. w każdy wolny termin wstawia materiał, którego jeszcze nie było (filmy, posty, stories),
4. zapisuje, co zaplanował: w `autopilot/uzyte.json` i w tabeli w `HARMONOGRAM.md`.

Zasady, których pilnuje:

- Na każdym kanale może być najwyżej **10 zaplanowanych postów** (limit darmowego Buffera).
  Kiedy kolejka jest pełna, reszta terminów czeka na następne uruchomienie.
- Filmy i posty na feed idą **tylko raz** na dany kanał. Kiedy skończą się nowe filmy, termin zostaje pusty.
- Stories najpierw idą nowe. Kiedy wszystkie już były, wraca to, które było najdawniej
  (najwcześniej 14 dni po ostatniej publikacji). Każde story ma naklejkę z linkiem do sklepu naffy.
- Produkty idą po kolei: Second Brain → Premium → Student OS → … (produkt bez materiału jest pomijany).
- Nigdy nie usuwa ani nie zmienia postów, które już są w Buffer.

---

## 1. Jak dodać nowe materiały (przez github.com)

Wejdź na https://github.com/ryszardbialogrodzki-code/media-szablony i zaloguj się.

Foldery produktów: `second-brain/`, `premium/`, `student-os/`.

### Nowy film

Potrzebujesz dwóch plików o tej samej nazwie, np. `wersja_C.mp4` i `wersja_C.txt`.

Plik `wersja_C.txt` (zrób go w Notatniku, zapisz jako UTF-8):

```
Tytuł na YouTube (najwyżej 60 znaków)
Opis na TikToka i Instagram – wszystko od drugiej linii.
Może mieć wiele linii i hashtagi.
```

- Na końcu tytułu autopilot sam dopisze `#shorts`, jeśli go nie ma.
- Na YouTube autopilot sam dopisze na końcu opisu link do sklepu naffy.
- Chcesz mieć inny opis na każdy kanał? Dodaj jeszcze (nieobowiązkowo):
  - `wersja_C.tiktok.txt` – cały plik to opis na TikToka,
  - `wersja_C.instagram.txt` – cały plik to opis na Instagram (reels),
  - `wersja_C.youtube.txt` – pierwsza linia to tytuł, reszta to opis na YouTube.

Jak wgrać:

1. Wejdź do folderu, np. `premium` → `filmy`.
2. Kliknij **Add file → Upload files**.
3. Przeciągnij plik `.mp4` i plik `.txt` (i ewentualne `.tiktok.txt` itd.).
4. Na dole kliknij zielony przycisk **Commit changes**.

Film trafi na Instagram (reels), TikToka i YouTube – na każdy kanał raz.
Pliki `*_bez_muzyki.mp4`, `storyboard*` i `podglad*` są pomijane.

### Nowy post na feed (Instagram)

Każdy post to osobny folder w `<produkt>/posty/`, np. `premium/posty/07_nawyki/`, w środku:

- obrazy 1080×1350 (`slajd_01.png`, `slajd_02.png`, … – kolejność slajdów = kolejność nazw),
- `opis.txt` – opis posta.

Jeden obraz w folderze = zwykły post. Kilka obrazów = karuzela (najwyżej 10).

Jak założyć nowy folder na github.com:

1. Wejdź do `premium` → `posty`.
2. Kliknij **Add file → Create new file**.
3. W polu nazwy wpisz `07_nawyki/opis.txt` (ukośnik tworzy folder), wklej opis, kliknij **Commit changes**.
4. Wejdź do nowego folderu `07_nawyki`, kliknij **Add file → Upload files**, przeciągnij obrazy, **Commit changes**.

### Nowe story

Wgraj obraz `.png` (1080×1920) albo krótki film `.mp4` do `<produkt>/stories/`
(**Add file → Upload files**). Okładki filmów `okladka_*.png` z folderu `filmy/` też są używane jako stories.
Story z „cta” w nazwie autopilot woli dawać wieczorem.

### Ważne

- **Nie zmieniaj nazw i nie przenoś plików**, które już są w repo – zaplanowane posty w Buffer używają ich adresów.
- Pliki ponad **50 MB** są pomijane.
- Nazwy plików najlepiej bez spacji i polskich znaków (np. `wersja_C.mp4`, `07_nawyki`).

---

## 2. Jak uruchomić autopilota ręcznie

1. W repo kliknij zakładkę **Actions**.
2. Po lewej wybierz **Uzupełnij kolejkę Buffer**.
3. Po prawej kliknij **Run workflow**.
4. Pole **„Tylko podgląd”** jest domyślnie zaznaczone – wtedy autopilot tylko pokazuje plan i nic nie tworzy.
   Odznacz je, jeśli ma naprawdę zaplanować posty.
5. Kliknij zielony **Run workflow**. Po ok. minucie kliknij uruchomienie na liście –
   na dole strony jest podsumowanie: co zaplanował, które terminy zostały puste i dlaczego.

---

## 3. Jak zmienić godziny i dni (harmonogram.json)

1. Otwórz plik `autopilot/harmonogram.json` i kliknij ołówek (**Edit this file**).
2. W części `"tydzien"` każdy dzień ma listę wpisów `"GODZINA KANAŁ TYP"`, np.:

```json
"sobota": [
  "10:00 instagram story",
  "12:30 instagram reels",
  "19:00 instagram reels"
],
```

- kanały: `instagram`, `tiktok`, `youtube`,
- typy na Instagramie: `story`, `post`, `reels`; na TikToku i YouTube: `film`,
- godziny w czasie polskim (zmianę czasu letni/zimowy autopilot liczy sam).

3. Pilnuj cudzysłowów i przecinków: **przecinek po każdym wpisie oprócz ostatniego** w nawiasie `[ ]`.
4. Kliknij **Commit changes**.

Inne ustawienia w tym pliku: linki naffy i ceny produktów (`"produkty"`), limit 10 (`"limit_na_kanal"`),
ile dni do przodu (`"horyzont_dni"`), po ilu dniach story może wrócić (`"story_powtorka_po_dniach"`).
Kolejność produktów na liście = kolejność rotacji.

Jeśli zrobisz literówkę, autopilot zakończy się błędem i napisze, w której linii jest problem.

---

## 4. Jak wymienić klucz API Buffer

1. Wejdź na https://publish.buffer.com/settings/api i wygeneruj nowy klucz. Skopiuj go.
2. W repo na GitHubie: **Settings → Secrets and variables → Actions**.
3. Przy sekrecie **BUFFER_API_KEY** kliknij ołówek (**Update**), wklej nowy klucz, **Update secret**.
4. Dla sprawdzenia uruchom autopilota ręcznie z zaznaczonym „Tylko podgląd” (punkt 2).

Klucza **nigdy** nie wklejaj do plików w repo – repo jest publiczne.
Wymień klucz, kiedy dostaniesz maila o błędzie z komunikatem „Klucz Buffer wygasł albo jest błędny”,
albo jeśli klucz mógł trafić w niepowołane ręce.

---

## 5. Co zrobić po mailu o błędzie z GitHuba

1. Kliknij link w mailu (albo wejdź w **Actions** i kliknij uruchomienie z czerwonym krzyżykiem).
2. Na dole strony jest podsumowanie, a w części **Uwagi** – opis problemu.

| Komunikat | Co zrobić |
|---|---|
| „Klucz Buffer wygasł albo jest błędny…” | Wymień klucz (punkt 4). |
| „Plik harmonogram.json ma błąd w linii …” | Popraw ten plik (zwykle brakujący przecinek albo cudzysłów) – punkt 3. |
| „Nie udało się utworzyć … postów” | Zobacz tabelę: przy pliku jest opis błędu z Buffera (np. zły format filmu). Popraw albo usuń ten plik. |
| „Przekroczony limit zapytań Buffer” | Nic – spróbuj ręcznie za 15 minut albo poczekaj na następne uruchomienie. |
| „Brak połączenia z Buffer” / „Serwer Buffer zwrócił błąd” | Chwilowa awaria. Uruchom ponownie później. |

Puste terminy z powodem „limit 10” albo „brak nowego filmu” to **nie błąd** – to normalna praca.
Nowe filmy dodawaj według punktu 1.

---

## Pliki autopilota

- `harmonogram.json` – szablon tygodnia, produkty, linki, limity (możesz edytować),
- `uzupelnij.py` – program autopilota,
- `uzyte.json` – co autopilot już zaplanował (nie edytuj),
- `ostatnie_uruchomienie.txt` – kiedy i z jakim wynikiem był ostatni raz uruchomiony,
- `test_uzupelnij.py` – testy programu,
- `../.github/workflows/uzupelnij-kolejke.yml` – harmonogram uruchomień na GitHubie.
