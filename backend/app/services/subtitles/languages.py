"""ISO 639 codes and language names for subtitle files (C9).

Every language with an ISO 639-1 code: its ISO 639-2 codes (T, and B where it differs) and its names in English and
German. A subtitle's language is stored as ISO 639-1.

* ``by_code``: an ISO 639-1 or ISO 639-2 code, case ignored. Filipino (``fil``, no ISO 639-1 code) counts as Tagalog.
  Retired codes (``iw``, ``in``, ``ji``, ``mo``) are not read: ``in`` is a word.
* ``by_name``: a name of one to three words in English or German, case ignored, the words joined by anything
  (``Portuguese (Brazil)``, ``Schottisch-Gälisch``). German umlauts also as ``ae``, ``oe``, ``ue`` or without their
  dots; accents also without.

Table: ``ISO 639-1|ISO 639-2 T|ISO 639-2 B when it differs|English names|German names``, names separated by ``;``. A
language may have further rows with more names and empty code fields.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator

from ..schreibweisen import nfc

_TABLE = """
aa|aar||Afar|Afar
ab|abk||Abkhazian;Abkhaz|Abchasisch
ae|ave||Avestan|Avestisch
af|afr||Afrikaans|Afrikaans
ak|aka||Akan|Akan
am|amh||Amharic|Amharisch
an|arg||Aragonese|Aragonesisch
ar|ara||Arabic|Arabisch
as|asm||Assamese|Assamesisch
av|ava||Avaric;Avar|Awarisch
ay|aym||Aymara|Aymara
az|aze||Azerbaijani;Azeri|Aserbaidschanisch
ba|bak||Bashkir|Baschkirisch
be|bel||Belarusian|Belarussisch;Weißrussisch
bg|bul||Bulgarian|Bulgarisch
bi|bis||Bislama|Bislama
bm|bam||Bambara|Bambara
bn|ben||Bengali;Bangla|Bengalisch
bo|bod|tib|Tibetan|Tibetisch
br|bre||Breton|Bretonisch
bs|bos||Bosnian|Bosnisch
ca|cat||Catalan;Valencian|Katalanisch;Valencianisch
ce|che||Chechen|Tschetschenisch
ch|cha||Chamorro|Chamorro
co|cos||Corsican|Korsisch
cr|cre||Cree|Cree
cs|ces|cze|Czech|Tschechisch
cu|chu||Church Slavic;Church Slavonic;Old Church Slavonic|Kirchenslawisch
cv|chv||Chuvash|Tschuwaschisch
cy|cym|wel|Welsh|Walisisch
da|dan||Danish|Dänisch
de|deu|ger|German|Deutsch
dv|div||Divehi;Dhivehi;Maldivian|Dhivehi
dz|dzo||Dzongkha|Dzongkha
ee|ewe||Ewe|Ewe
el|ell|gre|Greek|Griechisch
en|eng||English|Englisch
eo|epo||Esperanto|Esperanto
es|spa||Spanish;Castilian;Latino;European Spanish|Spanisch;Kastilisch
es|||Latin American Spanish;Spanish (Latin America)|Lateinamerikanisches Spanisch;Spanisch (Lateinamerika)
et|est||Estonian|Estnisch
eu|eus|baq|Basque|Baskisch
fa|fas|per|Persian;Farsi|Persisch;Farsi
ff|ful||Fula;Fulah;Fulani|Fulfulde;Ful
fi|fin||Finnish|Finnisch
fj|fij||Fijian|Fidschi;Fidschianisch
fo|fao||Faroese|Färöisch
fr|fra|fre|French;Canadian French;French (Canada)|Französisch;Kanadisches Französisch;Französisch (Kanada)
fy|fry||Western Frisian;West Frisian;Frisian|Westfriesisch;Friesisch
ga|gle||Irish|Irisch
gd|gla||Scottish Gaelic;Gaelic|Schottisch-Gälisch;Gälisch
gl|glg||Galician|Galicisch;Galizisch
gn|grn||Guarani|Guaraní
gu|guj||Gujarati|Gujarati
gv|glv||Manx|Manx
ha|hau||Hausa|Hausa
he|heb||Hebrew|Hebräisch
hi|hin||Hindi|Hindi
ho|hmo||Hiri Motu|Hiri Motu
hr|hrv||Croatian|Kroatisch
ht|hat||Haitian;Haitian Creole|Haitianisch;Haitianisches Kreolisch
hu|hun||Hungarian|Ungarisch
hy|hye|arm|Armenian|Armenisch
hz|her||Herero|Otjiherero;Herero
ia|ina||Interlingua|Interlingua
id|ind||Indonesian|Indonesisch
ie|ile||Interlingue;Occidental|Interlingue
ig|ibo||Igbo|Igbo
ii|iii||Sichuan Yi;Nuosu|Nuosu
ik|ipk||Inupiaq|Inupiaq
io|ido||Ido|Ido
is|isl|ice|Icelandic|Isländisch
it|ita||Italian|Italienisch
iu|iku||Inuktitut|Inuktitut
ja|jpn||Japanese|Japanisch
jv|jav||Javanese|Javanisch
ka|kat|geo|Georgian|Georgisch
kg|kon||Kongo;Kikongo|Kikongo
ki|kik||Kikuyu;Gikuyu|Kikuyu
kj|kua||Kuanyama;Kwanyama|Kwanyama
kk|kaz||Kazakh|Kasachisch
kl|kal||Kalaallisut;Greenlandic|Grönländisch
km|khm||Khmer;Central Khmer|Khmer
kn|kan||Kannada|Kannada
ko|kor||Korean|Koreanisch
kr|kau||Kanuri|Kanuri
ks|kas||Kashmiri|Kaschmiri
ku|kur||Kurdish|Kurdisch
kv|kom||Komi|Komi
kw|cor||Cornish|Kornisch
ky|kir||Kyrgyz;Kirghiz|Kirgisisch
la|lat||Latin|Latein;Lateinisch
lb|ltz||Luxembourgish;Letzeburgesch|Luxemburgisch
lg|lug||Ganda;Luganda|Luganda
li|lim||Limburgish;Limburgan|Limburgisch
ln|lin||Lingala|Lingala
lo|lao||Lao|Laotisch
lt|lit||Lithuanian|Litauisch
lu|lub||Luba-Katanga|Kiluba
lv|lav||Latvian|Lettisch
mg|mlg||Malagasy|Malagasy;Madagassisch
mh|mah||Marshallese|Marshallesisch
mi|mri|mao|Maori|Maori
mk|mkd|mac|Macedonian|Mazedonisch
ml|mal||Malayalam|Malayalam
mn|mon||Mongolian|Mongolisch
mr|mar||Marathi|Marathi
ms|msa|may|Malay|Malaiisch
mt|mlt||Maltese|Maltesisch
my|mya|bur|Burmese|Birmanisch
na|nau||Nauru;Nauruan|Nauruisch
nb|nob||Norwegian Bokmål;Bokmål|Norwegisch Bokmål;Bokmål
nd|nde||North Ndebele;Northern Ndebele|Nord-Ndebele
ne|nep||Nepali|Nepali;Nepalesisch
ng|ndo||Ndonga|Ndonga
nl|nld|dut|Dutch;Flemish|Niederländisch;Flämisch
nn|nno||Norwegian Nynorsk;Nynorsk|Norwegisch Nynorsk;Nynorsk
no|nor||Norwegian|Norwegisch
nr|nbl||South Ndebele;Southern Ndebele|Süd-Ndebele
nv|nav||Navajo;Navaho|Navajo
ny|nya||Chichewa;Chewa;Nyanja|Chichewa
oc|oci||Occitan|Okzitanisch
oj|oji||Ojibwe;Ojibwa|Ojibwe
om|orm||Oromo|Oromo
or|ori||Oriya;Odia|Oriya
os|oss||Ossetian;Ossetic|Ossetisch
pa|pan||Punjabi;Panjabi|Panjabi;Pandschabi
pi|pli||Pali|Pali
pl|pol||Polish|Polnisch
ps|pus||Pashto;Pushto|Paschtu
pt|por||Portuguese;Brazilian;European Portuguese|Portugiesisch;Brasilianisch
pt|||Brazilian Portuguese;Portuguese (Brazil)|Brasilianisches Portugiesisch;Portugiesisch (Brasilien)
qu|que||Quechua|Quechua
rm|roh||Romansh|Rätoromanisch
rn|run||Kirundi;Rundi|Kirundi
ro|ron|rum|Romanian;Moldavian;Moldovan|Rumänisch
ru|rus||Russian|Russisch
rw|kin||Kinyarwanda|Kinyarwanda
sa|san||Sanskrit|Sanskrit
sc|srd||Sardinian|Sardisch
sd|snd||Sindhi|Sindhi
se|sme||Northern Sami|Nordsamisch
sg|sag||Sango|Sango
si|sin||Sinhala;Sinhalese|Singhalesisch
sk|slk|slo|Slovak|Slowakisch
sl|slv||Slovenian;Slovene|Slowenisch
sm|smo||Samoan|Samoanisch
sn|sna||Shona|Shona
so|som||Somali|Somali
sq|sqi|alb|Albanian|Albanisch
sr|srp||Serbian|Serbisch
ss|ssw||Swati;Swazi|Siswati
st|sot||Southern Sotho;Sesotho|Sesotho
su|sun||Sundanese|Sundanesisch
sv|swe||Swedish|Schwedisch
sw|swa||Swahili|Swahili;Suaheli
ta|tam||Tamil|Tamil
te|tel||Telugu|Telugu
tg|tgk||Tajik|Tadschikisch
th|tha||Thai|Thailändisch;Thai
ti|tir||Tigrinya|Tigrinya
tk|tuk||Turkmen|Turkmenisch
tl|tgl||Tagalog;Filipino|Tagalog;Filipino
tn|tsn||Tswana;Setswana|Setswana
to|ton||Tongan;Tonga|Tongaisch
tr|tur||Turkish|Türkisch
ts|tso||Tsonga|Xitsonga
tt|tat||Tatar|Tatarisch
tw|twi||Twi|Twi
ty|tah||Tahitian|Tahitianisch
ug|uig||Uyghur;Uighur|Uigurisch
uk|ukr||Ukrainian|Ukrainisch
ur|urd||Urdu|Urdu
uz|uzb||Uzbek|Usbekisch
ve|ven||Venda|Tshivenda
vi|vie||Vietnamese|Vietnamesisch
vo|vol||Volapük|Volapük
wa|wln||Walloon|Wallonisch
wo|wol||Wolof|Wolof
xh|xho||Xhosa|Xhosa
yi|yid||Yiddish|Jiddisch
yo|yor||Yoruba|Yoruba
za|zha||Zhuang;Chuang|Zhuang
zh|zho|chi|Chinese;Mandarin;Cantonese|Chinesisch;Mandarin;Kantonesisch
zh|||Simplified Chinese;Chinese (Simplified);Traditional Chinese;Chinese (Traditional)|Vereinfachtes Chinesisch
zh||||Traditionelles Chinesisch;Chinesisch (vereinfacht);Chinesisch (traditionell)
zu|zul||Zulu|Zulu
"""

#: ISO 639-2 codes without an ISO 639-1 code of their own that stand for a language with one.
_EXTRA_CODES = {"fil": "tl"}
#: The most words a language name has.
MAX_NAME_WORDS = 3

_WORDS = re.compile(r"[^\W_]+")
_EXPANDED = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})


def entries() -> Iterator[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    """Every row of the table: the ISO 639-1 code, all its codes, all its names."""
    for line in _TABLE.strip().splitlines():
        code, terminology, bibliographic, english, german = line.split("|")
        codes = tuple(value for value in (code, terminology, bibliographic) if value)
        names = tuple(name for name in (*english.split(";"), *german.split(";")) if name)
        yield code, codes, names


def name_key(text: str) -> str:
    """A name as its words in lower case, joined by one space: ``Portuguese (Brazil)`` is ``portuguese brazil``."""
    return " ".join(_WORDS.findall(nfc(text).casefold()))


def _plain(text: str) -> str:
    return "".join(
        character for character in unicodedata.normalize("NFKD", text) if not unicodedata.combining(character)
    )


def spellings(key: str) -> set[str]:
    """A name key as written, with ``ae``, ``oe``, ``ue`` for the umlauts, and without accents."""
    return {key, key.translate(_EXPANDED), _plain(key)}


def _build() -> tuple[dict[str, str], dict[str, str], frozenset[str]]:
    by_code: dict[str, str] = dict(_EXTRA_CODES)
    by_name: dict[str, str] = {}
    iso: set[str] = set()
    for code, codes, names in entries():
        iso.add(code)
        for value in codes:
            by_code[value] = code
        for name in names:
            for spelling in spellings(name_key(name)):
                by_name.setdefault(spelling, code)
    return by_code, by_name, frozenset(iso)


_BY_CODE, _BY_NAME, ISO_CODES = _build()


def by_code(word: str) -> str | None:
    """The ISO 639-1 code of a word of 2 or 3 ASCII letters that is an ISO 639-1 or ISO 639-2 code; else None."""
    lowered = word.casefold()
    if len(lowered) not in (2, 3) or not (lowered.isascii() and lowered.isalpha()):
        return None
    return _BY_CODE.get(lowered)


def by_name(text: str) -> str | None:
    """The ISO 639-1 code of a language name in English or German; else None."""
    key = name_key(text)
    if not key or key.count(" ") >= MAX_NAME_WORDS:
        return None
    return _BY_NAME.get(key) or _BY_NAME.get(_plain(key))
