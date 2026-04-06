# NightCrawler — Crawler OSINT asynchrone via Tor

Crawler web asynchrone en Python parcourant récursivement les pages internes d'un site (clearnet ou `.onion`), extrayant et comptant la fréquence de mots-clés ciblés sur chaque page visitée. Toutes les requêtes transitent par le réseau **Tor** via un proxy SOCKS5 local.

---

## Fonctionnalités

- Crawl récursif des liens internes d'un domaine cible
- Anonymisation des requêtes via Tor (SOCKS5)
- Rotation aléatoire du User-Agent
- Comptage des occurrences de mots-clés configurables
- Gestion de la concurrence par sémaphore (20 workers asynchrones)
- Agrégation thread-safe des résultats via verrou asyncio
- Compatible sites `.onion` et clearnet

---

## Prérequis

### Système
- Python 3.10+
- Service **Tor** en écoute sur `127.0.0.1:9050`

```bash
# Debian/Ubuntu
sudo apt install tor
sudo systemctl start tor

# Vérification
curl --socks5-hostname 127.0.0.1:9050 https://check.torproject.org/api/ip
```

### Dépendances Python

```bash
pip install aiohttp aiohttp-socks selectolax
```

---

## Installation

```bash
git clone <repo>
cd tor-crawler
pip install -r requirements.txt
```

**requirements.txt**
```
aiohttp
aiohttp-socks
selectolax
```

---

## Utilisation

```bash
python3 tor_crawler.py <url> <fichier_mots>
```

### Arguments

| Argument        | Description                                          |
|-----------------|------------------------------------------------------|
| `url`           | URL de départ (ex: `http://example.onion`)           |
| `fichier_mots`  | Fichier texte contenant un mot-clé par ligne         |

### Exemple

```bash
# Fichier mots-clés
echo -e "exploit\nmalware\ncredential\nransomware" > keywords.txt

# Lancement
python3 tor_crawler.py http://example.onion keywords.txt
```

### Sortie

```
Démarrage du crawling de http://example.onion
http://example.onion - Statut: 200 - <!DOCTYPE html>
http://example.onion/about add to list
...
Crawling terminé. 42 URLs ont été visitées.
Liens internes trouvés: 87
Liens externes trouvés: 14

Liste des URLs visitées:
http://example.onion/
http://example.onion/about
http://example.onion/contact
...
```

---

## Architecture

```
tor_crawler.py
├── Utils           — Chargement du fichier de mots-clés
├── Parser          — Extraction des liens et comptage de mots (HTML)
├── TorRequester    — Client HTTP asynchrone via proxy Tor (context manager)
├── SharedDict      — Agrégation thread-safe des compteurs (asyncio.Lock)
└── AsyncCrawler    — Orchestration : file d'URLs, workers, résultats
```

### Flux de données

```
start_url
    │
    ▼
asyncio.Queue (URLs à visiter)
    │
    ├──► Worker 1 ──► TorRequester.fetch() ──► Parser.get_links()  ──► Queue (liens internes)
    ├──► Worker 2                          └──► Parser.get_words() ──► SharedDict (compteurs)
    └──► Worker N
```

---

## Configuration

Les constantes en tête de fichier permettent d'ajuster le comportement :

| Constante           | Valeur par défaut          | Description                          |
|---------------------|----------------------------|--------------------------------------|
| `TOR_PROXY`         | `socks5://127.0.0.1:9050`  | Adresse du proxy Tor                 |
| `USER_AGENT`        | 3 entrées (Chrome/Safari)  | Pool de User-Agents rotatifs         |
| `concurrency_limit` | `10` (TorRequester)        | Requêtes HTTP simultanées max        |
| workers             | `20` (dans `crawl()`)      | Nombre de workers asyncio            |

---

## Limitations connues

- Les variantes morphologiques des mots-clés ne sont pas gérées (pluriels, accords) — une normalisation par stemming est prévue
- Le rapport de comptage est affiché en console ; l'export fichier avec annotation par URL est en cours de développement (voir TODO dans le code)
- `_check_value_queue` consomme la file sans la restaurer — réservé au débogage
- Pas de gestion des redirections HTTP (3xx)
- Timeout fixe de 30s par requête (latence Tor variable)

---

## Avertissement légal

Cet outil est développé à des fins de **recherche en sécurité et OSINT légal**. Son utilisation doit respecter les législations en vigueur et les conditions d'utilisation des sites ciblés. L'auteur décline toute responsabilité en cas d'usage illicite.
